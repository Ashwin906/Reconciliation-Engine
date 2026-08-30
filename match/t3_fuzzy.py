"""
T3: fuzzy matching -- amount closeness + date proximity + a narration
keyword check, combined into a confidence score. Used only on what T0-T2
could not resolve by exact means (e.g. inject_utr_change and
inject_narration_truncation from gen/defects.py, both deliberately
should_be_detected=False: no money is at risk, resolution must happen
here quietly rather than escalating to a human).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

from ingest.normalise import clean_narration
from gen.models import BankLine, Settlement
from match.types import EntityRef, MatchCandidate, Unmatched

DEFAULT_WEIGHTS = {"amount": 0.6, "date": 0.3, "narration": 0.1}
DEFAULT_THRESHOLD = 0.85
_RAZORPAY_KEYWORD = "RAZORPAY"


_AMOUNT_TOLERANCE_PAISE = 100  # 1 rupee -- rounding-only slack, not a real gap


def _amount_score(expected: int, observed: int) -> float:
    """Near-exact gate, NOT a proportional decay.

    A real bug was caught here while writing match/run.py's integration
    tests: an earlier version scored amount closeness proportionally
    (`1 - relative_difference`), which meant a GENUINE shortfall -- an
    unbooked chargeback deducting a real few thousand rupees -- could
    still score close to 1.0 if the settlement it came out of was large,
    because the relative difference looked small. That let T3 silently
    "fuzzy match" a settlement with real money missing from it, which is
    exactly the failure mode this whole system exists to prevent: a
    confident match hiding an unexplained loss. Money either ties
    (within a rupee, for rounding) or it's a real gap that must go to
    residue -- there is no meaningful notion of an amount being
    'approximately' correct in this domain.
    """
    if abs(expected - observed) <= _AMOUNT_TOLERANCE_PAISE:
        return 1.0
    return 0.0


def _date_score(a: date, b: date) -> float:
    days_apart = abs((a - b).days)
    if days_apart == 0:
        return 1.0
    if days_apart >= 3:
        return 0.0
    return 1.0 - (days_apart / 3.0)


def _narration_score(narration: str, keyword: str = _RAZORPAY_KEYWORD) -> float:
    """Containment check first (the common, strong case), fuzzy ratio as
    a fallback for a truncated narration that no longer contains the
    keyword whole -- see inject_narration_truncation, which cuts a
    narration down to 10 characters and can chop the keyword itself."""
    cleaned = clean_narration(narration)
    if keyword in cleaned:
        return 1.0
    return SequenceMatcher(None, keyword, cleaned).ratio()


def score_candidate(
    settlement: Settlement, bank_line: BankLine, weights: dict[str, float] = None
) -> float:
    """Weighted confidence that settlement and bank_line are the same
    real-world transfer. Weights default to amount-heavy: an exact
    amount + same-day match is already strong evidence on its own in
    this domain (a coincidental exact-paisa collision on the same day is
    vanishingly unlikely), so narration -- the noisiest signal, since
    bank narration formatting varies by bank and can be truncated -- is
    weighted lowest, used more as a tiebreak/confirmation than as a
    primary signal."""
    w = weights or DEFAULT_WEIGHTS
    observed = -bank_line.debit if settlement.amount < 0 else bank_line.credit

    a_score = _amount_score(settlement.amount, observed)
    d_score = _date_score(settlement.created_at.date(), bank_line.txn_date)
    n_score = _narration_score(bank_line.narration)

    return w["amount"] * a_score + w["date"] * d_score + w["narration"] * n_score


def match_fuzzy(
    settlements: list[Settlement],
    bank_lines: list[BankLine],
    threshold: float = DEFAULT_THRESHOLD,
    weights: dict[str, float] = None,
) -> tuple[list[MatchCandidate], list[Unmatched]]:
    """Scores every remaining settlement against every remaining bank
    line, then greedily assigns highest-scoring pairs first, without
    reusing either side twice. A settlement/bank_line pair scoring below
    `threshold` is left unmatched rather than force-assigned -- fuzzy
    matching degrades gracefully into 'I don't know' instead of a
    low-confidence guess presented with the same certainty as an exact
    match."""
    w = weights or DEFAULT_WEIGHTS
    scored: list[tuple[float, Settlement, BankLine]] = []
    for settlement in settlements:
        for bank_line in bank_lines:
            score = score_candidate(settlement, bank_line, w)
            if score >= threshold:
                scored.append((score, settlement, bank_line))

    scored.sort(key=lambda t: t[0], reverse=True)

    matched: list[MatchCandidate] = []
    used_settlements: set[str] = set()
    used_bank_lines: set[str] = set()

    for score, settlement, bank_line in scored:
        if settlement.settlement_id in used_settlements:
            continue
        if bank_line.line_id in used_bank_lines:
            continue
        used_settlements.add(settlement.settlement_id)
        used_bank_lines.add(bank_line.line_id)
        matched.append(
            MatchCandidate(
                tier="T3",
                left=EntityRef("settlement", settlement.settlement_id),
                right=EntityRef("bank_line", bank_line.line_id),
                confidence=score,
            )
        )

    unmatched = [
        Unmatched(
            kind="settlement",
            id=s.settlement_id,
            reason="no_fuzzy_match",
            detail={"threshold": threshold},
        )
        for s in settlements
        if s.settlement_id not in used_settlements
    ]
    return matched, unmatched
