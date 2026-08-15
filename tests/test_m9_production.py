from __future__ import annotations

import os
import tempfile
import unittest

from foundry_council.datasource_connector import DatasourceConnector
from foundry_council.ledger import SQLiteLedger
from foundry_council.production_ops import (
    BackupRestore,
    ConnectorHealth,
    Observability,
    ReleaseManifest,
    SoakHarness,
)
from foundry_council.sentinel import Sentinel


class _MemStore:
    def __init__(self) -> None:
        self.e: dict[str, object] = {}

    def save_event(self, event):
        self.e[event.event_id] = event
        return event

    def get_event(self, event_id):
        return self.e.get(event_id)

    def list_events(self):
        return tuple(self.e.values())


class TestReleaseAndRollback(unittest.TestCase):
    def test_manifest_and_rollback_evidence(self) -> None:
        m = ReleaseManifest(release_id="rel-1", git_sha="abc", version="1.2.0",
                            deployed_at="now", rollback_to="1.1.0")
        d = m.to_dict()
        self.assertEqual(d["release_id"], "rel-1")
        self.assertTrue(d["release_digest"].startswith("sha256:"))
        rb = m.rollback_evidence(reason="regression")
        self.assertEqual(rb["rollback_to"], "1.1.0")
        self.assertTrue(rb["evidence_digest"].startswith("sha256:"))


class TestBackupRestore(unittest.TestCase):
    def test_backup_and_restore(self) -> None:
        db = tempfile.mktemp(suffix=".db")
        bk = tempfile.mktemp(suffix=".bk")
        try:
            SQLiteLedger(db)  # create
            br = BackupRestore()
            b = br.backup(db, bk)
            self.assertTrue(os.path.exists(b["backup"]))
            # restore into a fresh path
            restored = tempfile.mktemp(suffix=".db")
            br.restore(bk, restored)
            self.assertTrue(os.path.exists(restored))
            SQLiteLedger(restored)  # opens cleanly
            os.remove(restored)
        finally:
            for p in (db, bk, f"{db}-wal", f"{db}-shm", f"{bk}-wal", f"{bk}-shm"):
                if os.path.exists(p):
                    os.remove(p)


_GSE = "/home/ubuntu/.openclaw/workspace/GSE162256/GSE162256_DE_summary.csv"
_EF_DB = "/home/ubuntu/.openclaw/workspace/snapshots/eigenfield_v58.0.0.duckdb"


def _real_data_available() -> bool:
    """True only when the real datasources + duckdb are present (not offline CI)."""
    try:
        import duckdb  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return os.path.exists(_GSE) and os.path.exists(_EF_DB)


class TestRealConnectors(unittest.TestCase):
    @unittest.skipUnless(_real_data_available(), "real GSE162256 + EigenField datasets not available")
    def test_gse162256_crc_evidence_ingestion(self) -> None:
        sent = Sentinel(_MemStore())
        conn = DatasourceConnector(sent, program_id="CRC-DRY", seed=7)
        r = conn.ingest_crc_evidence()
        # Real GSE162256 has 5 treatment rows.
        self.assertEqual(r.events_ingested, 5)
        # Re-ingest is exactly-once.
        r2 = conn.ingest_crc_evidence()
        self.assertEqual(r2.events_ingested - r.events_ingested, 0)

    @unittest.skipUnless(_real_data_available(), "real EigenField dataset not available")
    def test_eigenfield_grounding_real_data(self) -> None:
        sent = Sentinel(_MemStore())
        conn = DatasourceConnector(sent, program_id="CRC-DRY", seed=7)
        g = conn.ground_prediction(prediction_id="p1", prediction_digest="dd", gene="TP53")
        self.assertGreaterEqual(g["evidence_count"], 1)
        self.assertEqual(g["grounding_source"], "patient_expression")

    @unittest.skipUnless(_real_data_available(), "real GSE162256 + EigenField datasets not available")
    def test_connector_health(self) -> None:
        h = ConnectorHealth().check()
        self.assertTrue(h["healthy"])
        self.assertTrue(h["checks"]["gse162256"]["ok"])
        self.assertTrue(h["checks"]["eigenfield_patient_expression"]["ok"])


class TestObservability(unittest.TestCase):
    def test_snapshot_counts(self) -> None:
        ledger = SQLiteLedger(tempfile.mktemp(suffix=".db"))
        obs = Observability().snapshot(ledger)
        self.assertEqual(obs["program_count"], 0)
        self.assertIn("session_count", obs)
        self.assertIn("observed_at", obs)


class TestSoakHarness(unittest.TestCase):
    def test_soak_completes_lifecycle(self) -> None:
        base = tempfile.mktemp(suffix="-soak")
        try:
            s = SoakHarness(base, rounds=2).run()
            self.assertTrue(s["all_ok"])
            self.assertEqual(s["rounds"], 2)
            for rec in s["results"]:
                self.assertEqual(rec["final_stage"], "F12")
        finally:
            import glob

            for p in glob.glob(f"{base}*"):
                os.remove(p)


if __name__ == "__main__":
    unittest.main()
