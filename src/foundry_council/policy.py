from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from .agents import validate_council_roster
from .errors import Forbidden, PolicyConfigurationRequired, ValidationFailure
from .models import (
    ArbitrationResult,
    CaseStatus,
    CaseType,
    ClaimState,
    CouncilSession,
    Disposition,
    GatePacket,
    Materiality,
    ProgramStage,
    ResolutionOutcome,
    RuleResult,
    SessionPhase,
    SnapshotRef,
    StableId,
)


ALLOWED_STAGE_TRANSITIONS: dict[ProgramStage, frozenset[ProgramStage]] = {
    ProgramStage.F0: frozenset({ProgramStage.F1}),
    ProgramStage.F1: frozenset({ProgramStage.F2}),
    ProgramStage.F2: frozenset({ProgramStage.F3}),
    ProgramStage.F3: frozenset({ProgramStage.F4}),
    ProgramStage.F4: frozenset({ProgramStage.F5}),
    ProgramStage.F5: frozenset({ProgramStage.F6A, ProgramStage.F6B, ProgramStage.F6C}),
    ProgramStage.F6A: frozenset({ProgramStage.F7}),
    ProgramStage.F6B: frozenset({ProgramStage.F7}),
    ProgramStage.F6C: frozenset({ProgramStage.F7}),
    ProgramStage.F7: frozenset({ProgramStage.F8}),
    ProgramStage.F8: frozenset({ProgramStage.F9}),
    ProgramStage.F9: frozenset({ProgramStage.F10}),
    ProgramStage.F10: frozenset({ProgramStage.F11}),
    ProgramStage.F11: frozenset({ProgramStage.F12}),
    ProgramStage.F12: frozenset(),
}


NEXT_PHASE: dict[SessionPhase, SessionPhase] = {
    SessionPhase.CONSTITUTED: SessionPhase.EVIDENCE_FROZEN,
    SessionPhase.EVIDENCE_FROZEN: SessionPhase.BLIND_OPINIONS,
    SessionPhase.BLIND_OPINIONS: SessionPhase.CLAIMS_REVEALED,
    SessionPhase.CLAIMS_REVEALED: SessionPhase.CHALLENGES,
    SessionPhase.CHALLENGES: SessionPhase.RESPONSES,
    SessionPhase.RESPONSES: SessionPhase.RED_TEAM,
    SessionPhase.RED_TEAM: SessionPhase.FINAL_CASE_STATUSES,
    SessionPhase.FINAL_CASE_STATUSES: SessionPhase.ARBITRATION,
    SessionPhase.ARBITRATION: SessionPhase.AWAITING_HUMAN_APPROVAL,
    SessionPhase.AWAITING_HUMAN_APPROVAL: SessionPhase.COMMITTED,
}


# Exact sign-offs stated by the Foundry governance draft. Unspecified gates block
# approval configuration instead of silently inheriting invented policy.
APPROVER_ROLES_BY_GATE: dict[ProgramStage, tuple[str, ...]] = {
    ProgramStage.F0: ("portfolio_lead", "scientific_lead", "product_commercial_lead", "finance"),
    ProgramStage.F1: ("scientific_lead", "product_commercial_lead"),
    ProgramStage.F2: ("scientific_lead", "data_evidence_qa"),
    ProgramStage.F3: ("experimental_lead", "statistics_qa"),
    ProgramStage.F4: ("scientific_lead", "translational_safety_reviewer", "independent_red_team"),
    ProgramStage.F5: (
        "scientific_lead",
        "product_lead",
        "ip_legal",
        "bd_lead",
        "cmc_lead",
        "regulatory",
        "finance",
    ),
    ProgramStage.F6A: ("scientific_lead", "cmc", "regulatory", "finance"),
    ProgramStage.F6B: ("scientific_lead", "product_lead", "regulatory"),
    ProgramStage.F6C: ("scientific_lead", "product_lead", "regulatory", "ip_legal"),
    ProgramStage.F7: ("scientific_lead", "translational_lead", "regulatory"),
    ProgramStage.F8: (
        "independent_program_committee",
        "scientific_lead",
        "translational_lead",
        "safety_lead",
        "cmc_lead",
        "regulatory",
        "ip_legal",
        "commercial",
        "finance",
    ),
    ProgramStage.F9: (
        "regulatory",
        "safety_lead",
        "cmc_lead",
        "medical_write",
        "statistics_qa",
    ),
    ProgramStage.F10: (
        "scientific_lead",
        "clinical_lead",
        "safety_lead",
        "biostatistics",
    ),
    ProgramStage.F11: (
        "clinical_lead",
        "translational_lead",
        "safety_lead",
        "biostatistics",
        "regulatory",
    ),
    ProgramStage.F12: (
        "independent_program_committee",
        "executive_approver",
        "regulatory",
        "commercial",
        "legal",
        "finance",
    ),
}


