"""
A deliberately naive reconciler, for comparison against the real one.

This is the thing most reconciliation scripts actually are: match each
settlement to a bank line by exact reference number, check the amount is
equal, and call anything left over "missing money". No fallback tiers, no
independent verification, no rate-card comparison, no conservation proof.

[WHY THIS EXISTS, stated plainly] It is easy to assert that a four-tier
matcher plus an independent verifier plus rule detectors is worth the
complexity. It is more useful to demonstrate it. This module makes the
claim falsifiable: run the SAME dataset through both and read the
difference. If the naive version ever matched the real one's output
across the scenario catalogue, that would be a genuine argument that the
architecture is over-built, and it should be reported rather than hidden.

[HONESTY NOTE] This is a fair-faith baseline, not a strawman built to
lose. It does the sensible thing at every step: exact reference lookup,
exact amount comparison, and it reports what it could not explain instead
of silently dropping it. What it lacks is not care -- it is the specific
machinery that the failure modes in DOMAIN_KNOWLEDGE.md actually require.
It is roughly what this project would have been at the end of day one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.money import Paise, apply_rate, sum_paise
from gen.models import Dataset


@dataclass
class NaiveResult:
    matched: int
    unresolved: list[dict] = field(default_factory=list)
    # Money the naive run would tell the merchant is missing. Includes
    # amounts that are NOT actually missing (a rewritten reference number
    # is a false alarm) -- which is precisely the failure being measured.
    flagged_missing_paise: Paise = 0
    # Money genuinely lost that this approach never even looks for. A fee
    # overcharge reconciles perfectly against the bank, so no amount of
    # settlement<->bank matching can surface it.
    blind_spot_paise: Paise = 0


def run_naive(ds: Dataset, rate_card_bps: dict[str, int]) -> NaiveResult:
    """Exact-reference, exact-amount matching. Nothing else.

    `rate_card_bps` is accepted but used ONLY to compute the blind-spot
    figure -- to quantify what this approach fails to see. The naive
    matcher itself never consults it, because a naive matcher would not:
    that is the point being measured.
    """
    by_ref = {b.ref_no: b for b in ds.bank_lines if b.ref_no}

    matched = 0
    unresolved: list[dict] = []
    flagged = 0

    for settlement in ds.settlements:
        bank_line = by_ref.get(settlement.utr)
        if bank_line is None:
            unresolved.append(
                {
                    "id": settlement.settlement_id,
                    "reason": "no bank line with this reference number",
                    "amount_paise": settlement.amount,
                }
            )
            flagged += abs(settlement.amount)
            continue

        actual = -bank_line.debit if bank_line.debit else bank_line.credit
        if actual != settlement.amount:
            gap = settlement.amount - actual
            unresolved.append(
                {
                    "id": settlement.settlement_id,
                    "reason": "bank amount does not equal settlement amount",
                    "amount_paise": gap,
                }
            )
            flagged += abs(gap)
            continue

        matched += 1

    return NaiveResult(
        matched=matched,
        unresolved=unresolved,
        flagged_missing_paise=flagged,
        blind_spot_paise=_fee_blind_spot(ds, rate_card_bps),
    )


def _fee_blind_spot(ds: Dataset, rate_card_bps: dict[str, int]) -> Paise:
    """Fee charged above the contracted rate card.

    Computed here so the comparison can state, in rupees, what a
    settlement<->bank matcher structurally cannot detect: this money is
    taken BEFORE the settlement is calculated, so the settlement and the
    bank credit agree with each other perfectly. Both numbers are
    internally consistent and both are wrong.

    Deliberately NOT imported from detect/rules.py. That module answers
    "did our detectors fire", which is a different question from "how much
    money is invisible to an approach that has no detectors at all" --
    and importing it would make the comparison depend on the very
    machinery whose value is being measured.

    Uses core.money.apply_rate rather than plain integer division. A first
    draft here used `amount * bps // 10_000`, which floors where the rest
    of this codebase rounds ROUND_HALF_UP -- so on a perfectly CLEAN
    dataset it reported a fraction of a paisa of "overcharge" on most
    payments and totalled them into a Rs 0.24 leak that did not exist. A
    comparison whose whole purpose is proving a real leak must not
    manufacture a fake one out of a rounding disagreement.
    """
    overcharge = 0
    for payment in ds.payments:
        if payment.status != "captured":
            continue
        contracted_bps = rate_card_bps.get(payment.method)
        if contracted_bps is None:
            continue
        expected = apply_rate(payment.amount, contracted_bps)
        if payment.fee > expected:
            overcharge += payment.fee - expected
    return overcharge


def compare(ds: Dataset, result, rate_card_bps: dict[str, int]) -> dict:
    """Side-by-side: the naive approach versus the real pipeline, over the
    identical dataset.

    `result` is a pipeline.run.BatchResult -- read, never recomputed, same
    contract as the rest of sim/.
    """
    naive = run_naive(ds, rate_card_bps)

    m = result.match_result
    real_matched = len(m.t1_matches) + len(m.t2_matches) + len(m.t3_matches)
    total = real_matched + len(m.residue)

    naive_total = naive.matched + len(naive.unresolved)

    # What the fallback tiers recovered that exact matching could not.
    # These are real money that ARRIVED and would have been reported
    # missing -- the most expensive kind of false alarm, because it sends
    # a human hunting for a payment that is already in the bank.
    false_alarms = max(0, len(naive.unresolved) - len(m.residue))

    return {
        "naive": {
            "matched": naive.matched,
            "unresolved": len(naive.unresolved),
            "match_rate_pct": round(100 * naive.matched / naive_total, 1) if naive_total else 100.0,
            "flagged_missing_paise": naive.flagged_missing_paise,
            "blind_spot_paise": naive.blind_spot_paise,
            "items": naive.unresolved,
            "finds_fee_leaks": False,
            "proves_books_balance": False,
            "verifies_independently": False,
        },
        "real": {
            "matched": real_matched,
            "unresolved": len(m.residue),
            "match_rate_pct": round(100 * real_matched / total, 1) if total else 100.0,
            "t1": len(m.t1_matches),
            "t2": len(m.t2_matches),
            "t3": len(m.t3_matches),
            "findings": [
                {"rule": f.rule, "severity": f.severity, "impact_paise": f.impact_paise}
                for f in result.findings
            ],
            "conservation_closes": result.conservation.closes,
            "finds_fee_leaks": True,
            "proves_books_balance": True,
            "verifies_independently": True,
        },
        "difference": {
            "false_alarms_avoided": false_alarms,
            "money_falsely_flagged_paise": max(
                0, naive.flagged_missing_paise - _real_flagged(result)
            ),
            "leak_money_naive_would_miss_paise": naive.blind_spot_paise,
        },
    }


def _real_flagged(result) -> Paise:
    """What the real pipeline actually reports as unexplained, so the
    comparison subtracts like for like rather than against zero."""
    return sum_paise(
        abs(u.detail.get("gap", 0)) if isinstance(u.detail, dict) else 0
        for u in result.match_result.residue
    )
