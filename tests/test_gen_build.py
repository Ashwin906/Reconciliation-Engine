import dataclasses

import pytest

from core.money import sum_paise
from gen.build import generate_clean
from gen.scenario import ScenarioSpec, default_spec


class TestScenarioSpecValidation:
    def test_default_spec_is_valid(self):
        default_spec()  # must not raise

    def test_method_mix_must_sum_to_one(self):
        with pytest.raises(ValueError, match="method_mix"):
            dataclasses.replace(default_spec(), method_mix={"upi": 0.5, "card": 0.3})

    def test_rate_card_missing_method_raises(self):
        with pytest.raises(ValueError, match="rate_card_bps"):
            dataclasses.replace(
                default_spec(),
                method_mix={"upi": 0.5, "amex": 0.5},
                rate_card_bps={"upi": 0},  # missing 'amex'
            )

    def test_orders_per_day_min_gt_max_raises(self):
        with pytest.raises(ValueError, match="orders_per_day"):
            dataclasses.replace(default_spec(), orders_per_day=(30, 10))

    @pytest.mark.parametrize("field_name", ["failure_rate", "refund_rate", "dispute_rate"])
    def test_rate_out_of_bounds_raises(self, field_name):
        with pytest.raises(ValueError):
            dataclasses.replace(default_spec(), **{field_name: 1.5})


class TestGenerateCleanDeterminism:
    def test_same_seed_produces_identical_dataset(self):
        spec = default_spec(seed=7)
        ds1 = generate_clean(spec)
        ds2 = generate_clean(spec)
        assert ds1 == ds2

    def test_different_seed_produces_different_dataset(self):
        ds1 = generate_clean(default_spec(seed=1))
        ds2 = generate_clean(default_spec(seed=2))
        assert ds1.orders != ds2.orders


class TestGenerateCleanStructure:
    def test_every_order_has_exactly_one_payment(self):
        ds = generate_clean(default_spec(seed=3))
        order_ids = {o.order_id for o in ds.orders}
        payment_order_ids = {p.order_id for p in ds.payments}
        assert order_ids == payment_order_ids
        assert len(ds.orders) == len(ds.payments)

    def test_failed_payments_have_no_settlement(self):
        ds = generate_clean(default_spec(seed=3))
        failed = [p for p in ds.payments if p.status == "failed"]
        assert len(failed) > 0, "test scenario should produce at least one failure"
        assert all(p.settlement_id is None for p in failed)

    def test_captured_payments_all_have_a_settlement(self):
        ds = generate_clean(default_spec(seed=3))
        captured = [p for p in ds.payments if p.status == "captured"]
        assert all(p.settlement_id is not None for p in captured)

    def test_every_settlement_has_exactly_one_bank_line(self):
        ds = generate_clean(default_spec(seed=3))
        settlement_utrs = {s.utr for s in ds.settlements}
        bank_ref_nos = {b.ref_no for b in ds.bank_lines}
        assert settlement_utrs == bank_ref_nos
        assert len(ds.settlements) == len(ds.bank_lines)


class TestGenerateCleanReconciles:
    """The core contract: generate_clean's output must be 100% correct by
    construction. This test independently recomputes the settlement
    arithmetic (deliberately NOT importing _batch_settlements' formula)
    to catch a bug in the generator itself -- the same independence
    principle the real verifier (Phase 4) will apply to the matcher."""

    def test_every_settlement_ties_to_its_bank_line_exactly(self):
        ds = generate_clean(default_spec(seed=11))

        for settlement in ds.settlements:
            members = ds.payments_in_settlement(settlement.settlement_id)
            assert len(members) > 0

            gross = sum_paise(p.amount for p in members)
            fees = sum_paise(p.fee for p in members)
            tax = sum_paise(p.tax for p in members)
            refund_total = sum_paise(
                r.amount
                for p in members
                for r in ds.refunds_for(p.payment_id)
            )
            dispute_total = sum_paise(
                d.amount + d.fee
                for p in members
                for d in ds.disputes_for(p.payment_id)
            )
            expected = gross - fees - tax - refund_total - dispute_total

            assert expected == settlement.amount, (
                f"{settlement.settlement_id}: computed {expected}, "
                f"settlement says {settlement.amount}"
            )

            bank_line = next(b for b in ds.bank_lines if b.ref_no == settlement.utr)
            assert bank_line.credit == settlement.amount, (
                f"{settlement.settlement_id}: bank line credit "
                f"{bank_line.credit} != settlement amount {settlement.amount}"
            )

    def test_no_payment_is_captured_and_missing_from_every_settlement(self):
        ds = generate_clean(default_spec(seed=11))
        all_settled_payment_ids = {
            p.payment_id
            for s in ds.settlements
            for p in ds.payments_in_settlement(s.settlement_id)
        }
        captured_ids = {p.payment_id for p in ds.payments if p.status == "captured"}
        assert captured_ids == all_settled_payment_ids

    def test_conservation_across_whole_dataset(self):
        """Sum of all captured payments must equal sum of all settlements
        plus everything subtracted from them -- the same invariant Phase 5
        will check per-batch, verified here across the whole dataset."""
        ds = generate_clean(default_spec(seed=11))

        captured_total = sum_paise(
            p.amount for p in ds.payments if p.status == "captured"
        )
        settled_total = sum_paise(s.amount for s in ds.settlements)
        fees_total = sum_paise(p.fee for p in ds.payments if p.status == "captured")
        tax_total = sum_paise(p.tax for p in ds.payments if p.status == "captured")
        refund_total = sum_paise(r.amount for r in ds.refunds)
        dispute_total = sum_paise(d.amount + d.fee for d in ds.disputes)

        residual = captured_total - (
            settled_total + fees_total + tax_total + refund_total + dispute_total
        )
        assert residual == 0, f"conservation residual is {residual}, must be 0"
