from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from .agents import participant_for_case, validate_council_roster
from .errors import ApprovalRequired, Forbidden, StateConflict, ValidationFailure
from .ledger import SQLiteLedger
from .models import (
    ActorKind,
    Approval,
    ApprovalDecision,
    ApprovalRequest,
    AuditEvent,
    CaseOpinion,
    CaseType,
    Challenge,
    ChallengeResolution,
    ChallengeResponse,
    Claim,
    ClaimState,
    CommandReceipt,
    CouncilSession,
    CouncilSessionView,
    DecisionCharter,
    Dissent,
    Disposition,
    EntryPoint,
    EvidenceManifest,
    FinalCaseAssessment,
    FiveCaseState,
    GateDecision,
    GatePacketInputs,
    GatePolicyArtifact,
    Materiality,
    ParticipantAssignment,
    ProgramPointers,
    ProgramCaseState,
    ProgramRecord,
    ProgramStage,
    ProgramStatus,
    RedTeamReport,
    Route,
    SessionPhase,
    SnapshotRef,
    StableId,
    utc_now,
)
from .policy import (
    DEFAULT_GATE_POLICY,
    GatePolicy,
    build_gate_packet,
    canonical_digest,
    evaluate_session,
    require_phase,
    require_stage_transition,
    required_approver_roles,
    validate_no_agent_approval,
)


@dataclass(frozen=True)
class CommandContext:
    actor_id: StableId
    actor_kind: ActorKind
    idempotency_key: StableId
    expected_version: int | None
    reason: str
    principal_roles: frozenset[str] = frozenset()


