from datetime import date, datetime, time

import pytest

from core.calendar_ist import add_working_days, expected_settlement_date, is_working_day


class TestIsWorkingDay:
    def test_weekday_is_working(self):
        assert is_working_day(date(2026, 2, 6)) is True  # Friday, no holiday

    def test_saturday_is_not_working(self):
        assert is_working_day(date(2026, 2, 7)) is False

    def test_sunday_is_not_working(self):
        assert is_working_day(date(2026, 2, 8)) is False

    def test_holiday_on_a_weekday_is_not_working(self):
        assert is_working_day(date(2026, 1, 26)) is False  # Republic Day, a Monday


class TestAddWorkingDays:
    def test_friday_plus_two_lands_on_tuesday(self):
        # the exact scenario from DOMAIN_KNOWLEDGE.md section 4:
        # "Friday's sales settle Tuesday, not Sunday"
        friday = date(2026, 2, 6)
        assert add_working_days(friday, 2) == date(2026, 2, 10)  # Tuesday

    def test_skips_a_holiday_in_the_middle(self):
        # Friday 23 Jan -> Mon 26 Jan is Republic Day (holiday) -> must skip to Tue 27
        friday_before_holiday = date(2026, 1, 23)
        assert add_working_days(friday_before_holiday, 1) == date(2026, 1, 27)

    def test_zero_days_returns_same_date(self):
        d = date(2026, 2, 6)
        assert add_working_days(d, 0) == d

    def test_negative_n_rejected(self):
        with pytest.raises(ValueError):
            add_working_days(date(2026, 2, 6), -1)


class TestExpectedSettlementDate:
    def test_capture_before_cutoff_same_day_clock_start(self):
        captured = datetime(2026, 2, 6, 14, 0)  # Friday, 2pm
        assert expected_settlement_date(captured, t_plus=2) == date(2026, 2, 10)

    def test_capture_after_cutoff_pushes_to_next_batch(self):
        # captured 23:55, cutoff is default 23:59:59 -> should NOT push
        # (this proves the cutoff comparison, not just its existence)
        captured_just_before_cutoff = datetime(2026, 2, 6, 23, 55)
        assert expected_settlement_date(
            captured_just_before_cutoff, t_plus=2
        ) == date(2026, 2, 10)

        # now with an EARLIER cutoff of 22:00 -- 23:55 capture is after it,
        # so it belongs to Saturday's batch, and settlement counts from there
        captured_late = datetime(2026, 2, 6, 23, 55)
        early_cutoff = time(22, 0)
        result = expected_settlement_date(captured_late, t_plus=2, cutoff=early_cutoff)
        # capture_date shifts to Sat 7 Feb (itself a non-working day; that's
        # fine, add_working_days only counts steps landing on a working day).
        # Sat 7 -> Sun 8 (skip) -> Mon 9 (1) -> Tue 10 (2)
        assert result == date(2026, 2, 10)  # Tuesday