TRANSACTION_APPROVERS = ("executive_approver", "legal", "finance", "bd_lead")


@dataclass(frozen=True)
class GatePolicy:
    """Versioned policy supplied by the control plane, never by an agent."""

    policy_id: str = "policy-foundry-council-v0.1"
    version: int = 1
    enabled_gates: frozenset[ProgramStage] = frozenset({ProgramStage.F0})
    allow_conditional_advance_for: frozenset[CaseType] = frozenset()
    allowed_not_applicable_rules: frozenset[tuple[ProgramStage, CaseType, str]] = frozenset()

    def payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "enabled_gates": sorted(stage.value for stage in self.enabled_gates),
            "allow_conditional_advance_for": sorted(case.value for case in self.allow_conditional_advance_for),
            "allowed_not_applicable_rules": sorted(
                (stage.value, case.value, rule_id)
                for stage, case, rule_id in self.allowed_not_applicable_rules
            ),
            "stage_transitions": {
                stage.value: sorted(next_stage.value for next_stage in destinations)
                for stage, destinations in ALLOWED_STAGE_TRANSITIONS.items()
            },
            "approver_roles_by_gate": {
                stage.value: list(roles) for stage, roles in APPROVER_ROLES_BY_GATE.items()
            },
            "transaction_approvers": list(TRANSACTION_APPROVERS),
        }

    def snapshot_ref(self) -> SnapshotRef:
        return SnapshotRef(
            object_id=self.policy_id,
            version=self.version,
            digest=canonical_digest(self.payload()),
        )


DEFAULT_GATE_POLICY = GatePolicy()


