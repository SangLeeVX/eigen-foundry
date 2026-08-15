"""M5 — Sentinel.

Ingests versioned evidence/source events and maps each to a Program **exactly
once** (idempotent on the event digest). It does NOT decide the scientific or
portfolio implication — the Crucible does. Handles material-change events but
never elevates its own role beyond ingestion/mapping.

Crash-safe: mapping is recorded atomically with the event status; replaying the
same event digest yields the same mapping (no duplicates).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from .m5_models import EventStatus, SentinelEvent, SentinelEventKind


def canonical_payload_digest(payload: dict[str, Any]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    )


def _snap_sha(payload_digest: str) -> str:
    """Derive a SnapshotRef-compatible sha256 digest from a payload digest prefix."""
    return "sha256:" + hashlib.sha256(payload_digest.encode()).hexdigest()


class SentinelStore(Protocol):
    """Minimal persistence surface the Sentinel needs (satisfied by the ledger)."""

    def save_event(self, event: SentinelEvent) -> SentinelEvent: ...
    def get_event(self, event_id: str) -> SentinelEvent | None: ...
    def list_events(self) -> tuple[SentinelEvent, ...]: ...


@dataclass
class Sentinel:
    """Versioned evidence-event listener with exactly-once program mapping."""

    store: SentinelStore
    _ingested: dict[str, SentinelEvent] = field(default_factory=dict)

    def ingest(self, source: dict[str, Any], *, program_id: str) -> SentinelEvent:
        """Ingest a mock/source event and map it to a Program exactly once.

        Replaying the same payload for the same program returns the SAME event
        (idempotent); it never creates a duplicate event or mapping.
        """
        from .models import SnapshotRef

        payload_digest = canonical_payload_digest(source)
        event_id = "evt-" + hashlib.sha256(
            json.dumps(
                {"program_id": program_id, "payload_digest": payload_digest},
                sort_keys=True,
            ).encode()
        ).hexdigest()[:24]

        existing = self.store.get_event(event_id)
        if existing is not None:
            return existing  # exactly-once replay

        event = SentinelEvent(
            event_id=event_id,
            program_id=program_id,
            kind=SentinelEventKind.EVIDENCE,
            status=EventStatus.INGESTED,
            payload_digest=payload_digest,
            source_ref=SnapshotRef(
                object_id=f"source-{event_id}",
                version=1,
                digest=_snap_sha(payload_digest),
            ),
        )
        return self.store.save_event(event)

    def map_to_program(self, event: SentinelEvent) -> SentinelEvent:
        """Map an ingested event to its Program (recorded atomically)."""
        if event.status is EventStatus.QUARANTINED:
            raise ValueError("cannot map a quarantined event")
        if event.status is EventStatus.MAPPED:
            return event
        from datetime import datetime, timezone

        mapped = event.model_copy(
            update={
                "status": EventStatus.MAPPED,
                "mapped_at": datetime.now(timezone.utc),
            }
        )
        saved = self.store.save_event(mapped)
        self._ingested[event.event_id] = saved
        return saved
