"""
Working-day and settlement-date math for Indian bank holidays.

[VERIFY] HOLIDAYS below is a placeholder covering 2026 major national/bank
holidays only -- state-specific holidays are NOT included (see
DOMAIN_KNOWLEDGE.md section 4: "bank holidays are state-dependent"). This
list must be reviewed against an authoritative RBI/bank holiday calendar
before being relied on for a real settlement date. Source and last-checked
date recorded below so staleness is visible.

Source: [VERIFY -- not yet checked against RBI negotiable instruments act
holiday list or a specific bank's calendar]
Last checked: never -- placeholder only
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

# Placeholder 2026 Indian national holidays likely to affect NEFT/RTGS
# settlement. [VERIFY before production use.]
HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2026, 1, 26),  # Republic Day
        date(2026, 3, 4),   # Holi [VERIFY exact date -- lunar calendar]
        date(2026, 8, 15),  # Independence Day
        date(2026, 10, 2),  # Gandhi Jayanti
        date(2026, 10, 20), # Diwali [VERIFY exact date -- lunar calendar]
        date(2026, 12, 25), # Christmas
    }
)


def is_working_day(d: date) -> bool:
    """Mon-Fri and not in HOLIDAYS. Saturday/Sunday are never working days
    -- Indian bank settlement does not run on weekends regardless of the
    holiday list."""
    if d.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    return d not in HOLIDAYS


def add_working_days(d: date, n: int) -> date:
    """Step forward n working days from d, skipping weekends and holidays.
    add_working_days(<a Friday>, 2) lands on the following Tuesday, not
    Sunday -- this is the function that makes 'settles T+2' mean the
    right calendar date instead of two raw days later."""
    if n < 0:
        raise ValueError("add_working_days does not support negative n")
    current = d
    remaining = n
    while remaining > 0:
        current += timedelta(days=1)
        if is_working_day(current):
            remaining -= 1
    return current


def expected_settlement_date(
    captured_at: datetime, t_plus: int, cutoff: time = time(23, 59, 59)
) -> date:
    """The date a payment captured at captured_at should settle by, given
    a T+n settlement cycle and a daily cutoff time.

    A capture AFTER cutoff belongs to the NEXT day's batch -- this is the
    detail that, if missed, makes every late-in-the-day payment look like
    a missing settlement (see DOMAIN_KNOWLEDGE.md section 2, "gaps that
    cause real bugs").
    """
    capture_date = captured_at.date()
    if captured_at.time() > cutoff:
        capture_date += timedelta(days=1)
    return add_working_days(capture_date, t_plus)
