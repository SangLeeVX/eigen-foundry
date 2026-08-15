"""M7 — governed F0–F8 preclinical stage runner (dry run).

Drives a Program through the FULL governed F0→F8 sequence for a given route
(rescue/asset via F6A, de novo via F6B, combination via F6C) with **no gate
skips**: each consecutive stage is evaluated by the preclinical gate policy and
committed only when admissible (real observed/supported evidence, never a
MODEL_PREDICTION).

For each stage it records a traceable per-stage packet:
  stage, route, tpp digest, decisive claim states, gate verdict, resulting
  program revision.

At the end it emits the route's transferable package anchors (what a
third-party reader would inspect) — a dry run only; it never implies a real
therapeutic outcome.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .frozen_tpp import FrozenTPP, make_frozen_tpp
from .governed_advance import GovernedAdvance
from .ledger import SQLiteLedger
from .models import (
    ActorKind,
    AuditEvent,
    ClaimState,
    EntryPoint,
    ProgramPointers,
    ProgramRecord,
    ProgramStage,
    Route,
    SnapshotRef,
)
from .route_policy import (
    DE_NOVO_ROUTES,
    PreclinicalGatePolicy,
    f6_stage_for,
    full_stage_sequence,
)
from .service import CouncilService, CommandContext


def _sha(x: str) -> str:
    return f"sha256:{hashlib.sha256(x.encode()).hexdigest()}"


def _packet_canon(data: dict[str, Any]) -> str:
    return _sha(json.dumps(data, sort_keys=True, separators=(",", ":"), default=str))


@dataclass
class StagePacket:
    stage: str
    route: str
    tpp_digest: str
    decisive_claim_states: list[str]
    gate_passed: bool
    gate_blockers: list[str]
    program_revision: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "route": self.route,
            "tpp_digest": self.tpp_digest,
            "decisive_claim_states": self.decisive_claim_states,
            "gate_passed": self.gate_passed,
            "gate_blockers": self.gate_blockers,
            "program_revision": self.program_revision,
        }


@dataclass
class StageRunResult:
    route: Route
    stage_packets: list[StagePacket] = field(default_factory=list)
    sequence_complete: bool = False
    transferable_package_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route.value,
            "stage_packets": [p.to_dict() for p in self.stage_packets],
            "sequence_complete": self.sequence_complete,
            "transferable_package_digest": self.transferable_package_digest,
            "harness_only": True,
        }


class StageRunner:
    """Dry-run stage advancer with no gate skips and per-stage traceable packets."""

    M7_STAGES = frozenset(
        {
            ProgramStage.F0, ProgramStage.F1, ProgramStage.F2, ProgramStage.F3,
            ProgramStage.F4, ProgramStage.F5, ProgramStage.F6A, ProgramStage.F6B,
            ProgramStage.F6C, ProgramStage.F7, ProgramStage.F8,
        }
    )

    def __init__(
        self,
        sqlite_path: str = "m7.db",
        *,
        route: Route,
        seed: int = 7,
        tpp: FrozenTPP | None = None,
        evidence_hook=None,
    ) -> None:
        from .policy import GatePolicy

        self.sqlite_path = sqlite_path
        self.route = route
        self.seed = seed
        self.tpp = tpp or make_frozen_tpp(seed=seed)
        self.evidence_hook = evidence_hook or (lambda stage: [ClaimState.OBSERVED])
        self.gate_policy = GatePolicy(
            policy_id="policy-foundry-council-m7-dryrun",
            version=1,
            enabled_gates=self.M7_STAGES,
        )
        self.ledger = SQLiteLedger(sqlite_path)
        self.service = CouncilService(self.ledger, gate_policy=self.gate_policy)
        self.sequence = full_stage_sequence(route)
        self.gate = PreclinicalGatePolicy(route=route, tpp_digest=self.tpp.digest)
        self.program = self._create_program(route)
        self._governed = GovernedAdvance(self.service, seed=seed)

    def _create_program(self, route: Route) -> ProgramRecord:
        # Create at F0 (route UNSELECTED), then select the route at F5 per policy.
        entry = EntryPoint.DISEASE_FIRST if route in DE_NOVO_ROUTES else EntryPoint.UNSPECIFIED
        program_id = f"M7-{route.value[:8]}-{self.seed}"
        pointers = ProgramPointers(
            portfolio_mandate=SnapshotRef(object_id="pm-v0", version=1, digest=_sha("pm-v0")),
            tpp=SnapshotRef(object_id="tpp-v0", version=1, digest=_sha("tpp-v0")),
            rights_snapshot=SnapshotRef(object_id="rights-v0", version=1, digest=_sha("rights-v0")),
            budget=SnapshotRef(object_id="budget-v0", version=1, digest=_sha("budget-v0")),
            risk_register=SnapshotRef(object_id="risk-v0", version=1, digest=_sha("risk-v0")),
            standard_of_care=SnapshotRef(object_id="soc-v0", version=1, digest=_sha("soc-v0")),
            gate_policy=self.gate_policy.snapshot_ref(),
        )
        ctx = CommandContext(
            actor_id="agent-drafter",
            actor_kind=ActorKind.AGENT,
            idempotency_key=f"m7-program-{self.seed}",
            expected_version=None,
            reason=f"Authorized M7 F0-F8 dry run ({route.value}).",
            principal_roles=frozenset({"program_drafter"}),
        )
        return self.service.create_program_draft(
            program_id=program_id,
            title=f"M7 {route.value} F0-F8 dry run (seed {self.seed})",
            entry_point=entry,
            route=Route.UNSELECTED,
            owner="owner-dryrun",
            pointers=pointers,
            context=ctx,
        )

    def run(self) -> StageRunResult:
        result = StageRunResult(route=self.route)
        program = self.program
        # Advance through the governed sequence: F0 -> F1 -> ... -> F8
        nonce = 0
        for target in self.sequence[1:]:  # sequence[0] is F0 (already at)
            decisive = self.evidence_hook(target)
            verdict = self.gate.evaluate(decisive, stage=target)
            if not verdict.passed:
                # A blocked gate must NOT advance (no gate skips).
                raise RuntimeError(
                    f"gate at {target.value} blocked: {verdict.blockers}"
                )
            program = self._advance(program, target, decisive, verdict, nonce=nonce)
            nonce += 1
            result.stage_packets.append(
                StagePacket(
                    stage=target.value,
                    route=self.route.value,
                    tpp_digest=self.tpp.digest,
                    decisive_claim_states=[c.value for c in decisive],
                    gate_passed=verdict.passed,
                    gate_blockers=list(verdict.blockers),
                    program_revision=program.state_version,
                )
            )
        result.sequence_complete = program.stage == ProgramStage.F8
        # Emit the transferable package digest (third-party-readable anchor).
        package = {
            "route": self.route.value,
            "tpp": self.tpp.to_dict(),
            "final_stage": program.stage.value,
            "stage_packets": [p.to_dict() for p in result.stage_packets],
        }
        result.transferable_package_digest = _packet_canon(package)
        return result

    def _advance(
        self,
        program: ProgramRecord,
        target: ProgramStage,
        decisive: list[ClaimState],
        verdict,
        nonce: int = 0,
    ) -> ProgramRecord:
        # Route through the GOVERNED commit path only (respects M2-C4: formal
        # stage changes must flow through commit_gate_decision, never save_program).
        route_for_stage = self.route if target in (ProgramStage.F6A, ProgramStage.F6B, ProgramStage.F6C) else program.route
        new_program, _session = self._governed.advance(
            program,
            proposed_stage=target,
            route=route_for_stage,
            decisive_claim_states=decisive,
            nonce=nonce,
        )
        return new_program

