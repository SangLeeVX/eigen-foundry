from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


StableId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


class ProgramStage(StrEnum):
    F0 = "F0"
    F1 = "F1"
    F2 = "F2"
    F3 = "F3"
    F4 = "F4"
    F5 = "F5"
    F6A = "F6A"
    F6B = "F6B"
    F6C = "F6C"
    F7 = "F7"
    F8 = "F8"
    F9 = "F9"
    F10 = "F10"
    F11 = "F11"
    F12 = "F12"


class ProgramStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    HOLD = "HOLD"
    REDESIGN = "REDESIGN"
    PARTNERING = "PARTNERING"
    LICENSE_NEGOTIATION = "LICENSE_NEGOTIATION"
    SPINOUT = "SPINOUT"
    TERMINATED = "TERMINATED"
    COMPLETED = "COMPLETED"


class Route(StrEnum):
    UNSELECTED = "UNSELECTED"
    EXISTING_ASSET = "EXISTING_ASSET"
    REPOSITIONING = "REPOSITIONING"
    ASSET_RESCUE = "ASSET_RESCUE"
    TARGET_RESCUE = "TARGET_RESCUE"
    TRIAL_RESCUE = "TRIAL_RESCUE"
    COMBINATION = "COMBINATION"
    KNOWN_TARGET_NEW_CANDIDATE = "KNOWN_TARGET_NEW_CANDIDATE"
    NOVEL_TARGET_DE_NOVO = "NOVEL_TARGET_DE_NOVO"
    INBOUND_DILIGENCE = "INBOUND_DILIGENCE"


class EntryPoint(StrEnum):
    UNSPECIFIED = "UNSPECIFIED"
    DISEASE_FIRST = "DISEASE_FIRST"
    TARGET_FIRST = "TARGET_FIRST"
    ASSET_FIRST = "ASSET_FIRST"
    FAILED_TRIAL_FIRST = "FAILED_TRIAL_FIRST"
    PATIENT_SEGMENT_FIRST = "PATIENT_SEGMENT_FIRST"
    DATA_FIRST = "DATA_FIRST"
    MODALITY_FIRST = "MODALITY_FIRST"
    INBOUND_FIRST = "INBOUND_FIRST"
    PORTFOLIO_GAP_FIRST = "PORTFOLIO_GAP_FIRST"


class CaseType(StrEnum):
    SCIENTIFIC = "SCIENTIFIC"
    PRODUCT = "PRODUCT"
    CONTROL = "CONTROL"
    EXECUTION = "EXECUTION"
    INVESTMENT = "INVESTMENT"


class CaseStatus(StrEnum):
    PASS = "PASS"
    CONDITIONAL = "CONDITIONAL"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ClaimState(StrEnum):
    OBSERVED = "OBSERVED"
    SUPPORTED_INFERENCE = "SUPPORTED_INFERENCE"
    MODEL_PREDICTION = "MODEL_PREDICTION"
    EXPERIMENTALLY_VALIDATED = "EXPERIMENTALLY_VALIDATED"
    TRANSLATIONALLY_VALIDATED = "TRANSLATIONALLY_VALIDATED"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"


class Materiality(StrEnum):
    NON_MATERIAL = "NON_MATERIAL"
    MATERIAL = "MATERIAL"
    FATAL = "FATAL"


class Disposition(StrEnum):
    ADVANCE = "ADVANCE"
    HOLD = "HOLD"
    REDESIGN_ONCE = "REDESIGN_ONCE"
    PARTNER = "PARTNER"
    LICENSE_OR_ACQUIRE = "LICENSE_OR_ACQUIRE"
    SPINOUT = "SPINOUT"
    TERMINATE = "TERMINATE"
    ESCALATE = "ESCALATE"


