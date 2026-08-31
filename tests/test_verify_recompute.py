from dataclasses import replace

import pytest

from core.proof import Proof, ProofStep
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from match.t0_key import group_payments_by_settlement
from match.t2_set import match_group_to_settlement
from match.types import MatchCandidate
from verify.recompute import recompute_observed, recompute_step, verify_all, verify_proof


def _t2_proofs_for(ds):
    """Builds real T2 proofs from real match/ code, for every settlement
    that ties -- these are genuine matcher output, not hand-constructed
    test fixtures, so verifying them is verifying the actual pipeline."""
    groups = group_payments_by_settlement(ds.payments)
    proofs = []
    for settlement in ds.settlements:
        members = groups.get(settlement.settlement_id, [])
        result = match_group_to_settlement(settlement, members, ds.refunds, ds.disputes)
        if isinstance(result, MatchCandidate):
            proofs.append(result.proof)
    return proofs


class TestRecomputeStep:
    def test_sums_payments_by_id_independently(self):
        ds = generate_clean(default_spec(seed=80))
        settlement = ds.settlements[0]
        groups = group_payments_by_settlement(ds.payments)
        members = groups[settlement.settlement_id]

        step = ProofStep(
            "sum",
            f"payments[settlement_id={settlement.settlement_id}]",
            tuple(p.payment_id for p in members),
            999999999,  # deliberately WRONG stored value
        )
        result = recompute_step(step, ds)
        # must recompute the REAL sum, ignoring step.value entirely
        expected = sum(p.amount for p in members)
        assert result == expected
        assert result != 999999999

    def test_gst_recomputed_from_fee_not_read_from_stored_tax(self):
        """The meaningful independence test: if this just summed
        payment.tax fields, it would trust the same number the matcher
        (and the generator) already trusted. It must recompute GST from
        the fee via core.money.gst_on."""
        ds = generate_clean(default_spec(seed=81))
        settlement = ds.settlements[0]
        groups = group_payments_by_settlement(ds.payments)
        members = groups[settlement.settlement_id]

        # corrupt the stored .tax fields -- if recompute_step were
        # secretly just summing .tax, this corruption would slip through
        for p in members:
            p.tax = 999999

        step = ProofStep(
            "subtract",
            "gst_on_fees",
            tuple(p.payment_id for p in members),
            sum(p.tax for p in members),  # the (now corrupted) stored total
        )
        result = recompute_step(step, ds)

        from core.money import gst_on

        # GST is rounded PER PAYMENT then summed, matching how gen/build.py
        # and match/t2_set.py both compute it -- NOT gst_on(sum(fees)),
        # which can differ by a paisa or two due to rounding accumulation
        # (a real mismatch caught while building this test suite; see
        # FUNCTION_REFERENCE.md and recompute_step's own docstring)
        expected = sum(gst_on(p.fee) for p in members)
        assert result == expected
        assert result != 999999 * len(members)

    def test_settlement_amount_source_recomputed_independently(self):
        """Added alongside agent/investigate.py (Phase 9): a T4 proof
        starts from the settlement's reported amount as a known input.
        Proves recompute_step still ignores step.value even for this
        new source -- reads raw.settlements fresh, same as every other
        source in this function."""
        ds = generate_clean(default_spec(seed=92))
        settlement = ds.settlements[0]
        step = ProofStep(
            "sum", "settlement_amount", (settlement.settlement_id,), value=999999999
        )
        result = recompute_step(step, ds)
        assert result == settlement.amount
        assert result != 999999999

    def test_unrecognised_source_raises(self):
        ds = generate_clean(default_spec(seed=82))
        step = ProofStep("sum", "something_unknown", (), 0)
        try:
            recompute_step(step, ds)
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestRecomputeObserved:
    def test_settlement_observed(self):
        ds = generate_clean(default_spec(seed=83))
        settlement = ds.settlements[0]
        result = recompute_observed(f"settlement:{settlement.settlement_id}", ds)
        assert result == settlement.amount

    def test_bank_line_observed_credit(self):
        ds = generate_clean(default_spec(seed=84))
        bank_line = ds.bank_lines[0]
        result = recompute_observed(f"bank_line:{bank_line.line_id}", ds)
        assert result == bank_line.credit

    def test_malformed_source_raises(self):
        ds = generate_clean(default_spec(seed=85))
        try:
            recompute_observed("no_colon_here", ds)
            assert False, "expected ValueError"
        except ValueError:
            pass


class TestVerifyProofAcceptsGenuineMatches:
    def test_clean_data_t2_proofs_all_accepted(self):
        ds = generate_clean(default_spec(seed=86))
        proofs = _t2_proofs_for(ds)
        assert len(proofs) > 0

        for proof in proofs:
            result = verify_proof(proof, ds)
            assert result.accepted, f"expected ACCEPT, residual={result.residual}"
            assert result.residual == 0


