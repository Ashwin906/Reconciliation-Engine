import json

import pytest

from detect.rules import DetectConfig
from pipeline.run import PipelineConfig
from sim.adversary import ATTACKS, AdversarialTransport, attack_catalog
from sim.journey import build_adversarial_journey

RATE_CARD = {"upi": 0, "card": 200, "amex": 300}


def _cfg() -> PipelineConfig:
    return PipelineConfig(detect_cfg=DetectConfig(rate_card_bps=RATE_CARD))


class TestEveryAttackIsStopped:
    """The claim under test is NOT 'the model never lies' -- it is
    supposed to be able to, that is the threat model. The claim is that
    no lie reaches AUTO, i.e. no false correction is applied to real
    books without a human."""

    @pytest.mark.parametrize("attack", ATTACKS)
    def test_nothing_is_auto_applied(self, attack):
        report = build_adversarial_journey(attack, _cfg()).payload["adversarial"]
        assert report["auto_applied"] == 0, f"{attack} got a false correction auto-applied"
        assert report["money_moved_without_a_human"] == 0
        assert report["held"] is True

    @pytest.mark.parametrize("attack", ATTACKS)
    def test_the_attack_was_actually_attempted(self, attack):
        """Guards against a vacuous pass: an attack that never reached the
        pipeline would also report zero auto-applies."""
        report = build_adversarial_journey(attack, _cfg()).payload["adversarial"]
        assert report["attempts"], f"{attack} never actually sent anything"
        assert report["investigated"] >= 1


class TestEachDefenceLayerCatchesItsOwnAttack:
    def test_fabricated_evidence_is_stopped_before_any_arithmetic(self):
        """D-12: the hallucinated-id check runs before a proof is even
        built, so this never reaches the gate at all."""
        report = build_adversarial_journey("fabricated_evidence", _cfg()).payload["adversarial"]
        assert report["stopped_before_gate"] == 1
        assert report["rejected_by_gate"] == 0

    def test_understated_amount_is_caught_by_independent_recompute(self):
        """THE one. The model cites real evidence but claims the
        adjustment is 1 paisa -- far under the gate's auto-apply ceiling.
        A gate that read the model's own number would apply it silently.
        This gate recomputes the real size from raw data, sees it exceeds
        the threshold, and routes to a human instead.

        This is the exact bug FAILURES.md #1 documents shipping once.
        """
        payload = build_adversarial_journey("understated_amount", _cfg()).payload
        report = payload["adversarial"]

        attempt = report["attempts"][0]
        assert attempt["claimed_paise"] == 1
        assert attempt["true_paise"] > 10_000, "the lie must be materially large to be meaningful"

        assert report["auto_applied"] == 0
        assert report["sent_to_human"] == 1

    def test_wrong_but_real_evidence_fails_verification(self):
        """Every id is real and the JSON is perfect -- only re-derivation
        from raw data can catch this."""
        report = build_adversarial_journey("wrong_but_real", _cfg()).payload["adversarial"]
        assert report["rejected_by_gate"] == 1
        assert report["auto_applied"] == 0

    def test_confidence_alone_buys_nothing(self):
        report = build_adversarial_journey("overconfident", _cfg()).payload["adversarial"]
        assert report["attempts"][0]["confidence"] == 1.0
        assert report["auto_applied"] == 0


class TestTransportBehavesLikeARealOne:
    def test_returns_parseable_json_matching_the_schema_shape(self):
        transport = AdversarialTransport("fabricated_evidence")
        packet = json.dumps(
            {
                "unexplained": {"id": "setl_1"},
                "disputes_on_members": [
                    {"dispute_id": "disp_1", "amount_paise": 5000, "fee_paise": 200}
                ],
                "refunds_on_members": [],
            }
        )
        raw = transport.complete("sys", packet, "model", 0.0, {})
        parsed = json.loads(raw)
        assert set(parsed) == {"hypothesis", "evidence_ids", "proposed_adjustments", "confidence"}

    def test_unknown_attack_rejected(self):
        with pytest.raises(ValueError, match="unknown attack"):
            AdversarialTransport("not_an_attack")

    def test_catalog_covers_every_attack_and_names_a_defence(self):
        catalog = attack_catalog()
        assert {entry["attack"] for entry in catalog} == set(ATTACKS)
        for entry in catalog:
            assert entry["defence"] and entry["attempt"]
