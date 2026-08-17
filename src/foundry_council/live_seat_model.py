"""M4-C1 — live seat model binding (Kimi K2.5 or DeepSeek, OpenAI-compatible).

M4 requires "live seats" to use real model inference while preserving every
bounded-runtime invariant: distinct run identity, bounded tool envelope, and
structured-output validation. This module provides a ``LiteralSeatModel``
adapter that calls an OpenAI-compatible chat-completions API (Kimi K2.5 via
api.moonshot.ai, or DeepSeek via api.deepseek.com) and returns the raw model
text. The *runtime* still performs structured-output validation (SeatRuntime /
working_conclave), so a live model can never bypass the schema contract.

Key properties:

  - The API key is loaded ONLY from the approved secrets store
    (``~/.openclaw/workspace/secrets/{kimi,deepseek}.env`` by default,
    overridable via ``FOUNDRY_*_ENV``) or from ambient ``KIMI_*`` / ``DEEPSEEK_*``
    environment variables when the secrets file is absent. The key is never
    accepted from prompts, chat content, context dicts, or logs, and no
    exception message ever contains key material.
  - Provider selection: ``FOUNDRY_SEAT_PROVIDER`` (kimi | deepseek); when unset,
    the first approved secrets file present wins (DeepSeek preferred for
    inference by operating policy). "live" mode requires an approved store.
  - Model selection is environment-driven: ``FOUNDRY_SEAT_MODEL=live`` binds
    live seats; the default ``deterministic`` keeps CI hermetic (no network).
  - The HTTP layer is injectable so unit tests exercise the adapter without
    network access.

Live inference failures raise :class:`LiveSeatError` (or
:class:`LiveSeatUnavailable` when configuration is missing); the caller
(seat runtime / working conclave) surfaces them as bounded, visible failures.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .seat_runtime import DeterministicSeatModel, LiteralSeatModel
from .models import ParticipantAssignment, SnapshotRef
from .agents import AgentContext, ROLE_CONTRACTS
from .personas import persona_for_role_and_case, render_persona

# Default approved secrets-store locations (never committed to the repository).
DEFAULT_KIMI_ENV = Path.home() / ".openclaw" / "workspace" / "secrets" / "kimi.env"
DEFAULT_DEEPSEEK_ENV = Path.home() / ".openclaw" / "workspace" / "secrets" / "deepseek.env"
DEFAULT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# DeepSeek's OpenAI-compatible endpoint is /chat/completions on this host.

Transport = Callable[[str, dict[str, Any], dict[str, str], float], tuple[int, bytes]]


class LiveSeatError(Exception):
    """A live model call failed (transport, HTTP, or malformed response)."""


class LiveSeatUnavailable(LiveSeatError):
    """Live mode was requested but the approved secret store is unavailable."""


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal KEY=VALUE secrets file (no external dependency)."""
    values: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


@dataclass(frozen=True)
class LiveSeatConfig:
    """Resolved live-model configuration (key material never printed)."""

    key: str
    base_url: str
    model: str
    timeout: float = 60.0


def _provider_from_env() -> str:
    """Resolve the live-seat provider (kimi | deepseek) from the environment.

    ``FOUNDRY_SEAT_PROVIDER`` wins when set. Otherwise pick the first approved
    secrets store that exists (DeepSeek preferred by operating policy: it is
    the configured default provider for inference). Raises nothing; returns
    "deepseek" or "kimi" accordingly.
    """
    explicit = os.environ.get("FOUNDRY_SEAT_PROVIDER", "").strip().lower()
    if explicit in ("deepseek", "kimi"):
        return explicit
    override = os.environ.get("FOUNDRY_SEAT_PROVIDER", "").strip().lower()
    if override:
        raise LiveSeatUnavailable(f"unknown FOUNDRY_SEAT_PROVIDER: {override}")
    # Auto-detect from what is actually available.
    if DEFAULT_DEEPSEEK_ENV.exists():
        return "deepseek"
    if DEFAULT_KIMI_ENV.exists():
        return "kimi"
    # Fall back to deepseek if ambient DEEPSEEK_API_KEY is present.
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.environ.get("KIMI_API_KEY"):
        return "kimi"
    raise LiveSeatUnavailable(
        "live seat model requested but no approved live-model secrets store is present "
        "(FOUNDRY_SEAT_MODEL=live requires secrets/kimi.env or secrets/deepseek.env)"
    )


