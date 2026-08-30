"""
T2: set matching -- verifies that a group of payments' own arithmetic
ties the settlement amount Razorpay reported for them. This is a
different check from T1 (which compares the settlement to the BANK
credit): T2 sanity-checks whether the settlement report is even
internally self-consistent with the payments, refunds, and disputes
currently known to belong to it, independent of what the bank shows.

Both checks can flag the same settlement for different reasons -- that's
not a bug. A fee_overcharge defect (gen/defects.py) moves the fee AND the
settlement amount together, so it ties here at T2 even though the
merchant is being overcharged (only detectable later, via rate
comparison, not via tie-out -- DOMAIN_KNOWLEDGE.md section 11). An
unbooked_chargeback defect adds a dispute without adjusting the
settlement amount, so it correctly fails here even though it also fails
at T1 -- two independent signals agreeing something needs investigation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.money import Paise, sum_paise
from gen.models import Dispute, Payment, Refund, Settlement
from core.proof import Proof, ProofStep
from match.types import EntityRef, MatchCandidate, Unmatched


def expected_settlement_amount(
    settlement_id: str,
    members: list[Payment],
    refunds: list[Refund],
    disputes: list[Dispute],
) -> tuple[Paise, list[ProofStep]]:
    """Computes what a settlement's amount SHOULD be, given its member
    payments and whatever refunds/disputes currently reference those
    payments. Also returns the proof steps, so a caller building a Proof
    doesn't have to recompute the same sums a second time to get the
    working shown."""
    member_ids = {p.payment_id for p in members}
    relevant_refunds = [r for r in refunds if r.payment_id in member_ids]
    relevant_disputes = [d for d in disputes if d.payment_id in member_ids]

    gross = sum_paise(p.amount for p in members)
    fees = sum_paise(p.fee for p in members)
    tax = sum_paise(p.tax for p in members)
    refund_total = sum_paise(r.amount for r in relevant_refunds)
    dispute_total = sum_paise(d.amount + d.fee for d in relevant_disputes)
    computed = gross - fees - tax - refund_total - dispute_total

    payment_ids = tuple(p.payment_id for p in members)
    steps = [
        ProofStep("sum", f"payments[settlement_id={settlement_id}]", payment_ids, gross),
        ProofStep("subtract", "fees", payment_ids, fees),
        ProofStep("subtract", "gst_on_fees", payment_ids, tax),
        ProofStep(
            "subtract", "refunds", tuple(r.refund_id for r in relevant_refunds), refund_total
        ),
        ProofStep(
            "subtract",
            "disputes_plus_fees",
            tuple(d.dispute_id for d in relevant_disputes),
            dispute_total,
        ),
    ]
    return computed, steps


def match_group_to_settlement(
    settlement: Settlement,
    members: list[Payment],
    refunds: list[Refund],
    disputes: list[Dispute],
) -> MatchCandidate | Unmatched:
    """Builds the proof and decides match vs. residue based on whether
    it ties to exactly zero residual."""
    if not members:
        return Unmatched(
            kind="settlement",
            id=settlement.settlement_id,
            reason="no_member_payments",
            detail={},
        )

    computed, steps = expected_settlement_amount(
        settlement.settlement_id, members, refunds, disputes
    )
    proof = Proof(
        claim=f"settlement {settlement.settlement_id} member arithmetic ties its reported amount",
        steps=tuple(steps),
        computed=computed,
        observed=settlement.amount,
        observed_source=f"settlement:{settlement.settlement_id}",
    )

    if proof.ties:
        return MatchCandidate(
            tier="T2",
            left=EntityRef("settlement", settlement.settlement_id),
            right=EntityRef("payment_group", settlement.settlement_id),
            confidence=1.0,
            proof=proof,
        )

    return Unmatched(
        kind="settlement",
        id=settlement.settlement_id,
        reason="t2_internal_mismatch",
        detail={"residual": proof.residual, "computed": computed, "reported": settlement.amount},
    )


@dataclass(frozen=True)
class SubsetResult:
    outcome: str                       # 'EXACT_ONE' | 'AMBIGUOUS' | 'NONE'
    member_ids: tuple[str, ...] = ()
    solutions_found: int = 0


def find_subset(
    candidates: list[tuple[str, Paise]], target: Paise, max_solutions_to_check: int = 2
) -> SubsetResult:
    """Finds which subset of candidates sums exactly to target -- used
    when a bank credit's member payments must be discovered rather than
    read directly off a settlement_id grouping (T0 already gives us that
    grouping in this dataset, so this function exists for the general
    case where it's missing).

    Stops searching as soon as it has found `max_solutions_to_check`
    (default 2) distinct solutions -- it does not need to enumerate every
    possible subset, only to know whether there is exactly one or more
    than one. DECISIONS.md D-13: an ambiguous match is not a match. If
    two different subsets both sum to the target, the data genuinely does
    not determine which one is correct, and fabricating a choice would
    be inventing a fact, not finding one. AMBIGUOUS is reported honestly
    rather than resolved by an arbitrary tie-break.

    The empty subset never counts as a solution, even if target is 0 --
    a settlement with a genuinely zero net amount still needs at least
    one real payment behind it to mean anything.
    """
    # sort descending -- lets the search prune a branch as soon as even
    # picking every remaining (smaller) candidate couldn't reach target
    items = sorted(candidates, key=lambda c: c[1], reverse=True)
    n = len(items)
    suffix_sum = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_sum[i] = suffix_sum[i + 1] + items[i][1]

    solutions: list[tuple[str, ...]] = []

    def search(i: int, remaining: Paise, chosen: list[str]) -> None:
        if len(solutions) >= max_solutions_to_check:
            return
        if remaining == 0 and chosen:
            solutions.append(tuple(chosen))
            return
        if i >= n or remaining < 0:
            return
        if suffix_sum[i] < remaining:
            return  # even taking everything left can't reach target
        # take items[i]
        search(i + 1, remaining - items[i][1], chosen + [items[i][0]])
        if len(solutions) >= max_solutions_to_check:
            return
        # skip items[i]
        search(i + 1, remaining, chosen)

    search(0, target, [])

    if not solutions:
        return SubsetResult(outcome="NONE", solutions_found=0)
    if len(solutions) == 1:
        return SubsetResult(outcome="EXACT_ONE", member_ids=solutions[0], solutions_found=1)
    return SubsetResult(outcome="AMBIGUOUS", solutions_found=len(solutions))
