"""Prioritization Council — domain models.

A separate construct from the WorkingConclave: this ranks a POOL of
(therapeutic hypothesis, modality) candidates into a defensible shortlist via a
full multi-seat debate loop, rather than emitting a gate verdict over a fixed
case set.

Invariants mirror the rest of the Foundry:
  - Every axis score is evidence-anchored; a missing-evidence axis is ``UNKNOWN``
    (fail-closed), never guessed.
  - The top-level output is a ``MODEL_PREDICTION``-class recommendation: it can
    never, on its own, satisfy an F0-F2 gate or advance a Program stage.
  - The packet is immutable and content-addressed; no seat self-approves.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, model_validator

from .models import FrozenModel, SnapshotRef, StableId, utc_now


class CandidateKind(StrEnum):
    """The class of thing being prioritized (each candidate embeds a modality)."""

    HYPOTHESIS_MODALITY = "HYPOTHESIS_MODALITY"


class Axis(StrEnum):
    """The 4 evaluation axes every candidate is scored on (0-10)."""

    SCIENTIFIC_VALIDITY = "scientific_validity"
    INDICATION_FIT = "indication_fit"
    FEASIBILITY_RISK = "feasibility_risk"
    STRATEGIC_VALUE = "strategic_value"


AXIS_SET = frozenset(Axis)

# 0-10 bounded score sentinel.
MIN_SCORE = 0
MAX_SCORE = 10


class Tier(StrEnum):
    RECOMMENDED = "RECOMMENDED"
    CONTENDER = "CONTENDER"
    EVIDENCE_GAP = "EVIDENCE_GAP"


class CandidateKindField(FrozenModel):
    """Frozen evidence reference for a candidate (content-addressed)."""

    object_id: StableId
    version: int = 1


@dataclass(frozen=True)
class Candidate:
    """A therapeutic hypothesis coupled to a modality — the ranking unit."""

    candidate_id: StableId
    hypothesis: str
    modality: str
    indication: str
    evidence: tuple[SnapshotRef, ...] = field(default_factory=tuple)
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.hypothesis} / {self.modality}"


@dataclass(frozen=True)
class AxisScore:
    """A single seat's score on one axis for one candidate."""

    axis: Axis
    value: int | None  # None == UNKNOWN (no supporting evidence)
    rationale: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SeatOpinion:
    """One seat's blind (or revised) full evaluation of one candidate."""

    seat_id: str
    candidate_id: str
    axis_scores: tuple[AxisScore, ...]
    confidence: float  # 0..1
    round_: int

    def score_for(self, axis: Axis) -> int | None:
        for s in self.axis_scores:
            if s.axis == axis:
                return s.value
        return None


@dataclass(frozen=True)
class Disagreement:
    """A surfaced conflict between two seats on one axis for one candidate."""

    candidate_id: str
    axis: Axis
    seat_a: str
    score_a: int | None
    seat_b: str
    score_b: int | None
    message: str


@dataclass(frozen=True)
class DebateTurn:
    round_: int
    disagreements: tuple[Disagreement, ...] = ()
    # Revised opinions for this round (only seats that changed emit here).
    revisions: tuple[SeatOpinion, ...] = ()


@dataclass(frozen=True)
class CandidateRank:
    candidate_id: str
    composite: float | None
    axis_scores: tuple[AxisScore, ...]  # converged (aggregate per axis)
    confidence: float
    tier: Tier


@dataclass(frozen=True)
class RankedShortlist:
    ranks: tuple[CandidateRank, ...]  # descending by composite
    consensus_note: str
    debate_rounds_used: int
    packet_digest: str


class PrioritizationPacket(FrozenModel):
    """Immutable, content-addressed audit packet for a prioritization run."""

    packet_id: StableId
    created_at: str  # ISO-8601 (durable, JSON-serializable)
    pool: tuple[dict[str, Any], ...] = ()
    opinions: tuple[dict[str, Any], ...] = ()
    debate: tuple[dict[str, Any], ...] = ()
    ranking: tuple[dict[str, Any], ...] = ()
    consensus_note: str = ""
    packet_digest: str = ""

    @model_validator(mode="after")
    def _check_digest(self) -> "PrioritizationPacket":
        if self.packet_digest:
            canonical = json.dumps(
                {
                    "pool": self.pool,
                    "opinions": self.opinions,
                    "debate": self.debate,
                    "ranking": self.ranking,
                    "consensus_note": self.consensus_note,
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            digest = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
            if digest != self.packet_digest:
                raise ValueError("prioritization packet digest does not match content")
        return self


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def packet_digest(payload: dict[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(_canon(payload).encode()).hexdigest()}"


__all__ = [
    "AXIS_SET",
    "Axis",
    "AxisScore",
    "Candidate",
    "CandidateKind",
    "CandidateRank",
    "DebateTurn",
    "Disagreement",
    "MAX_SCORE",
    "MIN_SCORE",
    "PrioritizationPacket",
    "RankedShortlist",
    "SeatOpinion",
    "Tier",
    "packet_digest",
    "utc_now",
]
