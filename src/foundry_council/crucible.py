"""M5 — self-contained F0 Crucible driver (to human approval).

Encapsulates the deterministic F0 council-session drive through the
CouncilService API — no test-suite dependency — so the M5 acceptance runner and
other production/acceptance code can reach the human-approval boundary
(AWAITING_HUMAN_APPROVAL) without importing tests.helpers.

Steps covered here (used by M5 acceptance):
  - create a synthetic Program + F0 council session (roster, charter, evidence)
  - freeze evidence
  - blind round (5 case captains -> claim + opinion)
  - reveal claims, open/close challenges
  - red team
  - final case assessments (all PASS)
  - gate packet inputs
  - arbitrate (seal the immutable packet)
  - request human approval

The driver never changes formal Program state beyond the governed service path;
the caller then applies authenticated approvals + the restricted commit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
from .service import CouncilService


def _sha(label: str) -> str:
    return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"


def _snap(object_id: str, version: int = 1) -> SnapshotRef:
    return SnapshotRef(object_id=object_id, version=version, digest=_sha(object_id))


def _future(*, hours: int = 12) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def _command(actor_id: str, key: str, version: int | None = None, *, kind=ActorKind.AGENT, roles=frozenset()):
    from .service import CommandContext

    return CommandContext(
        actor_id=actor_id,
        actor_kind=kind,
        idempotency_key=key,
        expected_version=version,
        reason="Deterministic F0 Crucible drive (acceptance harness only).",
        principal_roles=roles,
    )


def _roster(cases: tuple[CaseType, ...]) -> tuple[ParticipantAssignment, ...]:
    def seat(assignment_id, actor_id, kind, role, run_id, model_version, prompt_version, group, *, case=None):
        return ParticipantAssignment(
            assignment_id=assignment_id, actor_id=actor_id, actor_kind=kind, role=role,
            case=case, run_id=run_id, model_version=model_version, prompt_version=prompt_version,
            independence_group=group,
        )

    base = [
        seat("seat-commander", "agent-commander", ActorKind.AGENT, "foundry_commander",
             "run-commander", "model-commander", "prompt-commander", "group-commander"),
        seat("seat-evidence", "agent-evidence", ActorKind.AGENT, "evidence_steward",
             "run-evidence", "model-evidence", "prompt-evidence", "group-evidence"),
        seat("seat-architect", "agent-architect", ActorKind.AGENT, "program_architect",
             "run-architect", "model-architect", "prompt-architect", "group-architect"),
        seat("seat-red-team", "agent-red-team", ActorKind.AGENT, "independent_red_team",
             "run-red-team", "model-red-team", "prompt-red-team", "group-red-team"),
        seat("seat-arbiter", "policy-engine", ActorKind.SERVICE, "policy_arbiter",
             "run-policy", None, None, "group-policy"),
        seat("seat-reviewer", "agent-reviewer", ActorKind.AGENT, "independent_reviewer",
             "run-reviewer", "model-reviewer", "prompt-reviewer", "group-reviewer"),
    ]
    for case in cases:
        suffix = case.value.lower()
        base.append(
            seat(f"seat-{suffix}", f"captain-{suffix}", ActorKind.AGENT, "case_captain",
                 f"run-{suffix}", f"model-{suffix}", f"prompt-{suffix}", f"group-{suffix}", case=case)
        )
    return tuple(base)


@dataclass
class CrucibleState:
    program: ProgramRecord
    session: CouncilSession
    evidence_item: SnapshotRef


class CrucibleDriver:
    """Drives a deterministic F0 council session to the human-approval boundary."""

    def __init__(self, service: CouncilService, *, seed: int = 7, program_id: str = "CRUC-001") -> None:
        self.service = service
        self.seed = seed
        self.program_id = program_id

    def create(self) -> CrucibleState:
        session_id = f"session-{self.program_id}"
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
            program_id=self.program_id,
            title=f"Synthetic F0 Crucible (seed {self.seed})",
            entry_point=EntryPoint.DISEASE_FIRST,
            route=Route.UNSELECTED,
            owner="owner-harness",
            pointers=pointers,
            context=_command("agent-drafter", f"crucible-program-{self.seed}", roles=frozenset({"program_drafter"})),
        )
        program_ref = SnapshotRef(
            object_id=program.program_id,
            version=program.state_version,
            digest=canonical_digest(program),
        )
        charter = DecisionCharter(
            question="Should this synthetic F0 mandate be authorized for F1 mapping?",
            proposed_action="Advance the synthetic Program from F0 to F1.",
            exact_scope="Authorize F1 mapping only in an acceptance dry run; no real spend.",
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
            session_deadline=_future(hours=24),
        )
        session = self.service.create_session(
            session_id=session_id, program_id=program.program_id, charter=charter,
            participants=_roster(tuple(CaseType)),
            context=_command("agent-commander", f"crucible-session-{self.seed}"),
        )
        return CrucibleState(program, session, _snap(f"evidence-item-{session_id}"))

    def run_to_approval(self, state: CrucibleState) -> CouncilSession:
        service = self.service
        session = state.session
        # Freeze evidence
        evidence = EvidenceManifest(
            snapshot=SnapshotRef(
                object_id=f"evidence-{session.session_id}", version=1,
                digest=canonical_digest({"items": [state.evidence_item.model_dump(mode="json")]}),
            ),
            items=(state.evidence_item,),
        )
        session = service.freeze_evidence(
            session.session_id, evidence,
            _command("agent-evidence", f"crucible-evidence-{self.seed}", session.state_version),
        )
        # Blind round
        session = service.start_blind_round(
            session.session_id, _command("agent-commander", f"crucible-blind-{self.seed}", session.state_version)
        )
        exp_v = session.state_version
        for case in CaseType:
            suffix = case.value.lower()
            claim = Claim(
                claim_id=f"claim-{session.session_id}-{suffix}", owner_agent_id=f"captain-{suffix}",
                statement=f"The {case.value} case satisfies the bounded F0 test input.",
                state=ClaimState.SUPPORTED_INFERENCE, materiality=Materiality.MATERIAL,
                evidence_refs=(state.evidence_item,), context="Synthetic dry-run evidence.",
                gate_impact=f"Supports {case.value} determination.", proposed_falsifier="A required input is absent.",
            )
            opinion = CaseOpinion(
                opinion_id=f"opinion-{session.session_id}-{suffix}", case=case,
                captain_agent_id=f"captain-{suffix}", status=CaseStatus.PASS,
                rationale="All required synthetic dry-run fields are present.", claim_ids=(claim.claim_id,),
            )
            receipt = service.submit_blind_opinion(
                session.session_id, opinion, (claim,),
                _command(f"captain-{suffix}", f"crucible-opinion-{suffix}-{self.seed}", exp_v),
            )
            exp_v = receipt.state_version
        session = service.reveal_claims(
            session.session_id, _command("agent-commander", f"crucible-reveal-{self.seed}", exp_v)
        )
        session = service.open_challenges(
            session.session_id, _command("agent-commander", f"crucible-ch-open-{self.seed}", session.state_version)
        )
        session = service.close_challenges(
            session.session_id, _command("agent-commander", f"crucible-ch-close-{self.seed}", session.state_version)
        )
        # Red team
        session = service.start_red_team(
            session.session_id, _command("agent-commander", f"crucible-red-{self.seed}", session.state_version)
        )
        session = service.submit_red_team(
            session.session_id,
            RedTeamReport(report_id=f"report-{session.session_id}", reviewer_agent_id="agent-red-team",
                          findings=(), conclusion="Synthetic red team: no material contradiction within scope."),
            _command("agent-red-team", f"crucible-red-submit-{self.seed}", session.state_version),
        )
        # Final cases
        session = service.open_final_cases(
            session.session_id, _command("agent-commander", f"crucible-final-open-{self.seed}", session.state_version)
        )
        for case in CaseType:
            suffix = case.value.lower()
            session = service.submit_final_case(
                session.session_id,
                FinalCaseAssessment(assessment_id=f"final-{session.session_id}-{suffix}", case=case,
                                    captain_agent_id=f"captain-{suffix}", status=CaseStatus.PASS,
                                    rationale="Synthetic deterministic pass within harness scope.",
                                    claim_ids=(f"claim-{session.session_id}-{suffix}",)),
                _command(f"captain-{suffix}", f"crucible-final-{suffix}-{self.seed}", session.state_version),
            )
        # Packet inputs
        decisive = tuple(f"claim-{session.session_id}-{c.value.lower()}" for c in CaseType)
        session = service.submit_gate_packet_inputs(
            session.session_id,
            GatePacketInputs(capital_tranche=None, decisive_claim_ids=decisive, null_claim_ids=(), unknown_claim_ids=(),
                             product_and_standard_of_care_delta="None; harness dry run.",
                             rights_ip_control_supply_delta="None; harness dry run.",
                             results_vs_frozen_predictions="None; harness dry run.",
                             risk_register_delta="None; harness dry run.",
                             falsifiers=("No real F0 input asserted.",),
                             kill_criteria=("No product is finenced by this harness.",),
                             budget_time_capacity_and_catalyst="No spend; next bounded step is F1 mapping.",
                             intended_disposition_rationale="Deterministic harness advance to F1 mapping only."),
            _command("agent-commander", f"crucible-packet-{self.seed}", session.state_version),
        )
        # Arbitrate + request approval
        session = service.arbitrate(
            session.session_id, f"arbitration-{session.session_id}", f"packet-{session.session_id}",
            _command("policy-engine", f"crucible-arbitrate-{self.seed}", session.state_version, kind=ActorKind.SERVICE),
        )
        session = service.request_approval(
            session.session_id, f"approval-request-{session.session_id}",
            _future(hours=12),
            _command("agent-commander", f"crucible-approval-{self.seed}", session.state_version),
        )
        return session
