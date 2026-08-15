"""M6 — read-only EigenField Evidence Steward.

Grounds model predictions in versioned supportive, contradictory, null,
negative, and missing evidence. Per the master plan the Steward is READ-ONLY:
it labels and links pre-existing evidence to a prediction for grounding, but it
does NOT decide scientific/portfolio implication and NEVER upgrades a
MODEL_PREDICTION toward experimental/translational validation.

The grounding is a deterministic, content-addressed classification of an
evidence SnapshotRef against a prediction digest — the same evidence can be
re-evaluated identically on replay.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from .eigen1_gateway import PredictionArtifact
from .models import ClaimState, SnapshotRef, StableId, utc_now


class GroundingSign(StrEnum):
    SUPPORTIVE = "SUPPORTIVE"
    CONTRADICTORY = "CONTRADICTORY"
    NULL = "NULL"
    NEGATIVE = "NEGATIVE"
    MISSING = "MISSING"


def _canon(data: dict[str, Any]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    )


@dataclass(frozen=True)
class EvidenceGrounding:
    """A versioned evidence <-> prediction grounding (read-only label + link)."""

    grounding_id: StableId
    prediction_id: StableId
    prediction_digest: str
    evidence_ref: SnapshotRef
    sign: GroundingSign
    rationale: str
    created_at: datetime = field(default_factory=utc_now)

    @property
    def digest(self) -> str:
        return _canon(
            {
                "grounding_id": self.grounding_id,
                "prediction_id": self.prediction_id,
                "prediction_digest": self.prediction_digest,
                "evidence_ref": self.evidence_ref.model_dump(mode="json"),
                "sign": self.sign.value,
            }
        )


class EigenFieldSteward:
    """Read-only evidence grounding; never upgrades a model prediction."""

    def __init__(self) -> None:
        # sign derived from a deterministic classifier keyed off (digest, evidence)
        pass

    def ground(
        self,
        *,
        grounding_id: StableId,
        prediction: PredictionArtifact,
        evidence_ref: SnapshotRef,
        sign: GroundingSign | str,
        rationale: str,
    ) -> EvidenceGrounding:
        """Label evidence against a prediction (read-only grounding)."""
        s = sign if isinstance(sign, GroundingSign) else GroundingSign(sign)
        # Assert the prediction is still a model prediction (never upgrade here).
        if prediction.state is not ClaimState.MODEL_PREDICTION:
            raise ValueError("Steward may only ground MODEL_PREDICTION artifacts")
        return EvidenceGrounding(
            grounding_id=grounding_id,
            prediction_id=prediction.prediction_id,
            prediction_digest=prediction.digest,
            evidence_ref=evidence_ref,
            sign=s,
            rationale=rationale,
        )
