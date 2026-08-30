from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from match.t3_fuzzy import match_fuzzy, score_candidate


class TestUtrChangeResolvesViaFuzzy:
    """utr_change (gen/defects.py) is should_be_detected=False: no money
    at risk, must resolve quietly. This proves T3 actually does resolve
    it, using only amount+date+narration -- exact UTR is gone."""

    def test_utr_changed_settlements_still_match(self):
        ds = generate_clean(default_spec(seed=60))
        _, truth = apply_defects(ds, [DefectSpec("utr_change", {"n": 2})], seed=1)
        touched_ids = {d.subject_id for d in truth.defects}

        # simulate what T1 would leave unresolved: the touched settlements,
        # and ALL bank lines (since we don't have T1's own bookkeeping here)
        touched_settlements = [s for s in ds.settlements if s.settlement_id in touched_ids]
        matches, unmatched = match_fuzzy(touched_settlements, ds.bank_lines)

        matched_ids = {m.left.id for m in matches}
        assert touched_ids <= matched_ids
        for m in matches:
            assert m.confidence >= 0.85


class TestNarrationTruncationResolvesViaFuzzy:
    def test_truncated_narration_still_matches_on_amount_and_date(self):
        ds = generate_clean(default_spec(seed=61))
        _, truth = apply_defects(ds, [DefectSpec("narration_truncation", {"n": 2})], seed=1)
        touched_line_ids = {d.subject_id for d in truth.defects}

        touched_settlement_ids = {
            s.settlement_id
            for s in ds.settlements
            for b in ds.bank_lines
            if b.line_id in touched_line_ids and b.credit == s.amount
        }
        touched_settlements = [s for s in ds.settlements if s.settlement_id in touched_settlement_ids]

        matches, unmatched = match_fuzzy(touched_settlements, ds.bank_lines)
        matched_ids = {m.left.id for m in matches}
        assert touched_settlement_ids <= matched_ids


class TestScoreCandidate:
    def test_exact_amount_and_date_scores_high_even_with_weak_narration(self):
        ds = generate_clean(default_spec(seed=62))
        settlement = ds.settlements[0]
        bank_line = next(b for b in ds.bank_lines if b.ref_no == settlement.utr)
        bank_line.narration = "XX"  # deliberately weak/short narration

        score = score_candidate(settlement, bank_line)
        assert score >= 0.85, (
            "amount-exact + date-exact should carry the match even with "
            "a near-meaningless narration, given amount-heavy weights"
        )

    def test_wrong_amount_scores_low(self):
        ds = generate_clean(default_spec(seed=63))
        settlement = ds.settlements[0]
        bank_line = next(b for b in ds.bank_lines if b.ref_no == settlement.utr)
        bank_line.credit = settlement.amount * 3  # wildly wrong

        score = score_candidate(settlement, bank_line)
        assert score < 0.85


class TestMatchFuzzyDoesNotDoubleAssign:
    def test_each_bank_line_used_at_most_once(self):
        ds = generate_clean(default_spec(seed=64))
        # deliberately feed the SAME small pool of settlements/bank_lines
        # to confirm no bank line gets claimed by two settlements
        matches, unmatched = match_fuzzy(ds.settlements[:3], ds.bank_lines[:3], threshold=0.0)
        used_bank_lines = [m.right.id for m in matches]
        assert len(used_bank_lines) == len(set(used_bank_lines))

    def test_below_threshold_pairs_stay_unmatched_not_force_assigned(self):
        ds = generate_clean(default_spec(seed=65))
        # threshold impossibly high -- nothing should match, even the
        # objectively-correct pairs, proving the function respects the
        # threshold rather than always assigning its best guess
        matches, unmatched = match_fuzzy(ds.settlements, ds.bank_lines, threshold=1.01)
        assert matches == []
        assert len(unmatched) == len(ds.settlements)
