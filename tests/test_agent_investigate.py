import json

from agent.client import GroqClient
from agent.investigate import NoHypothesis, ProposedResolution, investigate
from agent.packet import PacketConfig
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from match.t0_key import group_payments_by_settlement
from match.t1_utr import index_bank_by_ref, match_settlement_to_bank
from verify.recompute import verify_proof


class FakeLLMTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, user, model, temperature, schema=None):
        self.calls.append((system, user, model, temperature))
        return self.responses.pop(0)


def _client(responses):
    return GroqClient(transport=FakeLLMTransport(responses), sleep_fn=lambda _: None)


def _unbooked_chargeback_scenario(seed=500):
    """A real unbooked_chargeback defect, matched at T1 to get the exact
    Unmatched this whole T4 pipeline is meant to explain, plus the
    specific dispute record that explains it."""
    ds = generate_clean(default_spec(seed=seed))
    ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)
    settlement_id = truth.defects[0].subject_id

    index = index_bank_by_ref(ds.bank_lines)
    _, unmatched = match_settlement_to_bank(ds.settlements, index)
    target = next(u for u in unmatched if u.id == settlement_id)

    members = [p for p in ds.payments if p.settlement_id == settlement_id]
    dispute = next(d for p in members for d in ds.disputes_for(p.payment_id))
    return ds, truth, target, dispute


class TestInvestigateEndToEnd:
    def test_correct_hypothesis_produces_a_proof_the_independent_verifier_accepts(self):
        """THE money test for this whole phase: the agent proposes a
        hypothesis citing a real dispute, ProposedResolution.to_proof()
        builds a Proof from it, and verify.recompute.verify_proof --
        Phase 4's independent verifier, which has never heard of agent/
        (DECISIONS.md D-02) -- independently ACCEPTS it. This is
        DECISIONS.md D-07 end to end: the agent proposes, deterministic
        code decides."""
        ds, truth, target, dispute = _unbooked_chargeback_scenario()

        response = json.dumps({
            "hypothesis": "unbooked_chargeback",
            "evidence_ids": [dispute.dispute_id],
            "proposed_adjustments": [
                {"kind": "dispute", "ref": dispute.dispute_id, "paise": dispute.amount + dispute.fee}
            ],
            "confidence": 0.9,
        })
        client = _client([response])

        result = investigate(target, ds, client, PacketConfig())

        assert isinstance(result, ProposedResolution)
        assert result.hypothesis == "unbooked_chargeback"

        proof = result.to_proof(ds)
        assert proof is not None

        verify_result = verify_proof(proof, ds)
        assert verify_result.accepted, (
            f"independent verifier rejected a correct T4 hypothesis: residual={verify_result.residual}"
        )


class TestInvestigateRejectsHallucination:
    def test_evidence_id_not_in_raw_data_produces_no_hypothesis(self):
        """DECISIONS.md D-12: hallucinated evidence rejected before any
        arithmetic runs."""
        ds, truth, target, dispute = _unbooked_chargeback_scenario(seed=501)

        response = json.dumps({
            "hypothesis": "unbooked_chargeback",
            "evidence_ids": ["disp_totally_made_up_00999"],
            "proposed_adjustments": [
                {"kind": "dispute", "ref": "disp_totally_made_up_00999", "paise": 50000}
            ],
            "confidence": 0.9,
        })
        client = _client([response])

        result = investigate(target, ds, client, PacketConfig())
        assert isinstance(result, NoHypothesis)
        assert "disp_totally_made_up_00999" in result.reason


class TestInvestigateTransportFailure:
    def test_transport_failure_degrades_to_no_hypothesis_not_a_crash(self):
        """A live rate-limit/network failure must not crash the whole
        batch -- same graceful-degradation contract as a schema failure.
        Caught live: Groq 429 during a real run previously took down the
        entire /runs request with an unhandled 500."""
        import requests

        class FailingTransport:
            def complete(self, system, user, model, temperature, schema=None):
                response = requests.Response()
                response.status_code = 429
                raise requests.exceptions.HTTPError("429 Too Many Requests", response=response)

        ds, truth, target, dispute = _unbooked_chargeback_scenario(seed=503)
        client = GroqClient(transport=FailingTransport(), sleep_fn=lambda _: None)

        result = investigate(target, ds, client, PacketConfig())
        assert isinstance(result, NoHypothesis)
        assert "429" in result.reason


