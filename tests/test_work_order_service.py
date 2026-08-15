from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timezone

from foundry_council.m5_models import (
    AttributionKind,
    QCStatus,
    WorkOrder,
    WorkOrderStatus,
)
from foundry_council.models import SnapshotRef
from foundry_council.work_order_service import MemoryWorkOrderStore, WorkOrderService


def _ref(oid: str) -> SnapshotRef:
    return SnapshotRef(
        object_id=oid, version=1, digest=f"sha256:{hashlib.sha256(oid.encode()).hexdigest()}"
    )


class TestWorkOrderService(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryWorkOrderStore()
        self.service = WorkOrderService(self.store)

    def _create(self, work_order_id: str = "wo0001") -> WorkOrder:
        return self.service.create_work_order(
            work_order_id=work_order_id,
            program_id="PRG",
            session_id="sess0001",
            gate_decision_id="gd0001",
            title="Decisive assay",
            prediction="The construct will show target engagement.",
            alternatives=("No effect.", "Off-target effect."),
            falsifier="No target engagement above baseline.",
            kill_criterion="No activity beyond baseline at any dose.",
            protocol_ref=_ref("proto1"),
            rights_ref=_ref("rights1"),
            budget_ref=_ref("budget1"),
            owner="owner-1",
            deadline=datetime(2026, 12, 31, tzinfo=timezone.utc),
            qc_standard_ref=_ref("qc1"),
        )

    def test_create_work_order_decisive_fields(self) -> None:
        wo = self._create()
        self.assertEqual(wo.status, WorkOrderStatus.DECISIVE_PENDING)
        self.assertEqual(wo.prediction_digest[:7], "sha256:")
        self.assertEqual(len(wo.alternatives), 2)
        # Duplicate rejected.
        with self.assertRaises(ValueError):
            self._create("wo0001")

    def test_ingest_result_and_attribution_preserves_outcome(self) -> None:
        self._create()
        result = self.service.ingest_result(
            result_id="res0001",
            work_order_id="wo0001",
            qc_status=QCStatus.PASS,
            payload={"value": 1.0},
            source_ref=_ref("src-res1"),
        )
        self.assertEqual(result.program_id, "PRG")
        attribution = self.service.attribute(
            attribution_id="attr0001", work_order_id="wo0001", result_id="res0001"
        )
        self.assertIn(attribution.kind, (AttributionKind.CONFIRMED, AttributionKind.REFUTED,
                                         AttributionKind.NULL, AttributionKind.UNKNOWN))
        # Outcome preserved: work order moves to terminal state.
        wo = self.store.get_work_order("wo0001")
        self.assertEqual(wo.status, WorkOrderStatus.COMPLETED)

    def test_failed_qc_is_preserved(self) -> None:
        self._create()
        self.service.ingest_result(
            result_id="res0002",
            work_order_id="wo0001",
            qc_status=QCStatus.CONTAMINATED,
            payload={"value": "garbage"},
            source_ref=_ref("src-res2"),
        )
        attribution = self.service.attribute(
            attribution_id="attr0002", work_order_id="wo0001", result_id="res0002"
        )
        self.assertEqual(attribution.kind, AttributionKind.FAILED_QC)
        wo = self.store.get_work_order("wo0001")
        self.assertEqual(wo.status, WorkOrderStatus.FAILED)

    def test_attribute_requires_matching_work_order(self) -> None:
        self._create()
        self.service.ingest_result(
            result_id="res0003",
            work_order_id="wo0001",
            qc_status=QCStatus.PASS,
            payload={"x": 1},
            source_ref=_ref("src-res3"),
        )
        with self.assertRaises(ValueError):
            self.service.attribute(
                attribution_id="attr0003", work_order_id="wo9999", result_id="res0003"
            )

    def test_learn_back_creates_successor_pointer(self) -> None:
        lb = self.service.create_learn_back(
            learn_back_id="lb0001",
            program_id="PRG",
            predecessor_session_id="sess0001",
            successor_evidence=_ref("evidence-v2"),
        )
        self.assertEqual(lb.preceding_session_id, "sess0001")
        self.assertEqual(lb.successor_evidence.object_id, "evidence-v2")


if __name__ == "__main__":
    unittest.main()