class TestVerifyProofIgnoresTamperedStepValue:
    """The core trust-boundary test from the module docstring: a step
    whose claimed .value is WRONG, but where the raw data independently
    still supports a correct proof, must still be REJECTED overall if
    the tampered value was what the (untrusted) claim relied on -- the
    verifier must compute its OWN total from raw data, never read
    step.value into that total."""

    def test_tampered_step_value_does_not_fool_the_verifier(self):
        ds = generate_clean(default_spec(seed=87))
        proofs = _t2_proofs_for(ds)
        proof = proofs[0]

        # tamper the FIRST step's stored value to something wrong
        tampered_steps = list(proof.steps)
        tampered_steps[0] = replace(tampered_steps[0], value=tampered_steps[0].value + 999_999)
        tampered_proof = replace(proof, steps=tuple(tampered_steps))

        # verify_proof must recompute step[0] from raw data (ignoring the
        # tampered .value) and therefore still reach the SAME correct
        # residual as the untampered proof
        result = verify_proof(tampered_proof, ds)
        original_result = verify_proof(proof, ds)
        assert result.residual == original_result.residual
        assert result.accepted == original_result.accepted


class TestVerifyProofRejectsWhenRawDataDisagrees:
    def test_settlement_matcher_claims_tie_but_raw_data_no_longer_supports_it(self):
        """Simulates the real scenario D-03 exists for: the matcher's
        Proof says residual==0 (proof.ties is True), but the underlying
        raw data has since changed such that independent recomputation
        disagrees. The matcher's own claim must NOT be trusted."""
        ds = generate_clean(default_spec(seed=88))
        proofs = _t2_proofs_for(ds)
        proof = proofs[0]
        assert proof.ties  # matcher believed this was a match

        # mutate the underlying payment amount AFTER the proof was built --
        # the proof's own .computed is now stale, but its .steps still
        # cite the real payment_ids, so recompute_step will pick up the change
        settlement_id = proof.observed_source.split(":")[1]
        member = next(p for p in ds.payments if p.settlement_id == settlement_id)
        member.amount += 50_000

        result = verify_proof(proof, ds)
        assert result.accepted is False
        assert result.residual == 50_000


class TestVerifyAll:
    def test_aggregates_accept_and_reject_counts(self):
        ds = generate_clean(default_spec(seed=89))
        proofs = _t2_proofs_for(ds)

        # corrupt raw data behind ONE proof to force a rejection
        settlement_id = proofs[0].observed_source.split(":")[1]
        member = next(p for p in ds.payments if p.settlement_id == settlement_id)
        member.amount += 12_345

        report = verify_all(proofs, ds)

        assert report.total == len(proofs)
        assert report.rejected == 1
        assert report.accepted == len(proofs) - 1
        assert 0.0 < report.acceptance_rate < 1.0
        assert len(report.rejected_items) == 1
        rejected_proof, rejected_result = report.rejected_items[0]
        assert rejected_result.residual == 12_345

    def test_empty_proof_list_has_acceptance_rate_one(self):
        ds = generate_clean(default_spec(seed=90))
        report = verify_all([], ds)
        assert report.total == 0
        assert report.acceptance_rate == 1.0


class TestVerifyProofOnRealDefects:
    """End-to-end: run a T2 proof for a settlement touched by
    fee_overcharge (should still ACCEPT -- DOMAIN_KNOWLEDGE.md section
    11) through the actual independent verifier, not just the matcher's
    own tie-out check.

    Parametrized over many seeds deliberately -- this exact scenario
    surfaced two real off-by-a-paisa rounding bugs (see
    gen/defects.py::inject_fee_overcharge and
    verify/recompute.py::recompute_step's docstrings for the full story),
    and a rounding bug is exactly the kind of thing that can pass by luck
    on any single seed. A one-off assertion on seed=91 alone would not
    have been trustworthy evidence that the fix is actually correct
    rather than merely correct for that one dataset."""

    @pytest.mark.parametrize("seed", range(20))
    def test_fee_overcharge_proof_accepted_by_independent_verifier_too(self, seed):
        ds = generate_clean(default_spec(seed=seed))
        _, truth = apply_defects(
            ds,
            [DefectSpec("fee_overcharge", {"method": "card", "extra_bps": 36, "days": 30})],
            seed=1,
        )
        if not truth.defects:
            pytest.skip(f"seed {seed} produced no card payments in the overcharge window")

        groups = group_payments_by_settlement(ds.payments)
        touched_settlement_ids = {
            ds.payment_by_id(d.subject_id).settlement_id for d in truth.defects
        }

        for sid in touched_settlement_ids:
            settlement = next(s for s in ds.settlements if s.settlement_id == sid)
            members = groups.get(sid, [])
            result = match_group_to_settlement(settlement, members, ds.refunds, ds.disputes)
            assert isinstance(result, MatchCandidate)

            verify_result = verify_proof(result.proof, ds)
            assert verify_result.accepted, (
                f"seed={seed} sid={sid}: fee_overcharge must be invisible to the "
                f"INDEPENDENT verifier too, not just to the matcher's own claim "
                f"(residual={verify_result.residual})"
            )
