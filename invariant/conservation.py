"""
The conservation invariant: DOMAIN_KNOWLEDGE.md section 12, DECISIONS.md
D-15. Every rupee collected must leave through exactly one of five doors
-- paid to the merchant, refunded, taken by a dispute, kept as a fee, or
held in reserve. If the sum closes to exactly zero, every paisa in the
batch is accounted for -- a PROOF of completeness, not a statistic about
how many records matched.

This is deliberately a DIFFERENT check from match/t1_utr.py and
match/t2_set.py, not a re-run of them: T1/T2 ask "is this settlement's
own REPORT correct?" This asks "across the whole dataset, using what
actually moved (bank lines) and what actually happened (refunds,
disputes), does every rupee add up?" A settlement's report can be wrong
(T1/T2 correctly flag it) while the GLOBAL conservation identity still
holds, because the real money movement and the real dispute/refund
records are self-consistent even when the REPORT describing them isn't.
inject_unbooked_chargeback is the clearest example of this -- see the
module's own defect-by-defect test notes in
tests/test_invariant_conservation.py for the worked-out reasoning, not
just the assertion.

Not restricted the way verify/ is (DECISIONS.md D-02 only applies to
verify/) -- this module may use gen.models freely, and does.

TWO SCOPE BOUNDARIES, discovered empirically while testing this module
against every defect category (not assumed up front -- see
tests/test_invariant_conservation.py for the full defect-by-defect table
this docstring summarizes):

1. Settlement<->bank matching here is EXACT-UTR ONLY, the same scope as
   match/t1_utr.py, not match/t3_fuzzy.py's fuzzy resolution.
   inject_utr_change and inject_narration_truncation both correctly fail
   to close as a result -- the real money is present in raw.bank_lines,
   just not findable under the settlement's own (now-stale) UTR. This is
   expected at this module's current scope: in a full pipeline (Phase
   11), conservation would run against T3-resolved settlement<->bank
   links, not raw settlement.utr. Standalone, it cannot see past an
   identity mismatch it wasn't given the resolution for.

2. This check proves "nothing COLLECTED went missing." It does NOT prove
   "nothing unexplained ARRIVED." Only bank lines linked to a known
   settlement are summed into `settled` -- an inject_offline_payment
   bank credit with no settlement behind it is simply never counted, in
   either direction, so it has zero effect on the residual and the
   invariant closes without ever noticing the extra money. Catching an
   unexplained credit is a genuinely different job, already covered by
   Phase 8's planned detect_unknown_credit and D-17's always-human gate
   rule for that category -- deliberately NOT folded into this check,
   to keep conservation's single responsibility (collected money is
   accounted for) from blurring into a second one (no unexplained money
   exists). Stated here rather than discovered later as a surprise gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.money import Paise, sum_paise
from gen.models import Dataset


@dataclass(frozen=True)
class ConservationResult:
    collected: Paise
    settled: Paise
    refunded: Paise
    disputed: Paise
    dispute_fees: Paise
    fees: Paise
    gst: Paise
    reserve_delta: Paise
    residual: Paise
    closes: bool


def _settled_amount_for(settlement, bank_lines) -> Paise:
    """The REAL amount that moved, from the bank line -- not the
    settlement's own reported amount. Using the report here would make
    this check redundant with match/t2_set.py's internal-consistency
    check instead of a genuinely different, complementary one. A missing
    bank line (inject_missing_settlement) correctly contributes 0, which
    is what makes conservation catch a missing payout as a real gap."""
    bank_line = next((b for b in bank_lines if b.ref_no == settlement.utr), None)
    if bank_line is None:
        return 0
    return -bank_line.debit if bank_line.debit else bank_line.credit


def check_conservation(raw: Dataset, reserve_delta: Paise = 0) -> ConservationResult:
    """Computes the conservation identity across the WHOLE dataset.

    reserve_delta is now a REAL parameter (Phase 16), no longer hardcoded
    to 0. It is passed in rather than computed here because reserve is
    the one door in this identity whose size cannot be derived from a
    single batch: a hold placed in one period and returned in another is
    the same rupees, and only a ledger that spans periods
    (reserve/ledger.py) can say what this period's net movement was.
    Computing it here from `raw` alone would mean guessing.

    Sign convention, matching reserve.ledger.reserve_delta: positive
    means more was withheld than returned (money legitimately left the
    payout and is still the merchant's), negative means past holds came
    back (the merchant received more than this period's sales alone
    explain). Both must be accounted for or conservation reports a false
    alarm in one direction or the other.

    Defaults to 0 so every existing caller keeps its exact previous
    behaviour: a dataset with a reserve hold and no ledger supplied still
    correctly FAILS to close, because the money genuinely is unaccounted
    for from that call's point of view. Supplying the ledger's real delta
    is what closes it -- which is the whole demonstration.
    """
    captured = [p for p in raw.payments if p.status == "captured"]

    collected = sum_paise(p.amount for p in captured)
    settled = sum_paise(_settled_amount_for(s, raw.bank_lines) for s in raw.settlements)
    refunded = sum_paise(r.amount for r in raw.refunds)
    disputed = sum_paise(d.amount for d in raw.disputes)
    dispute_fees = sum_paise(d.fee for d in raw.disputes)
    fees = sum_paise(p.fee for p in captured)
    gst = sum_paise(p.tax for p in captured)

    residual = collected - (settled + refunded + disputed + dispute_fees + fees + gst + reserve_delta)

    return ConservationResult(
        collected=collected,
        settled=settled,
        refunded=refunded,
        disputed=disputed,
        dispute_fees=dispute_fees,
        fees=fees,
        gst=gst,
        reserve_delta=reserve_delta,
        residual=residual,
        closes=residual == 0,
    )


@dataclass(frozen=True)
class Suspect:
    subject_id: str
    local_residual: Paise


def localise_gap(raw: Dataset) -> list[Suspect]:
    """When the whole-dataset conservation check does not close, narrows
    the search to individual settlements by running the SAME identity
    per-settlement instead of dataset-wide. Returns only settlements
    with a nonzero local residual, sorted by magnitude (largest gap
    first) -- turning 'somewhere in this batch, money doesn't add up'
    into 'here specifically'.

    Deliberately does NOT flag every settlement touched by every defect
    -- only ones whose LOCAL identity fails to close. A settlement
    touched by inject_unbooked_chargeback, for instance, closes locally
    (the dispute is fully and correctly accounted for at the settlement
    level too, even though its REPORT was wrong -- see this module's
    docstring); a settlement touched by inject_missing_settlement or
    inject_negative_settlement does not, because those defects introduce
    money that is genuinely unexplained at the settlement level, not
    just misreported.
    """
    suspects: list[Suspect] = []
    for settlement in raw.settlements:
        members = [
            p
            for p in raw.payments
            if p.settlement_id == settlement.settlement_id and p.status == "captured"
        ]
        member_ids = {p.payment_id for p in members}

        collected = sum_paise(p.amount for p in members)
        fees = sum_paise(p.fee for p in members)
        gst = sum_paise(p.tax for p in members)
        refunded = sum_paise(r.amount for r in raw.refunds if r.payment_id in member_ids)
        disputed = sum_paise(d.amount for d in raw.disputes if d.payment_id in member_ids)
        dispute_fees = sum_paise(d.fee for d in raw.disputes if d.payment_id in member_ids)
        settled = _settled_amount_for(settlement, raw.bank_lines)

        local_residual = collected - (settled + refunded + disputed + dispute_fees + fees + gst)
        if local_residual != 0:
            suspects.append(Suspect(settlement.settlement_id, local_residual))

    suspects.sort(key=lambda s: abs(s.local_residual), reverse=True)
    return suspects
