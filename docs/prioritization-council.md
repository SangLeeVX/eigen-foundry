# Prioritization Council — Design

**Status:** Design approved and implemented. Full debate loop (0–10 scale,
hypothesis+modality candidate unit) is built and seat-differentiated per §2.1 of
`eigen-foundry-pipeline/PLAN.md` (2026-08-20).
**Owner:** Eigen Bio Foundry
**Date:** 2026-08-16
**Branch:** `agent/fwi-m6-live-deepseek-hookup`

## 1. Intent

The Foundry must, from a **pool of candidate assets and/or potential indications**,
form a **collective, auditable prioritization** — i.e. a defensible *ranked shortlist*
— rather than a single go/no-go on one fixed decision. This is distinct from the
existing `WorkingConclave`, which is a **governance gate** over a fixed set of cases
(SCIENTIFIC / PRODUCT / CONTROL / EXECUTION / INVESTMENT) that yields PASS/FAIL verdicts.

This design adds a **separate** construct — the **Prioritization Council** — that
reuses the proven live-seat machinery (`LiteralSeatModel` → DeepSeek, structured
opinion, audit) but changes the **input shape** (a candidate pool) and the **output
shape** (a ranked shortlist), not a gate verdict.

## 2. Why separate from `WorkingConclave`

| Dimension | `WorkingConclave` (exists) | `PrioritizationCouncil` (new) |
|---|---|---|
| Input | Fixed 5 case types | Arbitrary candidate pool (assets / indications) |
| Output | Per-case gate verdict (claim, PASS/FAIL) | **Ranked shortlist** + per-axis scores + confidence |
| Seat purpose | Each seat owns one case | Multiple role-seats evaluate **each** candidate across axes |
| Convergence | One-shot blind opinions + challenge | Debate on disagreements → revised, converging ranking |
| Authority | Never upgrades past MODEL_PREDICTION | Same invariant (never satisfies a gate, no self-approve) |

The two share infrastructure: seat runtime, structured-output validation, bounded
tool envelope, immutable audit. They differ in orchestration and output contract, so
they are **separate modules over a shared foundation**.

## 3. Scope / non-goals

**In scope**
- Run a live (DeepSeek-backed) or deterministic prioritization over a candidate pool.
- Emit an ordered shortlist with per-axis scores and a consensus confidence.
- Full audit trail (who scored what, when, how conflict resolved).
- Enforce the no-promote / no-self-approval / no-authority invariants.

**Non-goals (this construct)**
- Making a final investment/BD decision (it produces a *recommendation model*, not a decision).
- Advancing a Program stage.
- Scientific validation of a single claim beyond the prioritization context.

## 4. Inputs

### Candidate Pool
```python
@dataclass(frozen=True)
class Candidate:
    candidate_id: str            # e.g. "ASST-KRASG12C-01" or "IND-PDAC-BONEMET"
    kind: CandidateKind          # ASSET | INDICATION
    label: str                   # human-readable name
    evidence: tuple[EvidenceRef, ...]  # frozen evidence bundle for THIS candidate
    attributes: dict             # optional: stage, target, modality, market, etc.
```

The pool is provided by the caller (from Eigenfield assets/indications, or a defined
list). **Evidence dependency:** prioritization is only as good as the per-candidate
evidence. If candidates carry real Eigenfield data, the ranking is meaningful; if
evidence is a placeholder, output is a process demo only.

