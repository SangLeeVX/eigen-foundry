"""Tests for governed persona instruction sets (M4 enrichment).

Covers:
  - every standing role and every case captain has a resolvable, versioned persona
  - persona composition renders the role's office brief, forbidden actions,
    allowed capabilities, and frozen snapshot refs
  - unknown roles fail closed (no persona)
  - the live seat factory binds the persona system prompt per seat in live mode,
    while the deterministic path stays persona-free (hermetic CI)
"""

from __future__ import annotations

import unittest
from unittest import mock

from foundry_council.agents import (
    AgentContext,
    Capability,
    REQUIRED_STANDING_ROLES,
    ROLE_CONTRACTS,
)
from foundry_council.models import ActorKind, CaseType, ParticipantAssignment
from foundry_council.personas import (
    PAD_REVISION,
    PERSONA_REGISTRY,
    persona_for_role_and_case,
    render_persona,
)


def _ctx(role: str, *, capabilities: set[Capability] | None = None) -> AgentContext:
    caps = capabilities or {Capability.READ_PROGRAM, Capability.READ_FROZEN_EVIDENCE}
    return AgentContext(
        session_id="sess-1",
        program_id="prog-1",
        assignment=None,
        program_snapshot="prog:1",
        evidence_snapshot="ev:1",
        tpp="tpp:1",
        rights="r:1",
        budget="b:1",
        risk_register="rk:1",
        gate_policy="gp:1",
        allowed_capabilities=frozenset(caps),
    )


def _assignment(role: str, case: CaseType | None = None) -> ParticipantAssignment:
    return ParticipantAssignment(
        assignment_id=f"seat-{role}",
        actor_id=f"agent-{role}",
        actor_kind=ActorKind.AGENT,
        role=role,
        case=case,
        run_id=f"run-{role}",
        model_version=f"mod-{role}",
        prompt_version=f"prompt-{role}",
        independence_group=f"grp-{role}",
    )


class TestPersonaRegistry(unittest.TestCase):
    def test_all_standing_roles_have_personas(self):
        self.assertLessEqual(REQUIRED_STANDING_ROLES, set(PERSONA_REGISTRY))

    def test_all_five_case_captains_resolve(self):
        for case in CaseType:
            p = persona_for_role_and_case("case_captain", case.value)
            self.assertEqual(p.role, "case_captain")
            self.assertIn(case.value.lower(), p.prompt_version)

    def test_personas_are_versioned(self):
        for persona in PERSONA_REGISTRY.values():
            self.assertIn(PAD_REVISION, persona.prompt_version)
            # digest is stable and non-trivial
            self.assertEqual(len(persona.digest()), 64)
            self.assertEqual(persona.digest(), persona.digest())

    def test_case_captain_without_case_fails_closed(self):
        with self.assertRaises(KeyError):
            persona_for_role_and_case("case_captain", None)

    def test_unknown_role_fails_closed(self):
        with self.assertRaises(KeyError):
            persona_for_role_and_case("no_such_role", None)


class TestRenderPersona(unittest.TestCase):
    def test_embeds_role_forbidden_and_caps(self):
        persona = persona_for_role_and_case("foundry_commander", None)
        ctx = _ctx("foundry_commander")
        out = render_persona(persona, ctx, ROLE_CONTRACTS["foundry_commander"])
        self.assertIn(persona.persona_name, out)
        # forbidden actions from the role contract are stated
        self.assertIn("gate_commit", out)
        self.assertIn("READ_PROGRAM", out)
        # version + digest present
        self.assertIn(persona.prompt_version, out)

    def test_case_captain_embeds_case_office(self):
        persona = persona_for_role_and_case("case_captain", "PRODUCT")
        ctx = _ctx("case_captain", capabilities={Capability.SUBMIT_CLAIM})
        out = render_persona(persona, ctx, ROLE_CONTRACTS["case_captain"])
        self.assertIn("Product Case Captain", out)
        self.assertIn("MODEL_PREDICTION", out)  # state-inflation guard language

    def test_render_never_authorizes(self):
        # The governing persona must never claim approval/commit authority.
        for role in REQUIRED_STANDING_ROLES:
            persona = persona_for_role_and_case(role, None)
            ctx = _ctx(role)
            out = render_persona(persona, ctx, ROLE_CONTRACTS[role])
            self.assertTrue("do not approve" in out or "do not commit" in out)


class TestLiveFactoryBindsPersona(unittest.TestCase):
    def test_deterministic_path_is_persona_free(self):
        from foundry_council.live_seat_model import (
            DeterministicSeatModel,
            default_seat_model_factory,
        )

        assignment = _assignment("case_captain", CaseType.PRODUCT)
        model = default_seat_model_factory(assignment, {"k": "v"})
        self.assertIsInstance(model, DeterministicSeatModel)

    def test_live_path_binds_persona_system_prompt(self):
        import foundry_council.live_seat_model as lm

        captured: dict = {}

        def _fake_from_secrets_env(cls, **kwargs):
            captured.update(kwargs)
            return cls

        with mock.patch.dict("os.environ", {"FOUNDRY_SEAT_MODEL": "live"}), mock.patch.object(
            lm.LiveSeatModel, "from_secrets_env", classmethod(_fake_from_secrets_env)
        ):
            assignment = _assignment("case_captain", CaseType.INVESTMENT)
            lm.default_seat_model_factory(assignment, {"k": "v"})

        sp = captured.get("system_prompt", "")
        self.assertIn("Investment Case Captain", sp)
        self.assertIn("MODEL_PREDICTION", sp)


if __name__ == "__main__":
    unittest.main()
