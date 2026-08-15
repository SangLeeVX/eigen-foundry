"""M7 — governed single-stage advance (respects the single-commit-path rule).

Advances an EXISTING Program by exactly one formal stage (no gate skips) through
the ONLY formal-state mutation path in the kernel: a governed council session
driven to human approval and committed via `commit_gate_decision`.

This is what makes M7 dry runs genuinely *governed*: stage/status/route changes
never occur through `save_program` — they flow through the Crucible → approval
quorum → atomic commit path that M2-C4 enforces.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .approval_console import ApprovalConsole
from .identity import Authorizer, Principal, StaticIdentityProvider
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
    EvidenceManifest,
    FinalCaseAssessment,
    GatePacketInputs,
    Materiality,
    ParticipantAssignment,
    ProgramRecord,
    SnapshotRef,
)
from .policy import DEFAULT_GATE_POLICY, canonical_digest
from .service import CommandContext, CouncilService


def _sha(label: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"


def _snap(oid: str, version: int = 1) -> SnapshotRef:
    return SnapshotRef(object_id=oid, version=version, digest=_sha(oid))


def _cmd(actor: str, key: str, version: int | None = None, *, kind=ActorKind.AGENT):
    return CommandContext(
        actor_id=actor,
        actor_kind=kind,
        idempotency_key=key,
        expected_version=version,
        reason="Authorized M7 governed dry-run stage advance.",
        principal_roles=frozenset(),
    )


def _roster(cases: tuple[CaseType, ...]) -> tuple[ParticipantAssignment, ...]:
    from .crucible import _roster as _cru_roster

    return _cru_roster(cases)


class GovernedAdvance:
    """Constitutes a governing session on an existing Program and commits ONE stage advance."""

    def __init__(self, service: CouncilService, *, seed: int = 7) -> None:
        self.service = service
        self.seed = seed

    def advance(
        self,
        program: ProgramRecord,
        *,
        proposed_stage,
        route,
        decisive_claim_states: list[ClaimState],
        nonce: int = 0,
    ) -> tuple[ProgramRecord, CouncilSession]:
        """Open a session on the existing Program, drive to approval, and commit."""
        # Distinct idempotency per stage advance (nonce) avoids replay collisions.
        seed_key = f"{self.seed}-{nonce}"
        session_id = f"sess-{program.program_id}-adv-{seed_key}"
        charter = self._charter(program, proposed_stage, route)
        session = self.service.create_session(
            session_id=session_id,
            program_id=program.program_id,
            charter=charter,
            participants=_roster(tuple(CaseType)),
            context=_cmd("agent-commander", f"adv-session-{seed_key}"),
        )
        session = self._drive_to_approval(session, seed_key)
        # Approve every required functional role (dry-run human approvers).
        authorizer = self._authorizer_for(session)
        console = ApprovalConsole(self.service, authorizer, ledger=self.service.ledger)
        assert session.approval_request is not None
        for i, role in enumerate(session.approval_request.required_roles):
            r = console.approve(
                session_id=session.session_id,
                approver_actor=f"human-approver-{i}",
                role=role,
                raw_assertion=f"human-approver-{i}".encode(),
            )
            if not r.ok:
                raise RuntimeError(f"approval failed for {role}: {r.message}")
        # Commit the formal stage transition (the ONLY formal-state path).
        commit = console.commit(
            session_id=session.session_id,
            approver_actor="committer-svc",
            raw_assertion="committer-svc".encode(),
            decision_id=f"decision-{session.session_id}",
        )
        if not commit.ok:
            raise RuntimeError(f"commit failed: {commit.message}")
        new_program = self.service.ledger.get_program(program.program_id)
        return new_program, session

    def _charter(self, program: ProgramRecord, proposed_stage, route):
        from .models import ProgramPointers

        pointers = program.current_versions
        ref = SnapshotRef(
            object_id=program.program_id,
            version=program.state_version,
            digest=canonical_digest(program),
        )
        # Fill any missing pointer with a deterministic placeholder so the charter
        # is fully specified even when the dry-run program only set a few pointers.
        def _ptr(value, fallback_id: str):
            if value is not None:
                return value
            return _snap(fallback_id)

        return DecisionCharter(
            question=f"Advance this synthetic Program from {program.stage.value} to {proposed_stage.value}?",
            proposed_action=f"Governed dry-run advance to {proposed_stage.value}.",
            exact_scope="Authorized M7 governed dry run; no real spend/outreach.",
            requested_disposition=Disposition.ADVANCE,
            current_stage=program.stage,
            proposed_stage=proposed_stage,
            expected_program_state_version=program.state_version,
            program_snapshot=ref,
            portfolio_mandate=_ptr(pointers.portfolio_mandate, "pm-v0"),
            tpp=_ptr(pointers.tpp, "tpp-v0"),
            rights=_ptr(pointers.rights_snapshot, "rights-v0"),
            budget=_ptr(pointers.budget, "budget-v0"),
            risk_register=_ptr(pointers.risk_register, "risk-v0"),
            standard_of_care=_ptr(pointers.standard_of_care, "soc-v0"),
            gate_policy=pointers.gate_policy or DEFAULT_GATE_POLICY.snapshot_ref(),
            proposed_route=route if program.stage.value == "F5" else None,
            session_deadline=datetime.now(timezone.utc) + timedelta(hours=24),
        )

    def _drive_to_approval(self, session: CouncilSession, seed_key: str) -> CouncilSession:
        service = self.service
        evidence_item = _snap(f"ev-item-{session.session_id}")
        evidence = EvidenceManifest(
            snapshot=SnapshotRef(
                object_id=f"ev-{session.session_id}", version=1,
                digest=canonical_digest({"items": [evidence_item.model_dump(mode="json")]}),
            ),
            items=(evidence_item,),
        )
        session = service.freeze_evidence(session.session_id, evidence, _cmd("agent-evidence", f"ev-{seed_key}", session.state_version))
        session = service.start_blind_round(session.session_id, _cmd("agent-commander", f"blind-{seed_key}", session.state_version))
        exp_v = session.state_version
        for case in CaseType:
            suffix = case.value.lower()
            claim = Claim(
                claim_id=f"c-{session.session_id}-{suffix}", owner_agent_id=f"captain-{suffix}",
                statement=f"The {case.value} case satisfies the bounded dry-run input.",
                state=ClaimState.SUPPORTED_INFERENCE, materiality=Materiality.MATERIAL,
                evidence_refs=(evidence_item,), context="Synthetic dry-run evidence.",
                gate_impact=f"Supports {case.value} determination.", proposed_falsifier="A required input is absent.",
            )
            opinion = CaseOpinion(
                opinion_id=f"o-{session.session_id}-{suffix}", case=case,
                captain_agent_id=f"captain-{suffix}", status=CaseStatus.PASS,
                rationale="All required synthetic dry-run fields are present.", claim_ids=(claim.claim_id,),
            )
            rec = service.submit_blind_opinion(session.session_id, opinion, (claim,), _cmd(f"captain-{suffix}", f"op-{suffix}-{seed_key}", exp_v))
            exp_v = rec.state_version
        session = service.reveal_claims(session.session_id, _cmd("agent-commander", f"reveal-{seed_key}", exp_v))
        session = service.open_challenges(session.session_id, _cmd("agent-commander", f"ch-open-{seed_key}", session.state_version))
        session = service.close_challenges(session.session_id, _cmd("agent-commander", f"ch-close-{seed_key}", session.state_version))
        session = service.start_red_team(session.session_id, _cmd("agent-commander", f"red-{seed_key}", session.state_version))
        session = service.submit_red_team(session.session_id, _red_report(session.session_id), _cmd("agent-red-team", f"red-sub-{seed_key}", session.state_version))
        session = service.open_final_cases(session.session_id, _cmd("agent-commander", f"fin-open-{seed_key}", session.state_version))
        for case in CaseType:
            suffix = case.value.lower()
            session = service.submit_final_case(session.session_id,
                FinalCaseAssessment(assessment_id=f"f-{session.session_id}-{suffix}", case=case,
                    captain_agent_id=f"captain-{suffix}", status=CaseStatus.PASS,
                    rationale="Synthetic deterministic pass within dry-run scope.", claim_ids=(f"c-{session.session_id}-{suffix}",)),
                _cmd(f"captain-{suffix}", f"fin-{suffix}-{seed_key}", session.state_version))
        decisive = tuple(f"c-{session.session_id}-{c.value.lower()}" for c in CaseType)
        session = service.submit_gate_packet_inputs(session.session_id,
            GatePacketInputs(capital_tranche=None, decisive_claim_ids=decisive, null_claim_ids=(), unknown_claim_ids=(),
                product_and_standard_of_care_delta="None; dry run.", rights_ip_control_supply_delta="None; dry run.",
                results_vs_frozen_predictions="None; dry run.", risk_register_delta="None; dry run.",
                falsifiers=("A required input is absent.",), kill_criteria=("No activity beyond baseline.",),
                budget_time_capacity_and_catalyst="No spend; next bounded stage.", intended_disposition_rationale="Advance the dry-run Program."),
            _cmd("agent-commander", f"packet-{seed_key}", session.state_version))
        session = service.arbitrate(session.session_id, f"arb-{session.session_id}", f"pkt-{session.session_id}",
            _cmd("policy-engine", f"arb-{seed_key}", session.state_version, kind=ActorKind.SERVICE))
        session = service.request_approval(session.session_id, f"ar-{session.session_id}",
            datetime.now(timezone.utc) + timedelta(hours=12),
            _cmd("agent-commander", f"request-{seed_key}", session.state_version))
        return session

    def _authorizer_for(self, session: CouncilSession) -> Authorizer:
        assert session.approval_request is not None
        required = session.approval_request.required_roles
        scoped = frozenset({session.program_id})
        principals = {}
        for i, role in enumerate(required):
            pid = f"human-approver-{i}"
            principals[pid] = Principal(principal_id=pid, kind=ActorKind.HUMAN,
                roles=frozenset({role}), allows_origin=scoped, mfa_verified=True)
        principals["committer-svc"] = Principal(principal_id="committer-svc", kind=ActorKind.SERVICE,
            roles=frozenset({"ledger_committer"}), allows_origin=None)
        return Authorizer(StaticIdentityProvider(principals))


def _red_report(session_id: str):
    from .models import RedTeamReport

    return RedTeamReport(report_id=f"rpt-{session_id}", reviewer_agent_id="agent-red-team",
                         findings=(), conclusion="Synthetic red team: no material contradiction within scope.")
