from __future__ import annotations

import os
import tempfile
import unittest

from foundry_council.f0f2_policies import F0F2GatePolicy
from foundry_council.models import ClaimState, ProgramStage
from foundry_council.prad_crc_dryrun import PradCrcDryRun


class TestF0F2GatePolicy(unittest.TestCase):
    def test_model_prediction_never_satisfies_gate(self) -> None:
        policy = F0F2GatePolicy(admission_kind="CRC")
        verdict = policy.evaluate_decisive_claims(
            [ClaimState.MODEL_PREDICTION, ClaimState.MODEL_PREDICTION],
            stage=ProgramStage.F0,
        )
        self.assertFalse(verdict.passed)
        self.assertTrue(any("model prediction claims cannot satisfy" in b for b in verdict.blockers))

    def test_observed_claims_can_pass(self) -> None:
        policy = F0F2GatePolicy(admission_kind="CRC")
        verdict = policy.evaluate_decisive_claims(
            [ClaimState.OBSERVED, ClaimState.SUPPORTED_INFERENCE],
            stage=ProgramStage.F1,
        )
        self.assertTrue(verdict.passed)

    def test_unknown_and_contradicted_block(self) -> None:
        policy = F0F2GatePolicy(admission_kind="PRAD")
        verdict = policy.evaluate_decisive_claims(
            [ClaimState.UNKNOWN, ClaimState.CONTRADICTED], stage=ProgramStage.F2
        )
        self.assertFalse(verdict.passed)
        self.assertTrue(any("UNKNOWN" in b for b in verdict.blockers))

    def test_model_never_upgrades(self) -> None:
        policy = F0F2GatePolicy()
        # A MODEL_PREDICTION is never in the admissible determinative set.
        self.assertTrue(policy.model_prediction_never_satisfies(ClaimState.MODEL_PREDICTION))
        self.assertFalse(policy.model_prediction_never_satisfies(ClaimState.OBSERVED))


class TestPradCrcDryRun(unittest.TestCase):
    def _run(self, kind: str, seed: int = 7):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            return PradCrcDryRun(path, workspace_kind=kind, seed=seed).run()
        finally:
            for p in (path, f"{path}-wal", f"{path}-shm"):
                if os.path.exists(p):
                    os.remove(p)

    def test_crc_dry_run_traceable_packet(self) -> None:
        packet = self._run("CRC")
        self.assertEqual(packet.workspace_kind, "CRC")
        self.assertEqual(packet.program_id, "CRC-DRY-7")
        self.assertIn("harness_only", packet.to_dict())
        self.assertFalse(packet.to_dict()["real_therapeutic_advance"])
        # Model predictions never satisfy the gate.
        self.assertFalse(packet.gate_verdict["passed"])
        self.assertEqual(set(packet.model_prediction_labels.values()), {"MODEL_PREDICTION"})

    def test_prad_dry_run_traceable_packet(self) -> None:
        packet = self._run("PRAD", seed=9)
        self.assertEqual(packet.workspace_kind, "PRAD")
        self.assertEqual(packet.program_id, "PRAD-DRY-9")
        self.assertFalse(packet.gate_verdict["passed"])

    def test_invalid_workspace_kind_rejected(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with self.assertRaises(ValueError):
                PradCrcDryRun(path, workspace_kind="BOGUS")
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
