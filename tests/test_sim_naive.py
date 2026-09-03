from detect.rules import DetectConfig
from pipeline.run import PipelineConfig
from sim.journey import build_journey
from sim.naive import compare, run_naive

RATE_CARD = {"upi": 0, "card": 200, "amex": 300}


def _cfg() -> PipelineConfig:
    return PipelineConfig(detect_cfg=DetectConfig(rate_card_bps=RATE_CARD))


def _run(slug: str):
    journey = build_journey(slug, _cfg())
    return journey.dataset, journey.result


class TestNaiveBaselineIsFair:
    def test_naive_reconciles_clean_data_perfectly(self):
        """A strawman that failed on clean data would prove nothing. The
        baseline has to be genuinely competent at the easy case for its
        failures on the hard cases to mean anything."""
        ds, result = _run("happy_path")
        naive = run_naive(ds, RATE_CARD)
        assert naive.unresolved == []
        assert naive.flagged_missing_paise == 0

    def test_blind_spot_is_exactly_zero_on_clean_data(self):
        """Caught during the build: computing expected fee with floor
        division instead of core.money.apply_rate's ROUND_HALF_UP invented
        a sub-paisa 'overcharge' on most payments, totalling a Rs 0.24
        leak on a dataset with no leak in it. A comparison meant to prove
        a real leak must not manufacture a fake one."""
        ds, _ = _run("happy_path")
        assert run_naive(ds, RATE_CARD).blind_spot_paise == 0


class TestWhereNaiveFails:
    def test_naive_falsely_flags_a_rewritten_reference_number(self):
        """The expensive failure: real money that ARRIVED gets reported
        missing, sending a human hunting for a payment already in the bank."""
        ds, result = _run("utr_change")
        naive = run_naive(ds, RATE_CARD)

        assert len(naive.unresolved) == 1
        assert naive.flagged_missing_paise > 0
        # the real pipeline recovers it via a fallback tier
        assert len(result.match_result.residue) == 0

        comparison = compare(ds, result, RATE_CARD)
        assert comparison["difference"]["false_alarms_avoided"] == 1

    def test_naive_is_structurally_blind_to_a_fee_overcharge(self):
        """Settlement and bank agree exactly -- both internally consistent,
        both wrong. No amount of settlement<->bank matching can see this."""
        ds, result = _run("fee_overcharge")
        naive = run_naive(ds, RATE_CARD)

        assert naive.unresolved == []  # naive sees nothing wrong at all
        assert naive.blind_spot_paise > 0  # yet real money was taken

        comparison = compare(ds, result, RATE_CARD)
        assert comparison["difference"]["leak_money_naive_would_miss_paise"] > 0
        assert any(f["rule"] == "fee_overcharge" for f in comparison["real"]["findings"])


class TestComparisonShape:
    def test_both_sides_are_measured_over_the_same_dataset(self):
        ds, result = _run("unbooked_chargeback")
        comparison = compare(ds, result, RATE_CARD)

        assert 0 <= comparison["naive"]["match_rate_pct"] <= 100
        assert 0 <= comparison["real"]["match_rate_pct"] <= 100
        assert comparison["real"]["verifies_independently"] is True
        assert comparison["naive"]["verifies_independently"] is False

    def test_real_pipeline_never_scores_worse_across_the_catalogue(self):
        """If the naive version ever beat the real one, that would be a
        genuine argument the architecture is over-built -- and this test
        would say so rather than the claim going unchecked."""
        for slug in ["happy_path", "utr_change", "fee_overcharge", "unbooked_chargeback", "short_credit"]:
            ds, result = _run(slug)
            comparison = compare(ds, result, RATE_CARD)
            assert (
                comparison["real"]["match_rate_pct"] >= comparison["naive"]["match_rate_pct"]
            ), f"naive beat the real pipeline on {slug}"
