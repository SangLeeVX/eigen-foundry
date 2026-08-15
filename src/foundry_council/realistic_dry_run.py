"""M9 — realistic governed F0–F12 dry run on REAL CRC evidence.

Composes the governed pipeline (StageRunner) with REAL inputs:

  1. Ingest real GSE162256 CRC differential-expression evidence into the
     Sentinel via DatasourceConnector.
  2. Ground decisive claims against REAL EigenField patient_expression via the
     Steward — never upgrading MODEL_PREDICTION.
  3. Run the governed F0->F12 stage runner with a real-evidence hook so each
     gate's decisive claim states reflect the actual ingested/grounded data
     (OBSERVED only where real observed/grounded evidence exists).
  4. Emit a transferable package with real data provenance (dataset, grounding
     source, gene set).

The run is a governed dry run: formal stage changes still flow only through the
restricted commit path, and it never implies a real therapeutic outcome.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Any

from .datasource_connector import DatasourceConnector
from .m5_models import QCStatus  # noqa: F401  (kept for intent)
from .models import ClaimState, Route
from .sentinel import Sentinel
from .stage_runner import StageRunResult, StageRunner
from .work_order_service import MemoryWorkOrderStore, WorkOrderService


@dataclass
class RealisticDryRunResult:
    program_id: str
    route: str
    sources: dict[str, Any] = field(default_factory=dict)
    events_ingested: int = 0
    grounding_genes: list[str] = field(default_factory=list)
    stage_result: dict[str, Any] = field(default_factory=dict)
    transferable_package_digest: str | None = None
    sequence_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "route": self.route,
            "sources": self.sources,
            "events_ingested": self.events_ingested,
            "grounding_genes": self.grounding_genes,
            "stage_result": self.stage_result,
            "transferable_package_digest": self.transferable_package_digest,
            "sequence_complete": self.sequence_complete,
            "harness_only": True,
            "real_therapeutic_advance": False,
            "real_data_provenance": True,
        }


# CRC-relevant genes to ground decisive claims against real EigenField data.
_GROUNDING_GENES = ["TP53", "KRAS", "APC", "PIK3CA", "SMAD4", "BRAF"]


class _EventStore:
    def __init__(self) -> None:
        self.e: dict[str, Any] = {}

    def save_event(self, event):
        self.e[event.event_id] = event
        return event

    def get_event(self, event_id):
        return self.e.get(event_id)

    def list_events(self):
        return tuple(self.e.values())


class RealisticDryRun:
    """Governed F0-F12 dry run consuming real GSE162256 + EigenField evidence."""

    def __init__(
        self,
        sqlite_path: str | None = None,
        *,
        route: Route = Route.NOVEL_TARGET_DE_NOVO,
        seed: int = 7,
        gse_path: str | None = None,
        eigenfield_db: str | None = None,
        grounding_genes: list[str] | None = None,
        lifecycle: bool = True,
    ) -> None:
        from .models import Route as _Rt

        # Coerce a string route to the Route enum for convenience.
        if isinstance(route, str):
            route = _Rt(route)
        self.sqlite_path = sqlite_path or tempfile.mktemp(suffix=".db")
        self.route = route
        self.seed = seed
        self.lifecycle = lifecycle
        self.gse_path = gse_path
        self.eigenfield_db = eigenfield_db
        self.grounding_genes = grounding_genes or _GROUNDING_GENES
        self._store = _EventStore()
        self._sentinel = Sentinel(self._store)
        self._connector = DatasourceConnector(
            self._sentinel,
            gse_path=gse_path,
            eigenfield_db=eigenfield_db,
            program_id="CRC-DRY",
            seed=seed,
        )
        self.sources: dict[str, Any] = {}

    def _real_evidence_hook(self):
        """Produce decisive claim states from REAL evidence per stage.

        A stage is admissible (OBSERVED) only when real GSE162256 events were
        ingested AND the grounding query against real EigenField returned data.
        If real data is absent, we fail closed (UNKNOWN, blocking the gate) so
        the dry run never fabricates evidence.
        """
        events_ok = self.sources.get("gse162256", {}).get("ok", False)
        grounding_ok = self.sources.get("eigenfield", {}).get("ok", False)

        def hook(stage):
            # Real observed evidence present -> decisive claim is OBSERVED.
            if events_ok and grounding_ok:
                return [ClaimState.OBSERVED, ClaimState.SUPPORTED_INFERENCE]
            # No real data -> fail closed (model prediction / unknown cannot pass).
            return [ClaimState.MODEL_PREDICTION, ClaimState.UNKNOWN]

        return hook

    def run(self) -> RealisticDryRunResult:
        # 1. Ingest real CRC evidence.
        report = self._connector.ingest_crc_evidence()
        self.sources["gse162256"] = {"ok": report.events_ingested > 0, "events": report.events_ingested}

        # 2. Ground predictions against real EigenField genes (only those present).
        grounded = []
        present = 0
        for gene in self.grounding_genes:
            try:
                g = self._connector.ground_prediction(
                    prediction_id=f"{self.route.value}-g-{gene}", prediction_digest="real", gene=gene
                )
                grounded.append({"gene": gene, "evidence_rows": g["evidence_count"]})
                if g["evidence_count"] > 0:
                    present += 1
            except Exception:  # noqa: BLE001 - individual gene unavailable
                grounded.append({"gene": gene, "evidence_rows": 0})
        # The pipeline is grounded if AT LEAST ONE real gene has evidence.
        grounding_ok = present >= 1
        self.sources["eigenfield"] = {"ok": grounding_ok, "genes": grounded}

        # 3. Run the governed pipeline with the real-evidence hook.
        runner = StageRunner(
            self.sqlite_path,
            route=self.route,
            seed=self.seed,
            lifecycle=self.lifecycle,
            evidence_hook=self._real_evidence_hook(),
        )
        result = runner.run()

        # 4. Emit transferable package with real provenance.
        return RealisticDryRunResult(
            program_id=runner.program.program_id,
            route=self.route.value,
            sources=self.sources,
            events_ingested=report.events_ingested,
            grounding_genes=[g["gene"] for g in grounded],
            stage_result=result.to_dict(),
            transferable_package_digest=result.transferable_package_digest,
            sequence_complete=result.sequence_complete,
        )