class TestInvestigateOtherUnexplained:
    def test_other_unexplained_is_honest_no_hypothesis(self):
        ds, truth, target, dispute = _unbooked_chargeback_scenario(seed=502)
        response = json.dumps({
            "hypothesis": "other_unexplained",
            "evidence_ids": [],
            "proposed_adjustments": [],
            "confidence": 0.1,
        })
        client = _client([response])

        result = investigate(target, ds, client, PacketConfig())
        assert isinstance(result, NoHypothesis)

    def test_note_carries_the_models_observation_into_no_hypothesis(self):
        """A novel failure mode the enum can't express should still reach
        a human carrying WHAT the model noticed, not just that it gave up.
        The enum is NOT widened -- this is advisory annotation only."""
        ds, truth, target, dispute = _unbooked_chargeback_scenario(seed=506)
        response = json.dumps({
            "hypothesis": "other_unexplained",
            "evidence_ids": [],
            "proposed_adjustments": [],
            "confidence": 0.1,
            "note": "Bank credit arrived 3 days late with no matching dispute record.",
        })
        client = _client([response])

        result = investigate(target, ds, client, PacketConfig())
        assert isinstance(result, NoHypothesis)
        assert result.note == "Bank credit arrived 3 days late with no matching dispute record."

    def test_blank_note_normalises_to_none(self):
        ds, truth, target, dispute = _unbooked_chargeback_scenario(seed=507)
        response = json.dumps({
            "hypothesis": "other_unexplained",
            "evidence_ids": [],
            "proposed_adjustments": [],
            "confidence": 0.1,
            "note": "   ",
        })
        client = _client([response])

        result = investigate(target, ds, client, PacketConfig())
        assert result.note is None


class TestProposedResolutionToProof:
    def test_unverifiable_adjustment_kind_produces_no_proof(self):
        """A 'fee_correction' adjustment cannot be independently
        re-derived by verify/recompute.py -- to_proof must return None
        rather than a Proof nothing can actually check. The hypothesis
        is still returned by investigate() (it's recorded), but stays
        unverifiable, human-review-only."""
        ds, truth, target, dispute = _unbooked_chargeback_scenario(seed=503)

        resolution = ProposedResolution(
            unmatched=target,
            hypothesis="fee_overcharge",
            evidence_ids=(),
            proposed_adjustments=({"kind": "fee_correction", "ref": "pay_1", "paise": 1000},),
            confidence=0.5,
        )
        assert resolution.to_proof(ds) is None

    def test_no_settlement_found_produces_no_proof(self):
        from match.types import Unmatched

        ds = generate_clean(default_spec(seed=504))
        resolution = ProposedResolution(
            unmatched=Unmatched(kind="settlement", id="setl_ghost", reason="x", detail={}),
            hypothesis="missing_settlement",
            evidence_ids=(),
            proposed_adjustments=(),
            confidence=0.5,
        )
        assert resolution.to_proof(ds) is None

    def test_incorrect_hypothesis_produces_a_proof_the_verifier_rejects(self):
        """The other essential case: a WRONG hypothesis must still be
        catchable. If the agent proposes an adjustment that does NOT
        actually explain the gap, the independent verifier must reject
        it -- proving the agent's confidence carries no weight on its own."""
        ds, truth, target, dispute = _unbooked_chargeback_scenario(seed=505)

        # propose a dispute_id that exists but ISN'T the one causing this
        # gap -- the verifier recomputes from raw data regardless of the
        # proposed paise value, so what matters is citing the WRONG source
        other_dispute = next(
            (d for d in ds.disputes if d.dispute_id != dispute.dispute_id), None
        )
        if other_dispute is None:
            from gen.models import Dispute
            from datetime import datetime
            other_dispute = Dispute("disp_decoy", "pay_00001", 1, 1, "raised", datetime(2026, 2, 3))
            ds.disputes.append(other_dispute)

        wrong_resolution = ProposedResolution(
            unmatched=target,
            hypothesis="unbooked_chargeback",
            evidence_ids=(other_dispute.dispute_id,),
            proposed_adjustments=(
                {"kind": "dispute", "ref": other_dispute.dispute_id, "paise": other_dispute.amount + other_dispute.fee},
            ),
            confidence=0.99,
        )
        proof = wrong_resolution.to_proof(ds)
        assert proof is not None
        result = verify_proof(proof, ds)
        assert result.accepted is False
