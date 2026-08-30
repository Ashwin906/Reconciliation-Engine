from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from match.t1_utr import index_bank_by_ref, match_settlement_to_bank


class TestCleanDataAllTiesAtT1:
    def test_every_settlement_matches_its_bank_line_exactly(self):
        ds = generate_clean(default_spec(seed=40))
        index = index_bank_by_ref(ds.bank_lines)
        matches, unmatched = match_settlement_to_bank(ds.settlements, index)

        assert len(unmatched) == 0
        assert len(matches) == len(ds.settlements)


class TestMissingSettlement:
    def test_flagged_as_missing_settlement(self):
        ds = generate_clean(default_spec(seed=41))
        _, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 2})], seed=1)

        index = index_bank_by_ref(ds.bank_lines)
        matches, unmatched = match_settlement_to_bank(ds.settlements, index)

        flagged_ids = {u.id for u in unmatched if u.reason == "missing_settlement"}
        expected_ids = {d.subject_id for d in truth.defects}
        assert expected_ids <= flagged_ids


class TestAmountMismatch:
    def test_unbooked_chargeback_flagged_with_correct_gap(self):
        ds = generate_clean(default_spec(seed=42))
        _, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 2})], seed=1)

        index = index_bank_by_ref(ds.bank_lines)
        matches, unmatched = match_settlement_to_bank(ds.settlements, index)

        by_id = {u.id: u for u in unmatched if u.reason == "amount_mismatch"}
        for d in truth.defects:
            assert d.subject_id in by_id
            assert by_id[d.subject_id].detail["gap"] == d.impact_paise

    def test_short_credit_flagged(self):
        ds = generate_clean(default_spec(seed=43))
        _, truth = apply_defects(ds, [DefectSpec("short_credit", {"n": 2})], seed=1)

        # short_credit touches a bank_line directly, not via a settlement
        # subject_id -- find which settlement owns that bank line
        index = index_bank_by_ref(ds.bank_lines)
        matches, unmatched = match_settlement_to_bank(ds.settlements, index)
        mismatched_settlement_ids = {u.id for u in unmatched if u.reason == "amount_mismatch"}

        touched_line_ids = {d.subject_id for d in truth.defects}
        touched_settlement_ids = {
            s.settlement_id for s in ds.settlements
            if any(b.line_id in touched_line_ids and b.ref_no == s.utr for b in ds.bank_lines)
        }
        assert touched_settlement_ids <= mismatched_settlement_ids


class TestNegativeSettlement:
    def test_matches_via_debit_when_amount_negative(self):
        ds = generate_clean(default_spec(seed=44))
        _, truth = apply_defects(ds, [DefectSpec("negative_settlement", {"n": 1})], seed=1)

        index = index_bank_by_ref(ds.bank_lines)
        matches, unmatched = match_settlement_to_bank(ds.settlements, index)

        touched_id = truth.defects[0].subject_id
        # negative_settlement's bank line has ref_no unchanged, credit=0,
        # debit=abs(amount) -- match_settlement_to_bank must compare against
        # debit (as -debit) for a negative settlement.amount, not credit
        matched_ids = {m.left.id for m in matches}
        assert touched_id in matched_ids, (
            "a correctly-modeled negative settlement (debit == abs(amount)) "
            "should still tie, not be flagged as a mismatch"
        )


class TestUtrChangeBreaksT1ByDesign:
    def test_utr_change_is_unmatched_at_t1_not_a_false_match(self):
        # T1 is exact-lookup only -- utr_change (D-13-adjacent, identity
        # only, no money at risk) MUST fail here so it correctly falls
        # through to T3 fuzzy matching, not silently resolved wrong here
        ds = generate_clean(default_spec(seed=45))
        _, truth = apply_defects(ds, [DefectSpec("utr_change", {"n": 2})], seed=1)

        index = index_bank_by_ref(ds.bank_lines)
        matches, unmatched = match_settlement_to_bank(ds.settlements, index)

        touched_ids = {d.subject_id for d in truth.defects}
        # the changed-UTR bank line is no longer findable by the OLD utr,
        # so these settlements must show up as missing_settlement here
        missing_ids = {u.id for u in unmatched if u.reason == "missing_settlement"}
        assert touched_ids <= missing_ids
