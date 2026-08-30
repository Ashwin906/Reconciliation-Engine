from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from match.t0_key import group_payments_by_settlement
from match.t2_set import find_subset, match_group_to_settlement, SubsetResult


class TestCleanDataTiesAtT2:
    def test_every_settlement_ties_its_own_arithmetic(self):
        ds = generate_clean(default_spec(seed=50))
        groups = group_payments_by_settlement(ds.payments)

        for settlement in ds.settlements:
            members = groups.get(settlement.settlement_id, [])
            result = match_group_to_settlement(settlement, members, ds.refunds, ds.disputes)
            from match.types import MatchCandidate

            assert isinstance(result, MatchCandidate), (
                f"{settlement.settlement_id} should tie in clean data, got {result}"
            )
            assert result.proof.ties


class TestFeeOverchargeStillTiesAtT2:
    """DOMAIN_KNOWLEDGE.md section 11 / gen/defects.py's own docstring:
    fee_overcharge moves the fee AND the settlement amount together, so
    it must be INVISIBLE to T2's tie-out -- only detectable later via
    rate comparison (Phase 8), not via reconciliation."""

    def test_fee_overcharge_ties(self):
        ds = generate_clean(default_spec(seed=51))
        _, truth = apply_defects(
            ds, [DefectSpec("fee_overcharge", {"method": "card", "extra_bps": 36, "days": 30})], seed=1
        )
        assert len(truth.defects) > 0

        groups = group_payments_by_settlement(ds.payments)
        touched_settlement_ids = {
            ds.payment_by_id(d.subject_id).settlement_id for d in truth.defects
        }

        from match.types import MatchCandidate

        for sid in touched_settlement_ids:
            settlement = next(s for s in ds.settlements if s.settlement_id == sid)
            members = groups.get(sid, [])
            result = match_group_to_settlement(settlement, members, ds.refunds, ds.disputes)
            assert isinstance(result, MatchCandidate), (
                f"{sid}: fee_overcharge should be invisible to T2, but got {result}"
            )


class TestUnbookedChargebackFailsAtT2:
    def test_flagged_as_internal_mismatch(self):
        ds = generate_clean(default_spec(seed=52))
        _, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 2})], seed=1)

        groups = group_payments_by_settlement(ds.payments)
        from match.types import Unmatched

        for d in truth.defects:
            settlement = next(s for s in ds.settlements if s.settlement_id == d.subject_id)
            members = groups.get(d.subject_id, [])
            result = match_group_to_settlement(settlement, members, ds.refunds, ds.disputes)
            assert isinstance(result, Unmatched)
            assert result.reason == "t2_internal_mismatch"
            # the residual should equal the negative of the unbooked shortfall --
            # computed (post-dispute) is LESS than reported (pre-dispute)
            assert result.detail["residual"] == -d.impact_paise


class TestReserveHoldStillTiesAtT2:
    """T2 doesn't know about reserves at all -- the settlement report
    itself is unchanged, only the bank line is short. That discrepancy
    belongs to T1, not T2."""

    def test_reserve_hold_ties_at_t2(self):
        ds = generate_clean(default_spec(seed=53))
        _, truth = apply_defects(ds, [DefectSpec("reserve_hold", {"n": 2})], seed=1)
        assert len(truth.defects) > 0

        groups = group_payments_by_settlement(ds.payments)
        from match.types import MatchCandidate

        for d in truth.defects:
            settlement = next(s for s in ds.settlements if s.settlement_id == d.subject_id)
            members = groups.get(d.subject_id, [])
            result = match_group_to_settlement(settlement, members, ds.refunds, ds.disputes)
            assert isinstance(result, MatchCandidate)


class TestFindSubset:
    def test_exact_one_solution(self):
        candidates = [("pay_1", 1000), ("pay_2", 2000), ("pay_3", 500)]
        result = find_subset(candidates, target=3000)
        assert result.outcome == "EXACT_ONE"
        assert set(result.member_ids) == {"pay_1", "pay_2"}

    def test_no_solution(self):
        candidates = [("pay_1", 1000), ("pay_2", 2000)]
        result = find_subset(candidates, target=999_999)
        assert result.outcome == "NONE"

    def test_ambiguous_when_two_subsets_match(self):
        # {1000,500} and {1500} (as a single candidate) both sum to 1500
        candidates = [("pay_1", 1000), ("pay_2", 500), ("pay_3", 1500)]
        result = find_subset(candidates, target=1500)
        assert result.outcome == "AMBIGUOUS"
        # D-13: ambiguous must not report a member_ids as if it were certain
        assert result.member_ids == ()

    def test_empty_subset_never_counts_as_a_solution(self):
        candidates = [("pay_1", 1000)]
        result = find_subset(candidates, target=0)
        assert result.outcome == "NONE"

    def test_stops_early_after_two_solutions_not_exhaustive(self):
        # many possible subsets sum to the target; function must not need
        # to enumerate all of them, only detect there's more than one
        candidates = [(f"pay_{i}", 100) for i in range(20)]  # 20 candidates of 100 each
        result = find_subset(candidates, target=300)  # C(20,3) = 1140 possible subsets
        assert result.outcome == "AMBIGUOUS"
        assert result.solutions_found == 2  # stopped at 2, did not count all 1140
