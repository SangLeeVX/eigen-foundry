from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .errors import ValidationFailure
from .models import ActorKind, CaseType, ParticipantAssignment, SnapshotRef, StableId


class Capability(StrEnum):
    READ_PROGRAM = "READ_PROGRAM"
    READ_FROZEN_EVIDENCE = "READ_FROZEN_EVIDENCE"
    SUBMIT_CLAIM = "SUBMIT_CLAIM"
    SUBMIT_OPINION = "SUBMIT_OPINION"
    SUBMIT_CHALLENGE = "SUBMIT_CHALLENGE"
    SUBMIT_RESPONSE = "SUBMIT_RESPONSE"
    SUBMIT_RED_TEAM = "SUBMIT_RED_TEAM"
    FINALIZE_CASE = "FINALIZE_CASE"
    APPLY_POLICY = "APPLY_POLICY"
    REQUEST_APPROVAL = "REQUEST_APPROVAL"


@dataclass(frozen=True)
class RoleContract:
    role: str
    capabilities: frozenset[Capability]
    forbidden: frozenset[str]


ROLE_CONTRACTS: dict[str, RoleContract] = {
    "foundry_commander": RoleContract(
        "foundry_commander",
        frozenset({Capability.READ_PROGRAM, Capability.READ_FROZEN_EVIDENCE, Capability.REQUEST_APPROVAL}),
        frozenset({"case_vote", "human_approval", "gate_commit"}),
    ),
    "evidence_steward": RoleContract(
        "evidence_steward",
        frozenset({Capability.READ_PROGRAM, Capability.READ_FROZEN_EVIDENCE}),
        frozenset({"recommend_disposition", "promote_evidence", "human_approval", "gate_commit"}),
    ),
    "program_architect": RoleContract(
        "program_architect",
        frozenset({Capability.READ_PROGRAM, Capability.READ_FROZEN_EVIDENCE, Capability.SUBMIT_CLAIM}),
        frozenset({"red_team_own_work", "arbitrate_own_work", "human_approval", "gate_commit"}),
    ),
    "case_captain": RoleContract(
        "case_captain",
        frozenset(
            {
                Capability.READ_PROGRAM,
                Capability.READ_FROZEN_EVIDENCE,
                Capability.SUBMIT_CLAIM,
                Capability.SUBMIT_OPINION,
                Capability.SUBMIT_CHALLENGE,
                Capability.SUBMIT_RESPONSE,
                Capability.FINALIZE_CASE,
            }
        ),
        frozenset({"override_other_case", "human_approval", "gate_commit"}),
    ),
    "independent_red_team": RoleContract(
        "independent_red_team",
        frozenset(
            {
                Capability.READ_PROGRAM,
                Capability.READ_FROZEN_EVIDENCE,
                Capability.SUBMIT_CHALLENGE,
                Capability.SUBMIT_RED_TEAM,
            }
        ),
        frozenset({"modify_evidence", "human_approval", "gate_commit"}),
    ),
    "policy_arbiter": RoleContract(
        "policy_arbiter",
        frozenset({Capability.READ_PROGRAM, Capability.READ_FROZEN_EVIDENCE, Capability.APPLY_POLICY}),
        frozenset({"create_evidence", "change_case_status", "invent_exception", "human_approval", "gate_commit"}),
    ),
    "independent_reviewer": RoleContract(
        "independent_reviewer",
        frozenset({Capability.READ_PROGRAM, Capability.READ_FROZEN_EVIDENCE}),
        frozenset({"review_own_claim", "resolve_own_challenge", "human_approval", "gate_commit"}),
    ),
}


REQUIRED_STANDING_ROLES = {
    "foundry_commander",
    "evidence_steward",
    "program_architect",
    "independent_red_team",
    "policy_arbiter",
    "independent_reviewer",
}


@dataclass(frozen=True)
class AgentContext:
    """Only immutable, structured inputs cross into a model adapter."""

    session_id: StableId
    program_id: StableId
    assignment: ParticipantAssignment
    program_snapshot: SnapshotRef
    evidence_snapshot: SnapshotRef
    tpp: SnapshotRef
    rights: SnapshotRef
    budget: SnapshotRef
    risk_register: SnapshotRef
    gate_policy: SnapshotRef
    allowed_capabilities: frozenset[Capability]


def validate_council_roster(participants: tuple[ParticipantAssignment, ...]) -> None:
    assignments = [p.assignment_id for p in participants]
    if len(assignments) != len(set(assignments)):
        raise ValidationFailure("participant assignment IDs must be unique")

    actor_ids = [p.actor_id for p in participants]
    if len(actor_ids) != len(set(actor_ids)):
        raise ValidationFailure("each council seat must use a distinct actor identity")

    roles = {p.role for p in participants}
    missing = REQUIRED_STANDING_ROLES - roles
    if missing:
        raise ValidationFailure("standing council roles are missing", roles=sorted(missing))
    for role in REQUIRED_STANDING_ROLES:
        if sum(1 for participant in participants if participant.role == role) != 1:
            raise ValidationFailure("standing council roles require exactly one assigned seat", role=role)
    for participant in participants:
        if participant.role == "policy_arbiter":
            if participant.actor_kind is not ActorKind.SERVICE:
                raise ValidationFailure("the policy arbiter must be a service identity")
        elif participant.actor_kind is not ActorKind.AGENT:
            raise ValidationFailure("council analysis and review seats must use agent identities", role=participant.role)

    case_assignments = [p for p in participants if p.role == "case_captain"]
    cases = [p.case for p in case_assignments]
    if set(cases) != set(CaseType) or len(cases) != len(CaseType):
        raise ValidationFailure("exactly one captain is required for each of the five cases")

    architect = next(p for p in participants if p.role == "program_architect")
    red_team = next(p for p in participants if p.role == "independent_red_team")
    reviewer = next(p for p in participants if p.role == "independent_reviewer")
    arbiter = next(p for p in participants if p.role == "policy_arbiter")
    if architect.actor_id in {red_team.actor_id, arbiter.actor_id}:
        raise ValidationFailure("the program architect cannot red-team or arbitrate its own case")
    if architect.independence_group == red_team.independence_group:
        raise ValidationFailure("red team must use a different independence group")
    if architect.run_id is not None and architect.run_id == red_team.run_id:
        raise ValidationFailure("the originating run cannot satisfy independent review")
    independence_seats = [architect, red_team, reviewer, *case_assignments]
    groups = [seat.independence_group for seat in independence_seats]
    if len(groups) != len(set(groups)):
        raise ValidationFailure("thesis, case, red-team, and resolution seats require distinct independence groups")
    run_ids = [seat.run_id for seat in independence_seats if seat.run_id is not None]
    if len(run_ids) != len(set(run_ids)):
        raise ValidationFailure("the same execution run cannot satisfy independent council seats")


def participant_for_case(
    participants: tuple[ParticipantAssignment, ...], case: CaseType
) -> ParticipantAssignment:
    for participant in participants:
        if participant.role == "case_captain" and participant.case is case:
            return participant
    raise ValidationFailure("no assigned captain for case", case=case.value)
