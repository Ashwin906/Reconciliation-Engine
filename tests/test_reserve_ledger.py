from datetime import date

import pytest

from reserve.ledger import (
    ReserveHold,
    outstanding_balance,
    overdue_holds,
    reserve_delta,
    run_periods,
    schedule_hold,
    settle_due,
)

JAN31 = date(2026, 1, 31)


class TestScheduleHold:
    def test_computes_reserve_at_the_configured_rate(self):
        hold = schedule_hold("setl_1", 1_000_000, JAN31, reserve_bps=500, hold_days=60)
        assert hold is not None
        assert hold.amount == 50_000  # 5% of Rs 10,000
        assert hold.release_due_on == date(2026, 4, 1)
        assert hold.is_outstanding

    def test_uses_the_projects_own_rounding_not_floor_division(self):
        """Same convention as every other rate in this codebase
        (core.money.apply_rate, ROUND_HALF_UP). FAILURES.md #10 is about
        exactly this going wrong elsewhere."""
        # 333 paise at 5% = 16.65 -> 17 under ROUND_HALF_UP, 16 under floor
        hold = schedule_hold("setl_1", 333, JAN31, reserve_bps=500, hold_days=60)
        assert hold is not None and hold.amount == 17

    def test_negative_settlement_withholds_nothing(self):
        """There is no positive payout to hold a percentage of, and a
        'negative reserve' would invent money owed the wrong way."""
        assert schedule_hold("setl_1", -50_000, JAN31, 500, 60) is None

    def test_a_reserve_rounding_to_zero_is_not_recorded(self):
        assert schedule_hold("setl_1", 1, JAN31, reserve_bps=1, hold_days=60) is None


class TestReleases:
    def test_hold_is_released_once_due(self):
        hold = schedule_hold("setl_1", 1_000_000, JAN31, 500, 60)
        released = settle_due([hold], date(2026, 4, 1))
        assert released[0].released_on == date(2026, 4, 1)
        assert not released[0].is_outstanding

    def test_hold_is_untouched_before_its_due_date(self):
        hold = schedule_hold("setl_1", 1_000_000, JAN31, 500, 60)
        assert settle_due([hold], date(2026, 3, 1))[0].is_outstanding

    def test_release_does_not_mutate_the_original_record(self):
        """An editable reserve ledger has the same problem an editable
        audit trail has -- the record stops being evidence."""
        hold = schedule_hold("setl_1", 1_000_000, JAN31, 500, 60)
        settle_due([hold], date(2026, 4, 1))
        assert hold.released_on is None


class TestBalanceIsAnswerableHistorically:
    def test_balance_excludes_a_release_that_happened_later(self):
        """Asking 'what was my balance on 1 March' must not be changed by
        a release in April."""
        hold = schedule_hold("setl_1", 1_000_000, JAN31, 500, 60)
        after = settle_due([hold], date(2026, 4, 1))
        assert outstanding_balance(after, date(2026, 3, 1)) == 50_000
        assert outstanding_balance(after, date(2026, 4, 2)) == 0

    def test_balance_excludes_a_hold_not_yet_placed(self):
        hold = schedule_hold("setl_1", 1_000_000, JAN31, 500, 60)
        assert outstanding_balance([hold], date(2026, 1, 1)) == 0


class TestReserveDeltaSignIsLoadBearing:
    def test_positive_when_more_is_withheld_than_returned(self):
        hold = schedule_hold("setl_1", 1_000_000, JAN31, 500, 60)
        assert reserve_delta([hold], date(2026, 1, 1), date(2026, 2, 1)) == 50_000

    def test_negative_when_past_holds_come_back(self):
        """The direction a single-period view misreads as an unexplained
        CREDIT rather than a shortfall."""
        hold = schedule_hold("setl_1", 1_000_000, JAN31, 500, 60)
        released = settle_due([hold], date(2026, 4, 1))
        assert reserve_delta(released, date(2026, 4, 1), date(2026, 4, 30)) == -50_000


class TestOverdue:
    def test_hold_past_its_due_date_is_overdue(self):
        hold = schedule_hold("setl_1", 1_000_000, JAN31, 500, 60)
        assert overdue_holds([hold], date(2026, 5, 1)) == [hold]

    def test_a_released_hold_is_never_overdue(self):
        hold = schedule_hold("setl_1", 1_000_000, JAN31, 500, 60)
        released = settle_due([hold], date(2026, 4, 1))
        assert overdue_holds(released, date(2026, 6, 1)) == []


class TestRunPeriods:
    def _periods(self, n: int):
        return [
            (
                f"P{i + 1}",
                date(2026, 1, 1) + __import__("datetime").timedelta(days=30 * i),
                date(2026, 1, 31) + __import__("datetime").timedelta(days=30 * i),
                1_000_000,
                f"setl_{i + 1}",
            )
            for i in range(n)
        ]

    def test_balance_accumulates_then_falls_when_releases_begin(self):
        summaries, _ = run_periods(self._periods(4), reserve_bps=500, hold_days=60)
        assert summaries[0].held == 50_000 and summaries[0].released == 0
        assert summaries[0].closing_balance == 50_000
        assert summaries[1].closing_balance == 100_000
        # by period 3, period 1's hold is due back
        assert summaries[2].released == 50_000
        assert summaries[2].net_delta == 0  # held 50k, released 50k

    def test_a_release_and_a_hold_in_the_same_period_are_both_counted(self):
        """Order is load-bearing: releases settle before the new hold is
        added, so neither masks the other."""
        summaries, _ = run_periods(self._periods(3), reserve_bps=500, hold_days=60)
        third = summaries[2]
        assert third.held == 50_000
        assert third.released == 50_000

    def test_no_hold_is_overdue_while_the_schedule_is_honoured(self):
        summaries, _ = run_periods(self._periods(5), reserve_bps=500, hold_days=60)
        assert all(s.overdue_count == 0 for s in summaries)
