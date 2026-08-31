from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from invariant.coverage import (
    check_every_capture_settled_or_pending,
    check_every_order_terminal,
    check_no_duplicate_settlement,
)


class TestCheckEveryOrderTerminal:
    def test_clean_data_has_no_gaps(self):
        ds = generate_clean(default_spec(seed=120))
        assert check_every_order_terminal(ds) == []

    def test_duplicate_order_id_creates_an_order_with_no_payment_of_its_own(self):
        ds = generate_clean(default_spec(seed=121))
        _, truth = apply_defects(ds, [DefectSpec("duplicate_order_id", {"n": 2})], seed=1)

        # every duplicated order_id now has a payment_by_order_id mapping
        # via SOME payment (the original's), so this check -- which only
        # asks "does ANY payment exist for this order_id" -- will NOT
        # flag it. That's a real, honest boundary: catching the
        # duplicate itself is match.t0_key.link_orders_to_payments'
        # job (it counts occurrences, not just existence); this function
        # only checks existence, a cheaper and narrower question.
        gaps = check_every_order_terminal(ds)
        assert gaps == []


class TestCheckEveryCaptureSettledOrPending:
    def test_clean_data_every_capture_has_a_settlement(self):
        ds = generate_clean(default_spec(seed=122))
        assert check_every_capture_settled_or_pending(ds) == []

    def test_flags_a_captured_payment_with_no_settlement_id(self):
        ds = generate_clean(default_spec(seed=123))
        captured = next(p for p in ds.payments if p.status == "captured")
        captured.settlement_id = None

        gaps = check_every_capture_settled_or_pending(ds)
        assert captured in gaps

    def test_does_not_flag_failed_payments(self):
        ds = generate_clean(default_spec(seed=124))
        failed = [p for p in ds.payments if p.status == "failed"]
        assert len(failed) > 0
        gaps = check_every_capture_settled_or_pending(ds)
        assert all(p not in gaps for p in failed)


class TestCheckNoDuplicateSettlement:
    def test_clean_data_has_no_duplicates(self):
        ds = generate_clean(default_spec(seed=125))
        assert check_no_duplicate_settlement(ds) == []

    def test_detects_a_duplicated_settlement_id(self):
        ds = generate_clean(default_spec(seed=126))
        original = ds.settlements[0]
        from dataclasses import replace

        ds.settlements.append(replace(original))  # same settlement_id, appended again

        duplicates = check_no_duplicate_settlement(ds)
        assert duplicates == [original.settlement_id]
