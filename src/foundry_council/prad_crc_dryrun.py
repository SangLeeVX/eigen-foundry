"""M6 — authorized CRC/PRAD F0–F2 dry run producing traceable packets.

Composes the M6 components to run ONE authorized CRC (Community Resource cohort)
or PRAD (Patient Registry and data) F0–F2 dry run:

  - Creates a program tagged to the workspace kind (CRC or PRAD).
  - Produces decisive claims from Eigen-1 (always ClaimState.MODEL_PREDICTION).
  - Applies the F0F2GatePolicy to CONFIRM model predictions never satisfy an
    experimental gate (the M6 exit criterion).
  - Emits a traceable packet: a content-addressed digest of the dry-run session
    state + the mapping of each model prediction to its MODEL_PREDICTION label.

It never changes formal Program state (dry run) and never upgrades model output.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .eigen1_gateway import Eigen1Gateway
from .f0f2_policies import F0F2GatePolicy
from .ledger import SQLiteLedger
from .models import ActorKind, ClaimState, CouncilSession, ProgramRecord
from .service import CommandContext, CouncilService


def _cannoness(data: dict[str, Any]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    )


@dataclass
class DryRunPacket:
    workspace_kind: str
    program_id: str
    session_id: str
    packet_digest: str
    decisive_model_claims: list[dict[str, Any]] = field(default_factory=list)
    gate_verdict: dict[str, Any] = field(default_factory=dict)
    model_prediction_labels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_kind": self.workspace_kind,
            "program_id": self.program_id,
            "session_id": self.session_id,
            "packet_digest": self.packet_digest,
            "decisive_model_claims": self.decisive_model_claims,
            "gate_verdict": self.gate_verdict,
            "model_prediction_labels": self.model_prediction_labels,
            "harness_only": True,
            "real_therapeutic_advance": False,
        }


class PradCrcDryRun:
    """Runs an authorized CRC/PRAD F0–F2 dry run and emits a traceable packet."""

    def __init__(
        self,
        sqlite_path: str | Path = "dryrun.db",
        *,
        workspace_kind: str = "CRC",
        seed: int = 7,
        model_version: str = "eigen-1.0",
    ) -> None:
        if workspace_kind not in {"CRC", "PRAD"}:
            raise ValueError("workspace_kind must be CRC or PRAD")
        self.sqlite_path = sqlite_path
        self.workspace_kind = workspace_kind
        self.seed = seed
        self.gateway = Eigen1Gateway(model_version=model_version, prompt_version="prompt-f0f2", run_id=f"run-{seed}")
        self.policy = F0F2GatePolicy(admission_kind=workspace_kind)
        self.ledger = SQLiteLedger(sqlite_path)
        self.service = CouncilService(self.ledger)

    def run(self) -> DryRunPacket:
        program = self._create_program()
        # Produce decisive claims from Eigen-1 (all MODEL_PREDICTION).
        model_claims = self._produce_model_claims(program.program_id)
        decisive_states = [ClaimState(c["state"]) for c in model_claims]
        # Confirm model predictions never satisfy an experimental gate.
        verdict = self.policy.evaluate_decisive_claims(
            decisive_states, stage=program.stage, packet_kind="DRY_RUN"
        )
        # Build the traceable packet over the frozen model claims + gate verdict.
        packet_state = {
            "workspace_kind": self.workspace_kind,
            "program_id": program.program_id,
            "model_claims": model_claims,
            "gate_verdict": verdict.to_dict(),
        }
        packet_digest = _cannoness(packet_state)
        labels = {c["claim_id"]: c["state"] for c in model_claims}

        return DryRunPacket(
            workspace_kind=self.workspace_kind,
            program_id=program.program_id,
            session_id="none",  # dry run stops before any council session is constituted
            packet_digest=packet_digest,
            decisive_model_claims=model_claims,
            gate_verdict=verdict.to_dict(),
            model_prediction_labels=labels,
        )

    def _create_program(self) -> ProgramRecord:
        from .models import EntryPoint, ProgramPointers, Route, SnapshotRef
        from .policy import DEFAULT_GATE_POLICY

        def sha(x: str) -> str:
            return f"sha256:{hashlib.sha256(x.encode()).hexdigest()}"

        entry = EntryPoint.DISEASE_FIRST if self.workspace_kind == "CRC" else EntryPoint.PATIENT_SEGMENT_FIRST
        program_id = f"{self.workspace_kind}-DRY-{self.seed}"
        pointers = ProgramPointers(
            portfolio_mandate=SnapshotRef(object_id="pm-v0", version=1, digest=sha("pm-v0")),
            tpp=SnapshotRef(object_id="tpp-v0", version=1, digest=sha("tpp-v0")),
            rights_snapshot=SnapshotRef(object_id="rights-v0", version=1, digest=sha("rights-v0")),
            budget=SnapshotRef(object_id="budget-v0", version=1, digest=sha("budget-v0")),
            risk_register=SnapshotRef(object_id="risk-v0", version=1, digest=sha("risk-v0")),
            standard_of_care=SnapshotRef(object_id="soc-v0", version=1, digest=sha("soc-v0")),
            gate_policy=DEFAULT_GATE_POLICY.snapshot_ref(),
        )
        ctx = CommandContext(
            actor_id="agent-drafter",
            actor_kind=ActorKind.AGENT,
            idempotency_key=f"dryrun-program-{self.seed}",
            expected_version=None,
            reason="Authorized F0-F2 dry run (harness only).",
            principal_roles=frozenset({"program_drafter"}),
        )
        return self.service.create_program_draft(
            program_id=program_id,
            title=f"{self.workspace_kind} F0-F2 dry run (seed {self.seed})",
            entry_point=entry,
            route=Route.UNSELECTED,
            owner="owner-dryrun",
            pointers=pointers,
            context=ctx,
        )

    def _produce_model_claims(self, program_id: str) -> list[dict[str, Any]]:
        contexts = {
            "CRC": ("Community resource cohort support", "Mechanism hypothesis"),
            "PRAD": ("Patient registry case support", "Intervention direction"),
        }
        context, mech = contexts[self.workspace_kind]
        claims = []
        for i in range(3):
            p = self.gateway.predict(
                prediction_id=f"{self.workspace_kind}-pred-{i}",
                program_id=program_id,
                context=context,
                mechanism=f"{mech} #{i}",
                calibration=0.61 + i * 0.01,
                uncertainty="Model-only; not experimentally validated.",
            )
            claims.append(
                {
                    "claim_id": p.prediction_id,
                    "state": p.state.value,  # always MODEL_PREDICTION
                    "digest": p.digest,
                }
            )
        return claims
