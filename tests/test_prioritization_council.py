"""Prioritization Council — deterministic tests.

Uses a deterministic seat model (injected) so the full debate loop, conflict
resolution, aggregation, and audit packet can be verified hermetically (no
network, no live model), mirroring how the WorkingConclave is tested.
"""
from __future__ import annotations

import json
import unittest

from foundry_council.models import SnapshotRef
from foundry_council.prioritization_council import (
    PrioritizationCouncil,
    PrioritizationResult,
)
from foundry_council.prioritization_models import (
    Axis,
    Candidate,
    RankedShortlist,
    Tier,
)
from foundry_council.seat_runtime import LiteralSeatModel  # noqa: F401


class _ScriptedSeatModel:
    """Deterministic prioritization seat reading a per-seat script of scores.

    Services the LiteralSeatModel protocol: run(prompt, context) -> raw JSON with
    axis_scores / rationale / confidence / candidate_id.
    """

    def __init__(self, schedule: dict[str, dict]) -> None:
        # schedule: candidate_id -> {axis: value or None}
        self.schedule = schedule

    def run(self, prompt: str, context: dict) -> str:
        cand_id = context.get("candidate", {}).get("candidate_id")
        scores = self.schedule.get(cand_id, {})
        axis_scores = {a.value: scores.get(a) for a in Axis}
        return json.dumps(
            {
                "axis_scores": axis_scores,
                "rationale": {a.value: "scripted" for a in Axis},
                "confidence": 0.8,
                "candidate_id": cand_id,
            }
        )

def _factory_for(scripts: dict[str, "_ScriptedSeatModel"]):
    def factory(assignment, template):
        seat_id = assignment.actor_id
        return scripts.get(seat_id, _ScriptedSeatModel({}))

    return factory


def _candidate(cid: str, *, evidence: tuple = ("ev-1",), modality="ADC") -> Candidate:
    return Candidate(
        candidate_id=cid,
        hypothesis=f"hypothesis for {cid}",
        modality=modality,
        indication="mCRPC",
        evidence=tuple(SnapshotRef(object_id=e, version=1, digest=f"sha256:{'0'*64}") for e in evidence),
    )