class SessionPhase(StrEnum):
    CONSTITUTED = "CONSTITUTED"
    EVIDENCE_FROZEN = "EVIDENCE_FROZEN"
    BLIND_OPINIONS = "BLIND_OPINIONS"
    CLAIMS_REVEALED = "CLAIMS_REVEALED"
    CHALLENGES = "CHALLENGES"
    RESPONSES = "RESPONSES"
    RED_TEAM = "RED_TEAM"
    FINAL_CASE_STATUSES = "FINAL_CASE_STATUSES"
    ARBITRATION = "ARBITRATION"
    AWAITING_HUMAN_APPROVAL = "AWAITING_HUMAN_APPROVAL"
    COMMITTED = "COMMITTED"
    RETURNED = "RETURNED"
    VOID = "VOID"


class ActorKind(StrEnum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SERVICE = "SERVICE"


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ChallengeDisposition(StrEnum):
    CONCEDE = "CONCEDE"
    AMEND = "AMEND"
    REBUT = "REBUT"
    MARK_UNKNOWN = "MARK_UNKNOWN"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class ResolutionOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    UNRESOLVED = "UNRESOLVED"


class SnapshotRef(FrozenModel):
    object_id: StableId
    version: Annotated[int, Field(ge=1)]
    digest: Digest


class EvidenceManifest(FrozenModel):
    snapshot: SnapshotRef
    items: tuple[SnapshotRef, ...]

    @model_validator(mode="after")
    def items_are_unique(self) -> "EvidenceManifest":
        identities = {(item.object_id, item.version, item.digest) for item in self.items}
        if not self.items or len(identities) != len(self.items):
            raise ValueError("evidence manifest requires one or more unique immutable items")
        return self

    def content(self) -> dict[str, Any]:
        return {"items": [item.model_dump(mode="json") for item in self.items]}


class ParticipantAssignment(FrozenModel):
    assignment_id: StableId
    actor_id: StableId
    actor_kind: ActorKind
    role: StableId
    case: CaseType | None = None
    run_id: StableId | None = None
    model_version: StableId | None = None
    prompt_version: StableId | None = None
    independence_group: StableId
    conflicts: tuple[str, ...] = ()


class CaseCondition(FrozenModel):
    condition: Annotated[str, Field(min_length=3, max_length=2000)]
    owner: StableId
    deadline: AwareDatetime
    expiry: AwareDatetime

    @model_validator(mode="after")
    def deadline_precedes_expiry(self) -> "CaseCondition":
        if self.expiry < self.deadline:
            raise ValueError("condition expiry must be on or after its deadline")
        return self


class Claim(FrozenModel):
    claim_id: StableId
    version: Annotated[int, Field(ge=1)] = 1
    owner_agent_id: StableId
    statement: Annotated[str, Field(min_length=3, max_length=4000)]
    state: ClaimState
    measured_null: bool = False
    materiality: Materiality
    evidence_refs: tuple[SnapshotRef, ...] = ()
    dependency_clusters: tuple[StableId, ...] = ()
    context: Annotated[str, Field(min_length=3, max_length=2000)]
    assumptions: tuple[str, ...] = ()
    gate_impact: Annotated[str, Field(min_length=3, max_length=2000)]
    proposed_falsifier: str | None = None
    supersedes_claim_id: StableId | None = None

    @model_validator(mode="after")
    def cited_unless_unknown(self) -> "Claim":
        if self.state is not ClaimState.UNKNOWN and not self.evidence_refs:
            raise ValueError("non-UNKNOWN claims require at least one evidence reference")
        if self.measured_null and self.state not in {
            ClaimState.OBSERVED,
            ClaimState.EXPERIMENTALLY_VALIDATED,
            ClaimState.TRANSLATIONALLY_VALIDATED,
        }:
            raise ValueError("a measured null must be an observed or validated evidence result")
        return self


class CaseOpinion(FrozenModel):
    opinion_id: StableId
    case: CaseType
    captain_agent_id: StableId
    status: CaseStatus
    rationale: Annotated[str, Field(min_length=3, max_length=6000)]
    claim_ids: tuple[StableId, ...]
    conditions: tuple[CaseCondition, ...] = ()
    submitted_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def conditional_has_controls(self) -> "CaseOpinion":
        if self.status is not CaseStatus.NOT_APPLICABLE and not self.claim_ids:
            raise ValueError("case opinions require at least one atomic claim")
        if self.status is CaseStatus.CONDITIONAL and not self.conditions:
            raise ValueError("CONDITIONAL opinions require an owner, deadline, and expiry")
        if self.status is not CaseStatus.CONDITIONAL and self.conditions:
            raise ValueError("conditions are only valid for CONDITIONAL opinions")
        return self


class Challenge(FrozenModel):
    challenge_id: StableId
    claim_id: StableId
    target_claim_version: Annotated[int, Field(ge=1)]
    challenger_agent_id: StableId
    challenge_type: Literal[
        "EVIDENCE",
        "CAUSAL_MODEL",
        "CONTEXT",
        "EXPOSURE",
        "SAFETY",
        "CONTROL",
        "EXECUTION",
        "ECONOMICS",
        "OTHER",
    ]
    grounds: Annotated[str, Field(min_length=3, max_length=4000)]
    evidence_refs: tuple[SnapshotRef, ...] = ()
    materiality: Materiality
    gate_impact: Annotated[str, Field(min_length=3, max_length=2000)]
    proposed_falsifier: Annotated[str, Field(min_length=3, max_length=2000)]
    created_at: AwareDatetime = Field(default_factory=utc_now)


class ChallengeResponse(FrozenModel):
    response_id: StableId
    challenge_id: StableId
    owner_agent_id: StableId
    disposition: ChallengeDisposition
    rationale: Annotated[str, Field(min_length=3, max_length=4000)]
    replacement_claim: Claim | None = None
    responded_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def amendment_supplies_claim(self) -> "ChallengeResponse":
        if self.disposition is ChallengeDisposition.AMEND and self.replacement_claim is None:
            raise ValueError("AMEND requires a replacement claim")
        if self.disposition is not ChallengeDisposition.AMEND and self.replacement_claim is not None:
            raise ValueError("only AMEND may supply a replacement claim")
        return self


class ChallengeResolution(FrozenModel):
    resolution_id: StableId
    challenge_id: StableId
    reviewer_agent_id: StableId
    outcome: ResolutionOutcome
    rationale: Annotated[str, Field(min_length=3, max_length=4000)]
    unresolved_material_disagreement: bool = False
    resolved_at: AwareDatetime = Field(default_factory=utc_now)


class RedTeamFinding(FrozenModel):
    finding_id: StableId
    category: Literal[
        "ALTERNATIVE_CAUSAL_MODEL",
        "EVIDENCE_DEPENDENCE",
        "EXPOSURE",
        "THERAPEUTIC_WINDOW",
        "STANDARD_OF_CARE",
        "RIGHTS_CONTROL",
        "EXECUTION",
        "ECONOMICS",
        "OTHER",
    ]
    statement: Annotated[str, Field(min_length=3, max_length=4000)]
    materiality: Materiality
    evidence_refs: tuple[SnapshotRef, ...] = ()
    unresolved: bool


class RedTeamReport(FrozenModel):
    report_id: StableId
    reviewer_agent_id: StableId
    findings: tuple[RedTeamFinding, ...]
    conclusion: Annotated[str, Field(min_length=3, max_length=6000)]
    submitted_at: AwareDatetime = Field(default_factory=utc_now)


class FinalCaseAssessment(FrozenModel):
    assessment_id: StableId
    case: CaseType
    captain_agent_id: StableId
    status: CaseStatus
    rationale: Annotated[str, Field(min_length=3, max_length=6000)]
    claim_ids: tuple[StableId, ...] = ()
    material_unknown: bool = False
    unresolved_material_disagreement: bool = False
    conditions: tuple[CaseCondition, ...] = ()
    not_applicable_rule_id: StableId | None = None

    @model_validator(mode="after")
    def validate_status_controls(self) -> "FinalCaseAssessment":
        if self.status is not CaseStatus.NOT_APPLICABLE and not self.claim_ids:
            raise ValueError("final case determinations require supporting atomic claim IDs")
        if self.status is CaseStatus.CONDITIONAL and not self.conditions:
            raise ValueError("CONDITIONAL assessment requires controlled conditions")
        if self.status is not CaseStatus.CONDITIONAL and self.conditions:
            raise ValueError("conditions are only valid for CONDITIONAL assessments")
        if self.status is CaseStatus.NOT_APPLICABLE and self.not_applicable_rule_id is None:
            raise ValueError("NOT_APPLICABLE requires the exact gate-rule citation")
        if self.status is not CaseStatus.NOT_APPLICABLE and self.not_applicable_rule_id is not None:
            raise ValueError("not_applicable_rule_id is only valid for NOT_APPLICABLE")
        if self.status is CaseStatus.UNKNOWN and not self.material_unknown:
            object.__setattr__(self, "material_unknown", True)
        return self


class Dissent(FrozenModel):
    dissent_id: StableId
    agent_id: StableId
    assignment_id: StableId
    role: StableId
    statement: Annotated[str, Field(min_length=3, max_length=4000)]
    materiality: Materiality
    submitted_at: AwareDatetime = Field(default_factory=utc_now)


class RuleResult(FrozenModel):
    rule_id: StableId
    passed: bool
    explanation: Annotated[str, Field(min_length=3, max_length=2000)]


class ArbitrationResult(FrozenModel):
    result_id: StableId
    arbiter_agent_id: StableId
    requested_disposition: Disposition
    recommended_disposition: Disposition
    eligible: bool
    blockers: tuple[str, ...]
    rules: tuple[RuleResult, ...]
    dissent: tuple[Dissent, ...] = ()
    created_at: AwareDatetime = Field(default_factory=utc_now)


class ProposedProgramOutputs(FrozenModel):
    portfolio_mandate: SnapshotRef | None = None
    tpp: SnapshotRef | None = None
    rights: SnapshotRef | None = None
    budget: SnapshotRef | None = None
    risk_register: SnapshotRef | None = None
    standard_of_care: SnapshotRef | None = None


class GatePacketInputs(FrozenModel):
    capital_tranche: SnapshotRef | None = None
    decisive_claim_ids: Annotated[tuple[StableId, ...], Field(min_length=1)]
    contradiction_claim_ids: tuple[StableId, ...] = ()
    null_claim_ids: tuple[StableId, ...] = ()
    unknown_claim_ids: tuple[StableId, ...] = ()
    product_and_standard_of_care_delta: Annotated[str, Field(min_length=3, max_length=6000)]
    rights_ip_control_supply_delta: Annotated[str, Field(min_length=3, max_length=6000)]
    results_vs_frozen_predictions: Annotated[str, Field(min_length=3, max_length=6000)]
    risk_register_delta: Annotated[str, Field(min_length=3, max_length=6000)]
    falsifiers: Annotated[tuple[str, ...], Field(min_length=1)]
    kill_criteria: Annotated[tuple[str, ...], Field(min_length=1)]
    budget_time_capacity_and_catalyst: Annotated[str, Field(min_length=3, max_length=6000)]
    proposed_task_ids: tuple[StableId, ...] = ()
    exception_ids: tuple[StableId, ...] = ()
    intended_disposition_rationale: Annotated[str, Field(min_length=3, max_length=6000)]


class DecisionCharter(FrozenModel):
    question: Annotated[str, Field(min_length=5, max_length=3000)]
    proposed_action: Annotated[str, Field(min_length=5, max_length=3000)]
    exact_scope: Annotated[str, Field(min_length=5, max_length=3000)]
    requested_disposition: Disposition
    current_stage: ProgramStage
    proposed_stage: ProgramStage
    expected_program_state_version: Annotated[int, Field(ge=1)]
    program_snapshot: SnapshotRef
    portfolio_mandate: SnapshotRef
    tpp: SnapshotRef
    rights: SnapshotRef
    budget: SnapshotRef
    risk_register: SnapshotRef
    standard_of_care: SnapshotRef
    gate_policy: SnapshotRef
    proposed_outputs: ProposedProgramOutputs = Field(default_factory=ProposedProgramOutputs)
    proposed_route: Route | None = None
    session_deadline: AwareDatetime
    hold_trigger: str | None = None
    hold_expiry: AwareDatetime | None = None

    @model_validator(mode="after")
    def hold_is_bounded(self) -> "DecisionCharter":
        if self.requested_disposition is Disposition.HOLD:
            if not self.hold_trigger or self.hold_expiry is None:
                raise ValueError("HOLD requires an external trigger and expiry")
        if self.current_stage is ProgramStage.F5 and self.requested_disposition is Disposition.ADVANCE:
            compatible_routes = {
                ProgramStage.F6A: {
                    Route.EXISTING_ASSET,
                    Route.REPOSITIONING,
                    Route.ASSET_RESCUE,
                    Route.TARGET_RESCUE,
                    Route.TRIAL_RESCUE,
                    Route.INBOUND_DILIGENCE,
                },
                ProgramStage.F6B: {
                    Route.KNOWN_TARGET_NEW_CANDIDATE,
                    Route.NOVEL_TARGET_DE_NOVO,
                },
                ProgramStage.F6C: {Route.COMBINATION},
            }
            if self.proposed_route not in compatible_routes.get(self.proposed_stage, set()):
                raise ValueError("F5 route selection must match the proposed F6 branch")
        elif self.proposed_route is not None:
            raise ValueError("formal route selection is permitted only in an F5 advancement charter")
        return self


class GatePacket(FrozenModel):
    packet_id: StableId
    session_id: StableId
    program_id: StableId
    session_version: Annotated[int, Field(ge=1)]
    evidence: SnapshotRef
    program_snapshot: SnapshotRef
    portfolio_mandate: SnapshotRef
    tpp: SnapshotRef
    rights: SnapshotRef
    budget: SnapshotRef
    risk_register: SnapshotRef
    standard_of_care: SnapshotRef
    gate_policy: SnapshotRef
    proposed_outputs: ProposedProgramOutputs
    proposed_route: Route | None = None
    decision_question: Annotated[str, Field(min_length=5, max_length=3000)]
    proposed_action: Annotated[str, Field(min_length=5, max_length=3000)]
    exact_scope: Annotated[str, Field(min_length=5, max_length=3000)]
    charter_digest: Digest
    deliberation_digest: Digest
    current_stage: ProgramStage
    proposed_stage: ProgramStage
    requested_disposition: Disposition
    final_cases: tuple[FinalCaseAssessment, ...]
    arbitration: ArbitrationResult
    inputs: GatePacketInputs
    required_approver_roles: tuple[StableId, ...]
    digest: Digest
    created_at: AwareDatetime = Field(default_factory=utc_now)


class ApprovalRequest(FrozenModel):
    request_id: StableId
    session_id: StableId
    program_id: StableId
    gate_packet_digest: Digest
    required_roles: tuple[StableId, ...]
    exact_scope: Annotated[str, Field(min_length=3, max_length=3000)]
    expires_at: AwareDatetime
    created_at: AwareDatetime = Field(default_factory=utc_now)


class Approval(FrozenModel):
    approval_id: StableId
    request_id: StableId
    session_id: StableId
    program_id: StableId
    approver_id: StableId
    approver_kind: ActorKind
    role: StableId
    decision: ApprovalDecision
    gate_packet_digest: Digest
    rationale: Annotated[str, Field(min_length=3, max_length=3000)]
    conflicts: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    decided_at: AwareDatetime = Field(default_factory=utc_now)


class ProgramPointers(FrozenModel):
    portfolio_mandate: SnapshotRef | None = None
    tpp: SnapshotRef | None = None
    evidence_snapshot: SnapshotRef | None = None
    rights_snapshot: SnapshotRef | None = None
    budget: SnapshotRef | None = None
    risk_register: SnapshotRef | None = None
    standard_of_care: SnapshotRef | None = None
    gate_policy: SnapshotRef | None = None


class ProgramCaseState(FrozenModel):
    status: CaseStatus = CaseStatus.UNKNOWN
    rationale: str = "Not yet reviewed."
    conditions: tuple[CaseCondition, ...] = ()


class FiveCaseState(FrozenModel):
    scientific: ProgramCaseState = Field(default_factory=ProgramCaseState)
    product: ProgramCaseState = Field(default_factory=ProgramCaseState)
    control: ProgramCaseState = Field(default_factory=ProgramCaseState)
    execution: ProgramCaseState = Field(default_factory=ProgramCaseState)
    investment: ProgramCaseState = Field(default_factory=ProgramCaseState)


class ProgramRecord(FrozenModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    program_id: StableId
    title: Annotated[str, Field(min_length=3, max_length=300)]
    status: ProgramStatus = ProgramStatus.DRAFT
    stage: ProgramStage = ProgramStage.F0
    entry_point: EntryPoint = EntryPoint.UNSPECIFIED
    route: Route = Route.UNSELECTED
    owner: StableId | None = None
    conversation_key: StableId
    state_version: Annotated[int, Field(ge=1)] = 1
    current_versions: ProgramPointers = Field(default_factory=ProgramPointers)
    current_five_cases: FiveCaseState = Field(default_factory=FiveCaseState)
    falsifiers: tuple[str, ...] = ()
    kill_criteria: tuple[str, ...] = ()
    open_conditions: tuple[CaseCondition, ...] = ()
    open_findings: tuple[StableId, ...] = ()
    active_workstreams: tuple[str, ...] = ()
    active_tasks: tuple[StableId, ...] = ()
    hold_trigger: str | None = None
    hold_expiry: AwareDatetime | None = None
    redesign_count: Annotated[int, Field(ge=0)] = 0
    last_gate_decision_id: StableId | None = None
    last_gate_packet_digest: Digest | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class CouncilSession(FrozenModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    session_id: StableId
    program_id: StableId
    charter: DecisionCharter
    phase: SessionPhase = SessionPhase.CONSTITUTED
    state_version: Annotated[int, Field(ge=1)] = 1
    evidence: EvidenceManifest | None = None
    participants: tuple[ParticipantAssignment, ...]
    claims: tuple[Claim, ...] = ()
    opinions: tuple[CaseOpinion, ...] = ()
    challenges: tuple[Challenge, ...] = ()
    responses: tuple[ChallengeResponse, ...] = ()
    resolutions: tuple[ChallengeResolution, ...] = ()
    red_team_report: RedTeamReport | None = None
    final_cases: tuple[FinalCaseAssessment, ...] = ()
    dissent: tuple[Dissent, ...] = ()
    gate_packet_inputs: GatePacketInputs | None = None
    arbitration: ArbitrationResult | None = None
    gate_packet: GatePacket | None = None
    approval_request: ApprovalRequest | None = None
    approval_ids: tuple[StableId, ...] = ()
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)


class AuditEvent(FrozenModel):
    event_id: StableId
    idempotency_key: StableId
    aggregate_type: Literal["PROGRAM", "COUNCIL_SESSION", "APPROVAL"]
    aggregate_id: StableId
    aggregate_version: Annotated[int, Field(ge=1)]
    actor_id: StableId
    actor_kind: ActorKind
    action: StableId
    reason: Annotated[str, Field(min_length=3, max_length=3000)]
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: AwareDatetime = Field(default_factory=utc_now)


class CommandReceipt(FrozenModel):
    aggregate_id: StableId
    state_version: Annotated[int, Field(ge=1)]
    phase: SessionPhase
    event_id: StableId
    action: StableId


class CouncilSessionView(FrozenModel):
    session_id: StableId
    program_id: StableId
    phase: SessionPhase
    state_version: Annotated[int, Field(ge=1)]
    evidence_snapshot: SnapshotRef | None = None
    opinions: tuple[CaseOpinion, ...] = ()
    claims: tuple[Claim, ...] = ()
    challenges: tuple[Challenge, ...] = ()
    responses: tuple[ChallengeResponse, ...] = ()
    red_team_report: RedTeamReport | None = None
    final_cases: tuple[FinalCaseAssessment, ...] = ()
    dissent: tuple[Dissent, ...] = ()


class GateDecision(FrozenModel):
    decision_id: StableId
    program_id: StableId
    session_id: StableId
    gate_packet_digest: Digest
    disposition: Disposition
    previous_stage: ProgramStage
    resulting_stage: ProgramStage
    approval_ids: tuple[StableId, ...]
    committed_program_revision: Annotated[int, Field(ge=2)]
    committed_at: AwareDatetime = Field(default_factory=utc_now)
