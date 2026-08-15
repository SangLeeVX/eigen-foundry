from __future__ import annotations

import os
import tempfile
import unittest

from foundry_council.working_conclave import WorkingConclave


class _TmpDB:
    def __enter__(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        return self.path

    def __exit__(self, *exc):
        for p in (self.path, f"{self.path}-wal", f"{self.path}-shm"):
            if os.path.exists(p):
                os.remove(p)


class TestWorkingConclave(unittest.TestCase):
    """M4: Working Conclave ties seat-executed outputs to the governed F0 flow."""

    def _run(self, seed: int = 7):
        with _TmpDB() as db:
            return WorkingConclave(db, seed=seed).run()

    def test_completes_full_f0_path(self) -> None:
        trace = self._run(seed=7)
        self.assertIn("freeze_evidence", trace.phases)
        self.assertIn("blind_round_complete", trace.phases)
        self.assertIn("challenge_round_complete", trace.phases)
        self.assertIn("final_cases_complete", trace.phases)

    def test_seat_outputs_have_distinct_run_identity(self) -> None:
        trace = self._run(seed=7)
        self.assertEqual(len(trace.seat_outputs), 5)  # 5 case captains
        run_ids = {o["run_id"] for o in trace.seat_outputs}
        # Each seat runs under its own versioned run identity.
        self.assertEqual(len(run_ids), 5)
        for out in trace.seat_outputs:
            self.assertIn(out["model_version"], {"model-scientific", "model-product", "model-control", "model-execution", "model-investment"})

    def test_audit_chains_valid_and_harness_only(self) -> None:
        trace = self._run(seed=11)
        self.assertTrue(trace.audit_chains_valid)
        self.assertTrue(trace.to_dict()["harness_only"])
        self.assertEqual(trace.to_dict()["program_id"], "WC-HARNESS-11")

    def test_deterministic_across_seeds_shares_shape(self) -> None:
        a = self._run(seed=3)
        b = self._run(seed=5)
        self.assertEqual(a.phases, b.phases)
        self.assertEqual(len(a.seat_outputs), len(b.seat_outputs))


if __name__ == "__main__":
    unittest.main()
