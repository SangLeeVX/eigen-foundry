from __future__ import annotations

import os
import unittest

_GSE = "/home/ubuntu/.openclaw/workspace/GSE162256/GSE162256_DE_summary.csv"
_EF_DB = "/home/ubuntu/.openclaw/workspace/snapshots/eigenfield_v58.0.0.duckdb"


def _real_data_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return os.path.exists(_GSE) and os.path.exists(_EF_DB)


@unittest.skipUnless(_real_data_available(), "real GSE162256 + EigenField not available (offline CI)")
class TestRealisticDryRun(unittest.TestCase):
    def test_real_data_dry_run_completes_f0_to_f12(self) -> None:
        from foundry_council.realistic_dry_run import RealisticDryRun

        r = RealisticDryRun(route="NOVEL_TARGET_DE_NOVO", seed=7).run()
        d = r.to_dict()
        self.assertTrue(d["real_data_provenance"])
        self.assertGreaterEqual(d["events_ingested"], 1)  # real GSE162256 rows
        self.assertTrue(d["sources"]["gse162256"]["ok"])
        self.assertTrue(d["sources"]["eigenfield"]["ok"])
        self.assertTrue(d["sequence_complete"])  # reaches F12
        self.assertTrue(d["transferable_package_digest"].startswith("sha256:"))
        self.assertTrue(d["harness_only"])
        self.assertFalse(d["real_therapeutic_advance"])

    def test_real_data_dry_run_rescue_route(self) -> None:
        from foundry_council.realistic_dry_run import RealisticDryRun

        r = RealisticDryRun(route="EXISTING_ASSET", seed=9).run()
        d = r.to_dict()
        self.assertTrue(d["sequence_complete"])
        self.assertEqual(d["route"], "EXISTING_ASSET")


if __name__ == "__main__":
    unittest.main()
