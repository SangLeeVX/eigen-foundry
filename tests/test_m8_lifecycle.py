from __future__ import annotations

import os
import tempfile
import unittest

from foundry_council.models import ClaimState, Disposition, ProgramStage, Route, SnapshotRef
from foundry_council.route_policy import full_lifecycle_sequence
from foundry_council.stage_runner import StageRunner
from foundry_council.termination_workflow import F12Outcome, TerminationWorkflow


def _ref(oid: str) -> SnapshotRef:
    import hashlib

    return SnapshotRef(
        object_id=oid, version=1, digest=f"sha256:{hashlib.sha256(oid.encode()).hexdigest()}"
    )


class TestLifecycleSequence(unittest.TestCase):
    def test_lifecycle_sequence_includes_f9_f12(self) -> None:
        seq = full_lifecycle_sequence(Route.NOVEL_TARGET_DE_NOVO)
        self.assertIn(ProgramStage.F9, seq)
        self.assertIn(ProgramStage.F10, seq)
        self.assertIn(ProgramStage.F11, seq)
        self.assertIn(ProgramStage.F12, seq)
        self.assertEqual(seq[-1], ProgramStage.F12)


class TestStageRunnerLifecycle(unittest.TestCase):
    def _run(self, route: Route, **kwargs):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            return StageRunner(path, route=route, seed=7, lifecycle=True, **kwargs).run()
        finally:
            for p in (path, f"{path}-wal", f"{path}-shm"):
                if os.path.exists(p):
                    os.remove(p)

    def test_de_novo_lifecycle_reaches_f12(self) -> None:
        r = self._run(Route.NOVEL_TARGET_DE_NOVO)
        self.assertTrue(r.sequence_complete)
        stages = [p.stage for p in r.stage_packets]
        self.assertEqual(stages[-1], "F12")
        for expected in ("F9", "F10", "F11", "F12"):
            self.assertIn(expected, stages)

    def test_rescue_lifecycle_and_combination(self) -> None:
        r = self._run(Route.EXISTING_ASSET)
        self.assertTrue(r.sequence_complete)
        self.assertIn("F6A", [p.stage for p in r.stage_packets])
        rc = self._run(Route.COMBINATION)
        self.assertIn("F6C", [p.stage for p in rc.stage_packets])

    def test_model_prediction_blocks_at_clinical_gate(self) -> None:
        # Even at F10-F12, model output must never satisfy a gate.
        def model_hook(stage):
            return [ClaimState.MODEL_PREDICTION]

        with self.assertRaises(RuntimeError):
            self._run(Route.NOVEL_TARGET_DE_NOVO, evidence_hook=model_hook)


class TestTerminationWorkflow(unittest.TestCase):
    def test_termination_and_externalize_outcomes(self) -> None:
        import hashlib

        from foundry_council.models import SnapshotRef

        def ref(oid: str) -> SnapshotRef:
            return SnapshotRef(
                object_id=oid, version=1,
                digest=f"sha256:{hashlib.sha256(oid.encode()).hexdigest()}",
            )

        wf = TerminationWorkflow()
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            from foundry_council.stage_runner import StageRunner

            runner = StageRunner(path, route=Route.NOVEL_TARGET_DE_NOVO, seed=7, lifecycle=True)
            # The runner already created a program at F0; promote it to F12 for
            # the terminal lifecycle gate.
            from foundry_council.models import ProgramRecord

            program = runner.program.model_copy(update={"stage": ProgramStage.F12})

            outcome = wf.execute(
                workflow_id="wf-ter-1", program=program,
                disposition=Disposition.TERMINATE, terms_ref=ref("terms-ter"),
            )
            self.assertEqual(outcome["outcome"], F12Outcome.TERMINATE.value)
            self.assertTrue(outcome["harness_only"])
            self.assertFalse(outcome["real_therapeutic_advance"])

            o2 = wf.execute(
                workflow_id="wf-lic-1", program=program,
                disposition=Disposition.LICENSE_OR_ACQUIRE, terms_ref=ref("terms-lic"),
            )
            self.assertEqual(o2["outcome"], F12Outcome.EXTERNALIZE_LICENSE.value)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
