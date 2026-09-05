from datetime import timedelta

from detect.patterns import scan_fee_drift, scan_settlement_cycle_drift
from detect.rules import DetectConfig
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import ScenarioSpec, default_spec

RATE_CARD = {"upi": 0, "card": 200, "amex": 300}


def _cfg(**overrides) -> DetectConfig:
    return DetectConfig(rate_card_bps=RATE_CARD, **overrides)


class TestScanFeeDrift:
    def test_ignores_a_single_transaction_overcharge_too_few_samples(self):
        """A one-off overcharge on one transaction is exactly the kind
        of noise this scan exists to ignore -- min_samples gates it out
        even though detect_fee_overcharge (the lenient single-run check)
        would flag it."""
        ds = generate_clean(default_spec(seed=320))
        ds, truth = apply_defects(
            ds, [DefectSpec("fee_overcharge", {"method": "card", "extra_bps": 200, "days": 1})], seed=1
        )
        findings = scan_fee_drift(ds, _cfg(), min_samples=20)
        assert findings == []

    def test_flags_a_genuinely_systematic_overcharge(self):
        """A large scenario with enough card volume that a sustained
        overcharge touches >=20 transactions at high consistency."""
        spec = ScenarioSpec(
            start_date=default_spec().start_date, days=20, orders_per_day=(20, 30),
            method_mix={"upi": 0.4, "card": 0.6}, rate_card_bps={"upi": 0, "card": 200},
            seed=321,
        )
        ds = generate_clean(spec)
        ds, truth = apply_defects(
            ds, [DefectSpec("fee_overcharge", {"method": "card", "extra_bps": 36, "days": 20})], seed=1
        )
        findings = scan_fee_drift(ds, _cfg(), min_samples=20)
        assert len(findings) == 1
        assert findings[0].subjects == ("card",)
        assert findings[0].evidence["n"] >= 20
        assert findings[0].evidence["consistency"] >= 0.9

    def test_clean_data_produces_no_pattern_finding(self):
        ds = generate_clean(default_spec(seed=322))
        findings = scan_fee_drift(ds, _cfg())
        assert findings == []


class TestScanSettlementCycleDrift:
    def test_clean_data_no_drift(self):
        ds = generate_clean(default_spec(seed=323))
        findings = scan_settlement_cycle_drift(ds, _cfg())
        assert findings == []

    def test_flags_when_most_recent_settlements_all_drifted(self):
        ds = generate_clean(default_spec(seed=324))
        assert len(ds.settlements) >= 3
        for s in ds.settlements[-3:]:
            s.created_at += timedelta(days=5)

        findings = scan_settlement_cycle_drift(ds, _cfg(), window_settlements=3)
        assert len(findings) == 1
        assert findings[0].rule == "settlement_cycle_drift"

    def test_not_flagged_if_only_some_of_the_recent_window_drifted(self):
        ds = generate_clean(default_spec(seed=325))
        assert len(ds.settlements) >= 3
        ds.settlements[-1].created_at += timedelta(days=5)  # only the most recent one

        findings = scan_settlement_cycle_drift(ds, _cfg(), window_settlements=3)
        assert findings == []

    def test_too_few_settlements_returns_empty_not_an_error(self):
        ds = generate_clean(
            ScenarioSpec(
                start_date=default_spec().start_date, days=1, orders_per_day=(2, 3),
                method_mix={"upi": 1.0}, rate_card_bps={"upi": 0}, seed=326,
            )
        )
        findings = scan_settlement_cycle_drift(ds, _cfg(), window_settlements=10)
        assert findings == []