class CouncilService:
    """The sole command path for the governed council aggregate."""

    def __init__(self, ledger: SQLiteLedger, gate_policy: GatePolicy = DEFAULT_GATE_POLICY) -> None:
        self.ledger = ledger
        self.gate_policy = gate_policy

    def _active_policy_artifact(self) -> GatePolicyArtifact:
        payload = self.gate_policy.payload()
        canonical_payload = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return GatePolicyArtifact(
            snapshot=self.gate_policy.snapshot_ref(),
            canonical_payload=canonical_payload,
        )

    @staticmethod
    def _policy_from_artifact(artifact: GatePolicyArtifact | None) -> GatePolicy:
        if artifact is None:
            raise ValidationFailure("a reconstructable gate-policy artifact is required")
        try:
            payload = artifact.payload()
            policy = GatePolicy(
                policy_id=payload["policy_id"],
                version=payload["version"],
                enabled_gates=frozenset(ProgramStage(item) for item in payload["enabled_gates"]),
                allow_conditional_advance_for=frozenset(
                    CaseType(item) for item in payload["allow_conditional_advance_for"]
                ),
                allowed_not_applicable_rules=frozenset(
                    (ProgramStage(stage), CaseType(case), rule_id)
                    for stage, case, rule_id in payload["allowed_not_applicable_rules"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailure("gate-policy artifact cannot be reconstructed") from exc
        if policy.snapshot_ref() != artifact.snapshot or policy.payload() != payload:
            raise ValidationFailure(
                "gate-policy artifact is incompatible with the deterministic policy engine"
            )
        return policy

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid4()}"

    def _event(
        self,
        context: CommandContext,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        action: str,
        payload: dict | None = None,
        idempotency_suffix: str = "",
    ) -> AuditEvent:
        key = f"{context.idempotency_key}{idempotency_suffix}"
        return AuditEvent(
            event_id=self._new_id("evt"),
            idempotency_key=key,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            actor_id=context.actor_id,
            actor_kind=context.actor_kind,
            action=action,
            reason=context.reason,
            payload=payload or {},
        )

    @staticmethod
    def _next_session(session: CouncilSession, **changes: object) -> CouncilSession:
        return session.model_copy(
            update={
                **changes,
                "state_version": session.state_version + 1,
                "updated_at": utc_now(),
            }
        )

    @staticmethod
    def _require_expected(actual: int, context: CommandContext) -> int:
        if context.expected_version is None:
            raise StateConflict("a mutation requires an expected aggregate version")
        if context.expected_version != actual:
            raise StateConflict(
                "aggregate changed after it was read",
                expected_version=context.expected_version,
                actual_version=actual,
            )
        return context.expected_version

    @staticmethod
    def _require_seat(
        session: CouncilSession,
        context: CommandContext,
        role: str,
    ) -> ParticipantAssignment:
        seat = next(
            (p for p in session.participants if p.actor_id == context.actor_id and p.role == role),
            None,
        )
        if seat is None:
            raise Forbidden("authenticated actor does not hold the required council seat", role=role)
        if seat.actor_kind is not context.actor_kind:
            raise Forbidden("authenticated actor kind does not match the assigned council seat", role=role)
        return seat

    def create_program_draft(
        self,
        program_id: StableId,
        title: str,
        entry_point: EntryPoint,
        route: Route,
        owner: StableId | None,
        pointers: ProgramPointers,
        context: CommandContext,
    ) -> ProgramRecord:
        if "program_drafter" not in context.principal_roles:
            raise Forbidden("program drafts require an authorized drafter")
        active_artifact = self._active_policy_artifact()
        if pointers.gate_policy != active_artifact.snapshot:
            raise ValidationFailure("Program draft must bind the active gate-policy snapshot")
        if (
            pointers.gate_policy_artifact is not None
            and pointers.gate_policy_artifact != active_artifact
        ):
            raise ValidationFailure("caller-supplied gate-policy artifact does not match the active policy")
        pointers = pointers.model_copy(update={"gate_policy_artifact": active_artifact})
        program = ProgramRecord(
            program_id=program_id,
            title=title,
            entry_point=entry_point,
            route=route,
            owner=owner,
            conversation_key=f"eigen-foundry:{program_id}",
            current_versions=pointers,
        )
        event = self._event(
            context,
            "PROGRAM",
            program_id,
            1,
            "PROGRAM.DRAFT_CREATED",
            {"stage": program.stage.value, "program_digest": canonical_digest(program)},
        )
        if self.ledger.is_idempotent_replay(event):
            return self.ledger.get_program(program_id)
        if route is not Route.UNSELECTED:
            raise ValidationFailure("new F0 Program drafts must keep the formal route UNSELECTED")
        return self.ledger.create_program(program, event)

    def migrate_program_policy_binding(
        self,
        program_id: StableId,
        context: CommandContext,
    ) -> ProgramRecord:
        """Bind a Program to the active server policy through a human-governed revision."""
        if context.actor_kind is not ActorKind.HUMAN or "policy_admin" not in context.principal_roles:
            raise Forbidden("gate-policy binding migration requires an authenticated human policy admin")
        program = self.ledger.get_program(program_id)
        target_policy = self.gate_policy.snapshot_ref()
        target_artifact = self._active_policy_artifact()
        event = self._event(
            context,
            "PROGRAM",
            program_id,
            (context.expected_version or program.state_version) + 1,
            "PROGRAM.GATE_POLICY_BINDING_MIGRATED",
            {
                "expected_program_version": context.expected_version,
                "target_gate_policy": target_policy.model_dump(mode="json"),
                "target_gate_policy_artifact_digest": target_artifact.snapshot.digest,
            },
        )
        if self.ledger.is_idempotent_replay(event):
            return program
        expected = self._require_expected(program.state_version, context)
        if (
            program.current_versions.gate_policy == target_policy
            and program.current_versions.gate_policy_artifact == target_artifact
        ):
            raise ValidationFailure("Program is already bound to the active gate policy")
        updated = program.model_copy(
            update={
                "state_version": program.state_version + 1,
                "current_versions": program.current_versions.model_copy(
                    update={
                        "gate_policy": target_policy,
                        "gate_policy_artifact": target_artifact,
                    }
                ),
                "updated_at": utc_now(),
            }
        )
        return self.ledger.save_program(updated, expected, event)

    def get_session_view(
        self, session_id: StableId, context: CommandContext
    ) -> CouncilSessionView:
        """Return an actor-scoped projection; never expose the internal aggregate to agents."""
        session = self.ledger.get_session(session_id)
        participant = next((item for item in session.participants if item.actor_id == context.actor_id), None)
        is_auditor = "auditor" in context.principal_roles and context.actor_kind is ActorKind.HUMAN
        if participant is None and not is_auditor:
            raise Forbidden("actor has no scoped read assignment for this council session")
        if participant is not None and participant.actor_kind is not context.actor_kind:
            raise Forbidden("actor kind does not match its council assignment")

        if session.phase is SessionPhase.BLIND_OPINIONS and not is_auditor:
            own_opinions = tuple(
                opinion for opinion in session.opinions if opinion.captain_agent_id == context.actor_id
            )
            own_claim_ids = {claim_id for opinion in own_opinions for claim_id in opinion.claim_ids}
            visible_claims = tuple(claim for claim in session.claims if claim.claim_id in own_claim_ids)
            return CouncilSessionView(
                session_id=session.session_id,
                program_id=session.program_id,
                phase=session.phase,
                state_version=session.state_version,
                evidence_snapshot=session.evidence.snapshot if session.evidence else None,
                opinions=own_opinions,
                claims=visible_claims,
            )

        revealed = session.phase not in {
            SessionPhase.CONSTITUTED,
            SessionPhase.EVIDENCE_FROZEN,
            SessionPhase.BLIND_OPINIONS,
        }
        return CouncilSessionView(
            session_id=session.session_id,
            program_id=session.program_id,
            phase=session.phase,
            state_version=session.state_version,
            evidence_snapshot=session.evidence.snapshot if session.evidence else None,
            opinions=session.opinions if revealed or is_auditor else (),
            claims=session.claims if revealed or is_auditor else (),
            challenges=session.challenges if revealed or is_auditor else (),
            responses=session.responses if revealed or is_auditor else (),
            red_team_report=session.red_team_report if revealed or is_auditor else None,
            final_cases=session.final_cases if revealed or is_auditor else (),
            dissent=session.dissent if revealed or is_auditor else (),
        )

    def create_session(
        self,
        session_id: StableId,
        program_id: StableId,
        charter: DecisionCharter,
        participants: tuple[ParticipantAssignment, ...],
        context: CommandContext,
    ) -> CouncilSession:
        program = self.ledger.get_program(program_id)
        if program.status in {ProgramStatus.TERMINATED, ProgramStatus.COMPLETED}:
            raise Forbidden("terminal Programs require a separately governed reopen decision")
        validate_council_roster(participants)
        commander = next(p for p in participants if p.role == "foundry_commander")
        if context.actor_id != commander.actor_id or context.actor_kind is not commander.actor_kind:
            raise Forbidden("only the assigned Foundry Commander may constitute this session")
        if charter.current_stage is not program.stage:
            raise ValidationFailure("decision charter does not match the Program stage")
        if charter.requested_disposition is Disposition.ADVANCE:
            require_stage_transition(charter.current_stage, charter.proposed_stage)
        elif charter.proposed_stage is not charter.current_stage:
            raise ValidationFailure("non-ADVANCE dispositions cannot change the formal stage")
        if charter.expected_program_state_version != program.state_version:
            raise StateConflict(
                "decision charter targets a stale Program version",
                expected_version=charter.expected_program_state_version,
                actual_version=program.state_version,
            )
        if charter.program_snapshot.object_id != program_id:
            raise ValidationFailure("program snapshot points to the wrong Program")
        if charter.program_snapshot.version != program.state_version:
            raise StateConflict("program snapshot version is stale")
        if charter.program_snapshot.digest != canonical_digest(program):
            raise ValidationFailure("program snapshot digest does not match the Program record")
        if charter.gate_policy != self.gate_policy.snapshot_ref():
            raise Forbidden("council session references a gate policy that is not active in this control plane")
        active_artifact = self._active_policy_artifact()
        program_artifact = program.current_versions.gate_policy_artifact
        bound_policy = self._policy_from_artifact(program_artifact)
        if bound_policy.snapshot_ref() != charter.gate_policy or program_artifact != active_artifact:
            raise ValidationFailure("Program policy artifact does not match the active charter binding")
        if (
            charter.gate_policy_artifact is not None
            and charter.gate_policy_artifact != program_artifact
        ):
            raise ValidationFailure("decision charter supplies a mismatched gate-policy artifact")
        charter = charter.model_copy(update={"gate_policy_artifact": program_artifact})
        if charter.current_stage not in self.gate_policy.enabled_gates:
            raise Forbidden("this stage-specific gate policy is not enabled")
        if (
            charter.requested_disposition is Disposition.HOLD
            and (charter.hold_expiry is None or charter.hold_expiry <= datetime.now(timezone.utc))
        ):
            raise ValidationFailure("HOLD expiry must be future-dated when the session is constituted")
        pointer_bindings = {
            "portfolio_mandate": (program.current_versions.portfolio_mandate, charter.portfolio_mandate),
            "tpp": (program.current_versions.tpp, charter.tpp),
            "rights": (program.current_versions.rights_snapshot, charter.rights),
            "budget": (program.current_versions.budget, charter.budget),
            "risk_register": (program.current_versions.risk_register, charter.risk_register),
            "standard_of_care": (program.current_versions.standard_of_care, charter.standard_of_care),
            "gate_policy": (program.current_versions.gate_policy, charter.gate_policy),
            "gate_policy_artifact": (
                program.current_versions.gate_policy_artifact,
                charter.gate_policy_artifact,
            ),
        }
        mismatched = [name for name, (current, bound) in pointer_bindings.items() if current != bound]
        if mismatched:
            raise ValidationFailure(
                "decision charter inputs do not match the current Program pointers",
                bindings=mismatched,
            )
        session = CouncilSession(
            session_id=session_id,
            program_id=program_id,
            charter=charter,
            participants=participants,
        )
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            1,
            "COUNCIL.SESSION_CONSTITUTED",
            {
                "program_id": program_id,
                "question": charter.question,
                "session_digest": canonical_digest(session),
            },
        )
        return self.ledger.create_session(session, charter.expected_program_state_version, event)

    def freeze_evidence(
        self, session_id: StableId, evidence: EvidenceManifest, context: CommandContext
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.CONSTITUTED)
        self._require_seat(session, context, "evidence_steward")
        expected = self._require_expected(session.state_version, context)
        if evidence.snapshot.digest != canonical_digest(evidence.content()):
            raise ValidationFailure("evidence snapshot digest does not match its immutable manifest")
        updated = self._next_session(session, phase=SessionPhase.EVIDENCE_FROZEN, evidence=evidence)
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.EVIDENCE_FROZEN",
            {"evidence": evidence.snapshot.model_dump(mode="json"), "item_count": len(evidence.items)},
        )
        return self.ledger.save_session(updated, expected, event)

    def start_blind_round(self, session_id: StableId, context: CommandContext) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.EVIDENCE_FROZEN)
        self._require_seat(session, context, "foundry_commander")
        expected = self._require_expected(session.state_version, context)
        updated = self._next_session(session, phase=SessionPhase.BLIND_OPINIONS)
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.BLIND_ROUND_OPENED",
        )
        return self.ledger.save_session(updated, expected, event)

    def submit_blind_opinion(
        self,
        session_id: StableId,
        opinion: CaseOpinion,
        claims: tuple[Claim, ...],
        context: CommandContext,
    ) -> CommandReceipt:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.BLIND_OPINIONS)
        expected = self._require_expected(session.state_version, context)
        captain = participant_for_case(session.participants, opinion.case)
        if context.actor_kind is not ActorKind.AGENT or context.actor_id != captain.actor_id:
            raise Forbidden("only the assigned case captain may submit this opinion")
        if opinion.captain_agent_id != captain.actor_id:
            raise ValidationFailure("opinion author does not match the assigned captain")
        if any(item.case is opinion.case for item in session.opinions):
            raise ValidationFailure("a blind opinion already exists for this case", case=opinion.case.value)
        submitted_claim_ids = {claim.claim_id for claim in claims}
        if submitted_claim_ids != set(opinion.claim_ids):
            raise ValidationFailure("the opinion must enumerate exactly its submitted claims")
        if any(claim.owner_agent_id != context.actor_id for claim in claims):
            raise ValidationFailure("a captain may submit only its own claims")
        if any(
            claim.state in {ClaimState.EXPERIMENTALLY_VALIDATED, ClaimState.TRANSLATIONALLY_VALIDATED}
            for claim in claims
        ):
            raise Forbidden("agent submissions cannot promote evidence to a validated state")
        existing_claim_ids = {claim.claim_id for claim in session.claims}
        if existing_claim_ids.intersection(submitted_claim_ids):
            raise ValidationFailure("claim IDs must be unique within the session")
        assert session.evidence is not None
        allowed_evidence = set(session.evidence.items)
        if any(not set(claim.evidence_refs).issubset(allowed_evidence) for claim in claims):
            raise ValidationFailure("claim cites evidence outside the frozen evidence manifest")
        updated = self._next_session(
            session,
            opinions=(*session.opinions, opinion),
            claims=(*session.claims, *claims),
        )
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.BLIND_OPINION_SUBMITTED",
            {
                "case": opinion.case.value,
                "opinion_id": opinion.opinion_id,
                "submission_digest": canonical_digest(
                    {
                        "opinion": opinion.model_dump(mode="json"),
                        "claims": [claim.model_dump(mode="json") for claim in claims],
                    }
                ),
            },
        )
        saved = self.ledger.save_session(updated, expected, event)
        return CommandReceipt(
            aggregate_id=saved.session_id,
            state_version=saved.state_version,
            phase=saved.phase,
            event_id=event.event_id,
            action=event.action,
        )

    def reveal_claims(self, session_id: StableId, context: CommandContext) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.BLIND_OPINIONS)
        self._require_seat(session, context, "foundry_commander")
        expected = self._require_expected(session.state_version, context)
        if len(session.opinions) != 5:
            raise ValidationFailure("all five blind opinions must be locked before reveal")
        updated = self._next_session(session, phase=SessionPhase.CLAIMS_REVEALED)
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.CLAIMS_REVEALED",
        )
        return self.ledger.save_session(updated, expected, event)

    def open_challenges(self, session_id: StableId, context: CommandContext) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.CLAIMS_REVEALED)
        self._require_seat(session, context, "foundry_commander")
        expected = self._require_expected(session.state_version, context)
        updated = self._next_session(session, phase=SessionPhase.CHALLENGES)
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.CHALLENGE_ROUND_OPENED",
        )
        return self.ledger.save_session(updated, expected, event)

    def add_challenge(
        self, session_id: StableId, challenge: Challenge, context: CommandContext
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.CHALLENGES)
        expected = self._require_expected(session.state_version, context)
        participant = next((p for p in session.participants if p.actor_id == context.actor_id), None)
        if participant is None or participant.role not in {"case_captain", "independent_red_team"}:
            raise Forbidden("this council seat cannot challenge a claim")
        if participant.actor_kind is not context.actor_kind:
            raise Forbidden("actor kind does not match the assigned challenge seat")
        claim = next((item for item in session.claims if item.claim_id == challenge.claim_id), None)
        if claim is None:
            raise ValidationFailure("challenge references a nonexistent claim")
        if claim.owner_agent_id == context.actor_id:
            raise Forbidden("an agent cannot challenge its own claim")
        if challenge.challenger_agent_id != context.actor_id:
            raise ValidationFailure("challenge author does not match authenticated actor")
        if challenge.target_claim_version != claim.version:
            raise ValidationFailure("challenge must bind the exact current claim version")
        assert session.evidence is not None
        if not set(challenge.evidence_refs).issubset(set(session.evidence.items)):
            raise ValidationFailure("challenge cites evidence outside the frozen evidence manifest")
        if any(item.challenge_id == challenge.challenge_id for item in session.challenges):
            raise ValidationFailure("challenge ID already exists")
        updated = self._next_session(session, challenges=(*session.challenges, challenge))
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.CHALLENGE_ADDED",
            {
                "challenge_id": challenge.challenge_id,
                "claim_id": challenge.claim_id,
                "challenge_digest": canonical_digest(challenge),
            },
        )
        return self.ledger.save_session(updated, expected, event)

    def close_challenges(self, session_id: StableId, context: CommandContext) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.CHALLENGES)
        self._require_seat(session, context, "foundry_commander")
        expected = self._require_expected(session.state_version, context)
        updated = self._next_session(session, phase=SessionPhase.RESPONSES)
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.CHALLENGES_CLOSED",
        )
        return self.ledger.save_session(updated, expected, event)

    def add_response(
        self, session_id: StableId, response: ChallengeResponse, context: CommandContext
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.RESPONSES)
        expected = self._require_expected(session.state_version, context)
        challenge = next((item for item in session.challenges if item.challenge_id == response.challenge_id), None)
        if challenge is None:
            raise ValidationFailure("response references a nonexistent challenge")
        claim = next(item for item in session.claims if item.claim_id == challenge.claim_id)
        owner_assignment = next(
            (item for item in session.participants if item.actor_id == claim.owner_agent_id), None
        )
        if owner_assignment is None or owner_assignment.actor_kind is not context.actor_kind:
            raise Forbidden("actor kind does not match the challenged claim owner")
        if context.actor_id != claim.owner_agent_id or response.owner_agent_id != context.actor_id:
            raise Forbidden("only the challenged claim owner may respond")
        if any(item.challenge_id == response.challenge_id for item in session.responses):
            raise ValidationFailure("challenge already has a response")
        claims = session.claims
        if response.replacement_claim is not None:
            replacement = response.replacement_claim
            if replacement.owner_agent_id != context.actor_id or replacement.supersedes_claim_id != claim.claim_id:
                raise ValidationFailure("replacement claim must preserve ownership and supersede the challenged claim")
            if any(item.claim_id == replacement.claim_id for item in claims):
                raise ValidationFailure("replacement claim ID already exists")
            assert session.evidence is not None
            if not set(replacement.evidence_refs).issubset(set(session.evidence.items)):
                raise ValidationFailure("replacement claim cites evidence outside the frozen evidence manifest")
            if replacement.state in {
                ClaimState.EXPERIMENTALLY_VALIDATED,
                ClaimState.TRANSLATIONALLY_VALIDATED,
            }:
                raise Forbidden("agent responses cannot promote evidence to a validated state")
            claims = (*claims, replacement)
        updated = self._next_session(
            session,
            responses=(*session.responses, response),
            claims=claims,
        )
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.CHALLENGE_RESPONDED",
            {
                "challenge_id": response.challenge_id,
                "disposition": response.disposition.value,
                "response_digest": canonical_digest(response),
            },
        )
        return self.ledger.save_session(updated, expected, event)

    def resolve_challenge(
        self, session_id: StableId, resolution: ChallengeResolution, context: CommandContext
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.RESPONSES)
        expected = self._require_expected(session.state_version, context)
        challenge = next((item for item in session.challenges if item.challenge_id == resolution.challenge_id), None)
        response = next((item for item in session.responses if item.challenge_id == resolution.challenge_id), None)
        if challenge is None or response is None:
            raise ValidationFailure("challenge must have a response before resolution")
        reviewer = self._require_seat(session, context, "independent_reviewer")
        claim = next(item for item in session.claims if item.claim_id == challenge.claim_id)
        if context.actor_id in {claim.owner_agent_id, challenge.challenger_agent_id}:
            raise Forbidden("reviewer must be independent of the claim and challenge")
        if resolution.reviewer_agent_id != context.actor_id:
            raise ValidationFailure("resolution reviewer does not match authenticated actor")
        if any(item.challenge_id == resolution.challenge_id for item in session.resolutions):
            raise ValidationFailure("challenge already has a resolution")
        updated = self._next_session(session, resolutions=(*session.resolutions, resolution))
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.CHALLENGE_RESOLVED",
            {
                "challenge_id": resolution.challenge_id,
                "outcome": resolution.outcome.value,
                "resolution_digest": canonical_digest(resolution),
            },
        )
        return self.ledger.save_session(updated, expected, event)

    def start_red_team(self, session_id: StableId, context: CommandContext) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.RESPONSES)
        self._require_seat(session, context, "foundry_commander")
        expected = self._require_expected(session.state_version, context)
        challenge_ids = {item.challenge_id for item in session.challenges}
        if challenge_ids != {item.challenge_id for item in session.responses}:
            raise ValidationFailure("every challenge requires a response")
        if challenge_ids != {item.challenge_id for item in session.resolutions}:
            raise ValidationFailure("every challenge requires an independent resolution")
        updated = self._next_session(session, phase=SessionPhase.RED_TEAM)
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.RED_TEAM_OPENED",
        )
        return self.ledger.save_session(updated, expected, event)

    def submit_red_team(
        self, session_id: StableId, report: RedTeamReport, context: CommandContext
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.RED_TEAM)
        expected = self._require_expected(session.state_version, context)
        assignment = self._require_seat(session, context, "independent_red_team")
        if context.actor_id != assignment.actor_id or report.reviewer_agent_id != context.actor_id:
            raise Forbidden("only the assigned independent red team may submit the report")
        if session.red_team_report is not None:
            raise ValidationFailure("red-team report is already locked")
        assert session.evidence is not None
        allowed_evidence = set(session.evidence.items)
        if any(not set(finding.evidence_refs).issubset(allowed_evidence) for finding in report.findings):
            raise ValidationFailure("red-team finding cites evidence outside the frozen evidence manifest")
        updated = self._next_session(session, red_team_report=report)
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.RED_TEAM_SUBMITTED",
            {"report_id": report.report_id, "report_digest": canonical_digest(report)},
        )
        return self.ledger.save_session(updated, expected, event)

    def open_final_cases(self, session_id: StableId, context: CommandContext) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.RED_TEAM)
        self._require_seat(session, context, "foundry_commander")
        expected = self._require_expected(session.state_version, context)
        if session.red_team_report is None:
            raise ValidationFailure("red-team report is required before final case determinations")
        updated = self._next_session(session, phase=SessionPhase.FINAL_CASE_STATUSES)
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.FINAL_CASES_OPENED",
        )
        return self.ledger.save_session(updated, expected, event)

    def submit_final_case(
        self, session_id: StableId, assessment: FinalCaseAssessment, context: CommandContext
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.FINAL_CASE_STATUSES)
        expected = self._require_expected(session.state_version, context)
        captain = participant_for_case(session.participants, assessment.case)
        if (
            context.actor_kind is not captain.actor_kind
            or context.actor_id != captain.actor_id
            or assessment.captain_agent_id != context.actor_id
        ):
            raise Forbidden("only the assigned captain may finalize this case")
        if any(item.case is assessment.case for item in session.final_cases):
            raise ValidationFailure("case already has a final determination")
        claims_by_id = {claim.claim_id: claim for claim in session.claims}
        if any(claim_id not in claims_by_id for claim_id in assessment.claim_ids):
            raise ValidationFailure("final case references a nonexistent claim")
        if any(claims_by_id[claim_id].owner_agent_id != captain.actor_id for claim_id in assessment.claim_ids):
            raise ValidationFailure("final case may cite only its assigned captain's claim lineage")
        superseded = {claim.supersedes_claim_id for claim in session.claims if claim.supersedes_claim_id}
        if set(assessment.claim_ids).intersection(superseded):
            raise ValidationFailure("final case cannot cite a superseded claim version")
        updated = self._next_session(session, final_cases=(*session.final_cases, assessment))
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.CASE_FINALIZED",
            {
                "case": assessment.case.value,
                "status": assessment.status.value,
                "assessment_digest": canonical_digest(assessment),
            },
        )
        return self.ledger.save_session(updated, expected, event)

    def arbitrate(
        self,
        session_id: StableId,
        result_id: StableId,
        packet_id: StableId,
        context: CommandContext,
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        expected = self._require_expected(session.state_version, context)
        arbiter = next(p for p in session.participants if p.role == "policy_arbiter")
        if context.actor_kind is not ActorKind.SERVICE or context.actor_id != arbiter.actor_id:
            raise Forbidden("only the deterministic policy service may arbitrate")
        bound_policy = self._policy_from_artifact(session.charter.gate_policy_artifact)
        if session.charter.gate_policy != bound_policy.snapshot_ref():
            raise ValidationFailure("gate-policy artifact does not match the session binding")
        arbitration = evaluate_session(session, result_id, arbiter.actor_id, bound_policy)
        updated = self._next_session(
            session,
            phase=SessionPhase.ARBITRATION if arbitration.eligible else SessionPhase.RETURNED,
            arbitration=arbitration,
        )
        packet = build_gate_packet(updated, packet_id)
        updated = updated.model_copy(update={"gate_packet": packet})
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.ARBITRATED",
            {"eligible": arbitration.eligible, "packet_digest": packet.digest},
        )
        return self.ledger.save_session(updated, expected, event)

    def submit_dissent(
        self,
        session_id: StableId,
        dissent_id: StableId,
        statement: str,
        materiality: Materiality,
        context: CommandContext,
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.FINAL_CASE_STATUSES)
        expected = self._require_expected(session.state_version, context)
        assignment = next(
            (participant for participant in session.participants if participant.actor_id == context.actor_id),
            None,
        )
        if (
            assignment is None
            or assignment.actor_kind is not context.actor_kind
            or assignment.role == "policy_arbiter"
        ):
            raise Forbidden("only an authenticated deliberating council participant may submit dissent")
        if any(item.dissent_id == dissent_id for item in session.dissent):
            raise ValidationFailure("dissent ID already exists", dissent_id=dissent_id)
        dissent = Dissent(
            dissent_id=dissent_id,
            agent_id=context.actor_id,
            assignment_id=assignment.assignment_id,
            role=assignment.role,
            statement=statement,
            materiality=materiality,
            submitted_at=utc_now(),
        )
        updated = self._next_session(session, dissent=(*session.dissent, dissent))
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.DISSENT_SUBMITTED",
            {
                "dissent_id": dissent_id,
                "assignment_id": assignment.assignment_id,
                "dissent_digest": canonical_digest(dissent),
            },
        )
        return self.ledger.save_session(updated, expected, event)

    def submit_gate_packet_inputs(
        self,
        session_id: StableId,
        packet_inputs: GatePacketInputs,
        context: CommandContext,
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.FINAL_CASE_STATUSES)
        self._require_seat(session, context, "foundry_commander")
        expected = self._require_expected(session.state_version, context)
        if len(session.final_cases) != 5:
            raise ValidationFailure("five final case determinations are required before packet assembly")
        if session.gate_packet_inputs is not None:
            raise ValidationFailure("gate-packet inputs are already locked")
        known_claim_ids = {claim.claim_id for claim in session.claims}
        referenced = (
            set(packet_inputs.decisive_claim_ids)
            | set(packet_inputs.contradiction_claim_ids)
            | set(packet_inputs.null_claim_ids)
            | set(packet_inputs.unknown_claim_ids)
        )
        if not referenced.issubset(known_claim_ids):
            raise ValidationFailure("gate-packet inputs reference claims outside the frozen council ledger")
        expected_decisive = {claim_id for assessment in session.final_cases for claim_id in assessment.claim_ids}
        expected_contradictions = {
            claim.claim_id for claim in session.claims if claim.state is ClaimState.CONTRADICTED
        }
        expected_nulls = {claim.claim_id for claim in session.claims if claim.measured_null}
        expected_unknowns = {claim.claim_id for claim in session.claims if claim.state is ClaimState.UNKNOWN}
        if set(packet_inputs.decisive_claim_ids) != expected_decisive:
            raise ValidationFailure("decisive claims must be derived from all five final case determinations")
        if set(packet_inputs.contradiction_claim_ids) != expected_contradictions:
            raise ValidationFailure("all contradicted claims must be included in the gate packet")
        if set(packet_inputs.null_claim_ids) != expected_nulls:
            raise ValidationFailure("all measured-null claims must be included in the gate packet")
        if set(packet_inputs.unknown_claim_ids) != expected_unknowns:
            raise ValidationFailure("all UNKNOWN claims must be included in the gate packet")
        updated = self._next_session(session, gate_packet_inputs=packet_inputs)
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.GATE_PACKET_INPUTS_LOCKED",
            {
                "decisive_claim_ids": list(packet_inputs.decisive_claim_ids),
                "proposed_task_ids": list(packet_inputs.proposed_task_ids),
                "packet_inputs_digest": canonical_digest(packet_inputs),
            },
        )
        return self.ledger.save_session(updated, expected, event)

    def request_approval(
        self,
        session_id: StableId,
        request_id: StableId,
        expires_at: datetime,
        context: CommandContext,
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.ARBITRATION)
        self._require_seat(session, context, "foundry_commander")
        expected = self._require_expected(session.state_version, context)
        if session.arbitration is None or not session.arbitration.eligible or session.gate_packet is None:
            raise ApprovalRequired("an ineligible gate packet cannot enter approval")
        if (
            expires_at.tzinfo is None
            or expires_at <= datetime.now(timezone.utc)
            or expires_at > session.charter.session_deadline
        ):
            raise ValidationFailure("approval request expiry must be future-dated and within the council session")
        request = ApprovalRequest(
            request_id=request_id,
            session_id=session_id,
            program_id=session.program_id,
            gate_packet_digest=session.gate_packet.digest,
            required_roles=required_approver_roles(session),
            exact_scope=session.charter.exact_scope,
            expires_at=expires_at,
        )
        updated = self._next_session(
            session,
            phase=SessionPhase.AWAITING_HUMAN_APPROVAL,
            approval_request=request,
        )
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.APPROVAL_REQUESTED",
            {
                "request_id": request_id,
                "required_roles": list(request.required_roles),
                "approval_request_digest": canonical_digest(request),
            },
        )
        return self.ledger.save_session(updated, expected, event)

    def record_approval(
        self, session_id: StableId, approval: Approval, context: CommandContext
    ) -> CouncilSession:
        session = self.ledger.get_session(session_id)
        require_phase(session, SessionPhase.AWAITING_HUMAN_APPROVAL)
        expected = self._require_expected(session.state_version, context)
        request = session.approval_request
        if request is None or session.gate_packet is None:
            raise ApprovalRequired("approval request is missing")
        if context.actor_kind is not ActorKind.HUMAN or approval.approver_kind is not ActorKind.HUMAN:
            raise Forbidden("only authenticated humans may approve")
        if approval.approver_id != context.actor_id:
            raise Forbidden("approval identity cannot be supplied by the request body")
        validate_no_agent_approval(session, approval.approver_id)
        if approval.request_id != request.request_id or approval.gate_packet_digest != request.gate_packet_digest:
            raise ValidationFailure("approval is not bound to the current review packet")
        if approval.session_id != session_id or approval.program_id != session.program_id:
            raise ValidationFailure("approval targets the wrong aggregate")
        if approval.role not in request.required_roles or approval.role not in context.principal_roles:
            raise Forbidden("approver lacks the required functional role", role=approval.role)
        if approval.conflicts:
            raise Forbidden("conflicted approval requires a governed recusal or exception workflow")
        if approval.conditions:
            raise ApprovalRequired("conditioned approvals are disabled until satisfaction evidence is implemented")
        if request.expires_at <= datetime.now(timezone.utc):
            raise ApprovalRequired("approval request has expired")
        approval = approval.model_copy(update={"decided_at": utc_now()})
        existing = self.ledger.get_approvals(session_id)
        if any(item.role == approval.role for item in existing):
            raise ValidationFailure("functional role has already signed", role=approval.role)
        next_phase = SessionPhase.RETURNED if approval.decision is ApprovalDecision.REJECTED else session.phase
        updated = self._next_session(
            session,
            phase=next_phase,
            approval_ids=(*session.approval_ids, approval.approval_id),
        )
        event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated.state_version,
            "COUNCIL.APPROVAL_RECORDED",
            {
                "approval_id": approval.approval_id,
                "role": approval.role,
                "decision": approval.decision.value,
                "approval_digest": canonical_digest(approval),
            },
        )
        return self.ledger.record_approval(updated, approval, expected, event)

    def commit_gate_decision(
        self,
        session_id: StableId,
        decision_id: StableId,
        context: CommandContext,
    ) -> tuple[ProgramRecord, CouncilSession]:
        if context.actor_kind is not ActorKind.SERVICE or "ledger_committer" not in context.principal_roles:
            raise Forbidden("only the internal ledger committer may commit a gate decision")
        session = self.ledger.get_session(session_id)
        if session.phase is SessionPhase.COMMITTED:
            current_program = self.ledger.get_program(session.program_id)
            packet = session.gate_packet
            if packet is None:
                raise StateConflict("committed session has no gate packet")
            replay_event = self._event(
                context,
                "PROGRAM",
                current_program.program_id,
                current_program.state_version,
                "PROGRAM.GATE_DECISION_COMMITTED",
                {
                    "decision_id": decision_id,
                    "previous_stage": session.charter.current_stage.value,
                    "resulting_stage": current_program.stage.value,
                    "disposition": session.charter.requested_disposition.value,
                    "packet_digest": packet.digest,
                },
                ".program",
            )
            if self.ledger.is_idempotent_replay(replay_event):
                return current_program, session
        require_phase(session, SessionPhase.AWAITING_HUMAN_APPROVAL)
        expected_session = self._require_expected(session.state_version, context)
        request = session.approval_request
        packet = session.gate_packet
        arbitration = session.arbitration
        if request is None or packet is None or arbitration is None or not arbitration.eligible:
            raise ApprovalRequired("eligible arbitration and a sealed packet are required")
        if request.expires_at <= datetime.now(timezone.utc):
            raise ApprovalRequired("approval request has expired")
        if request.gate_packet_digest != packet.digest:
            raise ValidationFailure("review packet digest changed after approval was requested")
        if session.charter.gate_policy != self.gate_policy.snapshot_ref():
            raise ApprovalRequired("the reviewed gate policy is no longer active")
        bound_policy = self._policy_from_artifact(session.charter.gate_policy_artifact)
        if bound_policy.snapshot_ref() != session.charter.gate_policy:
            raise ValidationFailure("reviewed gate-policy artifact does not match its snapshot")
        policy_projection = session.model_copy(update={"phase": SessionPhase.FINAL_CASE_STATUSES})
        policy_recheck = evaluate_session(
            policy_projection,
            arbitration.result_id,
            arbitration.arbiter_agent_id,
            bound_policy,
        )
        if not policy_recheck.eligible or policy_recheck.recommended_disposition is not arbitration.recommended_disposition:
            raise ApprovalRequired("current gate policy no longer permits the reviewed disposition")
        review_projection = session.model_copy(update={"state_version": packet.session_version})
        recomputed_packet = build_gate_packet(review_projection, packet.packet_id)
        if recomputed_packet.digest != packet.digest:
            raise ValidationFailure("sealed gate packet no longer matches the reviewed session content")
        policy_roles = required_approver_roles(session)
        if request.required_roles != policy_roles or packet.required_approver_roles != policy_roles:
            raise ValidationFailure("approval quorum no longer matches the bound gate policy")

        approvals = self.ledger.get_approvals(session_id)
        approvals_by_role = {
            approval.role: approval
            for approval in approvals
            if approval.decision is ApprovalDecision.APPROVED
            and approval.gate_packet_digest == packet.digest
            and approval.approver_kind is ActorKind.HUMAN
        }
        missing = set(request.required_roles) - set(approvals_by_role)
        if missing:
            raise ApprovalRequired("required functional sign-offs are missing", roles=sorted(missing))
        if any(approval.conditions for approval in approvals_by_role.values()):
            raise ApprovalRequired("approval conditions require explicit satisfaction evidence before commit")

        program = self.ledger.get_program(session.program_id)
        if program.status in {ProgramStatus.TERMINATED, ProgramStatus.COMPLETED}:
            raise StateConflict("terminal Program cannot be changed by a normal council commit")
        if program.state_version != session.charter.expected_program_state_version:
            raise StateConflict(
                "Program changed after the council session was constituted",
                expected_version=session.charter.expected_program_state_version,
                actual_version=program.state_version,
            )
        if program.stage is not session.charter.current_stage:
            raise StateConflict("Program stage changed after review")
        if canonical_digest(program) != session.charter.program_snapshot.digest:
            raise StateConflict("Program snapshot no longer matches the reviewed record")

        status_by_disposition = {
            Disposition.ADVANCE: ProgramStatus.ACTIVE,
            Disposition.HOLD: ProgramStatus.HOLD,
            Disposition.REDESIGN_ONCE: ProgramStatus.REDESIGN,
            Disposition.PARTNER: ProgramStatus.PARTNERING,
            Disposition.LICENSE_OR_ACQUIRE: ProgramStatus.LICENSE_NEGOTIATION,
            Disposition.SPINOUT: ProgramStatus.SPINOUT,
            Disposition.TERMINATE: ProgramStatus.TERMINATED,
        }
        if session.charter.requested_disposition is Disposition.ESCALATE:
            raise ValidationFailure("ESCALATE cannot be committed as a Program disposition")
        if (
            session.charter.requested_disposition is Disposition.HOLD
            and (session.charter.hold_expiry is None or session.charter.hold_expiry <= datetime.now(timezone.utc))
        ):
            raise ApprovalRequired("HOLD expired before the decision could be committed")
        if session.charter.requested_disposition is Disposition.REDESIGN_ONCE and program.redesign_count >= 1:
            raise ApprovalRequired("the one-redesign limit has been reached; a governed exception is required")
        new_stage = (
            session.charter.proposed_stage
            if session.charter.requested_disposition is Disposition.ADVANCE
            else program.stage
        )
        proposed = session.charter.proposed_outputs
        final_case_map = {assessment.case: assessment for assessment in session.final_cases}
        five_cases = FiveCaseState(
            scientific=ProgramCaseState(**final_case_map[CaseType.SCIENTIFIC].model_dump(include={"status", "rationale", "conditions"})),
            product=ProgramCaseState(**final_case_map[CaseType.PRODUCT].model_dump(include={"status", "rationale", "conditions"})),
            control=ProgramCaseState(**final_case_map[CaseType.CONTROL].model_dump(include={"status", "rationale", "conditions"})),
            execution=ProgramCaseState(**final_case_map[CaseType.EXECUTION].model_dump(include={"status", "rationale", "conditions"})),
            investment=ProgramCaseState(**final_case_map[CaseType.INVESTMENT].model_dump(include={"status", "rationale", "conditions"})),
        )
        red_team_findings = tuple(
            finding.finding_id
            for finding in (session.red_team_report.findings if session.red_team_report else ())
            if finding.unresolved
        )
        challenge_findings = tuple(
            resolution.resolution_id
            for resolution in session.resolutions
            if resolution.outcome.value in {"ACCEPTED", "PARTIAL", "UNRESOLVED"}
            or resolution.unresolved_material_disagreement
        )
        open_findings = (*red_team_findings, *challenge_findings)
        open_conditions = tuple(
            condition for assessment in session.final_cases for condition in assessment.conditions
        )
        updated_program = program.model_copy(
            update={
                "stage": new_stage,
                "status": status_by_disposition[session.charter.requested_disposition],
                "route": session.charter.proposed_route or program.route,
                "state_version": program.state_version + 1,
                "current_versions": ProgramPointers(
                    portfolio_mandate=proposed.portfolio_mandate or session.charter.portfolio_mandate,
                    tpp=proposed.tpp or session.charter.tpp,
                    evidence_snapshot=session.evidence.snapshot if session.evidence else None,
                    rights_snapshot=proposed.rights or session.charter.rights,
                    budget=proposed.budget or session.charter.budget,
                    risk_register=proposed.risk_register or session.charter.risk_register,
                    standard_of_care=proposed.standard_of_care or session.charter.standard_of_care,
                    gate_policy=session.charter.gate_policy,
                    gate_policy_artifact=session.charter.gate_policy_artifact,
                ),
                "current_five_cases": five_cases,
                "falsifiers": session.gate_packet_inputs.falsifiers,
                "kill_criteria": session.gate_packet_inputs.kill_criteria,
                "open_conditions": open_conditions,
                "open_findings": open_findings,
                "active_tasks": session.gate_packet_inputs.proposed_task_ids,
                "hold_trigger": session.charter.hold_trigger
                if session.charter.requested_disposition is Disposition.HOLD
                else None,
                "hold_expiry": session.charter.hold_expiry
                if session.charter.requested_disposition is Disposition.HOLD
                else None,
                "redesign_count": program.redesign_count
                + (1 if session.charter.requested_disposition is Disposition.REDESIGN_ONCE else 0),
                "last_gate_decision_id": decision_id,
                "last_gate_packet_digest": packet.digest,
                "updated_at": utc_now(),
            }
        )
        updated_session = self._next_session(session, phase=SessionPhase.COMMITTED)
        decision = GateDecision(
            decision_id=decision_id,
            program_id=program.program_id,
            session_id=session_id,
            gate_packet_digest=packet.digest,
            disposition=session.charter.requested_disposition,
            previous_stage=program.stage,
            resulting_stage=new_stage,
            approval_ids=tuple(approvals_by_role[role].approval_id for role in request.required_roles),
            committed_program_revision=updated_program.state_version,
        )
        program_event = self._event(
            context,
            "PROGRAM",
            program.program_id,
            updated_program.state_version,
            "PROGRAM.GATE_DECISION_COMMITTED",
            {
                "decision_id": decision_id,
                "previous_stage": program.stage.value,
                "resulting_stage": new_stage.value,
                "disposition": session.charter.requested_disposition.value,
                "packet_digest": packet.digest,
            },
            ".program",
        )
        session_event = self._event(
            context,
            "COUNCIL_SESSION",
            session_id,
            updated_session.state_version,
            "COUNCIL.DECISION_COMMITTED",
            {"decision_id": decision_id, "program_version": updated_program.state_version},
            ".session",
        )
        return self.ledger.commit_program_and_session(
            updated_program,
            updated_session,
            program.state_version,
            expected_session,
            program_event,
            session_event,
            decision,
        )
