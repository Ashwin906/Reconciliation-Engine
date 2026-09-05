"""
The rolling reserve as a real, dated entity -- closing a gap that three
separate modules independently documented as blocking them:

  invariant/conservation.py  reserve_delta hardcoded to 0, so any dataset
                             containing a reserve hold correctly FAILS to
                             close, because reserve is real money that
                             check could not account for
  detect/rules.py            detect_reserve_stuck() was a structural
                             no-op: "this function's precondition cannot
                             exist until that entity is added"
  gen/defects.py             inject_reserve_hold could only model the
                             SYMPTOM (a short bank credit), never the
                             mechanism

A rolling reserve is not a fee. Razorpay withholds a percentage of each
settlement against future chargeback risk and returns it after a fixed
period. The money is the merchant's the entire time -- it is deferred,
not lost. That single fact is why this needs its own ledger rather than
another deduction line: a deduction that comes back is a *balance*, and a
balance requires dates, not just amounts.

[WHY THIS IS THE HARD ONE] Every other check in this system operates on
one batch. Reserve is the first thing here that is only correct across
MULTIPLE periods: a hold in March and its release in April are the same
rupees, and a system looking at either month alone sees either
unexplained money missing or unexplained money arriving. Both look like
defects. Neither is.

This module is pure: dataclasses and functions over them, no I/O, no
database, no global state -- same shape as gate/policy.py and for the
same reason (it decides how money is accounted for, so it must be
readable and behave identically every time).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.money import Paise, apply_rate, sum_paise


@dataclass(frozen=True)
class ReserveHold:
    """One withholding event and its scheduled return.

    `released_on` is None while the money is still held. It is set (via
    settle_due, which returns a NEW record rather than mutating) when the
    release actually happens -- deliberately distinct from
    `release_due_on`, which is only when it was *supposed* to happen. A
    hold whose due date has passed and whose released_on is still None is
    exactly what detect_reserve_stuck exists to find, and collapsing the
    two fields would make that condition unrepresentable.
    """

    hold_id: str
    settlement_id: str
    amount: Paise
    held_on: date
    release_due_on: date
    released_on: date | None = None

    @property
    def is_outstanding(self) -> bool:
        return self.released_on is None

    def is_overdue(self, as_of: date) -> bool:
        return self.is_outstanding and as_of > self.release_due_on


def schedule_hold(
    settlement_id: str,
    settlement_amount: Paise,
    settled_on: date,
    reserve_bps: int,
    hold_days: int,
    hold_id: str | None = None,
) -> ReserveHold | None:
    """Computes one settlement's reserve withholding.

    Returns None when the computed reserve rounds to zero -- a hold of
    ₹0.00 is not a hold, and recording one would put noise in a ledger
    whose whole purpose is being auditable. Uses core.money.apply_rate,
    so reserve rounds the same ROUND_HALF_UP way every other rate in this
    codebase does (the convention FAILURES.md #4 and #10 are both about).

    A negative settlement (refunds exceeded collections) withholds
    nothing: there is no positive payout to hold a percentage of, and
    computing a "negative reserve" would invent money owed in the wrong
    direction.
    """
    if settlement_amount <= 0:
        return None

    amount = apply_rate(settlement_amount, reserve_bps)
    if amount <= 0:
        return None

    return ReserveHold(
        hold_id=hold_id or f"rsv_{settlement_id}",
        settlement_id=settlement_id,
        amount=amount,
        held_on=settled_on,
        release_due_on=settled_on + timedelta(days=hold_days),
    )


def settle_due(holds: list[ReserveHold], as_of: date) -> list[ReserveHold]:
    """Releases every outstanding hold whose due date has arrived.

    Returns a NEW list -- ReserveHold is frozen and nothing here mutates
    in place. A reserve ledger that could be edited retroactively would
    have the same problem an editable audit trail has (ledger/store.py,
    D-16): the record would stop being evidence of what happened.
    """
    return [
        (
            hold
            if not (hold.is_outstanding and as_of >= hold.release_due_on)
            else ReserveHold(
                hold_id=hold.hold_id,
                settlement_id=hold.settlement_id,
                amount=hold.amount,
                held_on=hold.held_on,
                release_due_on=hold.release_due_on,
                released_on=as_of,
            )
        )
        for hold in holds
    ]


def outstanding_balance(holds: list[ReserveHold], as_of: date) -> Paise:
    """Money withheld and not yet returned, as of a date.

    Counts a hold as outstanding if it was placed on or before `as_of`
    and either has not been released, or was released AFTER `as_of`. That
    second clause is what makes this answerable historically -- asking
    "what was my reserve balance on 1 March" must not be affected by a
    release that happened in April.
    """
    return sum_paise(
        hold.amount
        for hold in holds
        if hold.held_on <= as_of and (hold.released_on is None or hold.released_on > as_of)
    )


def reserve_delta(holds: list[ReserveHold], period_start: date, period_end: date) -> Paise:
    """Net reserve movement across one period: withheld minus returned.

    This is the number invariant/conservation.py needs. Its sign matters
    and must not be taken as a magnitude:

      positive -> more was withheld than returned; money left the payout
                  but is still the merchant's. Conservation must count it
                  as a door money legitimately left through.
      negative -> a past period's holds came back. The merchant received
                  MORE than this period's sales alone would explain, and
                  that surplus is explained by the reserve ledger rather
                  than being an unknown credit.

    A system without this returns a false alarm in both directions --
    which is precisely why reserve holds currently make conservation fail
    to close.
    """
    held = sum_paise(h.amount for h in holds if period_start <= h.held_on <= period_end)
    released = sum_paise(
        h.amount
        for h in holds
        if h.released_on is not None and period_start <= h.released_on <= period_end
    )
    return held - released


def overdue_holds(holds: list[ReserveHold], as_of: date) -> list[ReserveHold]:
    """Holds whose release date has passed with the money still withheld.

    This is the real precondition detect/rules.py's detect_reserve_stuck
    was written against and could never previously reach.
    """
    return [hold for hold in holds if hold.is_overdue(as_of)]


@dataclass(frozen=True)
class PeriodSummary:
    """One period's reserve activity, for display and for conservation."""

    label: str
    period_start: date
    period_end: date
    settled_gross: Paise
    held: Paise
    released: Paise
    net_delta: Paise
    closing_balance: Paise
    overdue_count: int


def run_periods(
    periods: list[tuple[str, date, date, Paise, str]],
    reserve_bps: int,
    hold_days: int,
) -> tuple[list[PeriodSummary], list[ReserveHold]]:
    """Walks several settlement periods in order, holding and releasing.

    `periods` is a list of (label, start, end, gross_settled, settlement_id).
    Deliberately takes plain values rather than a Dataset: reserve
    accounting depends only on amounts and dates, and keeping it free of
    gen.models means it can be pointed at real settlement data without
    touching this module.

    Order is load-bearing -- releases for a period are settled BEFORE that
    period's own hold is added, so a release and a hold landing in the
    same period are both counted rather than one masking the other.
    """
    holds: list[ReserveHold] = []
    summaries: list[PeriodSummary] = []

    for label, start, end, gross, settlement_id in periods:
        before = holds
        holds = settle_due(holds, end)
        released = sum_paise(
            new.amount
            for old, new in zip(before, holds)
            if old.released_on is None and new.released_on is not None
        )

        new_hold = schedule_hold(settlement_id, gross, end, reserve_bps, hold_days)
        if new_hold is not None:
            holds = [*holds, new_hold]

        held = new_hold.amount if new_hold else 0
        summaries.append(
            PeriodSummary(
                label=label,
                period_start=start,
                period_end=end,
                settled_gross=gross,
                held=held,
                released=released,
                net_delta=held - released,
                closing_balance=outstanding_balance(holds, end),
                overdue_count=len(overdue_holds(holds, end)),
            )
        )

    return summaries, holds
