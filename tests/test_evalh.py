from datetime import date

import pytest

from evalh.pipeline import run_full_pipeline
from evalh.score import _build_flagged_sets, _defect_was_flagged, score, to_exceptions_md, to_markdown
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import ScenarioSpec, default_spec


class TestRunFullPipelineOnCleanData:
    def test_clean_data_fully_resolves_and_closes(self):
        ds = generate_clean(default_spec(seed=200))
        run = run_full_pipeline(ds)

        assert run.match_result.residue == []
        assert run.verify_report.rejected == 0
        assert run.conservation.closes
        assert run.duration_s >= 0


class TestScoreOnCleanData:
    def test_metrics_reflect_a_fully_clean_run(self):
        ds = generate_clean(default_spec(seed=201))
        run = run_full_pipeline(ds)

        from gen.defects import TruthSet

        m = score(run, TruthSet())

        assert m.unmatched == 0
        assert m.proofs_rejected == 0
        assert m.false_matches == 0
        assert m.conservation_closes is True
        assert m.defects_planted == 0


class TestScoreDetectionPerDefectKind:
    """One test per defect kind, each pinned to a specific expected
    outcome verified empirically before being written -- not assumed.
    Two real gaps in THIS scoring module (not in match/verify/invariant,
    all three of which were already correct) were caught this way:

    1. A false alert on utr_change: invariant/conservation.py's own
       documented exact-UTR-only scope boundary meant it always flagged
       a utr_change-touched settlement, even after match/t3_fuzzy.py had
       already correctly, quietly resolved it. Fixed by excluding
       settlements match/ already resolved from the conservation-sourced
       flags in _build_flagged_sets.
    2. A missed detection on negative_settlement and cross_period_refund:
       both tie at T1 (the injector keeps settlement.amount and
       bank_line.credit mutually consistent), so the bank-reconciliation
       question alone says "fine" -- but match/t2_set.py's OWN Unmatched
       results correctly catch both, and _build_flagged_sets simply
       never looked at t2_unmatched at all. Fixed by including it.
    """

    def _run_single_defect(self, kind, params, seed=200, spec=None):
        ds = generate_clean(spec or default_spec(seed=seed))
        ds, truth = apply_defects(ds, [DefectSpec(kind, params)], seed=1)
        run = run_full_pipeline(ds)
        flagged = _build_flagged_sets(run)
        return ds, truth, flagged

    @pytest.mark.parametrize(
        "kind,params",
        [
            ("missing_settlement", {"n": 1}),
            ("short_credit", {"n": 1}),
            ("unbooked_chargeback", {"n": 1}),
            ("reserve_hold", {"n": 1}),
            ("negative_settlement", {"n": 1}),
            ("duplicate_order_id", {"n": 1}),
            ("cross_period_refund", {"n": 1}),
        ],
    )
    def test_should_alert_defects_are_detected(self, kind, params):
        ds, truth, flagged = self._run_single_defect(kind, params)
        assert len(truth.defects) > 0
        for d in truth.defects:
            assert d.should_be_detected is True
            result = _defect_was_flagged(d, ds, flagged)
            assert result is True, f"{kind} ({d.subject_id}) should have been flagged"

    def test_utr_change_correctly_stays_quiet(self):
        ds, truth, flagged = self._run_single_defect("utr_change", {"n": 2})
        for d in truth.defects:
            assert d.should_be_detected is False
            result = _defect_was_flagged(d, ds, flagged)
            assert result is False, "utr_change must resolve quietly, not be flagged"

    def test_late_capture_correctly_stays_quiet(self):
        ds, truth, flagged = self._run_single_defect("late_capture", {"n": 3})
        if not truth.defects:
            pytest.skip("no payment landed in the last business hour this seed")
        for d in truth.defects:
            assert d.should_be_detected is False
            result = _defect_was_flagged(d, ds, flagged)
            assert result is False

    def test_holiday_shift_correctly_stays_quiet(self):
        spec = ScenarioSpec(
            start_date=date(2026, 1, 22), days=7, orders_per_day=(10, 15),
            method_mix={"upi": 1.0}, rate_card_bps={"upi": 0}, seed=200,
        )
        ds, truth, flagged = self._run_single_defect("holiday_shift", {"n": 5}, spec=spec)
        assert len(truth.defects) > 0
        for d in truth.defects:
            assert d.should_be_detected is False
            result = _defect_was_flagged(d, ds, flagged)
            assert result is False

    def test_fee_overcharge_honestly_reported_as_undetected(self):
        """Deliberately NOT expected to be detected -- no detector exists
        yet for this phase's pipeline (Phase 8's job). Confirms the
        harness reports this honestly (False, not None -- it IS
        determinable, the answer is just 'no')."""
        ds, truth, flagged = self._run_single_defect(
            "fee_overcharge", {"method": "card", "extra_bps": 36, "days": 1}
        )
        if not truth.defects:
            pytest.skip("no card payment in the 1-day overcharge window this seed")
        for d in truth.defects:
            assert d.should_be_detected is True
            result = _defect_was_flagged(d, ds, flagged)
            assert result is False  # honestly undetected, not silently skipped

    @pytest.mark.parametrize("kind", ["narration_truncation", "offline_payment"])
    def test_not_settlement_mappable_kinds_are_undetermined_not_wrongly_scored(self, kind):
        """These two have no settlement-level signal to check against
        given current mechanisms -- must come back None (excluded from
        the detection-rate denominator), never silently coerced to True
        or False."""
        ds, truth, flagged = self._run_single_defect(kind, {"n": 1})
        assert len(truth.defects) > 0
        for d in truth.defects:
            result = _defect_was_flagged(d, ds, flagged)
            assert result is None


