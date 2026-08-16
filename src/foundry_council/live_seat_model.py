"""M4-C1 — live seat model binding (Kimi K2.5 via api.moonshot.ai).

M4 requires "live seats" to use real model inference while preserving every
bounded-runtime invariant: distinct run identity, bounded tool envelope, and
structured-output validation. This module provides a ``LiteralSeatModel``
adapter that calls the Kimi K2.5 chat-completions API and returns the raw model
text. The *runtime* still performs structured-output validation (SeatRuntime /
working_conclave), so a live model can never bypass the schema contract.

Key properties:

  - The API key is loaded ONLY from the approved secrets store
    (``~/.openclaw/workspace/secrets/kimi.env`` by default, overridable via
    ``FOUNDRY_KIMI_ENV``) or from the ambient ``KIMI_*`` environment variables
    when the secrets file is absent. The key is never accepted from prompts,
    chat content, context dicts, or logs, and no exception message ever
    contains key material.
  - Model selection is environment-driven: ``FOUNDRY_SEAT_MODEL=live`` binds
    live seats; the default ``deterministic`` keeps CI hermetic (no network).
  - The HTTP layer is injectable so unit tests exercise the adapter without
    network access.

Live inference failures raise :class:`LiveSeatError` (or
:class:`LiveSeatUnavailable` when configuration is missing); the caller
(seat runtime / working conclave) surfaces them as bounded, visible failures.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .seat_runtime import DeterministicSeatModel, LiteralSeatModel
from .models import ParticipantAssignment

# Default approved secrets-store location (never committed to the repository).
DEFAULT_KIMI_ENV = Path.home() / ".openclaw" / "workspace" / "secrets" / "kimi.env"
DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"

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

    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0


def load_live_seat_config(env_file: str | Path | None = None) -> LiveSeatConfig:
    """Load live-model config from the approved secrets store.

    Resolution order: explicit ``env_file`` > ``FOUNDRY_KIMI_ENV`` >
    the default secrets path. When the file is absent, falls back to ambient
    ``KIMI_*`` environment variables (operator injection). Raises
    :class:`LiveSeatUnavailable` without echoing any key material when the
    key cannot be resolved.
    """
    requested = env_file or os.environ.get("FOUNDRY_KIMI_ENV") or DEFAULT_KIMI_ENV
    values = _parse_env_file(Path(requested)) if isinstance(requested, (str, Path)) else {}
    if not values:
        values = {key: os.environ.get(key, "") for key in ("KIMI_API_KEY", "KIMI_BASE_URL", "KIMI_MODEL")}

    api_key = (values.get("KIMI_API_KEY") or "").strip()
    if not api_key:
        raise LiveSeatUnavailable(
            "live seat model requested but KIMI_API_KEY is not present in the "
            "approved secrets store (FOUNDRY_SEAT_MODEL=live requires "
            "secrets/kimi.env or ambient KIMI_API_KEY)"
        )
    base_url = (values.get("KIMI_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    model = (values.get("KIMI_MODEL") or "").strip()
    if not model:
        raise LiveSeatUnavailable(
            "live seat model requested but KIMI_MODEL is not configured "
            "in the approved secrets store"
        )
    return LiveSeatConfig(api_key=api_key, base_url=base_url, model=model)


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
    ) -> None:
        self.config = config or load_live_seat_config()
        self._transport = transport or _default_transport
        self._endpoint = f"{self.config.base_url}/chat/completions"

    @classmethod
    def from_secrets_env(cls, env_file: str | Path | None = None) -> "LiveSeatModel":
        """Build a live model from the approved secrets store."""
        return cls(load_live_seat_config(env_file))

    def run(self, prompt: str, context: dict[str, Any]) -> str:
        """Send prompt+context to the live model and return its raw text."""
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a council seat in a governed drug-foundry "
                        "working conclave. Respond with ONLY a single JSON "
                        "object matching the requested schema. Never include "
                        "anything outside the JSON object."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"prompt": prompt, "context": context}, sort_keys=True
                    ),
                },
            ],
            "temperature": 0.0,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
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


def default_seat_model_factory(
    assignment: ParticipantAssignment,
    response_template: dict[str, Any],
) -> LiteralSeatModel:
    """Environment-driven seat model selection.

    ``FOUNDRY_SEAT_MODEL=live`` binds a :class:`LiveSeatModel` (requires the
    approved Kimi secrets store); anything else (default) returns the
    deterministic mock so CI stays hermetic and network-free.
    """
    mode = os.environ.get("FOUNDRY_SEAT_MODEL", "deterministic").strip().lower()
    if mode == "live":
        return LiveSeatModel.from_secrets_env()
    return DeterministicSeatModel(response_template)
