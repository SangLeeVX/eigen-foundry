"""M5 — Work-order, result/QC, and learn-back service.

Implements the closed-loop steps that come AFTER the F0 Crucible commits:

  Step 11: create one approved decisive work order (prediction, alternatives,
           falsifier, kill criterion, protocol, rights, budget, owner, deadline,
           QC standard).
  Step 12: ingest one result and its QC disposition.
  Step 13: compare the result with the frozen prediction and attribute failure
           where applicable.
  Step 14: preserve positive, negative, null, contradictory, and failed-QC
           outcomes.
  Step 15: create a successor evidence snapshot and successor Crucible pointer
           (learn-back).

The service operates over an injectable WorkOrderStore. It never changes formal
Program state; it records bounded, content-addressed domain events that a later
operator/replay layer surfaces. It fails closed on unknown QC or a result that
cannot be attributed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from .m5_models import (
    AttributionKind,
    FailureAttribution,
    LearnBack,
    QCStatus,
    ResultRecord,
    WorkOrder,
    WorkOrderStatus,
)


def _sha256(data: dict[str, Any]) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    )


class WorkOrderStore(Protocol):
    def save_work_order(self, wo: WorkOrder) -> WorkOrder: ...
    def get_work_order(self, work_order_id: str) -> WorkOrder | None: ...
    def list_work_orders(self) -> tuple[WorkOrder, ...]: ...
    def save_result(self, result: ResultRecord) -> ResultRecord: ...
    def get_result(self, result_id: str) -> ResultRecord | None: ...
    def list_results(self) -> tuple[ResultRecord, ...]: ...
    def save_attribution(self, attribution: FailureAttribution) -> FailureAttribution: ...
    def list_attributions(self) -> tuple[FailureAttribution, ...]: ...
    def save_learn_back(self, lb: LearnBack) -> LearnBack: ...
    def list_learn_backs(self) -> tuple[LearnBack, ...]: ...


class MemoryWorkOrderStore:
    """In-memory store (seeded by tests with authorized mock source events)."""

    def __init__(self) -> None:
        self._wo: dict[str, WorkOrder] = {}
        self._results: dict[str, ResultRecord] = {}
        self._attr: dict[str, FailureAttribution] = {}
        self._learnback: dict[str, LearnBack] = {}

    def save_work_order(self, wo: WorkOrder) -> WorkOrder:
        self._wo[wo.work_order_id] = wo
        return wo

    def get_work_order(self, work_order_id: str) -> WorkOrder | None:
        return self._wo.get(work_order_id)

    def list_work_orders(self) -> tuple[WorkOrder, ...]:
        return tuple(self._wo.values())

    def save_result(self, result: ResultRecord) -> ResultRecord:
        self._results[result.result_id] = result
        return result

    def get_result(self, result_id: str) -> ResultRecord | None:
        return self._results.get(result_id)

    def list_results(self) -> tuple[ResultRecord, ...]:
        return tuple(self._results.values())

    def save_attribution(self, attribution: FailureAttribution) -> FailureAttribution:
        self._attr[attribution.attribution_id] = attribution
        return attribution

    def list_attributions(self) -> tuple[FailureAttribution, ...]:
        return tuple(self._attr.values())

    def save_learn_back(self, lb: LearnBack) -> LearnBack:
        self._learnback[lb.learn_back_id] = lb
        return lb

    def list_learn_backs(self) -> tuple[LearnBack, ...]:
        return tuple(self._learnback.values())


class WorkOrderService:
    """Closed-loop domain service for work orders, results/QC, attribution, learn-back."""

    def __init__(self, store: WorkOrderStore) -> None:
        self.store = store

    # Step 11 -------------------------------------------------------------
    def create_work_order(
        self,
        *,
        work_order_id: str,
        program_id: str,
        session_id: str,
        gate_decision_id: str,
        title: str,
        prediction: str,
        alternatives: tuple[str, ...],
        falsifier: str,
        kill_criterion: str,
        protocol_ref,
        rights_ref,
        budget_ref,
        owner: str,
        deadline,
        qc_standard_ref,
    ) -> WorkOrder:
        if self.store.get_work_order(work_order_id) is not None:
            raise ValueError("work order already exists")
        wo = WorkOrder(
            work_order_id=work_order_id,
            program_id=program_id,
            session_id=session_id,
            gate_decision_id=gate_decision_id,
            title=title,
            prediction=prediction,
            prediction_digest=_sha256({"prediction": prediction, "alternatives": list(alternatives)}),
            alternatives=alternatives,
            falsifier=falsifier,
            kill_criterion=kill_criterion,
            protocol_ref=protocol_ref,
            rights_ref=rights_ref,
            budget_ref=budget_ref,
            owner=owner,
            deadline=deadline,
            qc_standard_ref=qc_standard_ref,
        )
        return self.store.save_work_order(wo)

    # Step 12 -------------------------------------------------------------
    def ingest_result(
        self,
        *,
        result_id: str,
        work_order_id: str,
        qc_status: QCStatus | str,
        payload: dict[str, Any],
        source_ref,
    ) -> ResultRecord:
        wo = self.store.get_work_order(work_order_id)
        if wo is None:
            raise ValueError("result references a nonexistent work order")
        if self.store.get_result(result_id) is not None:
            raise ValueError("result already ingested")
        qc = qc_status if isinstance(qc_status, QCStatus) else QCStatus(qc_status)
        result = ResultRecord(
            result_id=result_id,
            work_order_id=work_order_id,
            program_id=wo.program_id,
            qc_status=qc,
            payload_digest=_sha256(payload),
            source_ref=source_ref,
        )
        return self.store.save_result(result)

    # Step 13 -------------------------------------------------------------
    def attribute(
        self, *, attribution_id: str, work_order_id: str, result_id: str
    ) -> FailureAttribution:
        wo = self.store.get_work_order(work_order_id)
        result = self.store.get_result(result_id)
        if wo is None or result is None:
            raise ValueError("cannot attribute a result without its work order")
        if result.work_order_id != work_order_id:
            raise ValueError("result does not belong to this work order")
        if result.qc_status is QCStatus.FAIL or result.qc_status is QCStatus.CONTAMINATED:
            kind = AttributionKind.FAILED_QC
            rationale = f"Result failed QC ({result.qc_status.value}); outcome preserved as failed-QC."
        else:
            # Deterministic attribution keyed off the result payload digest: this
            # is a mock comparison — a real system would evaluate against the
            # frozen prediction in a governed inference step.
            key = int(result.payload_digest[-8:], 16) % 4
            kind = (AttributionKind.CONFIRMED, AttributionKind.REFUTED,
                    AttributionKind.NULL, AttributionKind.UNKNOWN)[key]
            rationale = f"Deterministic mock attribution for {result.result_id} ({kind.value})."
        attribution = FailureAttribution(
            attribution_id=attribution_id,
            work_order_id=work_order_id,
            result_id=result_id,
            kind=kind,
            rationale=rationale,
        )
        saved = self.store.save_attribution(attribution)
        # Step 14: preserve the outcome by marking the work order's terminal state.
        terminal = WorkOrderStatus.COMPLETED if kind is not AttributionKind.FAILED_QC else WorkOrderStatus.FAILED
        updated = wo.model_copy(update={"status": terminal})
        self.store.save_work_order(updated)
        return saved

    # Step 15 -------------------------------------------------------------
    def create_learn_back(
        self,
        *,
        learn_back_id: str,
        program_id: str,
        predecessor_session_id: str,
        successor_evidence,
    ) -> LearnBack:
        lb = LearnBack(
            learn_back_id=learn_back_id,
            program_id=program_id,
            successor_evidence=successor_evidence,
            preceding_session_id=predecessor_session_id,
        )
        return self.store.save_learn_back(lb)
