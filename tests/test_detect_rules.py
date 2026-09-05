from dataclasses import replace
from datetime import datetime

from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.models import Dispute, Payment, Refund
from gen.scenario import default_spec
from detect.rules import DetectConfig, run_all

RATE_CARD = {"upi": 0, "card": 200, "amex": 300}


def _cfg(**overrides) -> DetectConfig:
    return DetectConfig(rate_card_bps=RATE_CARD, **overrides)


class TestCleanDataProducesNoFindings:
    def test_zero_findings_on_clean_data(self):
        ds = generate_clean(default_spec(seed=300))
        findings = run_all(ds, _cfg())
        assert findings == []

    def test_zero_false_positives_across_many_seeds(self):
        """Regression guard for the cycle_drift false-positive bug: a
        naive calendar-day comparison flagged every weekend-crossing
        settlement as 'drift' even in fully clean data. Stress-tested
        across 50 seeds before trusting the fix; kept as a smaller,
        permanent check here."""
        for seed in range(20):
            ds = generate_clean(default_spec(seed=seed))
            findings = run_all(ds, _cfg())
            assert findings == [], f"seed {seed} produced false positive(s): {findings}"


class TestDetectMissingSettlement:
    def test_fires_on_missing_settlement_defect(self):
        ds = generate_clean(default_spec(seed=301))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        from detect.rules import detect_missing_settlement

        findings = detect_missing_settlement(ds, _cfg())
        assert len(findings) == 1
        assert findings[0].subjects == (truth.defects[0].subject_id,)
        assert findings[0].impact_paise == truth.defects[0].impact_paise
        assert findings[0].severity == "P0"


class TestDetectAmountShort:
    def test_fires_on_unbooked_chargeback(self):
        ds = generate_clean(default_spec(seed=302))
        ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)
        from detect.rules import detect_amount_short

        findings = detect_amount_short(ds, _cfg())
        assert len(findings) == 1
        assert findings[0].impact_paise == truth.defects[0].impact_paise

    def test_fires_on_short_credit(self):
        ds = generate_clean(default_spec(seed=303))
        ds, truth = apply_defects(ds, [DefectSpec("short_credit", {"n": 1})], seed=1)
        from detect.rules import detect_amount_short

        findings = detect_amount_short(ds, _cfg())
        assert len(findings) == 1
        assert findings[0].impact_paise == truth.defects[0].impact_paise


class TestDetectFeeOvercharge:
    def test_fires_on_fee_overcharge_defect(self):
        ds = generate_clean(default_spec(seed=304))
        ds, truth = apply_defects(
            ds, [DefectSpec("fee_overcharge", {"method": "card", "extra_bps": 36, "days": 30})], seed=1
        )
        from detect.rules import detect_fee_overcharge

        findings = detect_fee_overcharge(ds, _cfg())
        card_findings = [f for f in findings if f.subjects == ("card",)]
        assert len(card_findings) == 1
        assert card_findings[0].impact_paise > 0

    def test_never_blends_methods(self):
        """DECISIONS.md D-14: fee comparison must be per method, never
        blended -- a real overcharge on card must not get diluted or
        hidden by UPI's correctly-zero rate averaging it out."""
        ds = generate_clean(default_spec(seed=305))
        ds, truth = apply_defects(
            ds, [DefectSpec("fee_overcharge", {"method": "card", "extra_bps": 36, "days": 30})], seed=1
        )
        from detect.rules import detect_fee_overcharge

        findings = detect_fee_overcharge(ds, _cfg())
        # UPI (0% contracted, untouched) must never appear as a finding
        assert all(f.subjects != ("upi",) for f in findings)


class TestDetectOrphanRefund:
    def test_fires_on_hand_built_orphan(self):
        """No current injector produces this -- a refund pointing at a
        payment_id absent from the dataset entirely. Hand-built fixture,
        as documented in the function's own docstring."""
        ds = generate_clean(default_spec(seed=306))
        ds.refunds.append(
            Refund(refund_id="rfnd_orphan", payment_id="pay_does_not_exist", amount=50000,
                   created_at=datetime(2026, 2, 3, 10, 0))
        )
        from detect.rules import detect_orphan_refund

        findings = detect_orphan_refund(ds, _cfg())
        assert len(findings) == 1
        assert findings[0].subjects == ("rfnd_orphan",)