The unit we prioritize is a **therapeutic hypothesis coupled to a modality** — i.e. a
candidate is a `(hypothesis, modality)` pair (e.g. *anti-IBSP*, hypothesis="IBSP drives
bone-metastasis in mCRPC", modality=ADC), NOT a bare asset or bare indication. A single
asset may spawn several candidate `(hypothesis, modality)` pairs (different modality
options), and we rank those pairs, not the asset alone.

### Evaluation Axes (the definition of "prioritize")
Each candidate is scored on a fixed set of axes. **Confirmed axes:**
1. `scientific_validity`  — strength/credibility of the underlying evidence.
2. `indication_fit`       — how well the hypothesis/modality matches the target indication.
3. `feasibility_risk`     — technical/CMC/execution risk (higher score = lower risk).
4. `strategic_value`      — market/portfolio/BD value.

**Confirmed scale: 0–10 integer** on every axis, with `UNKNOWN` when evidence is absent
(an axis with no supporting evidence must score `UNKNOWN`, not a guess — fail-closed,
mirrors existing rules). Every non-`UNKNOWN` score must be bound to specific evidence.

### Role Seats
A small fixed set of role seats, each with a distinct lens and tool envelope:
- **Scientific seat** — evidence credibility, mechanism, validity.
- **Risk/feasibility seat** — technical, CMC, execution, timeline risk.
- **Commercial/strategic seat** — opportunity, market, fit, value.
- (optional) **Fit seat** — explicit indication/asset match.

**Distinct lenses (implemented).** Each of the three `ROLE_SEATS` gets its own
seat-specific lens (`SEAT_LENSES` in `prioritization_council.py`) that is injected
into both the live system prompt (`_prio_schema_guide(seat_id)`) and the per-candidate
scoring prompt (`_score_prompt(candidate, seat_id, ...)`):

- **scientific** — weigh mechanism plausibility, evidence quality/replication, and
  biological credibility above all else; scrutinize `scientific_validity` and
  `indication_fit` most closely.
- **risk_feasibility** — weigh CMC/technical/execution risk, timeline risk, and
  tractability above all else; scrutinize `feasibility_risk` most closely and flag
  unvalidated claims/unproven manufacturability.
- **commercial_strategic** — weigh market size, portfolio fit, and strategic/BD value
  above all else; scrutinize `strategic_value` (and the commercial fit implied by
  `indication_fit`) most closely.

**Every seat still scores ALL FOUR axes.** Differentiation is in emphasis/scrutiny, not
in which axes are scored — the aggregation step (`_aggregate`) takes the median across
all seats' opinions per axis, so every seat must produce an opinion on every axis. The
lens only tells the seat where to direct its most rigorous scrutiny.

The deterministic hermetic model (`DeterministicPrioritizationModel`) mirrors the same
lenses: it derives a stable per-`(seat, candidate)` hash base, adds a small emphasis
toward the seat's own axis, and on a challenge/revision call dampens its deviation
toward the base — so CI runs produce genuine, non-identical seat scores and a
converging debate without any network access.

Reused from the live layer: each seat is a `LiteralSeatModel` (DeepSeek by default,
deterministic mock for hermetic CI), bound to a distinct run identity (each seat carries
its own `model_version`/`prompt_version` via `_assignment_for(seat_id)`).

## 5. Mechanism / Flow

### Phase 0 — Freeze
- Freeze the candidate pool + its evidence into an immutable manifest (content-addressed).
- Assign distinct run identities + tool envelopes to each role seat.

### Phase 1 — Blind independent scoring
- Each role seat rates **every candidate** on every axis, citing the evidence it used.
- Structured output: `{"candidate_id": str, "axis_scores": {axis: 1..5|"UNKNOWN"},
  "rationale": {axis: str}, "confidence": 0..1}`.
- Scores are validated against the axis/enum contract; `UNKNOWN` where evidence absent.

### Phase 2 — Reveal & conflict surfacing
- Scores are revealed. The council computes **disagreement map**: candidates where seats
  diverge beyond a threshold on any axis (or where `UNKNOWN` blocks a ranking).
- Divergent candidates are flagged for challenge (not silently averaged).

### Phase 3 — Challenge / revision (convergence)
- For flagged candidates, the disagreeing seats are prompted to resolve against the
  evidence, producing a revised score. `UNKNOWN` remains unless evidence is added.
- A bounded number of rounds; no unbounded debate. Each revision is audited.

### Phase 4 — Aggregation to a shortlist
- A deterministic, **evidence-anchored** aggregation produces the ranking:
  - A candidate's composite score = weighted sum over axes, weights fixed/auditable.
  - Candidates with unresolved `UNKNOWN` on a *material* axis are rank-ordered below
    evidence-complete candidates (or excluded from the "recommended" tier and moved to
    an "evidence-gap" tier).
- Output = **RankedShortlist** (ordered candidates + composite + per-axis + confidence).

### Phase 5 — Emit audit packet
- `RankingPacket` = ranking + all individual seat opinions + challenge/revision history
  + digest. Immutable, content-addressed, labeled **MODEL_PREDICTION / DRY_RUN / not
  authoritative** (M6-C5 invariant). Never advances a Program stage.

## 6. Output contract

```python
@dataclass(frozen=True)
class AxisScore:
    axis: str
    value: int | None      # None == UNKNOWN
    rationale: str
    evidence_ids: tuple[str, ...]

@dataclass(frozen=True)
class CandidateRank:
    candidate_id: str
    composite: float | None
    axis_scores: tuple[AxisScore, ...]
    confidence: float
    tier: str              # RECOMMENDED | CONTENDER | EVIDENCE_GAP

@dataclass(frozen=True)
class RankedShortlist:
    ranks: tuple[CandidateRank, ...]   # descending by composite (evidence-complete first)
    consensus_note: str
    packet_digest: str
```

## 7. Governance / safety invariants

- **No authority escalation:** a model-produced ranking is `MODEL_PREDICTION`-class; it
  can never by itself satisfy an F0-F2 gate (reuse `F0F2GatePolicy`).
- **No self-approval:** seats are agents; they cannot carry human protected approver
  roles or approve a commit.
- **Evidence-anchored:** every axis score must cite evidence; unsupported scores fail
  closed to `UNKNOWN`; `UNKNOWN` is visible, not averaged away.
- **Bounded debate:** challenge has a fixed max rounds; no unbounded divergence.
- **Hermetic default:** like the live conclave, `FOUNDRY_SEAT_MODEL=deterministic` keeps
  CI network-free; `live` enables DeepSeek.

## 8. Files / shape (proposed)

```
src/foundry_council/prioritization_council.py   # orchestrator + aggregation + consensus
src/foundry_council/prioritization_models.py    # Candidate, AxisScore, RankedShortlist, RankingPacket
tests/test_prioritization_council.py            # deterministic + adversarial tests
scripts/prioritization_dryrun.py                # live/deterministic dry-run harness -> RankingPacket
```

Reuses: `live_seat_model.py` (LiveSeatModel + factory), `seat_runtime.py`, `models.py`,
`F0F2GatePolicy`, audit/ledger primitives. No changes required to `WorkingConclave`.

## 9. Decisions locked before implementation

**Locked:**
1. **Candidate unit** — a `(therapeutic hypothesis, modality)` pair; we rank
   hypothesis+modality pairs over a candidate pool, not bare assets/indications.
2. **Axes** — `scientific_validity`, `indication_fit`, `feasibility_risk`, `strategic_value`
   (confirmed).
3. **Scale** — 0–10 integer per axis, with `UNKNOWN` when evidence is absent (fail-closed).
4. **Consensus method** — **FULL debate loop** (not a single-pass weighted composite): seats
   score in blind rounds, disagreements are surfaced, challenged, and revised until
   convergence, then the ranking is aggregated from the converged scores. Bounded max
   rounds; no unbounded debate.

**Still open (decide before coding):**
1. **Pool source** — real Eigenfield assets/indications, or a defined candidate list for the
   first build? (Decides whether output is meaningful vs. process-demo.)
2. **Weights / tiering** — how converged axis scores aggregate to a composite; how an
   unresolved `UNKNOWN` on a material axis is tiered (proposed: below evidence-complete /
   EVIDENCE_GAP).
3. **Debate bounds** — exact max rounds and the convergence threshold that stops debate.

## 10. Recommendation

Implement with the **full debate loop** as the consensus method (per locked decision 9.4),
on top of the proven live-seat machinery:
- deterministic mode first (hermetic, testable), then live DeepSeek.
- pool = a small defined candidate list (hypothesis+modality pairs, e.g. 4–6) with
  real-ish evidence bundles.
- emit a `RankedShortlist` + audit packet; prove the loop (including debate convergence
  and conflict resolution) with deterministic tests.
Then wire real Eigenfield candidate/evidence sourcing as a follow-up.

This keeps the two constructs cleanly separated: **WorkingConclave = governance gate;**
**PrioritizationCouncil = filter-and-rank engine.** Both share the same auditable,
live-model foundation — and both produce `MODEL_PREDICTION`-class output that never
satisfies a gate on its own.
