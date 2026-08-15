from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from foundry_council.errors import Forbidden, PolicyConfigurationRequired, StateConflict
from foundry_council.runtime_guard import (
    Budget,
    BudgetExceeded,
    KillSwitch,
    Lease,
    ProtectedPathGuard,
    ReplayGuard,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LeaseTests(unittest.TestCase):
    """M1-C11: lease acquisition and expiry fence the run."""

    def test_material_lease_not_expired(self) -> None:
        lease = Lease(
            run_id="run-a",
            aggregate_type="COUNCIL_SESSION",
            aggregate_id="session-x",
            acquired_at=_now(),
            expires_at=_now() + timedelta(hours=1),
        )
        self.assertFalse(lease.expired)

    def test_expired_lease_fences_the_run(self) -> None:
        lease = Lease(
            run_id="run-a",
            aggregate_type="COUNCIL_SESSION",
            aggregate_id="session-x",
            acquired_at=_now() - timedelta(hours=2),
            expires_at=_now() - timedelta(hours=1),
        )
        self.assertTrue(lease.expired)


class KillSwitchTests(unittest.TestCase):
    """M1-C12: an engaged kill switch refuses further processing and never approves."""

    def test_armed_switch_allows_operation(self) -> None:
        switch = KillSwitch(armed=True)
        switch.ensure_operational()  # should not raise

    def test_tripped_switch_refuses_operation(self) -> None:
        switch = KillSwitch(armed=True)
        switch.trip("detected injection pattern")
        with self.assertRaises(Forbidden):
            switch.ensure_operational()
        self.assertFalse(switch.armed)

    def test_tripped_switch_never_confers_authority(self) -> None:
        switch = KillSwitch(armed=True)
        switch.trip("security hold")
        # Trip must refuse, not approve anything.
        with self.assertRaises(Forbidden):
            switch.ensure_operational()


class BudgetTests(unittest.TestCase):
    """M1-C12: budget ceilings halt runaway processing deterministically."""

    def test_operation_ceiling_fails_closed(self) -> None:
        budget = Budget(max_operations=3, max_events=10, max_seconds=60)
        budget.check()
        budget.check()
        budget.check()
        with self.assertRaises(BudgetExceeded):
            budget.check()

    def test_event_ceiling_fails_closed(self) -> None:
        budget = Budget(max_operations=10, max_events=2, max_seconds=60)
        budget.check(event_cost=2)
        with self.assertRaises(BudgetExceeded):
            budget.check(event_cost=1)

    def test_wall_clock_ceiling_fails_closed(self) -> None:
        budget = Budget(max_operations=10, max_events=10, max_seconds=0.000001)
        with self.assertRaises(BudgetExceeded):
            budget.check()


class ReplayGuardTests(unittest.TestCase):
    """M1-C11: digested idempotency prevents duplicate application after a crash/resume."""

    def test_digest_identical_replay_is_idempotent(self) -> None:
        guard = ReplayGuard()
        guard.mark("cmd-1", "digest-a")
        self.assertTrue(guard.already_applied("cmd-1", "digest-a"))
        self.assertFalse(guard.conflicted("cmd-1", "digest-a"))

    def test_differing_body_same_key_is_a_conflict_not_a_pass(self) -> None:
        guard = ReplayGuard()
        guard.mark("cmd-1", "digest-a")
        self.assertFalse(guard.already_applied("cmd-1", "digest-b"))
        self.assertTrue(guard.conflicted("cmd-1", "digest-b"))

    def test_fresh_key_is_not_a_replay(self) -> None:
        guard = ReplayGuard()
        self.assertFalse(guard.already_applied("cmd-new", "digest-x"))


class ProtectedPathGuardTests(unittest.TestCase):
    """M1-C12: protected paths refuse actors without the protected operator role."""

    def test_protected_action_refused_for_ordinary_run(self) -> None:
        guard = ProtectedPathGuard(allowed_roles=frozenset({"forged_role"}))
        with self.assertRaises(Forbidden):
            guard.ensure_permitted("approve", frozenset({"program_drafter"}))

    def test_protected_action_refused_when_no_role_authorizes(self) -> None:
        guard = ProtectedPathGuard(allowed_roles=frozenset())
        with self.assertRaises(PolicyConfigurationRequired):
            guard.ensure_permitted("commit_gate", frozenset({"ledger_committer"}))

    def test_unprotected_action_permitted(self) -> None:
        guard = ProtectedPathGuard(allowed_roles=frozenset({"program_drafter"}))
        guard.ensure_permitted("create_program_draft", frozenset({"program_drafter"}))


if __name__ == "__main__":
    unittest.main()
