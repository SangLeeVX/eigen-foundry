"""M6 — versioned Eigen-1 analysis gateway and prediction controls.

Eigen-1 produces context-specific, versioned mechanism and intervention
predictions. Per the master plan:

  - Eigen-1 predictions remain MODEL_PREDICTION (model evidence) until
    independently validated — admission NEVER upgrades them to experimental or
    translational validation.
  - Every prediction carries model/run provenance, calibration, uncertainty,
    and context of use.

The gateway is versioned: distinct model_version/prompt_version/run_id and a
canonical prediction digest make each prediction traceable. It never writes a
ClaimState above MODEL_PREDICTION, so model output can never satisfy an
experimental gate by itself.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .m5_models import SentinelEvent
from .models import ClaimState, SnapshotRef, StableId, utc_now


def _cannoness(data: dict[str, Any]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    )


@dataclass(frozen=True)
class PredictionArtifact:
    """A versioned, content-addressed Eigen-1 prediction."""

    prediction_id: StableId
    program_id: StableId
    context: str  # context of use
    mechanism: str  # proposed mechanism / intervention direction
    model_version: str
    prompt_version: str
    run_id: StableId | None
    calibration: float  # 0..1 reported model confidence (not truth)
    uncertainty: str
    state: ClaimState  # MUST remain MODEL_PREDICTION
    source_event: SnapshotRef | None = None
    created_at: datetime = field(default_factory=utc_now)

    @property
    def digest(self) -> str:
        return _cannoness(
            {
                "prediction_id": self.prediction_id,
                "program_id": self.program_id,
                "context": self.context,
                "mechanism": self.mechanism,
                "model_version": self.model_version,
                "prompt_version": self.prompt_version,
                "run_id": self.run_id,
                "calibration": self.calibration,
                "state": self.state.value,
            }
        )


class Eigen1Gateway:
    """Versioned prediction gateway that never upgrades MODEL_PREDICTION."""

    def __init__(self, *, model_version: str, prompt_version: str, run_id: StableId | None = None) -> None:
        self.model_version = model_version
        self.prompt_version = prompt_version
        self.run_id = run_id

    def predict(
        self,
        *,
        prediction_id: StableId,
        program_id: StableId,
        context: str,
        mechanism: str,
        calibration: float,
        uncertainty: str,
        source_event: SnapshotRef | None = None,
    ) -> PredictionArtifact:
        if not (0.0 <= calibration <= 1.0):
            raise ValueError("calibration must be in [0, 1]")
        # The state is FORCED to MODEL_PREDICTION — never experimental/translational.
        return PredictionArtifact(
            prediction_id=prediction_id,
            program_id=program_id,
            context=context,
            mechanism=mechanism,
            model_version=self.model_version,
            prompt_version=self.prompt_version,
            run_id=self.run_id,
            calibration=calibration,
            uncertainty=uncertainty,
            state=ClaimState.MODEL_PREDICTION,
            source_event=source_event,
        )
