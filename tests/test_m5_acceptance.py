from __future__ import annotations

import os
import tempfile
import unittest

from foundry_council.m5_acceptance import run_m5_acceptance


class TestM5AcceptanceRunner(unittest.TestCase):
    """M5 integrated 18-step closed-loop acceptance runner."""

    def _run(self, seed: int = 7):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            return run_m5_acceptance(path, seed=seed)
        finally:
            for p in (path, f"{path}-wal", f"{path}-shm"):
                if os.path.exists(p):
                    os.remove(p)

    def test_all_18_steps_ok(self) -> None:
        result = self._run(seed=7)
        self.assertTrue(result.all_steps_ok())
        self.assertEqual(set(result.steps.keys()), set(range(1, 19)))

    def test_loop_advances_program_stage(self) -> None:
        result = self._run(seed=11)
        self.assertEqual(result.final_stage, "F1")
        self.assertIsNotNone(result.work_order_id)
        self.assertIsNotNone(result.attribution_kind)
        self.assertIsNotNone(result.learn_back_id)

    def test_replay_clean_and_crash_recovery(self) -> None:
        result = self._run(seed=5)
        self.assertTrue(result.replay_clean)
        self.assertIn("READY_TO_COMMIT", result.crash_recovery_kind or "")
        self.assertIn("SESSION_COMMITTED", result.crash_recovery_kind or "")

    def test_operator_overview_present(self) -> None:
        result = self._run(seed=13)
        self.assertGreaterEqual(result.operator_overview_programs, 1)


if __name__ == "__main__":
    unittest.main()
