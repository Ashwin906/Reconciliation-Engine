from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from match.run import MatchContext, run_matching


class TestCleanDataFullyResolvesAtT1:
    def test_zero_residue_on_clean_data(self):
        ds = generate_clean(default_spec(seed=70))
        result = run_matching(MatchContext(dataset=ds))

        assert len(result.residue) == 0
        assert len(result.t1_matches) == len(ds.settlements)
        assert len(result.t2_matches) == len(ds.settlements)  # all internally consistent too


class TestIdentityOnlyDefectsResolveQuietlyViaT3:
    def test_utr_change_ends_with_zero_residue(self):
        ds = generate_clean(default_spec(seed=71))
        _, truth = apply_defects(ds, [DefectSpec("utr_change", {"n": 2})], seed=1)

        result = run_matching(MatchContext(dataset=ds))

        # T1 must NOT have matched these (exact UTR is gone)
        t1_matched_ids = {m.left.id for m in result.t1_matches}
        touched_ids = {d.subject_id for d in truth.defects}
        assert touched_ids.isdisjoint(t1_matched_ids)

        # but the pipeline as a whole must still resolve them, via T3
        assert len(result.residue) == 0
        t3_matched_ids = {m.left.id for m in result.t3_matches}
        assert touched_ids <= t3_matched_ids


class TestGenuineLossesReachResidue:
    def test_missing_settlement_is_final_residue(self):
        ds = generate_clean(default_spec(seed=72))
        _, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 2})], seed=1)

        result = run_matching(MatchContext(dataset=ds))

        touched_ids = {d.subject_id for d in truth.defects}
        residue_ids = {u.id for u in result.residue}
        # a missing settlement has no bank line at all -- T3 cannot invent
        # one, so it MUST remain in residue, not be silently absorbed
        assert touched_ids <= residue_ids

    def test_unbooked_chargeback_visible_at_t1_and_t2_but_not_falsely_resolved(self):
        ds = generate_clean(default_spec(seed=73))
        _, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 2})], seed=1)

        result = run_matching(MatchContext(dataset=ds))

        touched_ids = {d.subject_id for d in truth.defects}
        t1_matched_ids = {m.left.id for m in result.t1_matches}
        t2_matched_ids = {m.left.id for m in result.t2_matches}

        # must not be silently matched at T1 (amount genuinely differs)
        assert touched_ids.isdisjoint(t1_matched_ids)
        # must not be silently matched at T2 (dispute isn't reflected in report)
        assert touched_ids.isdisjoint(t2_matched_ids)
        # T3 (fuzzy) should not resolve it either -- the amount really is
        # off by a real sum of money, not a formatting/identity quirk, so
        # it should still land in final residue for a future T4 to explain
        assert touched_ids <= {u.id for u in result.residue}


class TestTierCounts:
    def test_tier_counts_matches_actual_lengths(self):
        ds = generate_clean(default_spec(seed=74))
        _, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)

        result = run_matching(MatchContext(dataset=ds))
        counts = result.tier_counts()

        assert counts["T1"] == len(result.t1_matches)
        assert counts["T2"] == len(result.t2_matches)
        assert counts["T3"] == len(result.t3_matches)
        assert counts["unresolved"] == len(result.residue)
        assert counts["unresolved"] >= 1
