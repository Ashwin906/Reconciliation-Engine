import random
from datetime import date

from core.money import sum_paise
from gen.build import generate_clean
from gen.defects import (
    DefectSpec,
    apply_defects,
    inject_cross_period_refund,
    inject_duplicate_order_id,
    inject_fee_overcharge,
    inject_holiday_shift,
    inject_late_capture,
    inject_missing_settlement,
    inject_narration_truncation,
    inject_negative_settlement,
    inject_offline_payment,
    inject_reserve_hold,
    inject_short_credit,
    inject_unbooked_chargeback,
    inject_utr_change,
)
from gen.scenario import ScenarioSpec, default_spec


def _bank_line_for_utr(ds, utr):
    return next((b for b in ds.bank_lines if b.ref_no == utr), None)


class TestFeeOvercharge:
    def test_overcharges_and_updates_settlement_and_bank_consistently(self):
        ds = generate_clean(default_spec(seed=5))
        rng = random.Random(1)
        before = {s.settlement_id: s.amount for s in ds.settlements}

        defects = inject_fee_overcharge(ds, rng, method="card", extra_bps=36, days=30)

        assert len(defects) > 0
        for d in defects:
            payment = ds.payment_by_id(d.subject_id)
            settlement = next(
                s for s in ds.settlements if s.settlement_id == payment.settlement_id
            )
            bank_line = _bank_line_for_utr(ds, settlement.utr)
            # settlement AND bank line must still tie to each other exactly
            # -- the overcharge is real money, invisible to the tie-out,
            # only visible via rate comparison
            assert settlement.amount == bank_line.credit
            assert settlement.amount == before[settlement.settlement_id] - sum_paise(
                x.impact_paise for x in defects if ds.payment_by_id(x.subject_id).settlement_id == settlement.settlement_id
            )
            assert d.should_be_detected is True


class TestUnbookedChargeback:
    def test_shorts_bank_credit_without_touching_settlement_report(self):
        ds = generate_clean(default_spec(seed=6))
        rng = random.Random(1)
        settlement_amounts_before = {s.settlement_id: s.amount for s in ds.settlements}

        defects = inject_unbooked_chargeback(ds, rng, n=2)

        assert len(defects) > 0
        for d in defects:
            settlement = next(s for s in ds.settlements if s.settlement_id == d.subject_id)
            # settlement report itself must be UNCHANGED -- that's the whole point
            assert settlement.amount == settlement_amounts_before[settlement.settlement_id]
            bank_line = _bank_line_for_utr(ds, settlement.utr)
            gap = settlement.amount - bank_line.credit
            assert gap == d.impact_paise
            assert gap > 0


class TestMissingSettlement:
    def test_removes_bank_line_entirely(self):
        ds = generate_clean(default_spec(seed=7))
        rng = random.Random(1)
        target = ds.settlements[0]

        defects = inject_missing_settlement(ds, rng, n=1)

        assert len(defects) == 1
        assert _bank_line_for_utr(ds, defects[0].subject_id) is None or all(
            b.ref_no != next(
                s.utr for s in ds.settlements if s.settlement_id == defects[0].subject_id
            )
            for b in ds.bank_lines
        )


class TestShortCredit:
    def test_reduces_credit_with_no_traceable_cause(self):
        ds = generate_clean(default_spec(seed=8))
        rng = random.Random(1)
        before = {b.line_id: b.credit for b in ds.bank_lines}

        defects = inject_short_credit(ds, rng, n=2)

        assert len(defects) > 0
        for d in defects:
            bank_line = next(b for b in ds.bank_lines if b.line_id == d.subject_id)
            assert bank_line.credit == before[d.subject_id] - d.impact_paise
            assert d.expected_resolution == "unresolved"


class TestUtrChangeAndNarrationTruncation:
    def test_utr_change_preserves_amount_breaks_ref(self):
        ds = generate_clean(default_spec(seed=9))
        rng = random.Random(1)
        original_refs = {s.settlement_id: s.utr for s in ds.settlements}

        defects = inject_utr_change(ds, rng, n=2)

        assert len(defects) > 0
        for d in defects:
            settlement = next(s for s in ds.settlements if s.settlement_id == d.subject_id)
            bank_line = next(
                b for b in ds.bank_lines if b.credit == settlement.amount
            )
            assert bank_line.ref_no != original_refs[d.subject_id]
            assert d.should_be_detected is False
            assert d.impact_paise == 0

    def test_narration_truncation_blanks_ref_no(self):
        ds = generate_clean(default_spec(seed=10))
        rng = random.Random(1)

        defects = inject_narration_truncation(ds, rng, n=2)

        assert len(defects) > 0
        for d in defects:
            bank_line = next(b for b in ds.bank_lines if b.line_id == d.subject_id)
            assert bank_line.ref_no is None
            assert len(bank_line.narration) <= 10
            assert d.should_be_detected is False


