import pytest

from sim.reserve_sim import build_reserve_journey


class TestTheActualClaim:
    """Conservation FAILS without the reserve ledger and CLOSES with it,
    over identical data. Both figures come from the real
    invariant.conservation.check_conservation; the only difference between
    the two calls is whether the ledger's delta was supplied."""

    def test_every_period_fails_without_the_ledger_and_closes_with_it(self):
        journey = build_reserve_journey(n_periods=4)
        for period in journey["periods"]:
            without = period["conservation_without_ledger"]
            with_ledger = period["conservation_with_ledger"]

            assert without["closes"] is False, f"{period['label']} should not close unaided"
            assert without["residual_paise"] == period["reserve_delta_paise"]

            assert with_ledger["closes"] is True, f"{period['label']} should close with the ledger"
            assert with_ledger["residual_paise"] == 0

    def test_the_reserve_is_genuinely_absent_from_the_bank_not_just_declared(self):
        """Caught during the build: the first draft ran clean datasets, so
        conservation closed on its own and supplying a delta BROKE it --
        the numbers looked plausible and proved nothing. The money must
        actually be missing from the bank credit."""
        journey = build_reserve_journey(n_periods=2)
        first = journey["periods"][0]
        assert first["held"] > 0
        assert first["conservation_without_ledger"]["residual_paise"] == first["held"]


class TestMultiPeriodBehaviour:
    def test_a_release_actually_happens_within_the_default_window(self):
        """A demo that only ever accumulates would not show the thing
        being demonstrated."""
        journey = build_reserve_journey(n_periods=4)
        assert journey["verdict"]["any_release_happened"] is True
        assert journey["verdict"]["release_period_labels"]

    def test_a_release_period_can_have_a_negative_delta(self):
        """The second false-alarm direction: money ARRIVES that this
        period's own sales do not explain. A single-period view calls that
        an unexplained credit."""
        journey = build_reserve_journey(n_periods=4)
        assert any(p["reserve_delta_paise"] < 0 for p in journey["periods"])

    def test_balance_reconciles_with_holds_minus_releases(self):
        journey = build_reserve_journey(n_periods=5)
        totals = journey["totals"]
        assert (
            totals["outstanding_balance_paise"]
            == totals["total_held_paise"] - totals["total_released_paise"]
        )

    def test_periods_use_genuinely_different_data(self):
        """Each period is its own generated dataset, not one month
        repeated -- otherwise every held amount would be identical."""
        journey = build_reserve_journey(n_periods=4)
        assert len({p["settled_gross"] for p in journey["periods"]}) > 1

    def test_is_reproducible_for_a_seed(self):
        assert build_reserve_journey(n_periods=3, seed=555) == build_reserve_journey(
            n_periods=3, seed=555
        )

    @pytest.mark.parametrize("n", [1, 13])
    def test_rejects_an_unusable_period_count(self, n):
        with pytest.raises(ValueError):
            build_reserve_journey(n_periods=n)