def canonical_digest(value: BaseModel | dict[str, Any]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def require_phase(session: CouncilSession, phase: SessionPhase) -> None:
    if session.phase is not phase:
        raise ValidationFailure(
            "command is not valid in the current session phase",
            expected=phase.value,
            actual=session.phase.value,
        )


def require_next_phase(current: SessionPhase, proposed: SessionPhase) -> None:
    if NEXT_PHASE.get(current) is not proposed:
        raise ValidationFailure(
            "session phase transition is not permitted",
            current=current.value,
            proposed=proposed.value,
        )


def require_stage_transition(current: ProgramStage, proposed: ProgramStage) -> None:
    if proposed not in ALLOWED_STAGE_TRANSITIONS[current]:
        raise ValidationFailure(
            "formal gates cannot be skipped or moved laterally",
            current=current.value,
            proposed=proposed.value,
        )


def required_approver_roles(session: CouncilSession) -> tuple[str, ...]:
    disposition = session.charter.requested_disposition
    if disposition in {Disposition.PARTNER, Disposition.LICENSE_OR_ACQUIRE, Disposition.SPINOUT}:
        return TRANSACTION_APPROVERS
    roles = APPROVER_ROLES_BY_GATE.get(session.charter.current_stage)
    if roles is None:
        raise PolicyConfigurationRequired(
            "functional sign-offs have not been configured for this gate",
            gate=session.charter.current_stage.value,
        )
    return roles


def _rule(rule_id: str, passed: bool, explanation: str) -> RuleResult:
    return RuleResult(rule_id=rule_id, passed=passed, explanation=explanation)


def evaluate_session(
    session: CouncilSession,
    result_id: StableId,
    arbiter_agent_id: StableId,
    policy: GatePolicy | None = None,
) -> ArbitrationResult:
    require_phase(session, SessionPhase.FINAL_CASE_STATUSES)
    validate_council_roster(session.participants)
    policy = policy or GatePolicy()
    rules: list[RuleResult] = []
    blockers: list[str] = []

    cases = [item.case for item in session.final_cases]
    complete = len(cases) == len(CaseType) and set(cases) == set(CaseType)
    rules.append(_rule("RULE.FIVE_CASES.COMPLETE", complete, "Exactly one final status must exist for every case."))
    if not complete:
        blockers.append("Five-case determination is incomplete or duplicated.")

    packet_inputs_present = session.gate_packet_inputs is not None
    rules.append(
        _rule(
            "RULE.GATE_PACKET.INPUTS_COMPLETE",
            packet_inputs_present,
            "The gate packet must include evidence, product, control, execution, risk, and capital deltas.",
        )
    )
    if not packet_inputs_present:
        blockers.append("Required gate-packet inputs are missing.")

    gate_enabled = session.charter.current_stage in policy.enabled_gates
    rules.append(
        _rule(
            "RULE.GATE.DEFINITION_ENABLED",
            gate_enabled,
            "Only gates with accepted stage-specific artifact definitions may run.",
        )
    )
    if not gate_enabled:
        blockers.append("This gate has no enabled stage-specific acceptance definition.")

    known_claim_ids = {claim.claim_id for claim in session.claims}
    final_claims_valid = all(set(item.claim_ids).issubset(known_claim_ids) for item in session.final_cases)
    rules.append(
        _rule(
            "RULE.FIVE_CASES.CLAIMS_BOUND",
            final_claims_valid,
            "Every final case determination must reference claims in the frozen council ledger.",
        )
    )
    if not final_claims_valid:
        blockers.append("A final case determination references a nonexistent claim.")

    red_team_present = session.red_team_report is not None
    rules.append(_rule("RULE.RED_TEAM.REQUIRED", red_team_present, "Independent red-team review is mandatory."))
    if not red_team_present:
        blockers.append("Independent red-team report is missing.")

    resolved_ids = {item.challenge_id for item in session.resolutions}
    all_challenges_resolved = all(item.challenge_id in resolved_ids for item in session.challenges)
    rules.append(
        _rule(
            "RULE.CHALLENGES.RESOLVED",
            all_challenges_resolved,
            "Every challenge requires an explicit independent resolution.",
        )
    )
    if not all_challenges_resolved:
        blockers.append("One or more challenges lack resolution.")

    challenges_by_id = {item.challenge_id: item for item in session.challenges}
    unresolved_challenges = any(
        (
            item.outcome in {ResolutionOutcome.ACCEPTED, ResolutionOutcome.PARTIAL, ResolutionOutcome.UNRESOLVED}
            and challenges_by_id[item.challenge_id].materiality in {Materiality.MATERIAL, Materiality.FATAL}
        )
        or item.unresolved_material_disagreement
        for item in session.resolutions
        if item.challenge_id in challenges_by_id
    )
    if session.charter.requested_disposition is Disposition.ADVANCE and unresolved_challenges:
        blockers.append("An accepted, partial, or unresolved material challenge blocks ADVANCE.")
    rules.append(
        _rule(
            "RULE.CHALLENGES.NO_MATERIAL_DISAGREEMENT",
            not unresolved_challenges,
            "Accepted, partial, or unresolved material challenges require a successor case before ADVANCE.",
        )
    )

    unresolved_red_team = bool(
        session.red_team_report
        and any(
            finding.unresolved and finding.materiality in {Materiality.MATERIAL, Materiality.FATAL}
            for finding in session.red_team_report.findings
        )
    )
    if session.charter.requested_disposition is Disposition.ADVANCE and unresolved_red_team:
        blockers.append("The red team has an unresolved material or fatal finding.")
    rules.append(
        _rule(
            "RULE.RED_TEAM.NO_UNRESOLVED_BLOCKER",
            not unresolved_red_team,
            "Unresolved material or fatal red-team findings block ADVANCE.",
        )
    )

    if session.charter.requested_disposition is Disposition.ADVANCE:
        stage_valid = session.charter.proposed_stage in ALLOWED_STAGE_TRANSITIONS[session.charter.current_stage]
    else:
        stage_valid = session.charter.proposed_stage is session.charter.current_stage
    rules.append(_rule("RULE.STAGE.LEGAL", stage_valid, "The requested action must obey the formal gate graph."))
    if not stage_valid:
        blockers.append("The proposed stage transition is illegal.")

    if session.charter.requested_disposition is Disposition.ADVANCE:
        unknown_claim_ids = tuple(
            sorted(claim.claim_id for claim in session.claims if claim.state is ClaimState.UNKNOWN)
        )
        no_unknown_claims = not unknown_claim_ids
        rules.append(
            _rule(
                "RULE.EVIDENCE.NO_UNKNOWN_CLAIMS",
                no_unknown_claims,
                "Every frozen council claim must have a resolved evidence state before ADVANCE.",
            )
        )
        if not no_unknown_claims:
            blockers.append(
                "Frozen council evidence contains UNKNOWN claims: " + ", ".join(unknown_claim_ids) + "."
            )

        for assessment in session.final_cases:
            if assessment.status is CaseStatus.FAIL:
                blockers.append(f"{assessment.case.value} has a hard FAIL.")
            if assessment.status is CaseStatus.UNKNOWN or assessment.material_unknown:
                blockers.append(f"{assessment.case.value} has a material UNKNOWN.")
            if assessment.unresolved_material_disagreement:
                blockers.append(f"{assessment.case.value} has unresolved material disagreement.")
            if (
                assessment.status is CaseStatus.CONDITIONAL
                and assessment.case not in policy.allow_conditional_advance_for
            ):
                blockers.append(f"{assessment.case.value} is CONDITIONAL and this policy does not permit advance.")
            if assessment.status is CaseStatus.NOT_APPLICABLE:
                citation = (session.charter.current_stage, assessment.case, assessment.not_applicable_rule_id or "")
                if citation not in policy.allowed_not_applicable_rules:
                    blockers.append(f"{assessment.case.value} cites an unrecognized NOT_APPLICABLE rule.")

        material_dissent = any(
            item.materiality in {Materiality.MATERIAL, Materiality.FATAL}
            for item in session.dissent
        )
        if material_dissent:
            blockers.append("Material dissent remains unresolved.")
        rules.append(
            _rule(
                "RULE.DISSENT.NO_UNRESOLVED_MATERIAL",
                not material_dissent,
                "Unresolved material dissent blocks ADVANCE and remains visible.",
            )
        )

    cases_clear = not any(" has " in blocker or " is CONDITIONAL" in blocker for blocker in blockers)
    rules.append(
        _rule(
            "RULE.HARD_GATES.CLEAR",
            cases_clear,
            "A hard FAIL, material UNKNOWN, or unresolved material disagreement blocks ADVANCE.",
        )
    )

    not_expired = session.charter.session_deadline > datetime.now(timezone.utc)
    rules.append(_rule("RULE.SESSION.NOT_EXPIRED", not_expired, "The bounded council session must remain current."))
    if not not_expired:
        blockers.append("The council session has expired.")

    eligible = not blockers
    recommendation = session.charter.requested_disposition if eligible else Disposition.ESCALATE
    return ArbitrationResult(
        result_id=result_id,
        arbiter_agent_id=arbiter_agent_id,
        requested_disposition=session.charter.requested_disposition,
        recommended_disposition=recommendation,
        eligible=eligible,
        blockers=tuple(blockers),
        rules=tuple(rules),
        dissent=session.dissent,
    )


def build_gate_packet(session: CouncilSession, packet_id: StableId) -> GatePacket:
    if session.evidence is None or session.arbitration is None or session.gate_packet_inputs is None:
        raise ValidationFailure("a gate packet requires frozen evidence, complete inputs, and arbitration")
    raw: dict[str, Any] = {
        "packet_id": packet_id,
        "session_id": session.session_id,
        "program_id": session.program_id,
        "session_version": session.state_version,
        "evidence": session.evidence.snapshot.model_dump(mode="json"),
        "program_snapshot": session.charter.program_snapshot.model_dump(mode="json"),
        "portfolio_mandate": session.charter.portfolio_mandate.model_dump(mode="json"),
        "tpp": session.charter.tpp.model_dump(mode="json"),
        "rights": session.charter.rights.model_dump(mode="json"),
        "budget": session.charter.budget.model_dump(mode="json"),
        "risk_register": session.charter.risk_register.model_dump(mode="json"),
        "standard_of_care": session.charter.standard_of_care.model_dump(mode="json"),
        "gate_policy": session.charter.gate_policy.model_dump(mode="json"),
        "proposed_outputs": session.charter.proposed_outputs.model_dump(mode="json"),
        "proposed_route": session.charter.proposed_route.value if session.charter.proposed_route else None,
        "decision_question": session.charter.question,
        "proposed_action": session.charter.proposed_action,
        "exact_scope": session.charter.exact_scope,
        "charter_digest": canonical_digest(session.charter),
        "deliberation_digest": canonical_digest(
            {
                "participants": [item.model_dump(mode="json") for item in session.participants],
                "evidence_manifest": session.evidence.model_dump(mode="json"),
                "claims": [item.model_dump(mode="json") for item in session.claims],
                "opinions": [item.model_dump(mode="json") for item in session.opinions],
                "challenges": [item.model_dump(mode="json") for item in session.challenges],
                "responses": [item.model_dump(mode="json") for item in session.responses],
                "resolutions": [item.model_dump(mode="json") for item in session.resolutions],
                "red_team_report": session.red_team_report.model_dump(mode="json")
                if session.red_team_report
                else None,
                "final_cases": [item.model_dump(mode="json") for item in session.final_cases],
                "dissent": [item.model_dump(mode="json") for item in session.dissent],
            }
        ),
        "current_stage": session.charter.current_stage.value,
        "proposed_stage": session.charter.proposed_stage.value,
        "requested_disposition": session.charter.requested_disposition.value,
        "final_cases": [item.model_dump(mode="json") for item in session.final_cases],
        "arbitration": session.arbitration.model_dump(mode="json"),
        "inputs": session.gate_packet_inputs.model_dump(mode="json"),
        "required_approver_roles": list(required_approver_roles(session)),
    }
    return GatePacket(**raw, digest=canonical_digest(raw))


def validate_no_agent_approval(session: CouncilSession, approver_id: str) -> None:
    participant_ids = {p.actor_id for p in session.participants}
    if approver_id in participant_ids:
        raise Forbidden("a council participant cannot approve the session it helped produce", approver_id=approver_id)
