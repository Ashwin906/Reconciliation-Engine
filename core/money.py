"""
Money as integer paise. Never float. See DECISIONS.md D-06.

Rates are integer basis points (bps): 200 bps = 2.00%, 1800 bps = 18.00%.
Basis points avoid the same float-precision problem for rate math that
paise avoids for amount math — a rate is just as much "money math" as
a sum is.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable

Paise = int


def to_paise(value: str | Decimal) -> Paise:
    """Convert a rupee amount to integer paise.

    '1000.50' -> 100050
    Decimal('1000.5') -> 100050

    Rejects float input outright (float('1000.50') is not exactly
    representable in binary, so accepting it would let a precision bug
    back in through the front door). Rejects more than 2 decimal places
    -- a fractional paisa means either the caller made an error or the
    source data is lying about its own precision, and both deserve a
    loud failure, not a silent round.
    """
    if isinstance(value, float):
        raise TypeError(
            f"to_paise() rejects float input ({value!r}). "
            "Pass a str or Decimal -- float cannot represent money exactly."
        )

    try:
        d = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"not a valid decimal amount: {value!r}") from exc

    scaled = d * 100
    if scaled != scaled.to_integral_value():
        raise ValueError(
            f"amount has more than 2 decimal places: {value!r} "
            "(a fractional paisa is not real money)"
        )
    return int(scaled)


def format_paise(p: Paise) -> str:
    """100050 -> '1,000.50'. DISPLAY ONLY -- never parse this back into
    a computation; round-trip through to_paise() only if you must, and
    even then prefer keeping the original Paise value around instead."""
    if not isinstance(p, int):
        raise TypeError(f"format_paise() expects int paise, got {type(p)}")
    rupees = Decimal(p) / 100
    return f"{rupees:,.2f}"


def apply_rate(amount: Paise, rate_bps: int) -> Paise:
    """amount * rate_bps / 10000, rounded ROUND_HALF_UP to the nearest paisa.

    apply_rate(100000, 200) -> 2000   (Rs 1000 * 2.00% = Rs 20.00)

    ROUND_HALF_UP, not banker's rounding: an exact .5 paisa always rounds
    up. Chosen for predictability -- a finance user asking "why is this
    Rs 0.01 off" needs an answer everyone agrees on, and half-up is the
    convention non-engineers expect. Banker's rounding (Python's default
    for float/Decimal division) would silently round 2.5 -> 2, which
    looks like a bug even when it isn't one.
    """
    exact = Decimal(amount) * Decimal(rate_bps) / Decimal(10000)
    return int(exact.to_integral_value(rounding=ROUND_HALF_UP))


def gst_on(fee: Paise, rate_bps: int = 1800) -> Paise:
    """GST on a FEE, not on the sale amount. Default 1800 bps = 18%,
    the standard GST rate on payment-aggregator services. [VERIFY current
    rate before relying on the default in production -- see
    DOMAIN_KNOWLEDGE.md section 19.]

    gst_on(2000) -> 360   (18% of Rs 20.00 fee = Rs 3.60)
    """
    return apply_rate(fee, rate_bps)


def sum_paise(values: Iterable[Paise]) -> Paise:
    """Sum a sequence of paise amounts. Wrapped (rather than using the
    builtin sum() inline everywhere) so every summation in the codebase
    goes through one place -- if int summation ever needed an integrity
    check (e.g. overflow guard, or non-int rejection), one edit fixes
    every call site instead of hunting down every bare sum()."""
    total = 0
    for v in values:
        if not isinstance(v, int):
            raise TypeError(f"sum_paise() expects int paise, got {type(v)}: {v!r}")
        total += v
    return total
