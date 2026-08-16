"""Prioritization Council — orchestrator.

Ranks a POOL of (therapeutic hypothesis, modality) candidates into a defensible
shortlist through a FULL multi-seat debate loop:

  freeze -> blind independent scoring (all seats, all candidates)
         -> score conflict surfacing
         -> challenge / revise (bounded rounds until convergence)
         -> aggregation to a ranked shortlist
         -> immutable audit packet

The ranking produced is ALWAYS ``MODEL_PREDICTION``-class: it can never, on its
own, satisfy an F0-F2 gate, advance a Program stage, or authorize any action.

Model selection mirrors the live conclave: ``FOUNDRY_SEAT_MODEL=live`` binds the
real (DeepSeek / Kimi) model via `LiveSeatModel`; the default ``deterministic``
uses `DeterministicSeatModel` so CI stays hermetic and network-free. All seats
report structured opinions that are validated before any aggregation.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from typing import Any

from .live_seat_model import LiveSeatModel, LiveSeatUnavailable
from .models import utc_now
from .prioritization_models import (
    Axis,
    AxisScore,
    Candidate,
    CandidateRank,
    DebateTurn,
    Disagreement,
    MAX_SCORE,
    MIN_SCORE,
    PrioritizationPacket,
    RankedShortlist,
    SeatOpinion,
    Tier,
    packet_digest,
)
from .seat_runtime import LiteralSeatModel  # noqa: F401 (future protocol typing)

# Role seats: each has a distinct lens + envelope on the same candidate pool.
ROLE_SEATS = ("scientific", "risk_feasibility", "commercial_strategic")

# Debate bounds.
DEFAULT_MAX_ROUNDS = 3
DEFAULT_SPREAD_THRESHOLD = 2  # |a - b| > threshold on an axis == disagreement


@dataclass(frozen=True)
class PrioritizationResult:
    shortlist: RankedShortlist
    packet: PrioritizationPacket
    pool: tuple[Candidate, ...]
    opinions: tuple[SeatOpinion, ...]
    debate: tuple[DebateTurn, ...]


class PrioritizationBoundedError(Exception):
    """A prioritization run exceeded a bounded resource (rounds, model)."""


class DeterministicPrioritizationModel:
    """Hermetic default seat: deterministic 0-10 scores so the loop is testable
    offline. Base score + a stable per-candidate/per-axis offset from a hash, so
    a ranking and (across the three seats) occasional disagreements emerge."""

    def __init__(self, base: float = 7.0) -> None:
        self._base = base

    def run(self, prompt: str, context: dict) -> str:
        import hashlib

        cand = context.get("candidate", {}).get("candidate_id", "cand")
        seed = int(hashlib.sha256(cand.encode()).hexdigest()[:8], 16)
        axis_scores: dict[str, int | None] = {}
        for i, axis in enumerate(Axis):
            offset = (seed >> (i * 3)) % 5 - 2  # -2..2
            val = int(round(self._base + offset))
            val = max(0, min(10, val))
            if not context.get("evidence"):
                val = None  # no evidence -> UNKNOWN on every axis
            axis_scores[axis.value] = val
        return json.dumps(
            {
                "axis_scores": axis_scores,
                "rationale": {a.value: "deterministic" for a in Axis},
                "confidence": 0.8,
                "candidate_id": cand,
            }
        )


def _prio_schema_guide() -> str:
    axes = ", ".join(a.value for a in Axis)
    return (
        'Your entire reply must be EXACTLY ONE minified JSON object and nothing '
        'else (no markdown fences, no prose). Use exactly these keys: '
        '{"axis_scores": {<axis>: <0..10 integer or null>}, '
        '"rationale": {<axis>: string}, "confidence": <0..1> (float), '
        '"candidate_id": string}. '
        'The axes are: ' + axes + '. '
        'Score each axis 0-10 from ONLY the provided evidence. If an axis has no '
        'supporting evidence, set that axis value to null (do not guess). '
        'Rationale must cite evidence ids. Do not invent numbers. Output the single '
        'JSON object only.'
    )


def default_prioritization_seat_factory(assignment, _template) -> "LiteralSeatModel":
    """Env-driven prioritization seat: FOUNDRY_SEAT_MODEL=live -> real model (with
    the prioritization axis schema); default -> hermetic deterministic model."""
    import os

    mode = os.environ.get("FOUNDRY_SEAT_MODEL", "deterministic").strip().lower()
    if mode == "live":
        return LiveSeatModel.from_secrets_env(system_prompt=_prio_schema_guide())
    return DeterministicPrioritizationModel()


class PrioritizationCouncil:
    """Full-debate prioritization over a candidate pool."""

    def __init__(
        self,
        candidates: tuple[Candidate, ...],
        *,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        spread_threshold: int = DEFAULT_SPREAD_THRESHOLD,
        seed: int = 0,
        seat_model_factory=None,
    ) -> None:
        if not candidates:
            raise ValueError("prioritization council requires at least one candidate")
        self.pool = candidates
        self.max_rounds = max_rounds
        self.spread_threshold = spread_threshold
        self.seed = seed
        self._seat_factory = seat_model_factory or default_prioritization_seat_factory

    # ------------------------------------------------------------------ prompts

    def _score_prompt(self, candidate: Candidate, challenger_context: str | None = None) -> str:
        base = (
            f"Score candidate {candidate.candidate_id} on each axis. "
            f"Hypothesis: {candidate.hypothesis}. Modality: {candidate.modality}. "
            f"Indication: {candidate.indication}. "
            "Score each axis 0-10 using ONLY the frozen evidence. If an axis has no "
            "supporting evidence, score it null (UNKNOWN), never guess. "
            "Every non-null score must be justified in rationale and cite evidence ids. "
            "confidence = how sure you are of your overall assessment (0..1)."
        )
        if challenger_context:
            base += "\nCHALLENGE CONTEXT (reconcile against the evidence):\n" + challenger_context
        return base

    def _score_one(self, seat_id: str, assignment, candidate: Candidate, challenger: str | None = None) -> SeatOpinion:
        """Run a single seat on a single candidate, validate + parse the opinion."""
        model = self._seat_factory(
            assignment, {"claim_id": f"pc-{candidate.candidate_id}", "state": "MODEL_PREDICTION"}
        )
        context = {
            "candidate": {
                "candidate_id": candidate.candidate_id,
                "hypothesis": candidate.hypothesis,
                "modality": candidate.modality,
                "indication": candidate.indication,
            },
            "evidence": [
                {
                    "object_id": ev.object_id,
                    "version": ev.version,
                    "digest": ev.digest,
                    "content": candidate.attributes.get("evidence_content", {}).get(ev.object_id),
                }
                for ev in candidate.evidence
            ],
            "axes": [a.value for a in Axis],
            "score_range": f"{MIN_SCORE}..{MAX_SCORE} or null",
            "challenge": challenger,
        }
        raw = model.run(self._score_prompt(candidate, challenger), context)
        parsed = json.loads(raw) if isinstance(raw, dict) else _extract_json(raw)
        return self._parse_opinion(seat_id, candidate.candidate_id, parsed)

    def _parse_opinion(self, seat_id: str, candidate_id: str, parsed: dict[str, Any]) -> SeatOpinion:
        if not isinstance(parsed, dict):
            raise ValueError(f"seat {seat_id}: opinion must be a JSON object")
        axis_scores_raw = parsed.get("axis_scores")
        if not isinstance(axis_scores_raw, dict):
            raise ValueError(f"seat {seat_id}: axis_scores must be an object")
        scores: list[AxisScore] = []
        for axis in Axis:
            raw_value = axis_scores_raw.get(axis.value, axis_scores_raw.get(axis))
            if raw_value is None:
                value: int | None = None
            else:
                try:
                    value = int(raw_value)
                except (TypeError, ValueError):
                    value = None
                if value is not None and not (MIN_SCORE <= value <= MAX_SCORE):
                    raise ValueError(f"seat {seat_id}: {axis.value} score out of range: {value}")
            rationale_raw = parsed.get("rationale", {})
            rationale = rationale_raw.get(axis.value, rationale_raw.get(axis, ""))
            scores.append(AxisScore(axis=axis, value=value, rationale=str(rationale)))
        conf_raw = parsed.get("confidence")
        try:
            confidence = float(conf_raw)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        return SeatOpinion(
            seat_id=seat_id,
            candidate_id=candidate_id,
            axis_scores=tuple(scores),
            confidence=confidence,
            round_=0,
        )

    # ------------------------------------------------------------------ flow

    def freeze(self, candidate: Candidate) -> dict[str, Any]:
        return {
            "candidate_id": candidate.candidate_id,
            "hypothesis": candidate.hypothesis,
            "modality": candidate.modality,
            "indication": candidate.indication,
            "evidence_ids": [ev.object_id for ev in candidate.evidence],
            "frozen_at": utc_now().isoformat(),
        }

    def disagreements(self, opinions: tuple[SeatOpinion, ...]) -> tuple[Disagreement, ...]:
        """Surface axis conflicts (spread > threshold) between distinct seats."""
        out: list[Disagreement] = []
        by_candidate: dict[str, list[SeatOpinion]] = {}
        for op in opinions:
            by_candidate.setdefault(op.candidate_id, []).append(op)
        for candidate_id, ops in by_candidate.items():
            for axis in Axis:
                known = [(o.seat_id, o.score_for(axis)) for o in ops if o.score_for(axis) is not None]
                if len(known) < 2:
                    continue
                vals = [v for _, v in known]
                if max(vals) - min(vals) > self.spread_threshold:
                    lo = min(known, key=lambda kv: kv[1])
                    hi = max(known, key=lambda kv: kv[1])
                    out.append(
                        Disagreement(
                            candidate_id=candidate_id,
                            axis=axis,
                            seat_a=lo[0],
                            score_a=lo[1],
                            seat_b=hi[0],
                            score_b=hi[1],
                            message=f"{lo[0]}={lo[1]} vs {hi[0]}={hi[1]} on {axis.value} (spread>{self.spread_threshold})",
                        )
                    )
        return tuple(out)

    def challenge_revise(
        self,
        opinions: tuple[SeatOpinion, ...],
        disagreements: tuple[Disagreement, ...],
        round_: int,
    ) -> tuple[SeatOpinion, ...]:
        """Present disagreements to the involved seats and collect revised opinions."""
        if not disagreements:
            return opinions
        # Map seat->candidate revisions to collect.
        # For each disagreement, ask the two seats involved to re-score that candidate
        # with the conflict surfaced. We only retain changed values via fresh calls.
        revisions: list[SeatOpinion] = []
        original_by_key = {(o.seat_id, o.candidate_id): o for o in opinions}
        for d in disagreements:
            for seat_id in (d.seat_a, d.seat_b):
                key = (seat_id, d.candidate_id)
                orig = original_by_key.get(key)
                if orig is None:
                    continue
                candidate = next(c for c in self.pool if c.candidate_id == d.candidate_id)
                assignment = self._assignment_for(seat_id)
                challenge = (
                    f"Disagreement on {d.axis.value}: your seat scored {orig.score_for(d.axis)}, "
                    f"{d.seat_b if seat_id == d.seat_a else d.seat_a} scored "
                    f"{d.score_b if seat_id == d.seat_a else d.score_a}. "
                    f"Reconcile against the frozen evidence. If the evidence supports a "
                    f"different value, revise it; otherwise defend yours. {d.message}"
                )
                revised = self._score_one(seat_id, assignment, candidate, challenger=challenge)
                # Re-pin round + carry unchanged axes from the original where the model returned null.
                merged = _merge_axes(orig, revised, round_=round_)
                revisions.append(merged)
        return _apply_revisions(opinions, revisions)

    def _assignment_for(self, seat_id: str):
        from .models import ActorKind, CaseType, ParticipantAssignment

        return ParticipantAssignment(
            assignment_id="pc-seat",
            actor_id=seat_id,
            actor_kind=ActorKind.AGENT,
            role="candidate_prioritizer",
            case=CaseType.SCIENTIFIC,
            run_id="pc-run-001",
            model_version="pc-model-v1",
            prompt_version="pc-prompt-v1",
            independence_group="pc-group-a",
        )

    def run(self) -> PrioritizationResult:
        """Execute the full debate loop and emit the ranked shortlist + packet."""
        # Blind round: every seat scores every candidate.
        opinions: list[SeatOpinion] = []
        for seat_id in ROLE_SEATS:
            assignment = self._assignment_for(seat_id)
            for candidate in self.pool:
                opinions.append(self._score_one(seat_id, assignment, candidate))
        opinions = tuple(opinions)

        debate: list[DebateTurn] = []
        current = opinions
        for round_ in range(1, self.max_rounds + 1):
            conflicts = self.disagreements(current)
            if not conflicts:
                debate.append(DebateTurn(round_=round_))
                break
            debate.append(DebateTurn(round_=round_, disagreements=conflicts))
            current = self.challenge_revise(current, conflicts, round_)

        shortlist = self._aggregate(current)
        # Build packet (MODEL_PREDICTION-class; immutable, content-addressed).
        payload = {
            "ntype": "MODEL_PREDICTION",
            "note": "Prioritization recommendation. Not authoritative; cannot satisfy a gate or advance a Program.",
            "pool": [self.freeze(c) for c in self.pool],
            "opinions": [_op_to_dict(o) for o in current],
            "debate": [
                {
                    "round": t.round_,
                    "disagreements": [d.message for d in t.disagreements],
                    "revisions": len(t.revisions),
                }
                for t in debate
            ],
            "ranking": [_rank_to_dict(r) for r in shortlist.ranks],
            "consensus_note": shortlist.consensus_note,
        }
        # Digest binds the SAME subset the packet validator recomputes (pool,
        # opinions, debate, ranking, consensus_note) so the immutable check holds.
        digest_payload = {
            "pool": payload["pool"],
            "opinions": payload["opinions"],
            "debate": payload["debate"],
            "ranking": payload["ranking"],
            "consensus_note": payload["consensus_note"],
        }
        digest = packet_digest(digest_payload)
        shortlist = RankedShortlist(
            ranks=shortlist.ranks,
            consensus_note=shortlist.consensus_note,
            debate_rounds_used=len(debate),
            packet_digest=digest,
        )
        packet = PrioritizationPacket(
            packet_id="pc-packet-001",
            created_at=utc_now().isoformat(),
            pool=payload["pool"],
            opinions=payload["opinions"],
            debate=payload["debate"],
            ranking=payload["ranking"],
            consensus_note=shortlist.consensus_note,
            packet_digest=digest,
        )
        return PrioritizationResult(
            shortlist=shortlist,
            packet=packet,
            pool=self.pool,
            opinions=current,
            debate=tuple(debate),
        )

    # ------------------------------------------------------------------ aggregate

    def _aggregate(self, opinions: tuple[SeatOpinion, ...]) -> RankedShortlist:
        """Converge per-candidate per-axis opinions into a ranked shortlist.

        Composite = mean of non-null axis scores (equal weights). Candidates with an
        unresolved material-unknown on any axis are tiered EVIDENCE_GAP and ranked
        below evidence-complete candidates.
        """
        ranks: list[CandidateRank] = []
        by_candidate: dict[str, list[SeatOpinion]] = {}
        for o in opinions:
            by_candidate.setdefault(o.candidate_id, []).append(o)
        for candidate in self.pool:
            ops = by_candidate.get(candidate.candidate_id, [])
            if not ops:
                continue
            axis_scores: list[AxisScore] = []
            composite_sum = 0.0
            composite_n = 0
            has_unknown = False
            for axis in Axis:
                vals = [o.score_for(axis) for o in ops]
                non_null = [v for v in vals if v is not None]
                if non_null:
                    median = float(statistics.median(non_null))
                    composite_sum += median
                    composite_n += 1
                    axis_scores.append(
                        AxisScore(
                            axis=axis,
                            value=int(round(median)),
                            rationale="converged",
                            evidence_ids=(),
                        )
                    )
                else:
                    has_unknown = True
                    axis_scores.append(
                        AxisScore(axis=axis, value=None, rationale="UNKNOWN (no evidence)", evidence_ids=())
                    )
            confidence = float(statistics.mean([o.confidence for o in ops])) if ops else 0.0
            composite = (composite_sum / composite_n) if composite_n else None
            if has_unknown:
                tier = Tier.EVIDENCE_GAP
            elif composite is not None and composite >= 7.0:
                tier = Tier.RECOMMENDED
            else:
                tier = Tier.CONTENDER
            ranks.append(
                CandidateRank(
                    candidate_id=candidate.candidate_id,
                    composite=composite,
                    axis_scores=tuple(axis_scores),
                    confidence=confidence,
                    tier=tier,
                )
            )
        # Rank: evidence-complete (no unresolved UNKNOWN) first by composite desc,
        # then EVIDENCE_GAP (any unresolved UNKNOWN on a material axis) below.
        complete = [r for r in ranks if r.tier in (Tier.RECOMMENDED, Tier.CONTENDER)]
        gap = [r for r in ranks if r.tier == Tier.EVIDENCE_GAP]
        complete.sort(key=lambda r: r.composite, reverse=True)  # type: ignore[misc]
        ordered = tuple(complete + gap)
        consensus = (
            f"Debated over {len(ordered)} candidates; {len(complete)} evidence-complete "
            f"ranked by converged composite, {len(gap)} in EVIDENCE_GAP "
            f"(unresolved UNKNOWN on a material axis)."
        )
        return RankedShortlist(ranks=ordered, consensus_note=consensus, debate_rounds_used=0, packet_digest="")


def _extract_json(raw: str) -> dict[str, Any]:
    start = raw.find("{")
    if start < 0:
        raise ValueError("model output contained no JSON object")
    import json as _json

    decoder = _json.JSONDecoder()
    content, _ = decoder.raw_decode(raw[start:])
    if not isinstance(content, dict):
        raise ValueError("model output JSON is not an object")
    return content


def _merge_axes(orig: SeatOpinion, revised: SeatOpinion, round_: int) -> SeatOpinion:
    """Carry unchanged/unknown axes from the original where the revision is null."""
    merged: list[AxisScore] = []
    rev_by_axis = {s.axis: s for s in revised.axis_scores}
    for orig_score in orig.axis_scores:
        rev_score = rev_by_axis.get(orig_score.axis)
        if rev_score is None or rev_score.value is None:
            merged.append(orig_score)
        else:
            merged.append(rev_score)
    return SeatOpinion(
        seat_id=revised.seat_id,
        candidate_id=revised.candidate_id,
        axis_scores=tuple(merged),
        confidence=revised.confidence,
        round_=round_,
    )


def _apply_revisions(base: tuple[SeatOpinion, ...], revisions: tuple[SeatOpinion, ...]) -> tuple[SeatOpinion, ...]:
    pool = {o.seat_id: o for o in base}
    for r in revisions:
        pool[r.seat_id] = r
    return tuple(pool.values())


def _op_to_dict(op: SeatOpinion) -> dict[str, Any]:
    return {
        "seat_id": op.seat_id,
        "candidate_id": op.candidate_id,
        "scores": {s.axis.value: s.value for s in op.axis_scores},
        "confidence": op.confidence,
        "round": op.round_,
    }


def _rank_to_dict(r: CandidateRank) -> dict[str, Any]:
    return {
        "candidate_id": r.candidate_id,
        "composite": r.composite,
        "axis_scores": {s.axis.value: s.value for s in r.axis_scores},
        "confidence": r.confidence,
        "tier": r.tier.value,
    }


__all__ = [
    "PrioritizationCouncil",
    "PrioritizationResult",
    "PrioritizationBoundedError",
    "ROLE_SEATS",
    "DEFAULT_MAX_ROUNDS",
    "DEFAULT_SPREAD_THRESHOLD",
]
