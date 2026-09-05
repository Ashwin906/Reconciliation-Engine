from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from invariant.conservation import check_conservation, localise_gap


class TestCleanDataCloses:
    def test_clean_dataset_closes_to_exactly_zero(self):
        ds = generate_clean(default_spec(seed=100))
        result = check_conservation(ds)
        assert result.closes
        assert result.residual == 0

    def test_localise_gap_empty_on_clean_data(self):
        ds = generate_clean(default_spec(seed=100))
        result = check_conservation(ds)
        assert result.closes
        assert localise_gap(ds) == []


class TestDefectsInvisibleToGlobalConservation:
    """These defects close DESPITE being real, planted problems -- not a
    bug in the invariant. Each is correctly caught by a DIFFERENT
    mechanism (T1/T2 report accuracy, or per-method rate comparison in a
    later phase); global conservation asks a different question ("does
    money add up across the whole system") and these genuinely don't
    break that identity. Verified empirically, not assumed -- see
    invariant/conservation.py's own module docstring for why each one
    specifically still closes."""

    def test_fee_overcharge_still_closes(self):
        # the extra fee is REAL money, correctly deducted everywhere --
        # DOMAIN_KNOWLEDGE.md section 11: invisible to reconciliation,
        # only detectable via rate comparison (a later phase)
        ds = generate_clean(default_spec(seed=101))
        _, truth = apply_defects(
            ds, [DefectSpec("fee_overcharge", {"method": "card", "extra_bps": 36, "days": 30})], seed=1
        )
        assert len(truth.defects) > 0
        result = check_conservation(ds)
        assert result.closes

    def test_unbooked_chargeback_still_closes(self):
        # the settlement REPORT is wrong (T1/T2 correctly flag it), but
        # the real bank credit + the real dispute record are globally
        # self-consistent: money not paid out is fully accounted for by
        # the dispute+fee bucket
        ds = generate_clean(default_spec(seed=102))
        _, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 2})], seed=1)
        assert len(truth.defects) > 0
        result = check_conservation(ds)
        assert result.closes

    def test_duplicate_order_id_still_closes(self):
        # a data-integrity issue with zero money impact by design
        # (impact_paise=0) -- conservation correctly has nothing to say
        ds = generate_clean(default_spec(seed=103))
        _, truth = apply_defects(ds, [DefectSpec("duplicate_order_id", {"n": 2})], seed=1)
        assert len(truth.defects) > 0
        result = check_conservation(ds)
        assert result.closes


class TestDefectsCaughtByGlobalConservation:
    """The defects this check exists to catch: genuine, unexplained
    money gaps at the whole-dataset level."""

    def test_missing_settlement_breaks_closure(self):
        ds = generate_clean(default_spec(seed=104))
        _, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        result = check_conservation(ds)
        assert not result.closes
        assert result.residual == truth.defects[0].impact_paise

    def test_short_credit_breaks_closure(self):
        ds = generate_clean(default_spec(seed=105))
        _, truth = apply_defects(ds, [DefectSpec("short_credit", {"n": 1})], seed=1)
        result = check_conservation(ds)
        assert not result.closes
        assert result.residual == truth.defects[0].impact_paise

    def test_negative_settlement_breaks_closure(self):
        # deliberately unmotivated in the generator (an arbitrary
        # override, not a fully modeled scenario) -- correctly shows up
        # as a real, unexplained gap
        ds = generate_clean(default_spec(seed=106))
        apply_defects(ds, [DefectSpec("negative_settlement", {"n": 1})], seed=1)
        result = check_conservation(ds)
        assert not result.closes

    def test_reserve_hold_breaks_closure_because_reserve_is_not_modeled(self):
        # honest gap, not a false alarm -- see module docstring point 2-
        # adjacent note and gen.defects.inject_reserve_hold's own
        # documented limitation
        ds = generate_clean(default_spec(seed=107))
        apply_defects(ds, [DefectSpec("reserve_hold", {"n": 1})], seed=1)
        result = check_conservation(ds)
        assert not result.closes
        assert result.reserve_delta == 0  # confirms it's the unmodeled-reserve gap, not a bug


class TestScopeBoundariesDocumentedNotHidden:
    """These two are real, deliberate limitations of a STANDALONE
    conservation check -- not failures of the check to do its stated
    job. See invariant/conservation.py's module docstring, points 1 and 2."""

    def test_utr_change_breaks_closure_pending_fuzzy_resolution(self):
        # money is real and present in raw.bank_lines, just not findable
        # under the settlement's own now-stale UTR -- exact-lookup scope
        # only, same as T1; T3 resolution would fix this in a full pipeline
        ds = generate_clean(default_spec(seed=108))
        apply_defects(ds, [DefectSpec("utr_change", {"n": 1})], seed=1)
        result = check_conservation(ds)
        assert not result.closes

    def test_offline_payment_does_not_break_closure(self):
        """The important negative result: an unexplained EXTRA credit is
        invisible to this check, in either direction, because it isn't
        linked to any settlement. This is NOT a detection of the
        anomaly -- it's proof this check's scope doesn't cover it, kept
        as a permanent regression test so nobody mistakes a future
        change to this function for accidentally "fixing" this without
        updating the module's own documented scope statement."""
        ds = generate_clean(default_spec(seed=109))
        apply_defects(ds, [DefectSpec("offline_payment", {"n": 1})], seed=1)
        result = check_conservation(ds)
        assert result.closes  # correctly unable to see the anomaly -- by design, not accident


class TestLocaliseGap:
    def test_narrows_to_the_actual_affected_settlement(self):
        ds = generate_clean(default_spec(seed=110))
        _, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        touched_id = truth.defects[0].subject_id

        suspects = localise_gap(ds)
        assert len(suspects) >= 1
        assert suspects[0].subject_id == touched_id  # largest gap first

    def test_unbooked_chargeback_settlement_not_flagged_locally_either(self):
        """Confirms the global-closure result (still closes) holds at
        the PER-SETTLEMENT level too, not just in aggregate -- proves
        the dispute is genuinely, locally fully accounted for, not just
        coincidentally netting out to zero in aggregate across multiple
        settlements."""
        ds = generate_clean(default_spec(seed=111))
        _, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 2})], seed=1)
        touched_ids = {d.subject_id for d in truth.defects}

        suspects = localise_gap(ds)
        flagged_ids = {s.subject_id for s in suspects}
        assert touched_ids.isdisjoint(flagged_ids)

    def test_sorted_largest_gap_first(self):
        ds = generate_clean(default_spec(seed=112))
        apply_defects(
            ds,
            [DefectSpec("missing_settlement", {"n": 1}), DefectSpec("short_credit", {"n": 2})],
            seed=1,
        )
        suspects = localise_gap(ds)
        magnitudes = [abs(s.local_residual) for s in suspects]
        assert magnitudes == sorted(magnitudes, reverse=True)
