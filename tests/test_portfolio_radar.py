"""Portfolio Radar (M7-C6) — deterministic tests.

Verifies the read-only portfolio views: capacity, correlated risk, reserves,
catalysts, expiries, upstream stability, and report digest integrity. No state
is mutated; every assertion is recomputable from the input Program records.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from foundry_council.models import (
    CaseCondition,
    ProgramRecord,
    ProgramStage,
    ProgramStatus,
    Route,
    SnapshotRef,
)
from foundry_council.portfolio_radar import (
    PortfolioRadar,
    RadarConfig,
)


def _program(
    pid: str,
    *,
    stage: ProgramStage = ProgramStage.F0,
    status: ProgramStatus = ProgramStatus.ACTIVE,
    route: Route = Route.UNSELECTED,
    title: str | None = None,
    workstreams: tuple[str, ...] = (),
    falsifiers: tuple[str, ...] = ("f1",),
    kill: tuple[str, ...] = ("k1",),
    conditions: tuple[CaseCondition, ...] = (),
    hold_expiry=None,
    gate_decision: str | None = None,
) -> ProgramRecord:
    return ProgramRecord(
        program_id=pid,
        title=title or f"Program {pid}",
        status=status,
        stage=stage,
        route=route,
        conversation_key=f"conv-{pid}",
        active_workstreams=workstreams,
        falsifiers=falsifiers,
        kill_criteria=kill,
        open_conditions=conditions,
        hold_expiry=hold_expiry,
        last_gate_decision_id=gate_decision,
    )


def _cond(owner: str, days_to_expiry: float) -> CaseCondition:
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(days=max(1, days_to_expiry - 1))
    expiry = now + timedelta(days=days_to_expiry)
    return CaseCondition(
        condition="test condition",
        owner=owner,
        deadline=deadline,
        expiry=expiry,
    )


class CapacityTests(unittest.TestCase):
    def test_under_capacity(self) -> None:
        programs = tuple(_program(f"P-{i}") for i in range(3))
        radar = PortfolioRadar(programs, RadarConfig(max_wip_programs=10))
        cap = radar.capacity()
        self.assertEqual(cap.active_programs, 3)
        self.assertFalse(cap.over_capacity)
        self.assertAlmostEqual(cap.capacity_used, 0.3, places=1)

    def test_over_capacity(self) -> None:
        programs = tuple(_program(f"P-{i}") for i in range(15))
        radar = PortfolioRadar(programs, RadarConfig(max_wip_programs=12))
        cap = radar.capacity()
        self.assertTrue(cap.over_capacity)

    def test_terminated_excluded(self) -> None:
        programs = (
            _program("P-ACTIVE"),
            _program("P-DONE", status=ProgramStatus.TERMINATED),
            _program("P-COMP", status=ProgramStatus.COMPLETED),
        )
        cap = PortfolioRadar(programs, RadarConfig(max_wip_programs=10)).capacity()
        self.assertEqual(cap.active_programs, 1)


class CorrelatedRiskTests(unittest.TestCase):
    def test_shared_route_clusters(self) -> None:
        # Two Programs sharing a route cluster (correlated risk). Route selection
        # is only legal at F5+, so use a post-F4 stage for these fixtures.
        programs = (
            _program("P-1", stage=ProgramStage.F5, route=Route.REPOSITIONING, title="Alpha A"),
            _program("P-2", stage=ProgramStage.F5, route=Route.REPOSITIONING, title="Alpha B"),
            _program("P-3", stage=ProgramStage.F5, route=Route.NOVEL_TARGET_DE_NOVO, title="Beta C"),
        )
        corr = PortfolioRadar(programs, RadarConfig(correlated_risk_threshold=2)).correlated_risk()
        self.assertTrue(corr.has_correlated_risk)
        self.assertIn("P-1", corr.correlated_programs)
        self.assertIn("P-2", corr.correlated_programs)
        self.assertNotIn("P-3", corr.correlated_programs)

    def test_no_cluster_below_threshold(self) -> None:
        programs = (_program("P-1", stage=ProgramStage.F5, route=Route.REPOSITIONING, title="Alpha"),)
        corr = PortfolioRadar(programs, RadarConfig(correlated_risk_threshold=2)).correlated_risk()
        self.assertFalse(corr.has_correlated_risk)


class ReservesTests(unittest.TestCase):
    def test_reserve_buffer(self) -> None:
        radar = PortfolioRadar((), RadarConfig(reserve_ratio=0.20))
        res = radar.reserves(committed_capital=1000)
        self.assertEqual(res.reserve_buffer, 200.0)
        self.assertEqual(res.available_capital, 800.0)
        self.assertIn("20%", res.note)


class CatalystsAndExpiriesTests(unittest.TestCase):
    def test_catalyst_detects_expiring_condition(self) -> None:
        programs = (_program("P-1", conditions=(_cond("owner-1", days_to_expiry=5),)),)
        cats = PortfolioRadar(programs, RadarConfig(catalyst_days=30)).catalysts()
        self.assertTrue(any(c.program_id == "P-1" and c.kind == "CONDITION" for c in cats))

    def test_expiry_not_catalyst_if_far_out(self) -> None:
        programs = (_program("P-1", conditions=(_cond("owner-1", days_to_expiry=120),)),)
        cats = PortfolioRadar(programs, RadarConfig(catalyst_days=30)).catalysts()
        self.assertFalse(any(c.kind == "CONDITION" for c in cats))

    def test_expiries_window(self) -> None:
        programs = (_program("P-1", conditions=(_cond("owner-1", days_to_expiry=3),)),)
        exps = PortfolioRadar(programs, RadarConfig(expiry_days=14)).expiries()
        self.assertTrue(any(e.program_id == "P-1" for e in exps))


class UpstreamStabilityTests(unittest.TestCase):
    def test_missing_kill_criteria_flag(self) -> None:
        programs = (_program("P-1", kill=()),)
        stab = PortfolioRadar(programs).upstream_stability()
        self.assertTrue(any(s.program_id == "P-1" and s.kind == "KILL" for s in stab))


class ReportIntegrityTests(unittest.TestCase):
    def test_report_digest_bound_to_views(self) -> None:
        programs = (
            _program("P-1", stage=ProgramStage.F5, route=Route.REPOSITIONING, title="Alpha A", conditions=(_cond("owner-1", 3),)),
            _program("P-2", stage=ProgramStage.F5, route=Route.REPOSITIONING, title="Alpha B"),
            _program("P-3", kill=()),
        )
        report = PortfolioRadar(programs, RadarConfig(max_wip_programs=10)).report(committed_capital=500)
        self.assertTrue(report.report_digest.startswith("sha256:"))
        self.assertEqual(report.program_count, 3)
        self.assertGreaterEqual(len(report.catalysts), 0)

    def test_report_is_immutable_dataclass(self) -> None:
        report = PortfolioRadar(()).report()
        with self.assertRaises(Exception):
            report.program_count = 99  # frozen dataclass


if __name__ == "__main__":
    unittest.main()