PROVIDER_SPECS = {
    "kimi": {
        "env_file": DEFAULT_KIMI_ENV,
        "env_key": "KIMI_API_KEY",
        "env_base": "KIMI_BASE_URL",
        "env_model": "KIMI_MODEL",
        "override": "FOUNDRY_KIMI_ENV",
        "default_base": DEFAULT_KIMI_BASE_URL,
    },
    "deepseek": {
        "env_file": DEFAULT_DEEPSEEK_ENV,
        "env_key": "DEEPSEEK_API_KEY",
        "env_base": "DEEPSEEK_BASE_URL",
        "env_model": "DEEPSEEK_MODEL",
        "override": "FOUNDRY_DEEPSEEK_ENV",
        "default_base": DEFAULT_DEEPSEEK_BASE_URL,
    },
}


def load_live_seat_config(
    env_file: str | Path | None = None,
    provider: str | None = None,
) -> LiveSeatConfig:
    """Load live-model config from the approved secrets store.

    ``provider`` is "kimi" or "deepseek" (resolved from the environment when
    omitted; see :func:`_provider_from_env`). Resolution order per provider:
    explicit ``env_file`` > ``FOUNDRY_*_ENV`` override > the default secrets
    path. Falls back to ambient ``KIMI_*`` / ``DEEPSEEK_*`` environment
    variables when the file is absent. Raises :class:`LiveSeatUnavailable`
    without echoing any key material when the key cannot be resolved.
    """
    provider = provider or _provider_from_env()
    spec = PROVIDER_SPECS.get(provider)
    if spec is None:
        raise LiveSeatUnavailable(f"unknown live-seat provider: {provider}")
    requested = env_file or os.environ.get(spec["override"]) or spec["env_file"]
    values = _parse_env_file(Path(requested)) if isinstance(requested, (str, Path)) else {}
    if not values:
        values = {
            key: os.environ.get(key, "")
            for key in (spec["env_key"], spec["env_base"], spec["env_model"])
        }

    resolved_key = (values.get(spec["env_key"]) or "").strip()
    if not resolved_key:
        raise LiveSeatUnavailable(
            f"live seat model requested but {spec['env_key']} is not present "
            f"in the approved secrets store (FOUNDRY_SEAT_MODEL=live requires "
            f"{spec['env_file'].name} or ambient {spec['env_key']})"
        )
    base_url = (values.get(spec["env_base"]) or spec["default_base"]).strip().rstrip("/")
    model = (values.get(spec["env_model"]) or "").strip()
    if not model:
        # DeepSeek: default to the chat model when unset.
        base_model = "deepseek-chat" if provider == "deepseek" else ""
        model = base_model
    if not model:
        raise LiveSeatUnavailable(
            f"live seat model requested but {spec['env_model']} is not configured "
            "in the approved secrets store"
        )
    return LiveSeatConfig(key=resolved_key, base_url=base_url, model=model)


