from __future__ import annotations

import tempfile
import unittest

from foundry_council.synthetic_conclave import SyntheticConclave


class TestSyntheticConclave(unittest.TestCase):
    """M3: the Synthetic Conclave harness runs the deterministic F0 path."""

    def _run(self, seed: int = 7):
        import os

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            harness = SyntheticConclave(path, seed=seed)
            return harness.run()
        finally:
            if path.endswith(("-wal", "-shm")) is False and os.path.exists(path):
                for p in (path, f"{path}-wal", f"{path}-shm"):
                    if os.path.exists(p):
                        os.remove(p)

    def test_completes_all_deterministic_phases(self) -> None:
        trace = self._run(seed=7)
        phases = [p.phase for p in trace.phases]
        self.assertIn("freeze_evidence", phases)
        self.assertIn("blind_round_complete", phases)
        self.assertIn("challenge_round_complete", phases)
        self.assertIn("final_cases_complete", phases)
        self.assertIn("gate_packet_inputs", phases)

    def test_audit_chains_valid_and_harness_only(self) -> None:
        trace = self._run(seed=11)
        self.assertTrue(trace.audit_chains_valid)
        # The harness must NOT change formal Program state (reported as a harness).
        self.assertEqual(trace.finish_stage, "F0")
        self.assertEqual(trace.finish_status, "DRAFT")
        payload = trace.to_dict()
        self.assertTrue(payload["harness_only"])
        self.assertEqual(payload["program_id"], "EB-HARNESS-11")

    def test_deterministic_across_seeds_shares_shape(self) -> None:
        a = self._run(seed=3)
        b = self._run(seed=5)
        self.assertEqual([p.phase for p in a.phases], [p.phase for p in b.phases])
        self.assertEqual(len(a.phases), len(b.phases))


if __name__ == "__main__":
    unittest.main()