class TestDetectDoubleRefund:
    def test_does_not_flag_legitimate_sequential_partial_refunds(self):
        ds = generate_clean(default_spec(seed=307))
        payment = next(p for p in ds.payments if p.status == "captured")
        half = payment.amount // 2
        ds.refunds.append(Refund("rfnd_a", payment.payment_id, half, datetime(2026, 2, 3, 10, 0)))
        ds.refunds.append(
            Refund("rfnd_b", payment.payment_id, payment.amount - half, datetime(2026, 2, 3, 11, 0))
        )
        from detect.rules import detect_double_refund

        findings = detect_double_refund(ds, _cfg())
        assert findings == []  # sums exactly to payment.amount -- legitimate

    def test_flags_refund_total_exceeding_payment_amount(self):
        ds = generate_clean(default_spec(seed=308))
        payment = next(p for p in ds.payments if p.status == "captured")
        ds.refunds.append(Refund("rfnd_a", payment.payment_id, payment.amount, datetime(2026, 2, 3, 10, 0)))
        ds.refunds.append(Refund("rfnd_b", payment.payment_id, 10000, datetime(2026, 2, 3, 11, 0)))
        from detect.rules import detect_double_refund

        findings = detect_double_refund(ds, _cfg())
        assert len(findings) == 1
        assert findings[0].impact_paise == 10000


class TestDetectStaleChargeback:
    def test_no_current_injector_produces_won_status(self):
        """Documents the honest gap directly: generate_clean()/gen.defects.py
        never advance a dispute to 'won'."""
        ds = generate_clean(default_spec(seed=309))
        assert all(d.status != "won" for d in ds.disputes)

    def test_fires_on_hand_built_won_dispute(self):
        ds = generate_clean(default_spec(seed=310))
        payment = next(p for p in ds.payments if p.status == "captured")
        ds.disputes.append(
            Dispute("disp_won", payment.payment_id, 40000, 10000, "won", datetime(2026, 2, 3, 10, 0))
        )
        from detect.rules import detect_stale_chargeback

        findings = detect_stale_chargeback(ds, _cfg())
        assert len(findings) == 1
        assert findings[0].subjects == ("disp_won",)


class TestDetectReserveStuck:
    def test_always_empty_no_reserve_model_exists(self):
        """Honest structural no-op -- documented in the function itself.
        Confirmed empty even against a reserve_hold-touched dataset,
        because reserve_hold changes MONEY (bank credit) without
        creating the structured reserve-release-date record this rule
        needs."""
        ds = generate_clean(default_spec(seed=311))
        ds, truth = apply_defects(ds, [DefectSpec("reserve_hold", {"n": 1})], seed=1)
        from detect.rules import detect_reserve_stuck

        assert detect_reserve_stuck(ds, _cfg()) == []


class TestDetectUnknownCredit:
    def test_fires_on_offline_payment(self):
        ds = generate_clean(default_spec(seed=312))
        ds, truth = apply_defects(ds, [DefectSpec("offline_payment", {"n": 1})], seed=1)
        from detect.rules import detect_unknown_credit

        findings = detect_unknown_credit(ds, _cfg())
        assert len(findings) == 1
        assert findings[0].severity == "P0"
        assert findings[0].impact_paise == truth.defects[0].impact_paise


class TestDetectDuplicateSettlement:
    def test_fires_on_duplicated_settlement_id(self):
        ds = generate_clean(default_spec(seed=313))
        original = ds.settlements[0]
        ds.settlements.append(replace(original))
        from detect.rules import detect_duplicate_settlement

        findings = detect_duplicate_settlement(ds, _cfg())
        assert len(findings) == 1
        assert findings[0].subjects == (original.settlement_id,)


