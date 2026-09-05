from decimal import Decimal

import pytest

from core.money import apply_rate, format_paise, gst_on, sum_paise, to_paise


class TestToPaise:
    def test_basic_conversion(self):
        assert to_paise("1000.50") == 100050
        assert to_paise("1000") == 100000
        assert to_paise("0.01") == 1

    def test_the_float_bug_this_function_exists_to_prevent(self):
        # 0.1 + 0.2 != 0.3 in binary float. In paise, it must.
        assert to_paise("0.1") + to_paise("0.2") == to_paise("0.3")

    def test_rejects_float_input(self):
        with pytest.raises(TypeError):
            to_paise(1000.50)  # float, not str/Decimal -- must be refused

    def test_rejects_more_than_two_decimal_places(self):
        with pytest.raises(ValueError):
            to_paise("1000.505")

    def test_accepts_decimal(self):
        assert to_paise(Decimal("1000.50")) == 100050

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            to_paise("not a number")


class TestFormatPaise:
    def test_basic_format(self):
        assert format_paise(100050) == "1,000.50"

    def test_large_amount_gets_commas(self):
        assert format_paise(48239120) == "4,82,391.20" or format_paise(48239120) == "482,391.20"
        # NOTE: Python's ',' format spec uses Western grouping (482,391.20),
        # not Indian grouping (4,82,391.20). Documented gap -- if Indian
        # comma grouping is needed for the dashboard, format there with a
        # locale-aware formatter; this function stays simple and correct
        # for the arithmetic layer.

    def test_rejects_non_int(self):
        with pytest.raises(TypeError):
            format_paise(1000.50)


class TestApplyRate:
    def test_two_percent_fee(self):
        assert apply_rate(100000, 200) == 2000  # Rs 1000 * 2% = Rs 20

    def test_zero_percent_upi(self):
        assert apply_rate(100000, 0) == 0

    def test_half_up_rounding_even_base(self):
        # 2500 * 10 / 10000 = 2.5 -- ROUND_HALF_UP must give 3, not 2
        # (Python/Decimal's default banker's rounding would give 2 here)
        assert apply_rate(2500, 10) == 3

    def test_half_up_rounding_odd_base(self):
        # 3500 * 10 / 10000 = 3.5 -- must give 4
        assert apply_rate(3500, 10) == 4

    def test_rounds_down_below_half(self):
        assert apply_rate(2400, 10) == 2  # 2.4 -> 2

    def test_rounds_up_above_half(self):
        assert apply_rate(2600, 10) == 3  # 2.6 -> 3


class TestGstOnFee:
    def test_gst_is_on_the_fee_not_the_sale(self):
        # Rs 20.00 fee -> Rs 3.60 GST. NOT 18% of the Rs 1000 sale (Rs 180).
        fee = 2000
        assert gst_on(fee) == 360

    def test_custom_rate(self):
        assert gst_on(2000, rate_bps=1000) == 200  # 10% of Rs 20 = Rs 2


class TestSumPaise:
    def test_basic_sum(self):
        assert sum_paise([100000, 200000, 150000]) == 450000

    def test_empty(self):
        assert sum_paise([]) == 0

    def test_rejects_non_int_member(self):
        with pytest.raises(TypeError):
            sum_paise([100000, 200.50])
