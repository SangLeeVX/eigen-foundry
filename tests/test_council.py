from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from foundry_council.agents import validate_council_roster
from foundry_council.errors import (
    ApprovalRequired,
    Forbidden,
    IdempotencyKeyReused,
    StateConflict,
    ValidationFailure,
)
from foundry_council.ledger import SQLiteLedger
from foundry_council.models import (
    ActorKind,
    Approval,
    ApprovalDecision,
    CaseOpinion,
    CaseStatus,
    CaseType,
    Claim,
    ClaimState,
    Disposition,
    EntryPoint,
    FinalCaseAssessment,
    Materiality,
    ProgramStage,
    RedTeamFinding,
    Route,
    SessionPhase,
)
from foundry_council.policy import DEFAULT_GATE_POLICY, GatePolicy, build_gate_packet, evaluate_session
from foundry_council.service import CouncilService

from .helpers import (
    approve_all,
    command,
    create_program_and_session,
    roster,
    run_to_approval,
    run_to_final_cases,
    snapshot,
    start_blind,
)


class CouncilRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "foundry.sqlite3"
        self.ledger = SQLiteLedger(self.db_path)
        self.service = CouncilService(self.ledger)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_complete_f0_commit_and_idempotent_replay(self) -> None:
        _, session = create_program_and_session(self.service)
        session = approve_all(self.service, run_to_approval(self.service, session))
        commit_context = command(
            "ledger-committer",
            session.state_version,
            "cmd-session-f0-alpha-commit",
            kind=ActorKind.SERVICE,
            roles=frozenset({"ledger_committer"}),
        )
        program, committed = self.service.commit_gate_decision(
            session.session_id, "decision-f0-alpha", commit_context
        )
        self.assertEqual(program.stage, ProgramStage.F1)
        self.assertEqual(committed.phase, SessionPhase.COMMITTED)
        decision = self.ledger.get_gate_decision("decision-f0-alpha")
        self.assertEqual(decision.gate_packet_digest, committed.gate_packet.digest)
        self.assertEqual(len(decision.approval_ids), 4)
        original = self.ledger.get_program_version(program.program_id, 1)
        self.assertEqual(original.stage, ProgramStage.F0)
        self.assertTrue(self.ledger.verify_audit_chain("PROGRAM", program.program_id))
        self.assertTrue(self.ledger.verify_audit_chain("COUNCIL_SESSION", session.session_id))

        replayed_program, replayed_session = self.service.commit_gate_decision(
            session.session_id, "decision-f0-alpha", commit_context
        )
        self.assertEqual(replayed_program.state_version, program.state_version)
        self.assertEqual(replayed_session.state_version, committed.state_version)
        program_events = self.ledger.list_events("PROGRAM", program.program_id)
        self.assertEqual(
            len([e for e in program_events if e.action == "PROGRAM.GATE_DECISION_COMMITTED"]),
            1,
        )

    def test_hard_fail_blocks_advance(self) -> None:
        _, session = create_program_and_session(self.service)
        session = run_to_final_cases(self.service, session, failing_case=CaseType.CONTROL)
        session = self.service.arbitrate(
            session.session_id,
            "arbitration-fail",
            "packet-fail",
            command(
                "policy-engine",
                session.state_version,
                "cmd-fail-arbitrate",
                kind=ActorKind.SERVICE,
            ),
        )
        self.assertFalse(session.arbitration.eligible)
        self.assertEqual(session.phase, SessionPhase.RETURNED)
        self.assertIn("CONTROL has a hard FAIL.", session.arbitration.blockers)
        with self.assertRaises(ValidationFailure):
            self.service.request_approval(
                session.session_id,
                "request-fail",
                session.charter.session_deadline,
                command("agent-commander", session.state_version, "cmd-fail-approval"),
            )

    def test_agent_cannot_approve(self) -> None:
        _, session = create_program_and_session(self.service)
        session = run_to_approval(self.service, session)
        request = session.approval_request
        assert request is not None
        approval = Approval(
            approval_id="approval-agent-forbidden",
            request_id=request.request_id,
            session_id=session.session_id,
            program_id=session.program_id,
            approver_id="captain-scientific",
            approver_kind=ActorKind.AGENT,
            role=request.required_roles[0],
            decision=ApprovalDecision.APPROVED,
            gate_packet_digest=request.gate_packet_digest,
            rationale="An agent improperly attempts to approve its own work.",
        )
        with self.assertRaises(Forbidden):
            self.service.record_approval(
                session.session_id,
                approval,
                command(
                    "captain-scientific",
                    session.state_version,
                    "cmd-agent-approval",
                    roles=frozenset({request.required_roles[0]}),
                ),
            )

    def test_stage_skip_is_rejected_before_session_creation(self) -> None:
        program, _ = create_program_and_session(self.service)
        original = self.ledger.get_session("session-f0-alpha").charter
        skipped = original.model_copy(update={"proposed_stage": ProgramStage.F2})
        with self.assertRaises(ValidationFailure):
            self.service.create_session(
                "session-stage-skip",
                program.program_id,
                skipped,
                roster(),
                command("agent-commander", None, "cmd-stage-skip"),
            )

    def test_stale_program_blocks_second_atomic_commit(self) -> None:
        _, first = create_program_and_session(self.service)
        program = self.ledger.get_program(first.program_id)
        charter = first.charter
        second = self.service.create_session(
            "session-f0-beta",
            program.program_id,
            charter,
            roster(),
            command("agent-commander", None, "cmd-session-f0-beta-create"),
        )
        first = approve_all(self.service, run_to_approval(self.service, first))
        second = approve_all(self.service, run_to_approval(self.service, second))
        self.service.commit_gate_decision(
            first.session_id,
            "decision-first",
            command(
                "ledger-committer",
                first.state_version,
                "cmd-first-commit",
                kind=ActorKind.SERVICE,
                roles=frozenset({"ledger_committer"}),
            ),
        )
        with self.assertRaises(StateConflict):
            self.service.commit_gate_decision(
                second.session_id,
                "decision-second",
                command(
                    "ledger-committer",
                    second.state_version,
                    "cmd-second-commit",
                    kind=ActorKind.SERVICE,
                    roles=frozenset({"ledger_committer"}),
                ),
            )

    def test_audit_and_approval_rows_are_database_immutable(self) -> None:
        _, session = create_program_and_session(self.service)
        session = run_to_approval(self.service, session)
        request = session.approval_request
        assert request is not None
        role = request.required_roles[0]
        approval = Approval(
            approval_id="approval-immutable",
            request_id=request.request_id,
            session_id=session.session_id,
            program_id=session.program_id,
            approver_id="human-immutable",
            approver_kind=ActorKind.HUMAN,
            role=role,
            decision=ApprovalDecision.APPROVED,
            gate_packet_digest=request.gate_packet_digest,
            rationale="Record an immutable approval for the database trigger test.",
        )
        session = self.service.record_approval(
            session.session_id,
            approval,
            command(
                "human-immutable",
                session.state_version,
                "cmd-immutable-approval",
                kind=ActorKind.HUMAN,
                roles=frozenset({role}),
            ),
        )
        connection = sqlite3.connect(self.db_path)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE approvals SET payload_json = '{}' WHERE approval_id = 'approval-immutable'"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM audit_events")
        connection.close()

    def test_conditional_status_requires_bounded_controls(self) -> None:
        with self.assertRaises(ValidationError):
            from foundry_council.models import FinalCaseAssessment

            FinalCaseAssessment(
                assessment_id="assessment-conditional",
                case=CaseType.SCIENTIFIC,
                captain_agent_id="captain-scientific",
                status=CaseStatus.CONDITIONAL,
                rationale="A condition without controls must be rejected.",
                claim_ids=("claim-conditional",),
            )

    def test_off_manifest_and_self_promoted_validated_claims_are_rejected(self) -> None:
        _, session = create_program_and_session(self.service)
        session, evidence_item = start_blind(self.service, session)
        rogue = snapshot("rogue-evidence")
        rogue_claim = Claim(
            claim_id="claim-rogue-evidence",
            owner_agent_id="captain-scientific",
            statement="This claim cites evidence added after the freeze.",
            state=ClaimState.SUPPORTED_INFERENCE,
            materiality=Materiality.MATERIAL,
            evidence_refs=(rogue,),
            context="Synthetic security test.",
            gate_impact="Would improperly influence the F0 decision.",
        )
        rogue_opinion = CaseOpinion(
            opinion_id="opinion-rogue-evidence",
            case=CaseType.SCIENTIFIC,
            captain_agent_id="captain-scientific",
            status=CaseStatus.PASS,
            rationale="This submission must be rejected.",
            claim_ids=(rogue_claim.claim_id,),
        )
        with self.assertRaises(ValidationFailure):
            self.service.submit_blind_opinion(
                session.session_id,
                rogue_opinion,
                (rogue_claim,),
                command("captain-scientific", session.state_version, "cmd-rogue-evidence"),
            )

        promoted = rogue_claim.model_copy(
            update={
                "claim_id": "claim-self-promoted",
                "state": ClaimState.EXPERIMENTALLY_VALIDATED,
                "evidence_refs": (evidence_item,),
            }
        )
        promoted_opinion = rogue_opinion.model_copy(
            update={"opinion_id": "opinion-self-promoted", "claim_ids": (promoted.claim_id,)}
        )
        with self.assertRaises(Forbidden):
            self.service.submit_blind_opinion(
                session.session_id,
                promoted_opinion,
                (promoted,),
                command("captain-scientific", session.state_version, "cmd-self-promoted"),
            )

    def test_blind_session_view_hides_peer_opinions(self) -> None:
        _, session = create_program_and_session(self.service)
        session, evidence_item = start_blind(self.service, session)
        claim = Claim(
            claim_id="claim-private-scientific",
            owner_agent_id="captain-scientific",
            statement="A blind opinion must remain private until reveal.",
            state=ClaimState.SUPPORTED_INFERENCE,
            materiality=Materiality.MATERIAL,
            evidence_refs=(evidence_item,),
            context="Synthetic blindness test.",
            gate_impact="Tests only access control.",
        )
        opinion = CaseOpinion(
            opinion_id="opinion-private-scientific",
            case=CaseType.SCIENTIFIC,
            captain_agent_id="captain-scientific",
            status=CaseStatus.PASS,
            rationale="Valid synthetic opinion.",
            claim_ids=(claim.claim_id,),
        )
        receipt = self.service.submit_blind_opinion(
            session.session_id,
            opinion,
            (claim,),
            command("captain-scientific", session.state_version, "cmd-private-opinion"),
        )
        peer_view = self.service.get_session_view(
            session.session_id,
            command("captain-product", None, "cmd-peer-view"),
        )
        owner_view = self.service.get_session_view(
            session.session_id,
            command("captain-scientific", None, "cmd-owner-view"),
        )
        self.assertEqual(peer_view.opinions, ())
        self.assertEqual(peer_view.claims, ())
        self.assertEqual(len(owner_view.opinions), 1)
        self.assertEqual(owner_view.state_version, receipt.state_version)

    def test_unknown_and_measured_null_have_distinct_packet_bindings(self) -> None:
        _, session = create_program_and_session(self.service)
        session = run_to_final_cases(
            self.service,
            session,
            claim_state_by_case={
                CaseType.SCIENTIFIC: ClaimState.UNKNOWN,
                CaseType.PRODUCT: ClaimState.OBSERVED,
            },
            measured_null_cases=frozenset({CaseType.PRODUCT}),
        )
        inputs = session.gate_packet_inputs
        assert inputs is not None
        self.assertEqual(inputs.unknown_claim_ids, ("claim-session-f0-alpha-scientific",))
        self.assertEqual(inputs.null_claim_ids, ("claim-session-f0-alpha-product",))
        with self.assertRaises(ValidationError):
            Claim(
                claim_id="claim-invalid-unknown-null",
                owner_agent_id="captain-scientific",
                statement="Missing evidence cannot also be a measured null result.",
                state=ClaimState.UNKNOWN,
                measured_null=True,
                materiality=Materiality.MATERIAL,
                context="Synthetic evidence-state regression test.",
                gate_impact="Prevents missing evidence from being reported as a result.",
            )

    def test_f0_program_creation_rejects_formal_route_selection(self) -> None:
        program, _ = create_program_and_session(self.service)
        with self.assertRaises(ValidationFailure):
            self.service.create_program_draft(
                program_id="EB-TEST-ROUTE",
                title="Invalid preselected route",
                entry_point=EntryPoint.ASSET_FIRST,
                route=Route.EXISTING_ASSET,
                owner="owner-program",
                pointers=program.current_versions,
                context=command(
                    "agent-drafter",
                    None,
                    "cmd-invalid-f0-route",
                    roles=frozenset({"program_drafter"}),
                ),
            )

    def test_policy_binding_migration_is_human_governed_and_versioned(self) -> None:
        program, _ = create_program_and_session(self.service)
        replacement = GatePolicy(policy_id="policy-foundry-council-v0.2", version=2)
        self.service.gate_policy = replacement
        with self.assertRaises(Forbidden):
            self.service.migrate_program_policy_binding(
                program.program_id,
                command(
                    "agent-policy-admin",
                    program.state_version,
                    "cmd-agent-policy-migration",
                    roles=frozenset({"policy_admin"}),
                ),
            )

        migration_context = command(
            "human-policy-admin",
            program.state_version,
            "cmd-human-policy-migration",
            kind=ActorKind.HUMAN,
            roles=frozenset({"policy_admin"}),
        )
        migrated = self.service.migrate_program_policy_binding(program.program_id, migration_context)
        self.assertEqual(migrated.state_version, program.state_version + 1)
        self.assertEqual(migrated.current_versions.gate_policy, replacement.snapshot_ref())
        original = self.ledger.get_program_version(program.program_id, program.state_version)
        self.assertEqual(original.current_versions.gate_policy, DEFAULT_GATE_POLICY.snapshot_ref())
        self.assertEqual(
            self.ledger.list_events("PROGRAM", program.program_id)[-1].action,
            "PROGRAM.GATE_POLICY_BINDING_MIGRATED",
        )
        replayed = self.service.migrate_program_policy_binding(program.program_id, migration_context)
        self.assertEqual(replayed.state_version, migrated.state_version)

    def test_unresolved_red_team_finding_and_attributed_dissent_block_advance(self) -> None:
        _, session = create_program_and_session(self.service)
        finding = RedTeamFinding(
            finding_id="finding-fatal-unresolved",
            category="RIGHTS_CONTROL",
            statement="Control is unresolved in this synthetic policy test.",
            materiality=Materiality.FATAL,
            unresolved=True,
        )
        session = run_to_final_cases(self.service, session, red_findings=(finding,))
        with self.assertRaises(Forbidden):
            self.service.submit_dissent(
                session.session_id,
                "dissent-forged",
                "A nonparticipant cannot inject dissent into a council record.",
                Materiality.MATERIAL,
                command(
                    "agent-outsider",
                    session.state_version,
                    "cmd-forged-dissent",
                ),
            )
        session = self.service.submit_dissent(
            session.session_id,
            "dissent-material",
            "A material objection remains unresolved.",
            Materiality.MATERIAL,
            command(
                "agent-reviewer",
                session.state_version,
                "cmd-attributed-dissent",
            ),
        )
        dissent = session.dissent[0]
        self.assertEqual(dissent.agent_id, "agent-reviewer")
        self.assertEqual(dissent.assignment_id, "seat-reviewer")
        self.assertEqual(dissent.role, "independent_reviewer")
        prior = self.ledger.get_session_version(session.session_id, session.state_version - 1)
        self.assertEqual(prior.dissent, ())
        session = self.service.arbitrate(
            session.session_id,
            "arbitration-red-block",
            "packet-red-block",
            command(
                "policy-engine",
                session.state_version,
                "cmd-red-block-arbitrate",
                kind=ActorKind.SERVICE,
            ),
        )
        self.assertFalse(session.arbitration.eligible)
        self.assertIn(
            "The red team has an unresolved material or fatal finding.",
            session.arbitration.blockers,
        )
        self.assertIn("Material dissent remains unresolved.", session.arbitration.blockers)
        self.assertEqual(session.arbitration.dissent, session.dissent)

    def test_fake_not_applicable_rule_is_rejected_by_policy(self) -> None:
        _, session = create_program_and_session(self.service)
        session = run_to_final_cases(self.service, session)
        fake = FinalCaseAssessment(
            assessment_id="final-fake-na",
            case=CaseType.SCIENTIFIC,
            captain_agent_id="captain-scientific",
            status=CaseStatus.NOT_APPLICABLE,
            rationale="An invented rule cannot bypass the scientific case.",
            not_applicable_rule_id="RULE.INVENTED",
        )
        modified = session.model_copy(
            update={
                "final_cases": tuple(
                    fake if assessment.case is CaseType.SCIENTIFIC else assessment
                    for assessment in session.final_cases
                )
            }
        )
        result = evaluate_session(modified, "arbitration-fake-na", "policy-engine")
        self.assertFalse(result.eligible)
        self.assertIn("SCIENTIFIC cites an unrecognized NOT_APPLICABLE rule.", result.blockers)

    def test_packet_digest_binds_charter_and_deliberation(self) -> None:
        _, session = create_program_and_session(self.service)
        session = run_to_final_cases(self.service, session)
        session = self.service.arbitrate(
            session.session_id,
            "arbitration-packet-binding",
            "packet-binding",
            command(
                "policy-engine",
                session.state_version,
                "cmd-packet-binding",
                kind=ActorKind.SERVICE,
            ),
        )
        original = session.gate_packet
        assert original is not None
        changed_charter = session.charter.model_copy(update={"exact_scope": "A materially different approved scope."})
        changed = session.model_copy(update={"charter": changed_charter})
        rebuilt = build_gate_packet(changed, original.packet_id)
        self.assertNotEqual(original.digest, rebuilt.digest)

    def test_expired_approval_request_is_rejected(self) -> None:
        _, session = create_program_and_session(self.service)
        session = run_to_final_cases(self.service, session)
        session = self.service.arbitrate(
            session.session_id,
            "arbitration-expiry",
            "packet-expiry",
            command(
                "policy-engine",
                session.state_version,
                "cmd-expiry-arbitrate",
                kind=ActorKind.SERVICE,
            ),
        )
        with self.assertRaises(ValidationFailure):
            self.service.request_approval(
                session.session_id,
                "request-expired",
                datetime.now(timezone.utc) - timedelta(seconds=1),
                command("agent-commander", session.state_version, "cmd-expired-request"),
            )

    def test_idempotency_key_reuse_with_changed_body_is_rejected(self) -> None:
        _, _ = create_program_and_session(self.service)
        with self.assertRaises(IdempotencyKeyReused):
            self.service.create_program_draft(
                program_id="EB-TEST-001",
                title="A materially different Program body",
                entry_point=EntryPoint.ASSET_FIRST,
                route=Route.EXISTING_ASSET,
                owner="different-owner",
                pointers=self.ledger.get_program("EB-TEST-001").current_versions,
                context=command(
                    "agent-drafter",
                    None,
                    "cmd-session-f0-alpha-program",
                    roles=frozenset({"program_drafter"}),
                ),
            )

    def test_roster_rejects_actor_reuse_across_seats(self) -> None:
        participants = list(roster())
        red_index = next(index for index, item in enumerate(participants) if item.role == "independent_red_team")
        participants[red_index] = participants[red_index].model_copy(update={"actor_id": "captain-scientific"})
        with self.assertRaises(ValidationFailure):
            validate_council_roster(tuple(participants))

    def test_terminal_program_cannot_reenter_normal_council(self) -> None:
        program, original_session = create_program_and_session(self.service)
        terminal_charter = original_session.charter.model_copy(
            update={
                "requested_disposition": Disposition.TERMINATE,
                "proposed_stage": ProgramStage.F0,
                "proposed_action": "Terminate this synthetic Program after review.",
            }
        )
        terminal = self.service.create_session(
            "session-terminal",
            program.program_id,
            terminal_charter,
            roster(),
            command("agent-commander", None, "cmd-session-terminal-create"),
        )
        terminal = approve_all(self.service, run_to_approval(self.service, terminal))
        self.service.commit_gate_decision(
            terminal.session_id,
            "decision-terminal",
            command(
                "ledger-committer",
                terminal.state_version,
                "cmd-terminal-commit",
                kind=ActorKind.SERVICE,
                roles=frozenset({"ledger_committer"}),
            ),
        )
        with self.assertRaises(Forbidden):
            self.service.create_session(
                "session-illegal-reopen",
                program.program_id,
                original_session.charter,
                roster(),
                command("agent-commander", None, "cmd-illegal-reopen"),
            )

    def test_nonexistent_audit_chain_is_not_reported_valid(self) -> None:
        self.assertFalse(self.ledger.verify_audit_chain("PROGRAM", "EB-MISSING-001"))

    def test_past_hold_expiry_is_rejected_at_session_creation(self) -> None:
        program, original = create_program_and_session(self.service)
        hold_charter = original.charter.model_copy(
            update={
                "requested_disposition": Disposition.HOLD,
                "proposed_stage": ProgramStage.F0,
                "proposed_action": "Place the synthetic Program on a bounded hold.",
                "hold_trigger": "A named external evidence event occurs.",
                "hold_expiry": datetime.now(timezone.utc) - timedelta(days=1),
            }
        )
        with self.assertRaises(ValidationFailure):
            self.service.create_session(
                "session-expired-hold",
                program.program_id,
                hold_charter,
                roster(),
                command("agent-commander", None, "cmd-expired-hold"),
            )

    def test_policy_change_invalidates_approved_commit(self) -> None:
        _, session = create_program_and_session(self.service)
        session = approve_all(self.service, run_to_approval(self.service, session))
        self.service.gate_policy = GatePolicy(policy_id="policy-foundry-council-v0.2", version=2)
        with self.assertRaises(ApprovalRequired):
            self.service.commit_gate_decision(
                session.session_id,
                "decision-stale-policy",
                command(
                    "ledger-committer",
                    session.state_version,
                    "cmd-stale-policy-commit",
                    kind=ActorKind.SERVICE,
                    roles=frozenset({"ledger_committer"}),
                ),
            )


if __name__ == "__main__":
    unittest.main()
