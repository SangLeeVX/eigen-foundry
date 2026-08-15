from __future__ import annotations

import os
import tempfile
import unittest

from foundry_council.frozen_tpp import FrozenTPP, make_frozen_tpp
from foundry_council.models import ClaimState, ProgramStage, Route
from foundry_council.route_policy import (
    DE_NOVO_ROUTES,
    RESCUE_ROUTES,
    f6_stage_for,
    full_stage_sequence,
)
from foundry_council.stage_runner import StageRunner


class TestRoutePolicy(unittest.TestCase):
    def test_f6_stage_per_route_family(self) -> None:
        self.assertEqual(f6_stage_for(Route.EXISTING_ASSET), ProgramStage.F6A)
        self.assertEqual(f6_stage_for(Route.NOVEL_TARGET_DE_NOVO), ProgramStage.F6B)
        self.assertEqual(f6_stage_for(Route.COMBINATION), ProgramStage.F6C)
        self.assertIn(Route.EXISTING_ASSET, RESCUE_ROUTES)
        self.assertIn(Route.NOVEL_TARGET_DE_NOVO, DE_NOVO_ROUTES)

    def test_full_sequence_no_gate_skips(self) -> None:
        seq = full_stage_sequence(Route.KNOWN_TARGET_NEW_CANDIDATE)
        self.assertEqual(
            seq,
            (ProgramStage.F0, ProgramStage.F1, ProgramStage.F2, ProgramStage.F3,
             ProgramStage.F4, ProgramStage.F5, ProgramStage.F6B, ProgramStage.F7,
             ProgramStage.F8),
        )

    def test_frozen_tpp_content_addressed(self) -> None:
        t1 = make_frozen_tpp(seed=7)
        t2 = make_frozen_tpp(seed=7)
        t3 = make_frozen_tpp(seed=9)
        self.assertEqual(t1.digest, t2.digest)
        self.assertNotEqual(t1.digest, t3.digest)
        self.assertIsInstance(t1, FrozenTPP)


class TestStageRunner(unittest.TestCase):
    def _run(self, route: Route, seed: int = 7, **kwargs):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            return StageRunner(path, route=route, seed=seed, **kwargs).run()
        finally:
            for p in (path, f"{path}-wal", f"{path}-shm"):
                if os.path.exists(p):
                    os.remove(p)

    def test_de_novo_route_reaches_F8_no_gate_skip(self) -> None:
        r = self._run(Route.NOVEL_TARGET_DE_NOVO)
        self.assertTrue(r.sequence_complete)
        self.assertEqual([p.stage for p in r.stage_packets][-1], "F8")
        self.assertIn("F6B", [p.stage for p in r.stage_packets])
        self.assertIsNotNone(r.transferable_package_digest)

    def test_rescue_and_combination_routes(self) -> None:
        r_a = self._run(Route.EXISTING_ASSET)
        r_c = self._run(Route.COMBINATION)
        stages_a = [p.stage for p in r_a.stage_packets]
        stages_c = [p.stage for p in r_c.stage_packets]
        self.assertIn("F6A", stages_a)
        self.assertIn("F6C", stages_c)
        self.assertEqual(stages_a[-1], "F8")
        self.assertEqual(stages_c[-1], "F8")

    def test_model_prediction_blocks_advance(self) -> None:
        # M7 exit criterion: model output never satisfies a preclinical gate.
        def model_hook(stage):
            return [ClaimState.MODEL_PREDICTION]

        with self.assertRaises(RuntimeError):
            self._run(Route.NOVEL_TARGET_DE_NOVO, evidence_hook=model_hook)

    def test_stage_packets_recorded_per_stage(self) -> None:
        r = self._run(Route.NOVEL_TARGET_DE_NOVO, seed=11)
        self.assertEqual(len(r.stage_packets), 8)  # F1..F8
        for p in r.stage_packets:
            self.assertTrue(p.gate_passed)
            self.assertIn(p.tpp_digest[:7], "sha256:")


if __name__ == "__main__":
    unittest.main()