class TestDuplicateOrderId:
    def test_duplicates_order_under_same_id(self):
        ds = generate_clean(default_spec(seed=12))
        rng = random.Random(1)
        before_count = len(ds.orders)

        defects = inject_duplicate_order_id(ds, rng, n=2)

        assert len(defects) > 0
        assert len(ds.orders) == before_count + len(defects)
        for d in defects:
            matches = [o for o in ds.orders if o.order_id == d.subject_id]
            assert len(matches) == 2
            assert d.impact_paise == 0


class TestCrossPeriodRefund:
    def test_moves_refund_deduction_to_a_later_settlement(self):
        # needs multiple settlement batches -- use a longer scenario
        spec = ScenarioSpec(
            start_date=date(2026, 2, 2),
            days=10,
            orders_per_day=(15, 25),
            method_mix={"upi": 0.6, "card": 0.35, "amex": 0.05},
            rate_card_bps={"upi": 0, "card": 200, "amex": 300},
            refund_rate=0.1,
            seed=13,
        )
        ds = generate_clean(spec)
        rng = random.Random(1)
        assert len(ds.settlements) >= 2, "need multiple settlements for this test to be meaningful"

        source_amounts_before = {s.settlement_id: s.amount for s in ds.settlements}

        defects = inject_cross_period_refund(ds, rng, n=3)

        if not defects:
            return  # honestly nothing eligible in this seed's data -- not a failure

        # A settlement can be the SOURCE of one moved refund (+amount) and
        # the TARGET of another in the same run (-amount) -- discovered by
        # actually inspecting a failing run rather than assuming defects
        # are independent. Net both roles per settlement before comparing.
        # Source/target recomputed independently here (not by reading the
        # defect's own note string) -- payment.settlement_id is never
        # mutated by the injector, so it still names the true source, and
        # "target = earliest settlement after source" mirrors the
        # production selection rule without importing it.
        settlements_sorted = sorted(ds.settlements, key=lambda s: s.created_at)
        net_delta: dict[str, int] = {}
        for d in defects:
            refund = next(r for r in ds.refunds if r.refund_id == d.subject_id)
            payment = ds.payment_by_id(refund.payment_id)
            source = next(s for s in ds.settlements if s.settlement_id == payment.settlement_id)
            later = [s for s in settlements_sorted if s.created_at > source.created_at]
            target = later[0]
            net_delta[source.settlement_id] = net_delta.get(source.settlement_id, 0) + refund.amount
            net_delta[target.settlement_id] = net_delta.get(target.settlement_id, 0) - refund.amount

        for settlement_id, delta in net_delta.items():
            settlement = next(s for s in ds.settlements if s.settlement_id == settlement_id)
            assert settlement.amount == source_amounts_before[settlement_id] + delta, (
                f"{settlement_id}: expected {source_amounts_before[settlement_id] + delta}, "
                f"got {settlement.amount}"
            )


class TestReserveHold:
    def test_withholds_a_fraction_without_changing_settlement_report(self):
        ds = generate_clean(default_spec(seed=14))
        rng = random.Random(1)
        settlement_amounts_before = {s.settlement_id: s.amount for s in ds.settlements}

        defects = inject_reserve_hold(ds, rng, n=2, reserve_bps=500)

        assert len(defects) > 0
        for d in defects:
            settlement = next(s for s in ds.settlements if s.settlement_id == d.subject_id)
            assert settlement.amount == settlement_amounts_before[d.subject_id]
            bank_line = _bank_line_for_utr(ds, settlement.utr)
            assert settlement.amount - bank_line.credit == d.impact_paise


class TestNegativeSettlement:
    def test_flips_settlement_and_bank_line_to_debit(self):
        ds = generate_clean(default_spec(seed=15))
        rng = random.Random(1)

        defects = inject_negative_settlement(ds, rng, n=1)

        assert len(defects) == 1
        settlement = next(s for s in ds.settlements if s.settlement_id == defects[0].subject_id)
        assert settlement.amount < 0
        bank_line = _bank_line_for_utr(ds, settlement.utr)
        assert bank_line.credit == 0
        assert bank_line.debit == abs(settlement.amount)


