import pytest

from detect.rules import DetectConfig
from pipeline.run import PipelineConfig
from sim.journey import SCENARIOS, build_custom_journey, build_journey, list_scenarios

RATE_CARD = {"upi": 0, "card": 200, "amex": 300}


def _cfg() -> PipelineConfig:
    return PipelineConfig(detect_cfg=DetectConfig(rate_card_bps=RATE_CARD))


class TestScenarioCatalogue:
    def test_every_scenario_has_a_merchant_story(self):
        for s in list_scenarios():
            assert s["merchant_story"].strip(), s["slug"]

    def test_slugs_are_unique(self):
        slugs = [s.slug for s in SCENARIOS]
        assert len(slugs) == len(set(slugs))


class TestJourneyBuildsForEveryScenario:
    @pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.slug)
    def test_builds_without_error_and_narrates_stages(self, scenario):
        """Every scenario in the catalogue must actually run -- a broken
        injector param (fee_overcharge takes method/extra_bps/days, not n)
        would otherwise only surface in a live demo."""
        journey = build_journey(scenario.slug, _cfg()).payload
        assert len(journey["stages"]) >= 5
        for stage in journey["stages"]:
            assert stage["title"]
            assert stage["detail"]
            assert stage["status"] in {"ok", "warn", "fail", "info"}


class TestJourneyNumbersComeFromTheRealPipeline:
    def test_happy_path_fully_reconciles(self):
        journey = build_journey("happy_path", _cfg()).payload
        outcome = journey["outcome"]
        assert outcome["residue"] == 0
        assert outcome["match_rate_pct"] == 100.0
        assert outcome["conservation_closes"] is True
        assert journey["planted_defect"] is None

    def test_chargeback_scenario_produces_real_residue(self):
        journey = build_journey("unbooked_chargeback", _cfg()).payload
        assert journey["outcome"]["residue"] == 1
        assert journey["planted_defect"]["kind"] == "unbooked_chargeback"

    def test_fee_overcharge_reconciles_perfectly_but_still_gets_caught(self):
        """The scenario worth demoing: settlement and bank agree exactly,
        so matching finds nothing wrong -- and the rule-based detectors
        catch it anyway. Proves detectors are not redundant with matching."""
        journey = build_journey("fee_overcharge", _cfg()).payload
        outcome = journey["outcome"]
        assert outcome["residue"] == 0
        assert outcome["match_rate_pct"] == 100.0
        assert any(f["rule"] == "fee_overcharge" for f in outcome["findings"])

    def test_utr_change_is_rescued_by_a_fallback_tier_not_left_as_residue(self):
        """Exact (T1) matching cannot tie a rewritten UTR. If the fallback
        tiers did nothing useful, this would show up as residue -- money
        falsely reported missing. It must reconcile instead."""
        journey = build_journey("utr_change", _cfg()).payload
        assert journey["outcome"]["residue"] == 0

        matching = next(s for s in journey["stages"] if s["key"] == "matching")
        assert matching["facts"]["t3"] >= 1, "expected a T3 fuzzy match to do the rescuing"

    def test_unknown_scenario_raises_keyerror_for_the_route_to_404(self):
        with pytest.raises(KeyError):
            build_journey("no_such_scenario", _cfg())


class TestJourneyIsSerialisable:
    def test_stage_facts_contain_no_raw_datetimes(self):
        """Unmatched.detail carries dates from raw records -- they must be
        coerced before the API layer tries to serialise a trace."""
        import json

        for scenario in SCENARIOS:
            journey = build_journey(scenario.slug, _cfg()).payload
            json.dumps(journey)  # raises TypeError if anything is unserialisable


class TestMoneyFlowMatchesTheConservationProof:
    """The Sankey must show the same story the proof does -- it reads the
    conservation identity rather than recomputing a flow for display."""

    def test_flow_reconciles_to_the_conservation_identity(self):
        for scenario in SCENARIOS:
            run = build_journey(scenario.slug, _cfg())
            flow = run.payload["money_flow"]
            c = run.result.conservation

            assert flow["collected_paise"] == c.collected
            assert flow["reached_bank_paise"] == c.settled
            assert flow["residual_paise"] == c.residual
            assert flow["closes"] == c.closes

            # collected = reached bank + everything withheld + residual
            total = flow["reached_bank_paise"] + flow["withheld_paise"] + flow["residual_paise"]
            assert total == flow["collected_paise"], scenario.slug

    def test_zero_value_doors_are_kept_not_filtered(self):
        """A branch vanishing between runs is harder to read than one at
        zero, and 'no chargebacks this period' is information."""
        flow = build_journey("happy_path", _cfg()).payload["money_flow"]
        labels = {d["label"] for d in flow["deductions"]}
        assert labels == {"Razorpay fee", "GST", "Refunds", "Chargebacks", "Chargeback fees"}


class TestCustomJourney:
    def test_composes_multiple_defects_the_viewer_chose(self):
        run = build_custom_journey(
            [
                {"kind": "unbooked_chargeback", "params": {"n": 1}},
                {"kind": "missing_settlement", "params": {"n": 1}},
            ],
            _cfg(),
        )
        kinds = {d["kind"] for d in run.payload["planted_defects"]}
        assert kinds == {"unbooked_chargeback", "missing_settlement"}
        assert run.payload["outcome"]["residue"] >= 1

    def test_uses_the_same_pipeline_as_a_built_in_scenario(self):
        """No separate 'custom mode' path that could behave differently
        from the one being demoed -- that is the entire point of letting
        someone else choose the inputs."""
        run = build_custom_journey([{"kind": "unbooked_chargeback", "params": {"n": 1}}], _cfg())
        assert {s["key"] for s in run.payload["stages"]} >= {
            "customer_pays",
            "matching",
            "conservation",
        }
        assert "comparison" in run.payload
        assert "money_flow" in run.payload

    def test_is_reproducible_for_a_given_seed(self):
        first = build_custom_journey([{"kind": "short_credit", "params": {"n": 1}}], _cfg(), seed=99)
        second = build_custom_journey([{"kind": "short_credit", "params": {"n": 1}}], _cfg(), seed=99)

        # duration_s is wall-clock and legitimately differs run to run --
        # comparing it would make this test flaky for a reason that has
        # nothing to do with reproducibility of the RESULT.
        def without_timing(outcome: dict) -> dict:
            return {k: v for k, v in outcome.items() if k != "duration_s"}

        assert without_timing(first.payload["outcome"]) == without_timing(second.payload["outcome"])
        assert first.payload["planted_defects"] == second.payload["planted_defects"]
        assert first.payload["money_flow"] == second.payload["money_flow"]

    def test_empty_plan_is_rejected(self):
        with pytest.raises(ValueError, match="at least one"):
            build_custom_journey([], _cfg())

    def test_too_many_defects_rejected(self):
        with pytest.raises(ValueError, match="at most"):
            build_custom_journey(
                [{"kind": "unbooked_chargeback", "params": {"n": 1}}] * 10, _cfg()
            )

    def test_bad_param_surfaces_a_specific_message(self):
        with pytest.raises(ValueError, match="no parameter"):
            build_custom_journey([{"kind": "unbooked_chargeback", "params": {"zzz": 1}}], _cfg())
