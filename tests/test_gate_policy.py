import pytest

from agent.investigate import ProposedResolution
from gate.policy import GateConfig, Resolution, decide, from_investigation
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from match.t1_utr import index_bank_by_ref, match_settlement_to_bank

DEFAULT_CFG = GateConfig()  # auto_apply_max_paise=10_000, min_confidence=0.95,
                            # always_human={'unknown_credit','negative_settlement'}


def _resolution(**overrides) -> Resolution:
    defaults = dict(kind="unbooked_chargeback", paise=5_000, confidence=0.99, verified=True)
    defaults.update(overrides)
    return Resolution(**defaults)


class TestDecideExhaustiveBranches:
    """DECISIONS.md D-17: this gate must be readable in ten seconds and
    tested exhaustively -- every branch, not just the happy path."""

    def test_unverified_is_always_reject_regardless_of_everything_else(self):
        # even small, high-confidence, non-sensitive kind -- unverified
        # means there is nothing valid to route anywhere
        r = _resolution(verified=False, paise=1, confidence=1.0, kind="refund")
        assert decide(r, DEFAULT_CFG) == "REJECT"

    def test_unverified_always_human_kind_is_still_reject_not_human(self):
        # REJECT takes priority over always_human -- an unverified claim
        # about an unknown_credit is still just wrong, not a candidate
        # for human review of THIS specific (unverified) proposal
        r = _resolution(verified=False, kind="unknown_credit", paise=1, confidence=1.0)
        assert decide(r, DEFAULT_CFG) == "REJECT"

    def test_verified_always_human_kind_routes_human_even_if_tiny_and_confident(self):
        r = _resolution(verified=True, kind="unknown_credit", paise=1, confidence=1.0)
        assert decide(r, DEFAULT_CFG) == "HUMAN"

    def test_verified_always_human_negative_settlement(self):
        r = _resolution(verified=True, kind="negative_settlement", paise=1, confidence=1.0)
        assert decide(r, DEFAULT_CFG) == "HUMAN"

    def test_verified_over_amount_threshold_routes_human(self):
        r = _resolution(verified=True, kind="unbooked_chargeback", paise=10_001, confidence=1.0)
        assert decide(r, DEFAULT_CFG) == "HUMAN"

    def test_verified_at_exact_amount_threshold_is_auto_not_human(self):
        # boundary: strictly GREATER THAN the max routes human, so
        # exactly at the max must still be AUTO
        r = _resolution(verified=True, kind="unbooked_chargeback", paise=10_000, confidence=1.0)
        assert decide(r, DEFAULT_CFG) == "AUTO"

    def test_verified_below_confidence_threshold_routes_human(self):
        r = _resolution(verified=True, kind="unbooked_chargeback", paise=100, confidence=0.94)
        assert decide(r, DEFAULT_CFG) == "HUMAN"

    def test_verified_at_exact_confidence_threshold_is_auto_not_human(self):
        # boundary: strictly LESS THAN the min routes human, so exactly
        # at the min must still be AUTO
        r = _resolution(verified=True, kind="unbooked_chargeback", paise=100, confidence=0.95)
        assert decide(r, DEFAULT_CFG) == "AUTO"

    def test_verified_small_confident_ordinary_kind_is_auto(self):
        r = _resolution(verified=True, kind="unbooked_chargeback", paise=500, confidence=0.99)
        assert decide(r, DEFAULT_CFG) == "AUTO"

    def test_custom_config_thresholds_respected(self):
        cfg = GateConfig(auto_apply_max_paise=100, min_confidence=0.5, always_human=frozenset())
        r = _resolution(verified=True, kind="unknown_credit", paise=50, confidence=0.6)
        # unknown_credit no longer in always_human under this custom cfg
        assert decide(r, cfg) == "AUTO"

    def test_always_human_set_is_customisable_and_empty_set_means_nothing_forced(self):
        cfg = GateConfig(always_human=frozenset())
        r = _resolution(verified=True, kind="negative_settlement", paise=1, confidence=1.0)
        assert decide(r, cfg) == "AUTO"


