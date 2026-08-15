"""M5 — Working Foundry MVP domain models.

Domain objects for the closed Foundry loop that do not exist in the F0 council
kernel: Sentinel events (evidence ingestion), approved decisive work orders,
incoming results with QC disposition, failure attribution, and learn-back
successor snapshots.

These are immutable, content-addressed records (FrozenModel) — they carry only
pointers and canonical metadata; raw artifacts remain external. They never
change formal Program state; the governed commit path still does that.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field

from .models import FrozenModel, SnapshotRef, StableId, utc_now


def _canonical_digest(data: dict[str, Any]) -> str:
    import json

    return f"sha256:{hashlib.sha256(json.dumps(data, sort_keys=True, separators=(': ', ','), default=str).encode()).hexdigest()}"


class SentinelEventKind(StrEnum):
    EVIDENCE = "EVIDENCE"
    MATERIAL_CHANGE = "MATERIAL_CHANGE"
    REQUEST = "REQUEST"


class EventStatus(StrEnum):
    QUARANTINED = "QUARANTINED"
    INGESTED = "INGESTED"
    MAPPED = "MAPPED"
    PROCESSED = "PROCESSED"


class SentinelEvent(FrozenModel):
    """A versioned mock evidence/source event ingested by the Sentinel.

    The Sentinel ingests the event and maps it to a Program exactly once, but it
    does NOT decide the scientific or portfolio implication — that is the
    Crucible's job.
    """

    # Canonical content address (for replay/idempotency).
    schema_version: Literal["1.0.0"] = "1.0.0"
    event_id: StableId
    program_id: StableId
    kind: SentinelEventKind
    status: EventStatus = EventStatus.QUARANTINED
    payload_digest: str  # sha256 over the canonical source payload
    source_ref: SnapshotRef
    observed_at: AwareDatetime = Field(default_factory=utc_now)
    mapped_at: AwareDatetime | None = None

    @property
    def digest(self) -> str:
        return _canonical_digest(
            {
                "event_id": self.event_id,
                "program_id": self.program_id,
                "kind": self.kind.value,
                "payload_digest": self.payload_digest,
                "source_ref": self.source_ref.model_dump(mode="json"),
            }
        )


class WorkOrderStatus(StrEnum):
    DECISIVE_PENDING = "DECISIVE_PENDING"  # awaiting approval
    APPROVED = "APPROVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class WorkOrder(FrozenModel):
    """An approved decisive work order with prediction, alternatives, falsifier,
    kill criterion, protocol, rights, budget, owner, deadline, and QC standard."""

    work_order_id: StableId
    program_id: StableId
    session_id: StableId
    gate_decision_id: StableId
    title: Annotated[str, Field(min_length=3, max_length=300)]
    prediction: Annotated[str, Field(min_length=3, max_length=4000)]
    prediction_digest: str
    alternatives: tuple[str, ...] = ()
    falsifier: Annotated[str, Field(min_length=3, max_length=2000)]
    kill_criterion: Annotated[str, Field(min_length=3, max_length=2000)]
    protocol_ref: SnapshotRef
    rights_ref: SnapshotRef
    budget_ref: SnapshotRef
    owner: StableId
    deadline: AwareDatetime
    qc_standard_ref: SnapshotRef
    status: WorkOrderStatus = WorkOrderStatus.DECISIVE_PENDING
    created_at: AwareDatetime = Field(default_factory=utc_now)


class QCStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    CONTAMINATED = "CONTAMINATED"
    AMBIGUOUS = "AMBIGUOUS"


class ResultRecord(FrozenModel):
    """An ingested result with its QC disposition (preserved regardless of outcome)."""

    result_id: StableId
    work_order_id: StableId
    program_id: StableId
    qc_status: QCStatus
    payload_digest: str
    source_ref: SnapshotRef
    ingested_at: AwareDatetime = Field(default_factory=utc_now)


class AttributionKind(StrEnum):
    CONFIRMED = "CONFIRMED"  # result matches prediction
    REFUTED = "REFUTED"  # result contradicts prediction
    NULL = "NULL"  # measured null
    UNKNOWN = "UNKNOWN"
    FAILED_QC = "FAILED_QC"


class FailureAttribution(FrozenModel):
    """Compare the result against the frozen work-order prediction and attribute
    the outcome (preserving positive, negative, null, contradictory, failed-QC)."""

    attribution_id: StableId
    work_order_id: StableId
    result_id: StableId
    kind: AttributionKind
    rationale: Annotated[str, Field(min_length=3, max_length=4000)]
    attributed_at: AwareDatetime = Field(default_factory=utc_now)


class LearnBack(FrozenModel):
    """Successor evidence snapshot + successor Crucible pointer for learn-back."""

    learn_back_id: StableId
    program_id: StableId
    successor_evidence: SnapshotRef
    successor_session_id: StableId | None = None
    preceding_session_id: StableId
    created_at: AwareDatetime = Field(default_factory=utc_now)
