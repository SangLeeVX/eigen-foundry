#!/usr/bin/env python3
"""Prioritization Council dry run.

Runs the PrioritizationCouncil over a defined candidate pool (therapeutic
hypothesis + modality pairs) and prints the ranked shortlist + audit packet.

Mode:
  default            deterministic seats (hermetic, offline) — proves the loop
  FOUNDRY_SEAT_MODEL=live   real DeepSeek/Kimi inference over the pool

Governed: output is MODEL_PREDICTION-class; never satisfies a gate or advances
a Program stage.

Usage:
  PYTHONPATH=src python3 scripts/prioritization_dryrun.py [--candidates C1,C2|all]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from foundry_council.models import SnapshotRef  # noqa: E402
from foundry_council.prioritization_council import PrioritizationCouncil  # noqa: E402
from foundry_council.prioritization_models import Axis, Candidate  # noqa: E402


def _ev(object_id: str) -> SnapshotRef:
    digest = f"sha256:{'0' * 64}"
    return SnapshotRef(object_id=object_id, version=1, digest=digest)


def _pool() -> tuple[Candidate, ...]:
    """A defined candidate pool (hypothesis + modality) with evidence bundles.

    Each candidate carries substantive evidence CONTENT (via attributes
    ["evidence_content"]) so the live model can actually ground its 0-10 axis
    scores on real readouts rather than placeholder ids.
    """
    return (
        Candidate(
            candidate_id="ASST-IBSP-ADC",
            hypothesis="IBSP drives osteolytic bone-metastasis via integrin signaling in mCRPC",
            modality="ADC",
            indication="mCRPC bone metastasis",
            evidence=(_ev("e-ibsp-rna"), _ev("e-ibsp-ihc"), _ev("e-av-integrin"), _ev("e-perseus")),
            attributes={
                "evidence_content": {
                    "e-ibsp-rna": "RNA-seq bone-met vs non-bone mCRPC: IBSP log2FC +7.8 (p<0.0001); bone-specific, ~227x induction.",
                    "e-ibsp-ihc": "IHC on bone mets: strong IBSP protein in osteotropic tumor cells; absent in normal prostate.",
                    "e-av-integrin": "IBSP acts via alpha-v integrins (avb3/avb6); PERSEUS Phase II abituzumab (pan-av) showed reduced skeletal burden in high-bone-lesion mCRPC pts.",
                    "e-perseus": "Abituzumab randomized Phase II (NCT01360840) differential benefit on bone lesions; not approved.",
                }
            },
        ),
        Candidate(
            candidate_id="ASST-CCL2-MAB",
            hypothesis="CCL2/CCR2 axis recruits pro-tumor macrophages in TME",
            modality="mAb",
            indication="solid tumor (TME)",
            evidence=(_ev("e-ccl2-rna"), _ev("e-ccr2-mouse")),
            attributes={
                "evidence_content": {
                    "e-ccl2-rna": "Bulk RNA-seq: CCL2 high in TAM-rich tumors; correlates with CD68 infiltration (rho 0.6).",
                    "e-ccr2-mouse": "CCR2-KO or antagonist reduces TAM infiltration and slows growth in syngeneic models.",
                }
            },
        ),
        Candidate(
            candidate_id="ASST-MAGEA12-TCELL",
            hypothesis="MAGEA12 (cancer-testis antigen) is targetable with engineered T cells",
            modality="cell therapy / TCR",
            indication="CT antigen-high solid tumors",
            evidence=(_ev("e-magea12-cta"),),
            attributes={
                "evidence_content": {
                    "e-magea12-cta": "MAGEA12 is a cancer-testis antigen: expressed in multiple solid tumors, absent in normal adult tissue except testis.",
                }
            },
        ),
        Candidate(
            candidate_id="ASST-KRASG12C-ADC",
            hypothesis="KRAS(G12C) covalent inhibition achieves target engagement and tumor regression",
            modality="small molecule",
            indication="KRAS(G12C) mutant NSCLC",
            evidence=(_ev("e-kras-elisa"), _ev("e-kras-xeno")),
            attributes={
                "evidence_content": {
                    "e-kras-elisa": "Covalent occupancy KD 0.4 nM; on-target selectivity vs G12D (88% vs 31% at 1 uM).",
                    "e-kras-xeno": "NCI-H358 xenograft: tumor regression at tolerated doses; clinical class already validated (sotorasib/adagrasib approved).",
                }
            },
        ),
        Candidate(
            candidate_id="ASST-SPP1-FIBRO",
            hypothesis="Secreted SPP1 mediates fibrosis-associated immune escape",
            modality="mAb / decoy",
            indication="fibrotic tumor / hepatic mets",
            evidence=(_ev("e-spp1-fibro"),),
            attributes={
                "evidence_content": {
                    "e-spp1-fibro": "SPP1 elevated in fibrotic tumor stroma; anti-SPP1 reduces collagen and increases T-cell infiltration preclinically (low N).",
                }
            },
        ),
    )


def main() -> int:
    args = sys.argv[1:]
    select: set[str] | None = None
    for a in args:
        if a.startswith("--candidates="):
            raw = a.split("=", 1)[1]
            select = {x.strip() for x in raw.split(",") if x.strip()}

    pool = _pool()
    if select is not None and select != {"all"}:
        pool = tuple(c for c in pool if c.candidate_id in select)

    print(f"prioritization dry run  pool_size={len(pool)}  mode=env(FOUNDRY_SEAT_MODEL)")
    for c in pool:
        print(f"  - {c.candidate_id}: {c.hypothesis} [{c.modality}] (ev={len(c.evidence)})")
    print()

    result = PrioritizationCouncil(pool, max_rounds=3).run()

    print("=== RANKED SHORTLIST ===")
    for i, r in enumerate(result.shortlist.ranks, 1):
        ax = " ".join(f"{s.axis.value}={s.value}" for s in r.axis_scores)
        print(
            f"{i:2d}. {r.candidate_id:<18} {r.tier.value:<12} composite={r.composite} "
            f"conf={r.confidence:.2f} | {ax}"
        )
    print()
    print("debate rounds used:", result.shortlist.debate_rounds_used)
    print("consensus:", result.shortlist.consensus_note)
    print("packet_digest:", result.shortlist.packet_digest)
    print()
    print("=== per-seat opinions (first candidate) ===")
    for op in result.opinions:
        if op.candidate_id == result.shortlist.ranks[0].candidate_id:
            print(f"  {op.seat_id}: { {s.axis.value: s.value for s in op.axis_scores} } conf={op.confidence:.2f}")

    out_dir = ROOT / "live_runs"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / "prioritization_packet.json"
    payload = result.packet.model_dump()
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\npacket written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
