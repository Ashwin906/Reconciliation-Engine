import pytest

from core.ids import parse_prefix, validate


class TestParsePrefix:
    def test_basic(self):
        assert parse_prefix("pay_ABC123") == "pay"
        assert parse_prefix("setl_9F2K") == "setl"

    def test_no_underscore_raises(self):
        with pytest.raises(ValueError):
            parse_prefix("garbage")

    def test_empty_prefix_raises(self):
        with pytest.raises(ValueError):
            parse_prefix("_ABC123")

    def test_empty_suffix_raises(self):
        with pytest.raises(ValueError):
            parse_prefix("pay_")


class TestValidate:
    def test_matching_prefix_passes(self):
        validate("pay_ABC123", expect="pay")  # no exception

    def test_mismatched_prefix_raises(self):
        # the exact bug this exists to catch: a payment_id where a
        # settlement_id was expected
        with pytest.raises(ValueError):
            validate("pay_ABC123", expect="setl")

    def test_unknown_expect_raises(self):
        with pytest.raises(ValueError):
            validate("pay_ABC123", expect="bogus")
