from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from foundry_council.live_seat_model import (
    LiveSeatConfig,
    LiveSeatError,
    LiveSeatModel,
    LiveSeatUnavailable,
    _extract_json_object,
    default_seat_model_factory,
    load_live_seat_config,
)
from foundry_council.seat_runtime import DeterministicSeatModel


def _template():
    return {
        "claim_id": "wc-claim-scientific",
        "statement": "live",
        "state": "SUPPORTED_INFERENCE",
        "materiality": "MATERIAL",
        "evidence_refs": ("wc-evidence-item",),
        "context": "ctx",
        "gate_impact": "g",
    }


def _env_file(tmp: str, **values) -> Path:
    path = Path(tmp) / "kimi.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    return path


class _FakeTransport:
    """In-memory HTTP layer double; records requests for assertions."""

    def __init__(self, status=200, body=None, *, side_effect=None):
        self.status = status
        self.body = body
        self.side_effect = side_effect
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append((url, payload, headers))
        if self.side_effect is not None:
            raise self.side_effect
        return self.status, self.body


class LiveSeatModelTests(unittest.TestCase):
    """M4-C1: live seat model binds Kimi K2.5 through the LiteralSeatModel protocol."""

    def _model(self, transport, config=None):
        return LiveSeatModel(
            config or LiveSeatConfig(key="test-key", base_url="https://api.moonshot.ai/v1", model="kimi-k2.5"),
            transport=transport,
        )

    def test_run_returns_raw_model_content(self) -> None:
        expected = {"claim_id": "c1", "state": "SUPPORTED_INFERENCE"}
        transport = _FakeTransport(body=json.dumps({"choices": [{"message": {"content": json.dumps(expected)}}]}).encode())
        model = self._model(transport)
        self.assertEqual(json.loads(model.run("prompt", {"ctx": 1})), expected)

    def test_run_sends_openai_compatible_payload_without_key_in_messages(self) -> None:
        transport = _FakeTransport(body=b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}')
        model = self._model(transport)
        model.run("some prompt", {"case": "x"})
        url, payload, headers = transport.calls[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(payload["model"], "kimi-k2.5")
        self.assertEqual(payload["temperature"], 0.0)
        # The API key must only appear in the Authorization header, never in
        # any message content that could be logged or echoed.
        blob = json.dumps(payload)
        self.assertNotIn("test-key", blob)
        self.assertEqual(headers["Authorization"], "Bearer test-key")

    def test_run_tolerates_markdown_fence_around_json(self) -> None:
        transport = _FakeTransport(body=b'{"choices":[{"message":{"content":"```json\\n{\\"ok\\":true}\\n```"}}]}')
        model = self._model(transport)
        self.assertIn("ok", model.run("p", {}))

    def test_run_fails_closed_on_http_error(self) -> None:
        transport = _FakeTransport(status=500, body=b"leak-suspect")
        model = self._model(transport)
        with self.assertRaises(LiveSeatError) as ctx:
            model.run("p", {})
        # Error text never contains the response body or the key.
        self.assertNotIn("leak-suspect", str(ctx.exception))
        self.assertNotIn("test-key", str(ctx.exception))

    def test_run_fails_closed_on_malformed_body(self) -> None:
        transport = _FakeTransport(body=b"not json")
        model = self._model(transport)
        with self.assertRaises(LiveSeatError):
            model.run("p", {})

    def test_run_fails_closed_on_missing_content(self) -> None:
        transport = _FakeTransport(body=b'{"choices":[]}')
        model = self._model(transport)
        with self.assertRaises(LiveSeatError):
            model.run("p", {})

    def test_run_fails_closed_on_non_structured_content(self) -> None:
        transport = _FakeTransport(body=b'{"choices":[{"message":{"content":"just prose"}}]}')
        model = self._model(transport)
        with self.assertRaises(LiveSeatError):
            model.run("p", {})

    def test_run_surfaces_transport_failure_as_bounded_error(self) -> None:
        transport = _FakeTransport(side_effect=TimeoutError("slow"))
        model = self._model(transport)
        with self.assertRaises(LiveSeatError):
            model.run("p", {})

    def test_seat_runtime_validates_live_output_structure(self) -> None:
        # A live model output that misses required fields must fail the seat
        # runtime's structured-output check (M4-C2 boundary holds for live seats).
        from foundry_council.models import ActorKind, CaseType, ParticipantAssignment
        from foundry_council.seat_runtime import MalformedSeatOutput, SeatRuntime, bind_seat

        transport = _FakeTransport(body=json.dumps({"choices": [{"message": {"content": '{"claim_id":"x"}'}}]}).encode())
        model = self._model(transport)
        assignment = ParticipantAssignment(
            assignment_id="seat-x",
            actor_id="captain-x",
            actor_kind=ActorKind.AGENT,
            role="case_captain",
            case=CaseType.SCIENTIFIC,
            run_id="run-x",
            model_version="model-x",
            prompt_version="prompt-x",
            independence_group="group-x",
        )
        runtime = SeatRuntime(bind_seat(assignment, seed=1), model)
        with self.assertRaises(MalformedSeatOutput):
            runtime.produce(
                "p", {}, expected_kind="claim",
                required_fields=("claim_id", "statement", "state", "materiality", "context", "gate_impact"),
                seed=1,
            )

    def test_extract_json_object(self) -> None:
        self.assertEqual(_extract_json_object('prefix {"a": 1} suffix'), {"a": 1})
        with self.assertRaises(LiveSeatError):
            _extract_json_object("no braces here")


class LiveSeatConfigTests(unittest.TestCase):
    """M4-C1: config resolution stays on the approved secrets store."""

    def test_loads_key_from_secrets_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _env_file(tmp, KIMI_API_KEY="sk-test-123", KIMI_BASE_URL="https://api.moonshot.ai/v1", KIMI_MODEL="kimi-k2.5")
            config = load_live_seat_config(path)
            self.assertEqual(config.key, "sk-test-123")
            self.assertEqual(config.model, "kimi-k2.5")

    def test_missing_key_raises_without_echoing_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _env_file(tmp, KIMI_MODEL="kimi-k2.5")
            with self.assertRaises(LiveSeatUnavailable) as ctx:
                load_live_seat_config(path)
            self.assertNotIn("sk-", str(ctx.exception))

    def test_default_base_url_and_required_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _env_file(tmp, KIMI_API_KEY="sk-x", KIMI_MODEL="kimi-k2.5")
            config = load_live_seat_config(path)
            self.assertEqual(config.base_url, "https://api.moonshot.ai/v1")
            path2 = _env_file(tmp + "2") if False else None
        with tempfile.TemporaryDirectory() as tmp:
            path = _env_file(tmp, KIMI_API_KEY="sk-x")
            with self.assertRaises(LiveSeatUnavailable):
                load_live_seat_config(path)


class SeatModelSelectionTests(unittest.TestCase):
    """M4-C1: FOUNDRY_SEAT_MODEL drives live vs deterministic selection."""

    def setUp(self) -> None:
        self._previous = os.environ.get("FOUNDRY_SEAT_MODEL")
        if "FOUNDRY_SEAT_MODEL" in os.environ:
            del os.environ["FOUNDRY_SEAT_MODEL"]

    def tearDown(self) -> None:
        if self._previous is None:
            os.environ.pop("FOUNDRY_SEAT_MODEL", None)
        else:
            os.environ["FOUNDRY_SEAT_MODEL"] = self._previous

    def _assignment(self):
        from foundry_council.models import ActorKind, CaseType, ParticipantAssignment
        return ParticipantAssignment(
            assignment_id="seat-x", actor_id="actor-01", actor_kind=ActorKind.AGENT,
            role="case_captain", case=CaseType.SCIENTIFIC, run_id="run-001",
            model_version="model-1", prompt_version="prompt-1", independence_group="group-a",
        )

    def test_default_is_deterministic(self) -> None:
        model = default_seat_model_factory(self._assignment(), _template())
        self.assertIsInstance(model, DeterministicSeatModel)

    def test_live_without_secret_fails_closed(self) -> None:
        os.environ["FOUNDRY_SEAT_MODEL"] = "live"
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FOUNDRY_KIMI_ENV"] = str(_env_file(tmp))  # empty file
            with self.assertRaises(LiveSeatUnavailable):
                default_seat_model_factory(self._assignment(), _template())
        os.environ.pop("FOUNDRY_KIMI_ENV", None)

    def test_live_with_secret_binds_live_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = _env_file(tmp, KIMI_API_KEY="sk-live-1", KIMI_MODEL="kimi-k2.5")
            os.environ["FOUNDRY_SEAT_MODEL"] = "live"
            os.environ["FOUNDRY_KIMI_ENV"] = str(env)
            model = default_seat_model_factory(self._assignment(), _template())
            self.assertIsInstance(model, LiveSeatModel)
            os.environ.pop("FOUNDRY_KIMI_ENV", None)

    def test_working_conclave_accepts_injected_factory(self) -> None:
        # WorkingConclave's model selection is injectable; the default stays
        # deterministic (hermetic CI) and an injected factory is honored to
        # build every captain seat.
        from foundry_council.working_conclave import WorkingConclave

        seen: list[str] = []

        def factory(assignment: ParticipantAssignment, template: dict):
            seen.append(assignment.case.value)
            return DeterministicSeatModel(template)

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "wc.db"
            trace = WorkingConclave(db, seed=7, seat_model_factory=factory).run()
            self.assertTrue(trace.audit_chains_valid)
            # Every case captain was built through the injected factory.
            self.assertGreaterEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
