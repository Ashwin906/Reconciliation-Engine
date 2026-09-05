"""
T1: UTR (bank transfer reference) matching. Exact lookup, but unlike T0
it can find the right bank line and STILL disagree on the amount -- that
disagreement is exactly the signal T2/T4 need to investigate.
"""

from __future__ import annotations

from gen.models import BankLine, Settlement
from match.types import EntityRef, MatchCandidate, Unmatched


def index_bank_by_ref(bank_lines: list[BankLine]) -> dict[str, BankLine]:
    """Indexes bank lines by ref_no. Lines with ref_no=None (e.g. after
    inject_narration_truncation blanks it, or an offline payment with no
    reference at all) are skipped -- they cannot be looked up by
    reference and must fall through to T3 fuzzy matching instead."""
    index: dict[str, BankLine] = {}
    for line in bank_lines:
        if line.ref_no is not None:
            index[line.ref_no] = line
    return index


def match_settlement_to_bank(
    settlements: list[Settlement], index: dict[str, BankLine]
) -> tuple[list[MatchCandidate], list[Unmatched]]:
    """For each settlement, looks up its UTR in the bank-line index.

    Three outcomes:
      - no bank line found at all -> Unmatched('missing_settlement')
      - found, but credit/debit doesn't match settlement.amount exactly
        -> Unmatched('amount_mismatch') -- found the right line, just
        disagree on the number; this is the T2/T4 investigation case
      - found and amounts agree exactly -> MatchCandidate

    Deliberately handles negative settlements (DECISIONS.md /
    DOMAIN_KNOWLEDGE.md section 4): a negative settlement.amount is
    compared against bank_line.debit, not credit, since
    inject_negative_settlement (gen/defects.py) flips the bank line to a
    debit entry to model Razorpay debiting the merchant instead of
    crediting them.
    """
    matches: list[MatchCandidate] = []
    unmatched: list[Unmatched] = []

    for settlement in settlements:
        bank_line = index.get(settlement.utr) if settlement.utr else None

        if bank_line is None:
            unmatched.append(
                Unmatched(
                    kind="settlement",
                    id=settlement.settlement_id,
                    reason="missing_settlement",
                    detail={"expected_amount": settlement.amount, "utr": settlement.utr},
                )
            )
            continue

        if settlement.amount < 0:
            observed = -bank_line.debit
        else:
            observed = bank_line.credit

        if observed != settlement.amount:
            unmatched.append(
                Unmatched(
                    kind="settlement",
                    id=settlement.settlement_id,
                    reason="amount_mismatch",
                    detail={
                        "expected": settlement.amount,
                        "observed": observed,
                        "gap": settlement.amount - observed,
                        "bank_line_id": bank_line.line_id,
                    },
                )
            )
            continue

        matches.append(
            MatchCandidate(
                tier="T1",
                left=EntityRef("settlement", settlement.settlement_id),
                right=EntityRef("bank_line", bank_line.line_id),
                confidence=1.0,
            )
        )

    return matches, unmatched
