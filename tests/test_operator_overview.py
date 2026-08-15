from __future__ import annotations

import tempfile
import unittest

from foundry_council.ledger import SQLiteLedger
from foundry_council.operator_overview import OperatorOverview
from foundry_council.service import CouncilService
from foundry_council.work_order_service import MemoryWorkOrderStore, WorkOrderService
from tests.helpers import create_program_and_session


class TestOperatorOverview(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        self.ledger = SQLiteLedger(self.db)
        self.service = CouncilService(self.ledger)

    def tearDown(self) -> None:
        import os

        for p in (self.db, f"{self.db}-wal", f"{self.db}-shm"):
            if os.path.exists(p):
                os.remove(p)

    def test_overview_shows_program_state_and_next_actions(self) -> None:
        program, session = create_program_and_session(self.service)
        wo_store = MemoryWorkOrderStore()
        overview = OperatorOverview(
            self.ledger, work_order_store=wo_store, pending_session_ids=(session.session_id,)
        ).overview()
        self.assertEqual(overview["program_count"], 1)
        prog = overview["programs"][0]
        self.assertEqual(prog["program_id"], program.program_id)
        self.assertEqual(prog["stage"], "F0")
        # Sessions surface with approvals (empty here).
        self.assertEqual(len(prog["sessions"]), 1)
        # Since no approval request is outstanding at this phase, next action is
        # the generic one.
        self.assertTrue(prog["next_actions"])

    def test_overview_includes_work_orders_and_results(self) -> None:
        import hashlib

        from foundry_council.models import SnapshotRef

        def _ref(oid: str) -> SnapshotRef:
            return SnapshotRef(
                object_id=oid,
                version=1,
                digest=f"sha256:{hashlib.sha256(oid.encode()).hexdigest()}",
            )

        program, _ = create_program_and_session(self.service)
        store = MemoryWorkOrderStore()
        wos = WorkOrderService(store)

        wos.create_work_order(
            work_order_id="wo0001",
            program_id=program.program_id,
            session_id="sess0001",
            gate_decision_id="gd0001",
            title="Decisive assay",
            prediction="Target engagement observed.",
            alternatives=(),
            falsifier="No engagement.",
            kill_criterion="No activity.",
            protocol_ref=_ref("proto1"),
            rights_ref=_ref("rights1"),
            budget_ref=_ref("budget1"),
            owner="owner-1",
            deadline="2026-12-31T00:00:00Z",
            qc_standard_ref=_ref("qcstd1"),
        )
        overview = OperatorOverview(self.ledger, work_order_store=store).overview()
        prog = overview["programs"][0]
        self.assertEqual(len(prog["work_orders"]), 1)
        self.assertEqual(prog["work_orders"][0]["work_order_id"], "wo0001")


if __name__ == "__main__":
    unittest.main()
