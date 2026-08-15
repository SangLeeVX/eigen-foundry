"""M9 — real datasource connector (Sentinel + EigenField grounding).

Connects the governed Foundry to REAL inputs so the dry run is realistic:

  - CRC evidence events come from GSE162256 (actual differential-expression
    study of drug treatments) — each treatment row becomes a versioned Sentinel
    evidence event.
  - Grounding reads REAL EigenField data (patient_expression / clinical trials)
    so the Steward grounds predictions in actual supportive/contradictory
    evidence rather than mocks.

The connector is idempotent (exactly-once ingestion), versioned, and never
decides scientific implication — it only feeds versioned evidence events into
the Sentinel and lets the governed Crucible/gates interpret them.

Paths are defaulted to the workspace's real data; override for deployment.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .m5_models import SentinelEvent, SentinelEventKind
from .models import SnapshotRef
from .sentinel import Sentinel, canonical_payload_digest


def _sha(x: str) -> str:
    return f"sha256:{hashlib.sha256(x.encode()).hexdigest()}"


class EigenFieldReader:
    """Reads real grounding evidence from the EigenField DuckDB (read-only)."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = db_path or os.environ.get(
            "EIGENFIELD_DB", "/home/ubuntu/.openclaw/workspace/snapshots/eigenfield_v58.0.0.duckdb"
        )

    def top_expressed_genes(self, gene: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Query real patient_expression for a gene (or top genes)."""
        import duckdb

        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            if gene:
                rows = con.execute(
                    "SELECT patient_id, gene, expr_state, value FROM patient_expression "
                    "WHERE gene = ? ORDER BY value DESC LIMIT ?",
                    [gene, limit],
                ).fetchall()
                cols = ["patient_id", "gene", "expr_state", "value"]
            else:
                rows = con.execute(
                    "SELECT gene, COUNT(DISTINCT patient_id) AS n, AVG(value) AS mean_expr "
                    "FROM patient_expression GROUP BY gene ORDER BY mean_expr DESC LIMIT ?",
                    [limit],
                ).fetchall()
                cols = ["gene", "patient_count", "mean_expression"]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            con.close()

    def clinical_trial_evidence(self, condition: str = "colorectal", limit: int = 10) -> list[dict[str, Any]]:
        import duckdb

        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            rows = con.execute(
                "SELECT * FROM l13_trial WHERE data_source = 'trial_evidence' LIMIT ?",
                [limit],
            ).fetchall()
            cols = ["source", "target", "weight_val", "data_source"]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            con.close()


class GSEEvidenceSource:
    """Reads real GSE162256 CRC differential-expression evidence rows."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(
            path
            or os.environ.get(
                "GSE162256_DE",
                "/home/ubuntu/.openclaw/workspace/GSE162256/GSE162256_DE_summary.csv",
            )
        )

    def read_rows(self) -> list[dict[str, Any]]:
        with self.path.open(newline="") as f:
            reader = csv.DictReader(f)
            return [dict(r) for r in reader]


@dataclass
class ConnectorReport:
    source: str
    events_ingested: int = 0
    events_updated: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "events_ingested": self.events_ingested,
            "events_updated": self.events_updated,
            "harness_only": True,
        }


class DatasourceConnector:
    """Ingests real evidence into the Sentinel (idempotent, exactly-once)."""

    def __init__(
        self,
        sentinel: Sentinel,
        *,
        gse_path: str | Path | None = None,
        eigenfield_db: str | Path | None = None,
        program_id: str = "CRC-DRY",
        seed: int = 7,
    ) -> None:
        self.sentinel = sentinel
        self.gse = GSEEvidenceSource(gse_path)
        self.ef = EigenFieldReader(eigenfield_db)
        self.program_id = program_id
        self.seed = seed

    def ingest_crc_evidence(self) -> ConnectorReport:
        """Ingest each GSE162256 treatment row as a versioned Sentinel event."""
        report = ConnectorReport(source="GSE162256_DE_summary.csv")
        seen = set()
        for row in self.gse.read_rows():
            treatment = row.get("Treatment", "UNK")
            if not treatment or treatment in seen:
                continue
            seen.add(treatment)
            payload = {
                "dataset": "GSE162256",
                "treatment": treatment,
                "n_control": row.get("N_control"),
                "n_treat": row.get("N_treat"),
                "sig_genes": int(row.get("Sig_genes", 0)),
                "total_genes": int(row.get("Total_genes", 0)),
                "perc_sig": float(row.get("Perc_sig", 0)),
            }
            event = self.sentinel.ingest(payload, program_id=self.program_id)
            self.sentinel.map_to_program(event)
            report.events_ingested += 1
        return report

    def ground_prediction(self, *, prediction_id: str, prediction_digest: str, gene: str) -> dict[str, Any]:
        """Ground an Eigen-1 prediction against REAL EigenField expression data."""
        rows = self.ef.top_expressed_genes(gene=gene, limit=10)
        return {
            "prediction_id": prediction_id,
            "prediction_digest": prediction_digest,
            "gene": gene,
            "grounding_source": "patient_expression",
            "evidence_rows": rows,
            "evidence_count": len(rows),
            "harness_only": True,
        }