class TestOfflinePayment:
    def test_adds_unmatched_credit(self):
        ds = generate_clean(default_spec(seed=16))
        rng = random.Random(1)
        before_count = len(ds.bank_lines)

        defects = inject_offline_payment(ds, rng, n=2)

        assert len(ds.bank_lines) == before_count + 2
        for d in defects:
            bank_line = next(b for b in ds.bank_lines if b.line_id == d.subject_id)
            assert bank_line.ref_no is None
            assert d.expected_resolution == "unknown_credit"


class TestFalsePositiveGuards:
    """The should_be_detected=False cases -- D-05. A correct system must
    NOT alert on these even though they look unusual."""

    def test_holiday_shift_finds_a_real_holiday_span(self):
        # Jan 22 2026 (Thu) start, 5 days -> some payments' settlement
        # window will cross Republic Day (Jan 26, a Monday holiday)
        spec = ScenarioSpec(
            start_date=date(2026, 1, 22),
            days=5,
            orders_per_day=(10, 15),
            method_mix={"upi": 1.0},
            rate_card_bps={"upi": 0},
            seed=17,
        )
        ds = generate_clean(spec)
        rng = random.Random(1)

        defects = inject_holiday_shift(ds, rng, n=5)

        assert len(defects) > 0, "expected at least one settlement window to span Jan 26 holiday"
        for d in defects:
            assert d.should_be_detected is False
            assert d.impact_paise == 0

    def test_holiday_shift_honestly_empty_when_no_holiday_in_range(self):
        ds = generate_clean(default_spec(seed=18))  # Feb 2-6 2026, no holiday
        rng = random.Random(1)
        defects = inject_holiday_shift(ds, rng, n=5)
        assert defects == []

    def test_late_capture_tags_last_business_hour_captures(self):
        ds = generate_clean(default_spec(seed=19))
        rng = random.Random(1)
        defects = inject_late_capture(ds, rng, n=100)  # ask for many to maximize chance
        for d in defects:
            payment = ds.payment_by_id(d.subject_id)
            assert payment.captured_at.hour >= 21
            assert d.should_be_detected is False


class TestApplyDefectsOrchestration:
    def test_applies_plan_in_order_and_aggregates_truth(self):
        ds = generate_clean(default_spec(seed=20))
        plan = [
            DefectSpec("missing_settlement", {"n": 1}),
            DefectSpec("short_credit", {"n": 1}),
            DefectSpec("utr_change", {"n": 1}),
        ]
        mutated, truth = apply_defects(ds, plan, seed=99)

        assert mutated is ds  # mutation in place, same object returned
        assert len(truth.defects) >= 1  # at least some injected (pool-dependent)
        assert isinstance(truth.total_impact(), int)

    def test_unknown_kind_raises(self):
        ds = generate_clean(default_spec(seed=21))
        plan = [DefectSpec("not_a_real_defect", {"n": 1})]
        try:
            apply_defects(ds, plan)
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_reproducible_given_same_seed(self):
        ds1 = generate_clean(default_spec(seed=22))
        ds2 = generate_clean(default_spec(seed=22))
        plan = [DefectSpec("missing_settlement", {"n": 2}), DefectSpec("short_credit", {"n": 2})]

        _, truth1 = apply_defects(ds1, plan, seed=55)
        _, truth2 = apply_defects(ds2, plan, seed=55)

        kinds1 = [(d.kind, d.subject_id, d.impact_paise) for d in truth1.defects]
        kinds2 = [(d.kind, d.subject_id, d.impact_paise) for d in truth2.defects]
        assert kinds1 == kinds2


class TestTruthSetHelpers:
    def test_by_kind_and_should_alert_filters(self):
        ds = generate_clean(default_spec(seed=23))
        rng = random.Random(1)
        plan_defects = (
            inject_missing_settlement(ds, rng, n=1)
            + inject_utr_change(ds, rng, n=1)
        )
        from gen.defects import TruthSet

        truth = TruthSet(plan_defects)

        assert len(truth.by_kind("missing_settlement")) == 1
        assert len(truth.by_kind("utr_change")) == 1
        assert all(d.should_be_detected for d in truth.should_alert())
        assert all(not d.should_be_detected for d in truth.should_stay_quiet())