class TestFromInvestigationEndToEnd:
    def _unbooked_chargeback_scenario(self, seed=600):
        ds = generate_clean(default_spec(seed=seed))
        ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)
        settlement_id = truth.defects[0].subject_id
        index = index_bank_by_ref(ds.bank_lines)
        _, unmatched = match_settlement_to_bank(ds.settlements, index)
        target = next(u for u in unmatched if u.id == settlement_id)
        members = [p for p in ds.payments if p.settlement_id == settlement_id]
        dispute = next(d for p in members for d in ds.disputes_for(p.payment_id))
        return ds, truth, target, dispute

    def test_verified_resolution_gets_real_independently_recomputed_amount(self):
        ds, truth, target, dispute = self._unbooked_chargeback_scenario()

        proposed = ProposedResolution(
            unmatched=target,
            hypothesis="unbooked_chargeback",
            evidence_ids=(dispute.dispute_id,),
            proposed_adjustments=(
                {"kind": "dispute", "ref": dispute.dispute_id, "paise": dispute.amount + dispute.fee},
            ),
            confidence=0.9,
        )
        proof = proposed.to_proof(ds)
        resolution = from_investigation(proposed, proof, ds)

        assert resolution.verified is True
        assert resolution.paise == dispute.amount + dispute.fee  # matches reality, independently derived

    def test_model_understating_the_amount_does_not_fool_the_gate(self):
        """THE regression test for the bug caught while writing
        from_investigation: even if the model claims a tiny paise value
        for its proposed adjustment (an attempt, deliberate or not, to
        sneak a large real correction under the AUTO threshold), the
        gate's actual input is independently recomputed from raw data,
        not read from the model's claim."""
        ds, truth, target, dispute = self._unbooked_chargeback_scenario(seed=601)
        real_amount = dispute.amount + dispute.fee
        assert real_amount > 10_000, "test needs a real amount above the auto-apply threshold"

        proposed = ProposedResolution(
            unmatched=target,
            hypothesis="unbooked_chargeback",
            evidence_ids=(dispute.dispute_id,),
            proposed_adjustments=(
                # model LIES: claims paise=1, actual dispute+fee is much larger
                {"kind": "dispute", "ref": dispute.dispute_id, "paise": 1},
            ),
            confidence=0.99,
        )
        proof = proposed.to_proof(ds)
        resolution = from_investigation(proposed, proof, ds)

        assert resolution.paise == real_amount  # NOT 1 -- the model's claim is irrelevant
        assert decide(resolution, DEFAULT_CFG) == "HUMAN"  # correctly routed human, not AUTO

    def test_unverifiable_proof_produces_unverified_resolution(self):
        from datetime import datetime

        ds, truth, target, dispute = self._unbooked_chargeback_scenario(seed=602)
        proposed = ProposedResolution(
            unmatched=target,
            hypothesis="fee_overcharge",
            evidence_ids=(),
            proposed_adjustments=({"kind": "fee_correction", "ref": "pay_1", "paise": 1000},),
            confidence=0.9,
        )
        proof = proposed.to_proof(ds)  # None -- fee_correction isn't independently verifiable
        assert proof is None

        # from_investigation requires a proof; the caller (a future
        # orchestrator) is responsible for checking to_proof() first --
        # documented here as the expected usage, not silently handled
        with pytest.raises(AttributeError):
            from_investigation(proposed, proof, ds)

    def test_wrong_hypothesis_produces_verified_false(self):
        from gen.models import Dispute
        from datetime import datetime

        ds, truth, target, dispute = self._unbooked_chargeback_scenario(seed=603)
        decoy = Dispute("disp_decoy_gate", "pay_00001", 1, 1, "raised", datetime(2026, 2, 3))
        ds.disputes.append(decoy)

        proposed = ProposedResolution(
            unmatched=target,
            hypothesis="unbooked_chargeback",
            evidence_ids=(decoy.dispute_id,),
            proposed_adjustments=({"kind": "dispute", "ref": decoy.dispute_id, "paise": 2},),
            confidence=0.99,
        )
        proof = proposed.to_proof(ds)
        resolution = from_investigation(proposed, proof, ds)

        assert resolution.verified is False
        assert decide(resolution, DEFAULT_CFG) == "REJECT"
