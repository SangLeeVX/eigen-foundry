"""M7-C6 — Portfolio and Opportunity Radar.

The portfolio loop surfaces decision-useful, non-authoritative views over the
full Program set so a human can allocate capacity, eyes, and capital. It does
NOT decide or execute anything: every output is a passive, recomputable view
over the frozen Program records (MODEL_PREDICTION-class; never satisfies a gate
or advances a Program).

Views (per the production implementation plan, Increment 5):
  - WIP / capacity   : active workstreams vs a configured cap.
  - Correlated risk  : Programs that share route / indication / target cluster.
  - Reserves         : capital allocation suggestion with a reserve buffer.
  - Catalysts        : near-term decision points (gate decisions, expiring
                       conditions/holds) needing attention.
  - Expiries         : expiring conditions/holds and upstream stability flags.
  - Upstream stability: Programs with stale open conditions or missing coverage.

All scores/views are recomputable and evidence-bound to the input Program
records; nothing here mutates state.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import (
    Disposition,
    ProgramRecord,
    ProgramStage,
    ProgramStatus,
    Route,
    utc_now,
)


@dataclass(frozen=True)
class RadarConfig:
    """Bounded knobs for the portfolio views (all non-authoritative)."""

    max_wip_programs: int = 12
    reserve_ratio: float = 0.20  # fraction of committed capital kept as reserve
    correlated_risk_threshold: int = 2  # share of a cluster > this = correlated
    catalyst_days: int = 30  # upcoming decision window
    expiry_days: int = 14  # expiring-condition window


@dataclass(frozen=True)
class CapacityView:
    active_programs: int
    active_workstreams: int
    capacity_used: float  # 0..1 (active / max)
    over_capacity: bool


@dataclass(frozen=True)
class CorrelatedRiskView:
    clusters: tuple[tuple[str, ...], ...]  # groups of correlated program ids
    correlated_programs: frozenset[str]

    @property
    def has_correlated_risk(self) -> bool:
        return bool(self.clusters)


@dataclass(frozen=True)
class ReservesView:
    committed_capital: float
    reserve_buffer: float
    available_capital: float
    note: str


@dataclass(frozen=True)
class Catalyst:
    program_id: str
    kind: str  # GATE | CONDITION | HOLD | EXPIRY
    due_at: str
    detail: str


@dataclass(frozen=True)
class ExpiryItem:
    program_id: str
    kind: str  # CONDITION | HOLD | CASE
    expires_at: str
    detail: str


@dataclass(frozen=True)
class UpstreamStabilityItem:
    program_id: str
    kind: str
    detail: str


@dataclass(frozen=True)
class PortfolioRadarReport:
    generated_at: str
    capacity: CapacityView
    correlated_risk: CorrelatedRiskView
    reserves: ReservesView
    catalysts: tuple[Catalyst, ...]
    expiries: tuple[ExpiryItem, ...]
    upstream_stability: tuple[UpstreamStabilityItem, ...]
    program_count: int
    report_digest: str


def _digest(payload: dict[str, Any]) -> str:
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return f"sha256:{hashlib.sha256(canon.encode()).hexdigest()}"


class PortfolioRadar:
    """Recomputes read-only portfolio views over a set of Programs."""

    def __init__(self, programs: tuple[ProgramRecord, ...], config: RadarConfig | None = None) -> None:
        self.programs = programs
        self.config = config or RadarConfig()

    # ------------------------------------------------------------------ views

    def capacity(self) -> CapacityView:
        active = [p for p in self.programs if p.status not in (ProgramStatus.TERMINATED, ProgramStatus.COMPLETED)]
        workstreams = sum(len(p.active_workstreams) for p in active)
        used = len(active) / self.config.max_wip_programs if self.config.max_wip_programs else 1.0
        return CapacityView(
            active_programs=len(active),
            active_workstreams=workstreams,
            capacity_used=min(1.0, used),
            over_capacity=len(active) > self.config.max_wip_programs,
        )

    def correlated_risk(self) -> CorrelatedRiskView:
        """Group Programs that share a route (and/or the same indication/target
        markers) into clusters; report clusters above the correlation threshold."""
        buckets: dict[str, list[str]] = {}
        for p in self.programs:
            key = f"{p.route.value}:{p.title.lower().split(' ')[0]}" if p.route is not Route.UNSELECTED else "unselected"
            buckets.setdefault(key, []).append(p.program_id)
        clusters: list[tuple[str, ...]] = []
        for ids in buckets.values():
            if len(ids) >= self.config.correlated_risk_threshold:
                clusters.append(tuple(ids))
        correlated = frozenset(i for c in clusters for i in c)
        return CorrelatedRiskView(clusters=tuple(clusters), correlated_programs=correlated)

    def reserves(self, committed_capital: float = 0.0) -> ReservesView:
        reserve = committed_capital * self.config.reserve_ratio
        return ReservesView(
            committed_capital=committed_capital,
            reserve_buffer=round(reserve, 2),
            available_capital=round(committed_capital - reserve, 2),
            note=(
                f"Keep a {self.config.reserve_ratio:.0%} reserve ({reserve:.0f}) for "
                "correlated-risk and upstream-stability draws; deploy the rest."
            ),
        )

    def catalysts(self, now: datetime | None = None) -> tuple[Catalyst, ...]:
        now = now or utc_now()
        out: list[Catalyst] = []
        for p in self.programs:
            if p.last_gate_decision_id:
                # A recent gate decision is an upcoming catalyst (next action).
                out.append(
                    Catalyst(
                        program_id=p.program_id,
                        kind="GATE",
                        due_at=p.last_gate_decision_id,
                        detail=f"gate decided ({p.stage.value}); next decisive action due",
                    )
                )
            for cond in p.open_conditions:
                if 0 <= (cond.expiry - now).total_seconds() <= self.config.catalyst_days * 86400:
                    out.append(
                        Catalyst(
                            program_id=p.program_id,
                            kind="CONDITION",
                            due_at=cond.expiry.isoformat(),
                            detail=f"condition expires {cond.expiry:%Y-%m-%d} (owner {cond.owner})",
                        )
                    )
            if p.hold_expiry and 0 <= (p.hold_expiry - now).total_seconds() <= self.config.catalyst_days * 86400:
                out.append(
                    Catalyst(
                        program_id=p.program_id,
                        kind="HOLD",
                        due_at=p.hold_expiry.isoformat(),
                        detail=f"hold expires {p.hold_expiry:%Y-%m-%d}",
                    )
                )
        out.sort(key=lambda c: c.due_at)
        return tuple(out)

    def expiries(self, now: datetime | None = None) -> tuple[ExpiryItem, ...]:
        now = now or utc_now()
        out: list[ExpiryItem] = []
        for p in self.programs:
            for cond in p.open_conditions:
                if (cond.expiry - now).total_seconds() <= self.config.expiry_days * 86400:
                    out.append(
                        ExpiryItem(
                            program_id=p.program_id,
                            kind="CONDITION",
                            expires_at=cond.expiry.isoformat(),
                            detail=f"condition for owner {cond.owner} expiring",
                        )
                    )
            if p.hold_expiry and (p.hold_expiry - now).total_seconds() <= self.config.expiry_days * 86400:
                out.append(
                    ExpiryItem(
                        program_id=p.program_id,
                        kind="HOLD",
                        expires_at=p.hold_expiry.isoformat(),
                        detail="hold expiring",
                    )
                )
        out.sort(key=lambda e: e.expires_at)
        return tuple(out)

    def upstream_stability(self) -> tuple[UpstreamStabilityItem, ...]:
        out: list[UpstreamStabilityItem] = []
        for p in self.programs:
            if not p.falsifiers:
                out.append(
                    UpstreamStabilityItem(p.program_id, "FALSIFIER", "no falsifier recorded — upstream weak")
                )
            if not p.kill_criteria:
                out.append(
                    UpstreamStabilityItem(p.program_id, "KILL", "no kill criteria recorded")
                )
            if p.status is ProgramStatus.DRAFT and p.route is Route.UNSELECTED and p.stage is ProgramStage.F0:
                out.append(
                    UpstreamStabilityItem(p.program_id, "ROUTE", "F0 route unselected (expected until F5)")
                )
        return tuple(out)

    def report(self, *, committed_capital: float = 0.0) -> PortfolioRadarReport:
        cap = self.capacity()
        corr = self.correlated_risk()
        res = self.reserves(committed_capital)
        cats = self.catalysts()
        exp = self.expiries()
        stab = self.upstream_stability()
        payload = {
            "capacity": {"active": cap.active_programs, "workstreams": cap.active_workstreams,
                         "used": cap.capacity_used, "over": cap.over_capacity},
            "correlated_risk": {"clusters": [list(c) for c in corr.clusters],
                                "programs": sorted(corr.correlated_programs)},
            "reserves": {"committed": res.committed_capital, "reserve": res.reserve_buffer,
                         "available": res.available_capital},
            "catalysts": [{"pid": c.program_id, "kind": c.kind, "due": c.due_at} for c in cats],
            "expiries": [{"pid": e.program_id, "kind": e.kind, "at": e.expires_at} for e in exp],
            "upstream": [{"pid": s.program_id, "kind": s.kind, "detail": s.detail} for s in stab],
            "program_count": len(self.programs),
            "note": "Portfolio view only; not authoritative, cannot satisfy a gate or advance a Program.",
        }
        return PortfolioRadarReport(
            generated_at=utc_now().isoformat(),
            capacity=cap,
            correlated_risk=corr,
            reserves=res,
            catalysts=cats,
            expiries=exp,
            upstream_stability=stab,
            program_count=len(self.programs),
            report_digest=_digest(payload),
        )


__all__ = [
    "CapacityView",
    "Catalyst",
    "CorrelatedRiskView",
    "ExpiryItem",
    "PortfolioRadar",
    "PortfolioRadarReport",
    "RadarConfig",
    "ReservesView",
    "UpstreamStabilityItem",
]
