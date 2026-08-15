from __future__ import annotations

import unittest

from foundry_council.models import ActorKind, CaseType, ParticipantAssignment
from foundry_council.seat_runtime import (
    DeterministicSeatModel,
    MalformedSeatOutput,
    ROLE_ENVELOPES,
    SeatRuntime,
    TOOL_READ_EVIDENCE,
    TOOL_PROPOSE_CLAIM,
    ToolOutsideEnvelope,
    bind_seat,
)


def _captain(case: CaseType, actor: str = "captain") -> ParticipantAssignment:
    suffix = case.value.lower()
    return ParticipantAssignment(
        assignment_id=f"seat-{suffix}",
        actor_id=f"{actor}-{suffix}",
        actor_kind=ActorKind.AGENT,
        role="case_captain",
        case=case,
        run_id=f"run-{suffix}",
        model_version=f"model-{suffix}",
        prompt_version=f"prompt-{suffix}",
        independence_group=f"group-{suffix}",
    )


class TestBindSeat(unittest.TestCase):
    def test_envelope_per_role(self) -> None:
        captain = _captain(CaseType.SCIENTIFIC)
        seat = bind_seat(captain, seed=0)
        self.assertIn(TOOL_PROPOSE_CLAIM, seat.envelope)
        self.assertIn(TOOL_READ_EVIDENCE, seat.envelope)

    def test_unknown_role_gets_no_tools(self) -> None:
        unknown = ParticipantAssignment(
            assignment_id="seat-x",
            actor_id="actor-x",
            actor_kind=ActorKind.AGENT,
            role="some_unknown_role",
            run_id=None,
            model_version=None,
            prompt_version=None,
            independence_group="group-x",
        )
        seat = bind_seat(unknown, seed=0)
        self.assertEqual(seat.envelope, frozenset())

    def test_run_digest_is_distinct_per_run_identity(self) -> None:
        a = bind_seat(_captain(CaseType.SCIENTIFIC), seed=1)
        b = bind_seat(_captain(CaseType.PRODUCT), seed=1)
        self.assertNotEqual(a.run_digest, b.run_digest)
        # Same assignment, same seed -> same digest (deterministic attribution).
        c = bind_seat(_captain(CaseType.SCIENTIFIC), seed=1)
        self.assertEqual(a.run_digest, c.run_digest)


class TestToolEnvelope(unittest.TestCase):
    def test_inside_envelope_allowed(self) -> None:
        captain = bind_seat(_captain(CaseType.SCIENTIFIC), seed=0)
        runtime = SeatRuntime(captain, DeterministicSeatModel({}))
        runtime.call_tool(TOOL_PROPOSE_CLAIM)  # should not raise

    def test_outside_envelope_fails_closed(self) -> None:
        # A reviewer/red-team seat should NOT be able to propose a case.
        reviewer = ParticipantAssignment(
            assignment_id="seat-reviewer",
            actor_id="agent-reviewer",
            actor_kind=ActorKind.AGENT,
            role="independent_reviewer",
            run_id="run-reviewer",
            model_version="model-reviewer",
            prompt_version="prompt-reviewer",
            independence_group="group-reviewer",
        )
        seat = bind_seat(reviewer, seed=0)
        runtime = SeatRuntime(seat, DeterministicSeatModel({}))
        with self.assertRaises(ToolOutsideEnvelope):
            runtime.call_tool("propose_case")


class TestStructuredOutput(unittest.TestCase):
    def test_valid_output_accepted(self) -> None:
        captain = bind_seat(_captain(CaseType.SCIENTIFIC), seed=0)
        model = DeterministicSeatModel(
            {"claim_id": "c1", "statement": "A synthetic deterministic claim."}
        )
        runtime = SeatRuntime(captain, model)
        out = runtime.produce(
            "produce a claim",
            {},
            expected_kind="claim",
            required_fields=("claim_id", "statement"),
            seed=0,
        )
        self.assertEqual(out.kind, "claim")
        self.assertEqual(out.run_id, "run-scientific")
        self.assertEqual(out.model_version, "model-scientific")

    def test_malformed_json_fails_closed(self) -> None:
        class BadModel:
            def run(self, prompt, context):  # noqa: ANN001
                return "not json{{{"

        captain = bind_seat(_captain(CaseType.CONTROL), seed=0)
        runtime = SeatRuntime(captain, BadModel())
        with self.assertRaises(MalformedSeatOutput):
            runtime.produce(
                "produce a claim", {}, expected_kind="claim", required_fields=("claim_id",)
            )

    def test_missing_fields_fails_closed(self) -> None:
        captain = bind_seat(_captain(CaseType.EXECUTION), seed=0)
        model = DeterministicSeatModel({"surprise": "no claim_id here"})
        runtime = SeatRuntime(captain, model)
        with self.assertRaises(MalformedSeatOutput):
            runtime.produce(
                "produce a claim", {}, expected_kind="claim", required_fields=("claim_id",)
            )


class TestCommandNoEscalation(unittest.TestCase):
    def test_command_asserts_only_own_role(self) -> None:
        captain = bind_seat(_captain(CaseType.INVESTMENT), seed=0)
        runtime = SeatRuntime(captain, DeterministicSeatModel({}))
        ctx = runtime.command(expected_version=5)
        self.assertEqual(ctx.actor_id, "captain-investment")
        self.assertEqual(ctx.actor_kind, ActorKind.AGENT)
        self.assertEqual(ctx.principal_roles, frozenset({"case_captain"}))
        self.assertEqual(ctx.expected_version, 5)
        # It must never assert a privileged role.
        self.assertNotIn("foundry_commander", ctx.principal_roles)
        self.assertNotIn("policy_admin", ctx.principal_roles)


if __name__ == "__main__":
    unittest.main()
