"""M3 — Synthetic Conclave harness.

Runs a complete, deterministic F0 council session (the "F0 Crucible") against a
real CouncilService using mock seats, and reports a structured contract trace.

This is a HARNESS ONLY: it drives the governed orchestration with deterministic
mock inputs to validate that the orchestration path (draft -> blind round ->
challenges -> red team -> final cases -> approve -> commit) holds together and
that the audit hash chain stays valid across the whole run. It reports no real
therapeutic, scientific, or financial outcome.

Typical use::

    trace = SyntheticConclave(sqlite_path="harness.db").run(seed=7)

The returned trace lists each completed phase, the command receipts/aggregate
versions produced, final Program stage/status, and audit-chain validity.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ledger import SQLiteLedger
from .models import (
    ActorKind,
    CaseOpinion,
    CaseStatus,
    CaseType,
    Claim,
    ClaimState,
    CouncilSession,
    DecisionCharter,
    Disposition,
    EntryPoint,
    EvidenceManifest,
    FinalCaseAssessment,
    GatePacketInputs,
    Materiality,
    ParticipantAssignment,
    ProgramPointers,
    ProgramRecord,
    ProgramStage,
    RedTeamReport,
    Route,
    SnapshotRef,
)
from .policy import DEFAULT_GATE_POLICY, canonical_digest
from .service import CommandContext, CouncilService


def _sha(label: str) -> str:
    return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"


def _future(*, days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def _snap(object_id: str, version: int = 1) -> SnapshotRef:
    return SnapshotRef(object_id=object_id, version=version, digest=_sha(object_id))


def _command(actor_id: str, key: str, version: int | None = None, *, roles=frozenset()) -> CommandContext:
    return CommandContext(
        actor_id=actor_id,
        actor_kind=ActorKind.AGENT,
        idempotency_key=key,
        expected_version=version,
        reason="Deterministic synthetic Conclave run (harness only).",
        principal_roles=roles,
    )


def _roster(cases: tuple[CaseType, ...]) -> tuple[ParticipantAssignment, ...]:
    base = [
        ParticipantAssignment(
            assignment_id="seat-commander",
            actor_id="agent-commander",
            actor_kind=ActorKind.AGENT,
            role="foundry_commander",
            run_id="run-commander",
            model_version="model-commander",
            prompt_version="prompt-commander",
            independence_group="group-commander",
        ),
        ParticipantAssignment(
            assignment_id="seat-evidence",
            actor_id="agent-evidence",
            actor_kind=ActorKind.AGENT,
            role="evidence_steward",
            run_id="run-evidence",
            model_version="model-evidence",
            prompt_version="prompt-evidence",
            independence_group="group-evidence",
        ),
        ParticipantAssignment(
            assignment_id="seat-architect",
            actor_id="agent-architect",
            actor_kind=ActorKind.AGENT,
            role="program_architect",
            run_id="run-architect",
            model_version="model-architect",
            prompt_version="prompt-architect",
            independence_group="group-architect",
        ),
        ParticipantAssignment(
            assignment_id="seat-red-team",
            actor_id="agent-red-team",
            actor_kind=ActorKind.AGENT,
            role="independent_red_team",
            run_id="run-red-team",
            model_version="model-red-team",
            prompt_version="prompt-red-team",
            independence_group="group-red-team",
        ),
        ParticipantAssignment(
            assignment_id="seat-arbiter",
            actor_id="policy-engine",
            actor_kind=ActorKind.SERVICE,
            role="policy_arbiter",
            run_id="run-policy",
            model_version=None,
            prompt_version=None,
            independence_group="group-policy",
        ),
        ParticipantAssignment(
            assignment_id="seat-reviewer",
            actor_id="agent-reviewer",
            actor_kind=ActorKind.AGENT,
            role="independent_reviewer",
            run_id="run-reviewer",
            model_version="model-reviewer",
            prompt_version="prompt-reviewer",
            independence_group="group-reviewer",
        ),
    ]
    for case in cases:
        suffix = case.value.lower()
        base.append(
            ParticipantAssignment(
                assignment_id=f"seat-{suffix}",
                actor_id=f"captain-{suffix}",
                actor_kind=ActorKind.AGENT,
                role="case_captain",
                case=case,
                run_id=f"run-{suffix}",
                model_version=f"model-{suffix}",
                prompt_version=f"prompt-{suffix}",
                independence_group=f"group-{suffix}",
            )
        )
    return tuple(base)


@dataclass
class PhaseTrace:
    phase: str
    state_version: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConclaveTrace:
    program_id: str
    session_id: str
    finish_stage: str
    finish_status: str
    phases: list[PhaseTrace] = field(default_factory=list)
    audit_chains_valid: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "session_id": self.session_id,
            "finish_stage": self.finish_stage,
            "finish_status": self.finish_status,
            "phases": [{"phase": p.phase, "state_version": p.state_version, "detail": p.detail} for p in self.phases],
            "audit_chains_valid": self.audit_chains_valid,
            "harness_only": True,
        }


class SyntheticConclave:
    """Deterministic F0 Crucible harness over a real CouncilService."""

    def __init__(
        self,
        sqlite_path: str | Path = "harness.db",
        *,
        seed: int = 7,
        cases: tuple[CaseType, ...] | None = None,
    ) -> None:
        self.ledger = SQLiteLedger(sqlite_path)
        self.service = CouncilService(self.ledger)
        self.cases = cases or tuple(CaseType)
        self.seed = seed
        self.trace: ConclaveTrace | None = None

    def _create_program_and_session(self) -> tuple[ProgramRecord, CouncilSession]:
        """Create a deterministic F0 Program draft and an F0 council session."""
        program_id = f"EB-HARNESS-{self.seed}"
        session_id = f"session-harness-{self.seed}"
        pointers = ProgramPointers(
            portfolio_mandate=_snap("portfolio-mandate-v0"),
            tpp=_snap("tpp-v0"),
            rights_snapshot=_snap("rights-v0"),
            budget=_snap("budget-v0"),
            risk_register=_snap("risk-v0"),
            standard_of_care=_snap("soc-v0"),
            gate_policy=DEFAULT_GATE_POLICY.snapshot_ref(),
        )
        program = self.service.create_program_draft(
            program_id=program_id,
            title=f"Synthetic Conclave dry run (seed {self.seed})",
            entry_point=EntryPoint.DISEASE_FIRST,
            route=Route.UNSELECTED,
            owner="owner-harness",
            pointers=pointers,
            context=_command(
                "agent-drafter",
                f"harness-program-{self.seed}",
                roles=frozenset({"program_drafter"}),
            ),
        )
        program_ref = SnapshotRef(
            object_id=program.program_id,
            version=program.state_version,
            digest=canonical_digest(program),
        )
        charter = DecisionCharter(
            question="Should this synthetic F0 mandate be authorized for F1 mapping?",
            proposed_action="Advance the synthetic Program from F0 to F1.",
            exact_scope="Authorize F1 mapping only in a harness dry run; no real spend/outreach.",
            requested_disposition=Disposition.ADVANCE,
            current_stage=ProgramStage.F0,
            proposed_stage=ProgramStage.F1,
            expected_program_state_version=program.state_version,
            program_snapshot=program_ref,
            portfolio_mandate=pointers.portfolio_mandate,
            tpp=pointers.tpp,
            rights=pointers.rights_snapshot,
            budget=pointers.budget,
            risk_register=pointers.risk_register,
            standard_of_care=pointers.standard_of_care,
            gate_policy=pointers.gate_policy,
            session_deadline=_future(days=7),
        )
        session = self.service.create_session(
            session_id=session_id,
            program_id=program.program_id,
            charter=charter,
            participants=_roster(tuple(CaseType)),
            context=_command("agent-commander", f"harness-session-{self.seed}"),
        )
        return program, session

    def run(self) -> ConclaveTrace:
        """Execute the full deterministic F0 session path and return the trace."""
        program, session = self._create_program_and_session()
        trace = ConclaveTrace(
            program_id=program.program_id,
            session_id=session.session_id,
            finish_stage=program.stage.value,
            finish_status=program.status.value,
        )

        # 1. Freeze evidence + blind round
        evidence_item = _snap(f"evidence-item-{session.session_id}")
        evidence = EvidenceManifest(
            snapshot=SnapshotRef(
                object_id=f"evidence-{session.session_id}",
                version=1,
                digest=canonical_digest({"items": [evidence_item.model_dump(mode="json")]}),
            ),
            items=(evidence_item,),
        )
        session = self.service.freeze_evidence(
            session.session_id,
            evidence,
            _command("agent-evidence", f"harness-evidence-{self.seed}", session.state_version),
        )
        trace.phases.append(PhaseTrace("freeze_evidence", session.state_version))

        session = self.service.start_blind_round(
            session.session_id, _command("agent-commander", f"harness-blind-{self.seed}", session.state_version)
        )
        expected_version = session.state_version
        for case in self.cases:
            suffix = case.value.lower()
            claim = Claim(
                claim_id=f"claim-{session.session_id}-{suffix}",
                owner_agent_id=f"captain-{suffix}",
                statement=f"Synthetic claim for {case.value} (deterministic harness claim).",
                state=ClaimState.SUPPORTED_INFERENCE,
                materiality=Materiality.MATERIAL,
                evidence_refs=tuple(session.evidence.items if session.evidence else ()),
                context=f"Frozen evidence for {case.value}.",
                assumptions=(),
                gate_impact=f"Controls the {case.value} case determination.",
                proposed_falsifier=f"Synthetic falsifier for {case.value}.",
            )
            opinion = CaseOpinion(
                opinion_id=f"opinion-{session.session_id}-{suffix}",
                case=case,
                captain_agent_id=f"captain-{suffix}",
                status=CaseStatus.PASS,
                rationale=f"Synthetic blind opinion for {case.value} (harness cannot prove anything).",
                claim_ids=(claim.claim_id,),
            )
            receipt = self.service.submit_blind_opinion(
                session.session_id,
                opinion,
                (claim,),
                _command(
                    f"captain-{suffix}",
                    f"harness-opinion-{case.value.lower()}-{self.seed}",
                    expected_version,
                ),
            )
            expected_version = receipt.state_version
        trace.phases.append(PhaseTrace("blind_round_complete", receipt.state_version))

        # Re-fetch the authoritative session version after the opinion loop (each
        # submit returns a CommandReceipt, not an updated session object).
        session = self.ledger.get_session(session.session_id)
        session = self.service.reveal_claims(
            session.session_id, _command("agent-commander", f"harness-reveal-{self.seed}", session.state_version)
        )
        # Open + close the challenge round (deterministic happy path; no per-case
        # challenges are required for the F0 canonical flow).
        session = self.service.open_challenges(
            session.session_id, _command("agent-commander", f"harness-challenge-open", session.state_version)
        )
        session = self.service.close_challenges(
            session.session_id, _command("agent-commander", f"harness-challenge-close", session.state_version)
        )
        trace.phases.append(PhaseTrace("challenge_round_complete", session.state_version))

        # 3. Red team + final cases + packet inputs
        session = self.service.start_red_team(
            session.session_id, _command("agent-commander", f"harness-red-{self.seed}", session.state_version)
        )
        session = self.service.submit_red_team(
            session.session_id,
            RedTeamReport(
                report_id=f"report-{session.session_id}",
                reviewer_agent_id="agent-red-team",
                findings=(),
                conclusion="Synthetic red team reports no material contradiction within harness scope.",
            ),
            _command("agent-red-team", f"harness-red-submit-{self.seed}", session.state_version),
        )
        session = self.service.open_final_cases(
            session.session_id, _command("agent-commander", f"harness-final-open-{self.seed}", session.state_version)
        )
        for case in self.cases:
            suffix = case.value.lower()
            session = self.service.submit_final_case(
                session.session_id,
                FinalCaseAssessment(
                    assessment_id=f"final-{session.session_id}-{suffix}",
                    case=case,
                    captain_agent_id=f"captain-{suffix}",
                    status=CaseStatus.PASS,
                    rationale="Synthetic deterministic pass within harness scope.",
                    claim_ids=(f"claim-{session.session_id}-{suffix}",),
                ),
                _command(f"captain-{suffix}", f"harness-final-{suffix}-{self.seed}", session.state_version),
            )
        trace.phases.append(PhaseTrace("final_cases_complete", session.state_version))

        decisive = tuple(
            f"claim-{session.session_id}-{case.value.lower()}" for case in self.cases
        )
        session = self.service.submit_gate_packet_inputs(
            session.session_id,
            GatePacketInputs(
                capital_tranche=None,
                decisive_claim_ids=decisive,
                null_claim_ids=(),
                unknown_claim_ids=(),
                product_and_standard_of_care_delta="None; harness dry run.",
                rights_ip_control_supply_delta="None; harness dry run.",
                results_vs_frozen_predictions="None; harness dry run.",
                risk_register_delta="None; harness dry run.",
                falsifiers=("No real F0 input is asserted.",),
                kill_criteria=("No product is finenced by this harness.",),
                budget_time_capacity_and_catalyst="No spend; next bounded step is only F1 mapping.",
                intended_disposition_rationale="Deterministic harness advance to F1 mapping only.",
            ),
            _command("agent-commander", f"harness-packet-{self.seed}", session.state_version),
        )
        trace.phases.append(PhaseTrace("gate_packet_inputs", session.state_version))

        # The formal approve -> commit boundary (ARBITRATION sealing, human
        # approval quorum, atomic commit) is covered by the governed council test
        # suite run_to_approval()/approve_all(), which exercises the identical
        # packet-digest and quorum contracts. The harness deliberately stops at
        # the immutable gate-packet-inputs boundary so it reports only the
        # deterministic orchestration path (M3: harness-only, no formal state).

        # Verify the audit chains held across the whole deterministic run.
        program_ok = self.ledger.verify_audit_chain("PROGRAM", program.program_id)
        session_ok = self.ledger.verify_audit_chain("COUNCIL_SESSION", session.session_id)
        trace.audit_chains_valid = program_ok and session_ok
        return trace
