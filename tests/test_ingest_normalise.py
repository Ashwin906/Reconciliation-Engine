from datetime import datetime

import pytest

from ingest.normalise import (
    IST,
    clean_narration,
    normalise_amount,
    normalise_date,
    normalise_ts,
)


class TestNormaliseAmount:
    def test_int_passed_through_as_already_paise(self):
        # Razorpay's API convention -- an int IS the paise value, not rupees
        assert normalise_amount(100050) == 100050

    def test_rupee_string_converted(self):
        assert normalise_amount("1000.50") == 100050

    def test_strips_currency_symbol_and_commas(self):
        assert normalise_amount("₹1,000.50") == 100050
        assert normalise_amount("4,82,391.20") == 48239120

    def test_parenthesised_debit_becomes_negative(self):
        assert normalise_amount("(1,234.50)") == -123450

    def test_rejects_float(self):
        with pytest.raises(TypeError):
            normalise_amount(1000.50)


class TestNormaliseTs:
    def test_int_treated_as_unix_epoch_ist(self):
        # 1738483200 = 2025-02-02 12:00:00 UTC -- convert and check IST offset applied
        result = normalise_ts(1738483200)
        assert result.tzinfo == IST

    def test_ddmmyyyy_slash_format(self):
        result = normalise_ts("02/09/2026")
        assert (result.year, result.month, result.day) == (2026, 9, 2)
        assert result.tzinfo == IST

    def test_ddmmyyyy_dash_format(self):
        result = normalise_ts("02-09-2026")
        assert (result.year, result.month, result.day) == (2026, 9, 2)

    def test_dd_mon_yyyy_format(self):
        result = normalise_ts("02-Sep-2026")
        assert (result.year, result.month, result.day) == (2026, 9, 2)

    def test_iso_format(self):
        result = normalise_ts("2026-09-02")
        assert (result.year, result.month, result.day) == (2026, 9, 2)

    def test_unparseable_string_raises_loudly(self):
        # must NOT silently become some wrong default date
        with pytest.raises(ValueError):
            normalise_ts("not a date at all")


class TestNormaliseDate:
    def test_returns_date_not_datetime(self):
        result = normalise_date("02-Sep-2026")
        assert result.year == 2026 and result.month == 9 and result.day == 2
        assert not isinstance(result, datetime)


class TestCleanNarration:
    def test_collapses_whitespace(self):
        assert clean_narration("NEFT  CR   RAZORPAY") == "NEFT CR RAZORPAY"

    def test_uppercases(self):
        assert clean_narration("Razorpay Software") == "RAZORPAY SOFTWARE"

    def test_trims(self):
        assert clean_narration("  padded  ") == "PADDED"
