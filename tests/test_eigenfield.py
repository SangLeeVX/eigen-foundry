from __future__ import annotations

import hashlib
import unittest

from foundry_council.eigen1_gateway import Eigen1Gateway
from foundry_council.eigenfield_steward import EigenFieldSteward, EvidenceGrounding, GroundingSign
from foundry_council.models import ClaimState, SnapshotRef


def _ref(oid: str) -> SnapshotRef:
    return SnapshotRef(
        object_id=oid, version=1, digest=f"sha256:{hashlib.sha256(oid.encode()).hexdigest()}"
    )


class TestEigen1Gateway(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = Eigen1Gateway(model_version="v1.0", prompt_version="p1", run_id="run-1")

    def test_prediction_is_always_model_prediction(self) -> None:
        p = self.gateway.predict(
            prediction_id="pred0001", program_id="PRG",
            context="CRC F0 context of use", mechanism="Intervention hypothesis",
            calibration=0.73, uncertainty="High (model-only)",
        )
        # M6 exit criterion: model output NEVER satisfies experimental gates.
        self.assertEqual(p.state, ClaimState.MODEL_PREDICTION)
        self.assertIsNot(p.state, ClaimState.EXPERIMENTALLY_VALIDATED)
        self.assertIsNot(p.state, ClaimState.TRANSLATIONALLY_VALIDATED)

    def test_prediction_is_versioned_and_content_addressed(self) -> None:
        a = self.gateway.predict(
            prediction_id="pred0001", program_id="PRG", context="c1",
            mechanism="m", calibration=0.5, uncertainty="u",
        )
        b = self.gateway.predict(
            prediction_id="pred0001", program_id="PRG", context="c1",
            mechanism="m", calibration=0.5, uncertainty="u",
        )
        self.assertEqual(a.digest, b.digest)
        self.assertEqual(a.model_version, "v1.0")

    def test_calibration_bounds(self) -> None:
        with self.assertRaises(ValueError):
            self.gateway.predict(
                prediction_id="pred0002", program_id="PRG", context="c",
                mechanism="m", calibration=2.0, uncertainty="u",
            )


class TestEigenFieldSteward(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway = Eigen1Gateway(model_version="v1", prompt_version="p1")
        self.steward = EigenFieldSteward()

    def test_grounding_classifies_signs(self) -> None:
        prediction = self.gateway.predict(
            prediction_id="pred0001", program_id="PRG", context="c",
            mechanism="m", calibration=0.5, uncertainty="u",
        )
        g = self.steward.ground(
            grounding_id="gnd0001", prediction=prediction,
            evidence_ref=_ref("ev1"), sign=GroundingSign.SUPPORTIVE,
            rationale="Versioned supportive evidence.",
        )
        self.assertIsInstance(g, EvidenceGrounding)
        self.assertEqual(g.sign, GroundingSign.SUPPORTIVE)
        self.assertEqual(g.prediction_digest, prediction.digest)

    def test_steward_never_grounds_upgraded_prediction(self) -> None:
        p = self.gateway.predict(
            prediction_id="pred0001", program_id="PRG", context="c",
            mechanism="m", calibration=0.5, uncertainty="u",
        )
        # Simulate an attempt to ground an (illegal) upgraded artifact.
        from dataclasses import replace

        upgraded = replace(p, state=ClaimState.EXPERIMENTALLY_VALIDATED)
        with self.assertRaises(ValueError):
            self.steward.ground(
                grounding_id="gnd0001", prediction=upgraded,
                evidence_ref=_ref("ev1"), sign=GroundingSign.SUPPORTIVE,
                rationale="must reject upgraded artifacts",
            )


if __name__ == "__main__":
    unittest.main()