class TestScoreAggregation:
    def test_defects_undetermined_excluded_from_should_alert_denominator_correctly(self):
        ds = generate_clean(default_spec(seed=210))
        ds, truth = apply_defects(
            ds,
            [DefectSpec("missing_settlement", {"n": 1}), DefectSpec("offline_payment", {"n": 1})],
            seed=1,
        )
        run = run_full_pipeline(ds)
        m = score(run, truth)

        assert m.defects_should_alert == 2  # both should_be_detected=True
        assert m.defects_undetermined == 1  # offline_payment has no settlement signal
        assert m.defects_detected == 1      # only missing_settlement is determinable+True

    def test_false_alerts_counted_for_should_stay_quiet_defects(self):
        ds = generate_clean(default_spec(seed=211))
        ds, truth = apply_defects(ds, [DefectSpec("utr_change", {"n": 2})], seed=1)
        run = run_full_pipeline(ds)
        m = score(run, truth)

        assert m.defects_correctly_silent_expected == 2
        assert m.false_alerts == 0  # both correctly resolved quietly


class TestMarkdownAndExceptionsOutput:
    def test_to_markdown_runs_without_error_and_contains_key_numbers(self):
        ds = generate_clean(default_spec(seed=212))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        run = run_full_pipeline(ds)
        m = score(run, truth)
        report = to_markdown(m)

        assert "BATCH" in report
        assert "VERIFICATION" in report
        assert "GROUND TRUTH" in report
        assert "INVARIANTS" in report
        assert str(m.records) in report

    def test_to_exceptions_md_on_clean_data_says_none(self):
        ds = generate_clean(default_spec(seed=213))
        run = run_full_pipeline(ds)
        report = to_exceptions_md(run)
        assert "None" in report

    def test_to_exceptions_md_lists_each_unresolved_settlement(self):
        ds = generate_clean(default_spec(seed=214))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        run = run_full_pipeline(ds)
        report = to_exceptions_md(run)

        assert truth.defects[0].subject_id in report
        assert "missing_settlement" in report