def _default_transport(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - pinned https URL
        return response.status, response.read()


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Extract the first balanced JSON object from a model response.

    Tolerates markdown code fences and prose around the object (common with
    chat models), while still failing closed when no object is present.
    """
    start = raw.find("{")
    if start < 0:
        raise LiveSeatError("live seat output contained no JSON object")
    decoder = json.JSONDecoder()
    try:
        content, _ = decoder.raw_decode(raw[start:])
    except ValueError as exc:
        raise LiveSeatError("live seat output contained malformed JSON") from exc
    if not isinstance(content, dict):
        raise LiveSeatError("live seat output JSON is not an object")
    return content


class LiveSeatModel:
    """A ``LiteralSeatModel`` backed by Kimi K2.5 (OpenAI-compatible API).

    ``run`` returns the raw model text; the seat runtime validates its
    structure against the seat contract. The API key is only ever read from
    the approved secrets store / ambient environment, never from prompts,
    context, or chat.
    """

    def __init__(
        self,
        config: LiveSeatConfig | None = None,
        *,
        transport: Transport | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.config = config or load_live_seat_config()
        self._transport = transport or _default_transport
        self._system_prompt = system_prompt
        self._endpoint = f"{self.config.base_url}/chat/completions"

    @classmethod
    def from_secrets_env(
        cls,
        provider: str | None = None,
        env_file: str | Path | None = None,
        *,
        system_prompt: str | None = None,
    ) -> "LiveSeatModel":
        """Build a live model from the approved secrets store."""
        return cls(
            load_live_seat_config(provider=provider, env_file=env_file),
            system_prompt=system_prompt,
        )

    def run(self, prompt: str, context: dict[str, Any]) -> str:
        """Send prompt+context to the live model and return its raw text."""
        schema_guide = self._system_prompt or (
            'Your entire reply must be EXACTLY ONE minified JSON object and nothing '
            'else (no markdown fences, no prose, no preamble). Use exactly these keys: '
            '{"claim_id": string, "statement": string, "state": string, '
            '"materiality": string, "context": string, "gate_impact": string}. '
            'The "state" value must be one of: OBSERVED, SUPPORTED_INFERENCE, '
            'MODEL_PREDICTION, CONTRADICTED, UNKNOWN. '
            'NEVER use EXPERIMENTALLY_VALIDATED or TRANSLATIONALLY_VALIDATED for '
            '"state": a council agent seat may not promote evidence to a validated state. '
            'The "materiality" value must be one of: NON_MATERIAL, MATERIAL, FATAL. '
            'Output the single JSON object only.'
        )
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": schema_guide,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"prompt": prompt, "context": context}, sort_keys=True
                    ),
                },
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.config.key}",
            "Content-Type": "application/json",
        }
        try:
            status, body = self._transport(self._endpoint, payload, headers, self.config.timeout)
        except LiveSeatError:
            raise
        except Exception as exc:  # noqa: BLE001 - bounded transport failure
            raise LiveSeatError(
                f"live seat transport failed: {type(exc).__name__}"
            ) from exc
        if status != 200:
            # Never include response body: it may echo the key or prompt.
            raise LiveSeatError(f"live seat API returned HTTP {status}")
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise LiveSeatError("live seat API returned non-JSON body") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LiveSeatError("live seat API response missing completion content") from exc
        if not isinstance(content, str) or not content.strip():
            raise LiveSeatError("live seat API returned empty completion")
        # Structure is still validated downstream by the seat runtime; here we
        # only fail fast on responses that cannot possibly be structured.
        _extract_json_object(content)
        return content


def _persona_for_assignment(
    assignment: ParticipantAssignment,
) -> "object":
    """Resolve the versioned persona for a seat's role + case."""
    return persona_for_role_and_case(
        assignment.role, assignment.case.value if assignment.case is not None else None
    )


def _agent_context_from_assignment(
    assignment: ParticipantAssignment,
) -> AgentContext:
    """Build a minimal immutable AgentContext from a seat assignment.

    Snapshot refs are derived deterministically from the seat's own identity so
    the rendered persona always references *some* frozen snapshot IDs without
    carrying mutable data into the model. The persona layer only consumes these
    IDs for governance framing; the real evidence the seat reads is supplied by
    the underlying prompt/context from the working conclave.
    """
    suffix = assignment.case.value if assignment.case is not None else assignment.role
    _seed = f"{assignment.assignment_id}:{assignment.run_id or suffix}"
    _digest = "sha256:" + hashlib.sha256(_seed.encode()).hexdigest()
    snap = lambda tag: SnapshotRef(
        object_id=f"{tag}:{assignment.assignment_id}",
        version=1,
        digest=_digest,
    )
    return AgentContext(
        session_id=assignment.run_id or assignment.assignment_id,
        program_id=assignment.assignment_id,
        assignment=assignment,
        program_snapshot=snap("program"),
        evidence_snapshot=snap("evidence"),
        tpp=snap("tpp"),
        rights=snap("rights"),
        budget=snap("budget"),
        risk_register=snap("risk"),
        gate_policy=snap("gate_policy"),
        allowed_capabilities=frozenset(),
    )


def default_seat_model_factory(
    assignment: ParticipantAssignment,
    response_template: dict[str, Any],
) -> LiteralSeatModel:
    """Environment-driven seat model selection with per-seat persona binding.

    ``FOUNDRY_SEAT_MODEL=live`` binds a :class:`LiveSeatModel` (requires the
    approved Kimi or DeepSeek secrets store; provider via
    ``FOUNDRY_SEAT_PROVIDER`` or auto-detected); anything else (default)
    returns the deterministic mock so CI stays hermetic and network-free.

    In live mode each seat is bound to its versioned persona (role + case):
    the rendered governing system prompt is injected at construction, so a
    live seat both reasons like its office and still emits validated JSON
    (structured-output validation remains downstream in the seat runtime).
    """
    mode = os.environ.get("FOUNDRY_SEAT_MODEL", "deterministic").strip().lower()
    if mode != "live":
        return DeterministicSeatModel(response_template)
    persona = _persona_for_assignment(assignment)
    agent_ctx = _agent_context_from_assignment(assignment)
    system_prompt = render_persona(
        persona, agent_ctx, ROLE_CONTRACTS[assignment.role]
    )
    return LiveSeatModel.from_secrets_env(system_prompt=system_prompt)
