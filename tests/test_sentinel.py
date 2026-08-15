from __future__ import annotations

import unittest

from foundry_council.m5_models import (
    AttributionKind,
    EventStatus,
    FailureAttribution,
    QCStatus,
    ResultRecord,
    SentinelEvent,
    SentinelEventKind,
    WorkOrder,
    WorkOrderStatus,
)
from foundry_council.sentinel import Sentinel, canonical_payload_digest
from foundry_council.models import SnapshotRef


class _MemoryStore:
    def __init__(self) -> None:
        self._events: dict[str, SentinelEvent] = {}

    def save_event(self, event: SentinelEvent) -> SentinelEvent:
        self._events[event.event_id] = event
        return event

    def get_event(self, event_id: str) -> SentinelEvent | None:
        return self._events.get(event_id)

    def list_events(self) -> tuple[SentinelEvent, ...]:
        return tuple(self._events.values())


def _ref(oid: str) -> SnapshotRef:
    import hashlib

    return SnapshotRef(
        object_id=oid, version=1, digest=f"sha256:{hashlib.sha256(oid.encode()).hexdigest()}"
    )


class TestSentinelModels(unittest.TestCase):
    def test_event_digest_is_stable(self) -> None:
        e1 = SentinelEvent(
            event_id="evt1",
            program_id="PRG",
            kind=SentinelEventKind.EVIDENCE,
            payload_digest="d1",
            source_ref=_ref("src1"),
        )
        e2 = SentinelEvent(
            event_id="evt1",
            program_id="PRG",
            kind=SentinelEventKind.EVIDENCE,
            payload_digest="d1",
            source_ref=_ref("src1"),
        )
        self.assertEqual(e1.digest, e2.digest)

    def test_work_order_requires_all_decisive_fields(self) -> None:
        wo = WorkOrder(
            work_order_id="wo1",
            program_id="PRG",
            session_id="sess1",
            gate_decision_id="gd1",
            title="Decisive assay",
            prediction="The measurement will confirm the hypothesis.",
            prediction_digest="d",
            falsifier="A contrary measurement refutes.",
            kill_criterion="No activity beyond baseline.",
            protocol_ref=_ref("proto1"),
            rights_ref=_ref("rights1"),
            budget_ref=_ref("budget1"),
            owner="owner-1",
            deadline="2026-12-31T00:00:00Z",
            qc_standard_ref=_ref("qc1"),
        )
        self.assertEqual(wo.status, WorkOrderStatus.DECISIVE_PENDING)

    def test_result_and_attribution(self) -> None:
        res = ResultRecord(
            result_id="res1",
            work_order_id="wo1",
            program_id="PRG",
            qc_status=QCStatus.PASS,
            payload_digest="d",
            source_ref=_ref("src-res1"),
        )
        attr = FailureAttribution(
            attribution_id="attr1",
            work_order_id="wo1",
            result_id="res1",
            kind=AttributionKind.REFUTED,
            rationale="Result contradicts the frozen prediction.",
        )
        self.assertEqual(res.qc_status, QCStatus.PASS)
        self.assertEqual(attr.kind, AttributionKind.REFUTED)


class TestSentinel(unittest.TestCase):
    def setUp(self) -> None:
        self.store = _MemoryStore()
        self.sentinel = Sentinel(self.store)

    def test_ingest_maps_exactly_once(self) -> None:
        source = {"assay": "ELISA", "value": "cutoff"}
        first = self.sentinel.ingest(source, program_id="PRG")
        second = self.sentinel.ingest(source, program_id="PRG")
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(len(self.store.list_events()), 1)  # exactly once

    def test_ingest_status_and_mapping(self) -> None:
        source = {"note": "synthetic evidence"}
        event = self.sentinel.ingest(source, program_id="PRG")
        self.assertEqual(event.status, EventStatus.INGESTED)
        mapped = self.sentinel.map_to_program(event)
        self.assertEqual(mapped.status, EventStatus.MAPPED)
        self.assertIsNotNone(mapped.mapped_at)

    def test_payload_digest_deterministic(self) -> None:
        d1 = canonical_payload_digest({"a": 1, "b": "x"})
        d2 = canonical_payload_digest({"b": "x", "a": 1})
        self.assertEqual(d1, d2)


if __name__ == "__main__":
    unittest.main()
