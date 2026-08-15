from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from foundry_council.models import (
    ActorKind,
    Approval,
    ApprovalDecision,
    CaseOpinion,
    CaseStatus,
    CaseType,
    Claim,
    ClaimState,
    DecisionCharter,
    Disposition,
    EntryPoint,
    EvidenceManifest,
    FinalCaseAssessment,
    GatePacketInputs,
    Materiality,
    ParticipantAssignment,
    ProgramPointers,
    ProgramStage,
    RedTeamReport,
    Route,
    SnapshotRef,
)
from foundry_council.policy import DEFAULT_GATE_POLICY, canonical_digest
from foundry_council.service import CommandContext, CouncilService


def digest(label: str) -> str:
    return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"


def snapshot(object_id: str, version: int = 1) -> SnapshotRef:
    return SnapshotRef(object_id=object_id, version=version, digest=digest(f"{object_id}:{version}"))


def roster() -> tuple[ParticipantAssignment, ...]:
    assignments = [
        ParticipantAssignment(
            assignment_id="seat-commander",
            actor_id="agent-commander",
            actor_kind=ActorKind.AGENT,
            role="foundry_commander",
            run_id="run-command",
            model_version="model-command",
            prompt_version="prompt-command",
            independence_group="group-command",
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
    for case in CaseType:
        suffix = case.value.lower()
        assignments.append(
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
    return tuple(assignments)


def command(
    actor_id: str,
    version: int | None,
    key: str,
    *,
    kind: ActorKind = ActorKind.AGENT,
    roles: frozenset[str] = frozenset(),
) -> CommandContext:
    return CommandContext(
        actor_id=actor_id,
        actor_kind=kind,
        idempotency_key=key,
        expected_version=version,
        reason="Test the governed council workflow.",
        principal_roles=roles,
    )


def create_program_and_session(
    service: CouncilService,
    session_id: str = "session-f0-alpha",
    program_id: str = "EB-TEST-001",
) -> tuple:
    pointers = ProgramPointers(
        portfolio_mandate=snapshot("portfolio-mandate-v0"),
        tpp=snapshot("tpp-v0"),
        rights_snapshot=snapshot("rights-v0"),
        budget=snapshot("budget-v0"),
        risk_register=snapshot("risk-v0"),
        standard_of_care=snapshot("soc-v0"),
        gate_policy=DEFAULT_GATE_POLICY.snapshot_ref(),
    )
    program = service.create_program_draft(
        program_id=program_id,
        title="Controlled F0 dry run",
        entry_point=EntryPoint.DISEASE_FIRST,
        route=Route.UNSELECTED,
        owner="owner-program",
        pointers=pointers,
        context=command(
            "agent-drafter",
            None,
            f"cmd-{session_id}-program",
            roles=frozenset({"program_drafter"}),
        ),
    )
    program_ref = SnapshotRef(
        object_id=program.program_id,
        version=program.state_version,
        digest=canonical_digest(program),
    )
    charter = DecisionCharter(
        question="Should this F0 product mandate be authorized for clinical opportunity mapping?",
        proposed_action="Advance the controlled test Program from F0 to F1.",
        exact_scope="Authorize F1 mapping only; no spend, outreach, transaction, or evidence promotion.",
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
        gate_policy_artifact=program.current_versions.gate_policy_artifact,
        session_deadline=datetime.now(timezone.utc) + timedelta(days=1),
    )
    session = service.create_session(
        session_id=session_id,
        program_id=program_id,
        charter=charter,
        participants=roster(),
        context=command("agent-commander", None, f"cmd-{session_id}-create"),
    )
    return program, session


def run_to_final_cases(
    service: CouncilService,
    session,
    *,
    failing_case: CaseType | None = None,
    red_findings=(),
    claim_state_by_case: dict[CaseType, ClaimState] | None = None,
    measured_null_cases: frozenset[CaseType] = frozenset(),
):
    claim_state_by_case = claim_state_by_case or {}
    session, evidence_item = start_blind(service, session)
    blind_version = session.state_version
    for case in CaseType:
        suffix = case.value.lower()
        captain = f"captain-{suffix}"
        claim = Claim(
            claim_id=f"claim-{session.session_id}-{suffix}",
            owner_agent_id=captain,
            statement=f"The {case.value} case satisfies the bounded F0 test input.",
            state=claim_state_by_case.get(case, ClaimState.SUPPORTED_INFERENCE),
            measured_null=case in measured_null_cases,
            materiality=Materiality.MATERIAL,
            evidence_refs=(evidence_item,),
            dependency_clusters=(f"dependency-{suffix}",),
            context="Synthetic dry-run evidence; not a therapeutic conclusion.",
            gate_impact="Supports only the controlled F0 software workflow test.",
            proposed_falsifier="A required F0 input is absent or contradictory.",
        )
        opinion = CaseOpinion(
            opinion_id=f"opinion-{session.session_id}-{suffix}",
            case=case,
            captain_agent_id=captain,
            status=CaseStatus.PASS,
            rationale="All required synthetic dry-run fields are present.",
            claim_ids=(claim.claim_id,),
        )
        receipt = service.submit_blind_opinion(
            session.session_id,
            opinion,
            (claim,),
            command(captain, blind_version, f"cmd-{session.session_id}-opinion-{suffix}"),
        )
        blind_version = receipt.state_version
    session = service.reveal_claims(
        session.session_id,
        command("agent-commander", blind_version, f"cmd-{session.session_id}-reveal"),
    )
    session = service.open_challenges(
        session.session_id,
        command("agent-commander", session.state_version, f"cmd-{session.session_id}-challenge-open"),
    )
    session = service.close_challenges(
        session.session_id,
        command("agent-commander", session.state_version, f"cmd-{session.session_id}-challenge-close"),
    )
    session = service.start_red_team(
        session.session_id,
        command("agent-commander", session.state_version, f"cmd-{session.session_id}-red-open"),
    )
    session = service.submit_red_team(
        session.session_id,
        RedTeamReport(
            report_id=f"report-{session.session_id}",
            reviewer_agent_id="agent-red-team",
            findings=tuple(red_findings),
            conclusion="The synthetic F0 workflow case completed the bounded red-team test.",
        ),
        command("agent-red-team", session.state_version, f"cmd-{session.session_id}-red-submit"),
    )
    session = service.open_final_cases(
        session.session_id,
        command("agent-commander", session.state_version, f"cmd-{session.session_id}-final-open"),
    )
    for case in CaseType:
        suffix = case.value.lower()
        status = CaseStatus.FAIL if case is failing_case else CaseStatus.PASS
        session = service.submit_final_case(
            session.session_id,
            FinalCaseAssessment(
                assessment_id=f"final-{session.session_id}-{suffix}",
                case=case,
                captain_agent_id=f"captain-{suffix}",
                status=status,
                rationale="Synthetic final status used to verify deterministic policy behavior.",
                claim_ids=(f"claim-{session.session_id}-{suffix}",),
            ),
            command(
                f"captain-{suffix}",
                session.state_version,
                f"cmd-{session.session_id}-final-{suffix}",
            ),
        )
    decisive_claims = tuple(f"claim-{session.session_id}-{case.value.lower()}" for case in CaseType)
    null_claims = tuple(
        claim.claim_id for claim in session.claims if claim.measured_null
    )
    unknown_claims = tuple(
        claim.claim_id for claim in session.claims if claim.state is ClaimState.UNKNOWN
    )
    session = service.submit_gate_packet_inputs(
        session.session_id,
        GatePacketInputs(
            capital_tranche=None,
            decisive_claim_ids=decisive_claims,
            null_claim_ids=null_claims,
            unknown_claim_ids=unknown_claims,
            product_and_standard_of_care_delta="Initial synthetic F0 mandate; no prior product delta.",
            rights_ip_control_supply_delta="Synthetic rights pointer is unchanged and grants no real control.",
            results_vs_frozen_predictions="No experiment ran; this field records a software dry run only.",
            risk_register_delta="No material risk change in the synthetic dry run.",
            falsifiers=("A required F0 input is absent or contradictory.",),
            kill_criteria=("The product mandate is not clinically useful or financeable.",),
            budget_time_capacity_and_catalyst="No spend requested; F1 mapping is the next bounded catalyst.",
            intended_disposition_rationale="Advance only to F1 clinical opportunity mapping.",
        ),
        command(
            "agent-commander",
            session.state_version,
            f"cmd-{session.session_id}-packet-inputs",
        ),
    )
    return session


def start_blind(service: CouncilService, session):
    evidence_item = snapshot(f"evidence-item-{session.session_id}")
    evidence = EvidenceManifest(
        snapshot=SnapshotRef(
            object_id=f"evidence-{session.session_id}",
            version=1,
            digest=canonical_digest({"items": [evidence_item.model_dump(mode="json")]}),
        ),
        items=(evidence_item,),
    )
    session = service.freeze_evidence(
        session.session_id,
        evidence,
        command("agent-evidence", session.state_version, f"cmd-{session.session_id}-freeze"),
    )
    session = service.start_blind_round(
        session.session_id,
        command("agent-commander", session.state_version, f"cmd-{session.session_id}-blind"),
    )
    return session, evidence_item


def run_to_approval(service: CouncilService, session):
    session = run_to_final_cases(service, session)
    session = service.arbitrate(
        session.session_id,
        f"arbitration-{session.session_id}",
        f"packet-{session.session_id}",
        command(
            "policy-engine",
            session.state_version,
            f"cmd-{session.session_id}-arbitrate",
            kind=ActorKind.SERVICE,
        ),
    )
    session = service.request_approval(
        session.session_id,
        f"approval-request-{session.session_id}",
        datetime.now(timezone.utc) + timedelta(hours=12),
        command("agent-commander", session.state_version, f"cmd-{session.session_id}-approval-request"),
    )
    return session


def approve_all(service: CouncilService, session):
    assert session.approval_request is not None
    request = session.approval_request
    for index, role in enumerate(request.required_roles):
        approver_id = f"human-approver-{index}"
        approval = Approval(
            approval_id=f"approval-{session.session_id}-{index}",
            request_id=request.request_id,
            session_id=session.session_id,
            program_id=session.program_id,
            approver_id=approver_id,
            approver_kind=ActorKind.HUMAN,
            role=role,
            decision=ApprovalDecision.APPROVED,
            gate_packet_digest=request.gate_packet_digest,
            rationale="The bounded F0 software dry run is approved for commit.",
        )
        session = service.record_approval(
            session.session_id,
            approval,
            command(
                approver_id,
                session.state_version,
                f"cmd-{session.session_id}-approval-{index}",
                kind=ActorKind.HUMAN,
                roles=frozenset({role}),
            ),
        )
    return session