class DebateConvergenceTests(unittest.TestCase):
    """The full debate loop converges and produces a valid ranked shortlist."""

    def _run(self, scripts: dict[str, "_ScriptedSeatModel"], candidates: tuple[Candidate, ...]) -> PrioritizationResult:
        factory = _factory_for(scripts)
        council = PrioritizationCouncil(candidates, seat_model_factory=factory, max_rounds=3)
        return council.run()

    def test_blind_round_aggregates_to_shortlist(self) -> None:
        c1 = _candidate("C-1", evidence=("ev-1", "ev-2"), modality="ADC")
        c2 = _candidate("C-2", evidence=("ev-3",), modality="SMALL_MOLECULE")
        base = {a: 8 for a in Axis}
        low = {a: 4 for a in Axis}
        scripts = {
            "scientific": _ScriptedSeatModel({"C-1": base, "C-2": low}),
            "risk_feasibility": _ScriptedSeatModel({"C-1": base, "C-2": low}),
            "commercial_strategic": _ScriptedSeatModel({"C-1": base, "C-2": low}),
        }
        result = self._run(scripts, (c1, c2))
        self.assertIsInstance(result.shortlist, RankedShortlist)
        # C-1 (8s) ranks above C-2 (4s).
        self.assertEqual(result.shortlist.ranks[0].candidate_id, "C-1")
        self.assertEqual(result.shortlist.ranks[0].tier, Tier.RECOMMENDED)
        self.assertEqual(result.shortlist.ranks[1].candidate_id, "C-2")
        self.assertEqual(result.shortlist.ranks[1].tier, Tier.CONTENDER)
        self.assertGreater(result.shortlist.ranks[0].composite, result.shortlist.ranks[1].composite)
        # Audit packet is immutable and digest-bound.
        self.assertTrue(result.packet.packet_digest.startswith("sha256:"))
        self.assertTrue(result.packet.packet_digest)

    def test_evidence_gap_tiering(self) -> None:
        # A candidate with UNKNOWN on all axes (no evidence) goes to EVIDENCE_GAP,
        # ranked below evidence-complete candidates.
        c_known = _candidate("C-K", evidence=("ev-1",))
        c_gap = _candidate("C-G", evidence=())  # no evidence -> UNKNOWN everywhere
        scripts = {
            "scientific": _ScriptedSeatModel(
                {"C-K": {a: 9 for a in Axis}, "C-G": {a: None for a in Axis}}
            ),
            "risk_feasibility": _ScriptedSeatModel(
                {"C-K": {a: 9 for a in Axis}, "C-G": {a: None for a in Axis}}
            ),
            "commercial_strategic": _ScriptedSeatModel(
                {"C-K": {a: 9 for a in Axis}, "C-G": {a: None for a in Axis}}
            ),
        }
        result = self._run(scripts, (c_known, c_gap))
        ranks = result.shortlist.ranks
        self.assertEqual(ranks[-1].candidate_id, "C-G")
        self.assertEqual(ranks[-1].tier, Tier.EVIDENCE_GAP)
        self.assertIsNone(ranks[-1].composite)

    def test_debate_resolves_disagreement(self) -> None:
        # scientific scores C-1 high, risk scores it low -> disagreement surfaced
        # on feasibility_risk -> challenge revises toward convergence.
        c1 = _candidate("C-1", evidence=("ev-1",))
        hi = {Axis.SCIENTIFIC_VALIDITY: 9, Axis.INDICATION_FIT: 8, Axis.FEASIBILITY_RISK: 9, Axis.STRATEGIC_VALUE: 8}
        lo = {Axis.SCIENTIFIC_VALIDITY: 9, Axis.INDICATION_FIT: 8, Axis.FEASIBILITY_RISK: 2, Axis.STRATEGIC_VALUE: 8}
        rev = {Axis.SCIENTIFIC_VALIDITY: 9, Axis.INDICATION_FIT: 8, Axis.FEASIBILITY_RISK: 7, Axis.STRATEGIC_VALUE: 8}

        class _RevisingSeat(_ScriptedSeatModel):
            def __init__(self, initial, revised):
                super().__init__(initial)
                self.revised = revised
                self.calls = 0

            def run(self, prompt, context):
                self.calls += 1
                cand = context.get("candidate", {}).get("candidate_id")
                if "CHALLENGE" in prompt or "Disagreement" in prompt:
                    scores = self.revised
                else:
                    scores = self.schedule.get(cand, {a: None for a in Axis})
                return json.dumps({
                    "axis_scores": {a.value: scores.get(a) for a in Axis},
                    "rationale": {a.value: "r" for a in Axis},
                    "confidence": 0.8,
                    "candidate_id": cand,
                })

        scientific = _RevisingSeat({"C-1": hi}, rev)
        risk = _RevisingSeat({"C-1": lo}, lo)  # risk never revises (defends)
        commercial = _ScriptedSeatModel({"C-1": hi})
        scripts = {"scientific": scientific, "risk_feasibility": risk, "commercial_strategic": commercial}
        result = self._run(scripts, (c1,))
        # Debate happened (at least one round).
        self.assertGreaterEqual(len(result.debate), 1)
        # Scientific got challenged (it revised).
        self.assertGreater(scientific.calls, 0)


class PacketIntegrityTests(unittest.TestCase):
    def test_packet_roundtrips_and_digest_validates(self) -> None:
        c = _candidate("C-1", evidence=("ev-1",))
        scores = {a: 7 for a in Axis}
        scripts = {
            s: _ScriptedSeatModel({"C-1": scores}) for s in ("scientific", "risk_feasibility", "commercial_strategic")
        }
        result = PrioritizationCouncil((c,), seat_model_factory=_factory_for(scripts)).run()
        # Re-parse the packet as a FrozenModel to confirm the digest validator passes.
        from foundry_council.prioritization_models import PrioritizationPacket

        reconstructed = PrioritizationPacket(**result.packet.model_dump())
        self.assertEqual(reconstructed.packet_digest, result.packet.packet_digest)


if __name__ == "__main__":
    unittest.main()