class TestDetectCaptureGap:
    def test_no_current_generator_produces_authorized_status(self):
        ds = generate_clean(default_spec(seed=314))
        assert all(p.status in {"captured", "failed"} for p in ds.payments)

    def test_fires_on_hand_built_authorized_payment(self):
        ds = generate_clean(default_spec(seed=315))
        ds.payments.append(
            Payment("pay_stuck", "order_stuck", 100000, "upi", "authorized")
        )
        from detect.rules import detect_capture_gap

        findings = detect_capture_gap(ds, _cfg())
        assert len(findings) == 1
        assert findings[0].subjects == ("pay_stuck",)


class TestDetectCycleDrift:
    def test_no_drift_on_clean_data(self):
        ds = generate_clean(default_spec(seed=316))
        from detect.rules import detect_cycle_drift

        assert detect_cycle_drift(ds, _cfg()) == []

    def test_single_outlier_not_enough_to_flag(self):
        """Requires >=2 drifted settlements -- a systematic pattern, not
        one anomaly."""
        ds = generate_clean(default_spec(seed=317))
        from datetime import timedelta

        from detect.rules import detect_cycle_drift

        # artificially delay ONE settlement by 5 days beyond its correct date
        ds.settlements[0].created_at += timedelta(days=5)
        findings = detect_cycle_drift(ds, _cfg())
        assert findings == []

    def test_two_or_more_drifted_settlements_flagged(self):
        from datetime import timedelta

        from detect.rules import detect_cycle_drift

        ds = generate_clean(default_spec(seed=318))
        assert len(ds.settlements) >= 2
        ds.settlements[0].created_at += timedelta(days=5)
        ds.settlements[1].created_at += timedelta(days=5)

        findings = detect_cycle_drift(ds, _cfg())
        assert len(findings) == 2
        assert all(f.rule == "cycle_drift" for f in findings)


class TestRunAll:
    def test_registry_covers_all_eleven_rules(self):
        from detect.rules import REGISTRY

        assert len(REGISTRY) == 11


class TestReserveStuckIsNoLongerANoOp:
    """This rule returned [] unconditionally until Phase 16, with a
    comment saying its precondition could not exist. reserve/ledger.py
    made it real."""

    def _dataset_with_holds(self, holds):
        from datetime import datetime

        from gen.models import Dataset, Settlement

        ds = Dataset(merchant_id="M1")
        ds.settlements.append(
            Settlement("setl_1", 1_000_000, "utr_1", datetime(2026, 5, 1, 12, 0))
        )
        ds.reserve_holds = holds
        return ds

    def test_fires_on_a_hold_past_its_release_date(self):
        from datetime import date

        from detect.rules import DetectConfig, detect_reserve_stuck
        from reserve.ledger import schedule_hold

        hold = schedule_hold("setl_1", 1_000_000, date(2026, 1, 31), 500, 60)
        findings = detect_reserve_stuck(
            self._dataset_with_holds([hold]), DetectConfig(rate_card_bps={})
        )

        assert len(findings) == 1
        assert findings[0].rule == "reserve_stuck"
        assert findings[0].impact_paise == 50_000
        assert findings[0].evidence["days_overdue"] == 30

    def test_silent_on_a_hold_still_within_its_window(self):
        from datetime import date

        from detect.rules import DetectConfig, detect_reserve_stuck
        from reserve.ledger import schedule_hold

        hold = schedule_hold("setl_1", 1_000_000, date(2026, 4, 20), 500, 60)
        assert detect_reserve_stuck(
            self._dataset_with_holds([hold]), DetectConfig(rate_card_bps={})
        ) == []

    def test_a_dataset_that_does_not_track_reserve_degrades_to_no_findings(self):
        """An ingest path that knows nothing about reserve must not error."""
        from detect.rules import DetectConfig, detect_reserve_stuck

        assert detect_reserve_stuck(
            self._dataset_with_holds([]), DetectConfig(rate_card_bps={})
        ) == []
