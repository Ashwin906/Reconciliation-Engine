"""
The independent verifier. See DECISIONS.md D-02: this module may import
core/ and the plain data-model types in gen.models, but must NEVER import
match/ or agent/ -- enforced by verify/test_independence.py's AST check,
not just this comment.

A match/Proof is a CLAIM. This module re-derives it from raw data using
its own implementation, and only ever trusts what IT computes -- never a
step's stored .value, never a Proof's own .computed or .observed. Both
sides of every comparison (the running total, and the observed figure)
are rebuilt from raw.payments / raw.refunds / raw.disputes /
raw.settlements / raw.bank_lines from scratch on every call.

Where possible this goes one level lower than "re-read a stored field":
GST is recomputed via core.money.gst_on(fee) from the fee, not by
summing each payment's stored .tax value -- summing a value the matcher
already trusted would not be independent re-derivation, it would just be
reading the same number back.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.money import Paise, gst_on, sum_paise
from core.proof import Proof, ProofStep
from gen.models import Dataset


def recompute_step(step: ProofStep, raw: Dataset) -> Paise:
    """Independently recomputes ONE proof step's value from raw data,
    using step.source_ids to know WHICH rows to look at (naming which
    rows to re-check is not the same as trusting their arithmetic -- an
    auditor re-adding a named list of invoices still redoes the
    addition themselves) and step.source to know WHICH KIND of quantity
    is being computed. Never reads step.value."""
    ids = set(step.source_ids)

    if step.source.startswith("payments[settlement_id="):
        return sum_paise(p.amount for p in raw.payments if p.payment_id in ids)

    if step.source == "fees":
        return sum_paise(p.fee for p in raw.payments if p.payment_id in ids)

    if step.source == "gst_on_fees":
        # Recomputed from each payment's fee, not read from payment.tax --
        # see module docstring on why that's the meaningfully independent
        # choice. But GST must be rounded PER PAYMENT, then summed -- NOT
        # summed-then-rounded-once. A real bug was caught here: the first
        # version did gst_on(sum(fees)), which occasionally disagreed with
        # the real system by 1-2 paise, because sum-of-individually-rounded
        # values is not always equal to round-of-the-summed-total (classic
        # rounding accumulation). That produced FALSE REJECTIONS on
        # genuinely correct settlements -- residual off by exactly the
        # rounding delta, not by any real missing money. GST invoicing
        # practice rounds per line item for the same reason this must:
        # each payment IS its own line item, and gen/build.py and
        # match/t2_set.py both already sum per-payment tax, so the
        # verifier must replicate that same aggregation order to be
        # checking the same computation, not a different one that happens
        # to usually agree.
        return sum_paise(
            gst_on(p.fee) for p in raw.payments if p.payment_id in ids
        )

    if step.source == "refunds":
        return sum_paise(r.amount for r in raw.refunds if r.refund_id in ids)

    if step.source == "disputes_plus_fees":
        return sum_paise(
            d.amount + d.fee for d in raw.disputes if d.dispute_id in ids
        )

    if step.source == "settlement_amount":
        # Added for Phase 9 (agent/investigate.py): a T4 proof starts from
        # the settlement's REPORTED amount as a known input (T4 is
        # explaining a settlement<->bank gap, not re-deriving the
        # settlement from its members the way T2 does), then subtracts
        # proposed adjustments the same way T2 subtracts refunds/disputes.
        # This is the same class of operation as every source above --
        # re-read a specific raw entity by id, ignore any claimed value --
        # just reading a different table (settlements) instead of
        # payments/refunds/disputes. It does NOT weaken independence:
        # this function still never reads step.value, and the agent (or
        # anything else) still cannot invent a settlement_id whose
        # amount doesn't genuinely exist in raw.settlements.
        return sum_paise(
            s.amount for s in raw.settlements if s.settlement_id in ids
        )

    raise ValueError(f"recompute_step: unrecognised step.source {step.source!r}")


def _apply_op(op: str, running_total: Paise, value: Paise) -> Paise:
    if op in ("sum", "add"):
        return running_total + value
    if op == "subtract":
        return running_total - value
    raise ValueError(f"recompute: unrecognised ProofStep.op {op!r}")


def recompute_observed(observed_source: str, raw: Dataset) -> Paise:
    """Independently re-derives the OBSERVED side of a proof (what the
    real world actually shows) from raw data -- never trusts
    Proof.observed directly. observed_source is a "kind:id" string, e.g.
    "settlement:setl_1" or "bank_line:bnk_00003"."""
    kind, sep, entity_id = observed_source.partition(":")
    if not sep:
        raise ValueError(f"recompute_observed: malformed observed_source {observed_source!r}")

    if kind == "settlement":
        settlement = next((s for s in raw.settlements if s.settlement_id == entity_id), None)
        if settlement is None:
            raise ValueError(f"recompute_observed: no settlement {entity_id!r} in raw data")
        return settlement.amount

    if kind == "bank_line":
        bank_line = next((b for b in raw.bank_lines if b.line_id == entity_id), None)
        if bank_line is None:
            raise ValueError(f"recompute_observed: no bank_line {entity_id!r} in raw data")
        return -bank_line.debit if bank_line.debit else bank_line.credit

    raise ValueError(f"recompute_observed: unrecognised observed_source kind {kind!r}")


@dataclass(frozen=True)
class VerifyResult:
    accepted: bool
    recomputed_total: Paise
    recomputed_observed: Paise
    residual: Paise
    failing_step: ProofStep | None = None
    matcher_claim_agrees: bool = False   # diagnostic only -- never drives accepted


def verify_proof(proof: Proof, raw: Dataset) -> VerifyResult:
    """The core trust boundary. Recomputes every step and the observed
    figure independently, sums them by op, and accepts ONLY if the
    verifier's OWN residual is exactly zero -- regardless of what the
    Proof itself claims. A Proof with proof.ties == True can still be
    REJECTED here if the matcher's claim doesn't hold up under
    independent recomputation; that is the entire point of this module,
    not an edge case to special-case away.
    """
    running_total = 0
    failing_step: ProofStep | None = None

    for step in proof.steps:
        recomputed_value = recompute_step(step, raw)
        if recomputed_value != step.value and failing_step is None:
            # the matcher's own stated step value disagrees with our
            # independent recomputation -- worth surfacing even though
            # ACCEPT/REJECT below is decided on the total, not on this
            failing_step = step
        running_total = _apply_op(step.op, running_total, recomputed_value)

    recomputed_observed = recompute_observed(proof.observed_source, raw)
    residual = running_total - recomputed_observed
    accepted = residual == 0

    matcher_claim_agrees = (
        running_total == proof.computed and recomputed_observed == proof.observed
    )

    return VerifyResult(
        accepted=accepted,
        recomputed_total=running_total,
        recomputed_observed=recomputed_observed,
        residual=residual,
        failing_step=failing_step,
        matcher_claim_agrees=matcher_claim_agrees,
    )


@dataclass
class VerifyReport:
    total: int
    accepted: int
    rejected: int
    rejected_items: list[tuple[Proof, VerifyResult]] = field(default_factory=list)

    @property
    def acceptance_rate(self) -> float:
        return self.accepted / self.total if self.total else 1.0


def verify_all(proofs: list[Proof], raw: Dataset) -> VerifyReport:
    """Runs verify_proof over every proof and aggregates the result.
    This is the function that produces the two numbers D-03 says must be
    published: how many proofs were rejected, and (by cross-referencing
    proof.ties against each result.accepted) how many of those the
    matcher itself believed were correct matches."""
    accepted = 0
    rejected_items: list[tuple[Proof, VerifyResult]] = []
    for proof in proofs:
        result = verify_proof(proof, raw)
        if result.accepted:
            accepted += 1
        else:
            rejected_items.append((proof, result))

    return VerifyReport(
        total=len(proofs),
        accepted=accepted,
        rejected=len(rejected_items),
        rejected_items=rejected_items,
    )
