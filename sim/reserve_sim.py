"""
The multi-period reserve demonstration.

Shows the one thing a single-batch reconciler structurally cannot: money
withheld in one period and returned in another is the SAME money, and a
system that looks at either period alone reports a false alarm in both
directions -- "money missing" in the month it was held, "unexplained
credit" in the month it came back.

[WHAT IS BEING PROVEN] Not that the reserve ledger produces nice numbers.
That the conservation identity FAILS to close without it and CLOSES with
it, over the same data. Both figures are computed by the real
invariant.conservation.check_conservation -- the only difference between
the two calls is whether the reserve ledger's delta is supplied.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

from core.money import sum_paise
from gen.build import generate_clean
from gen.models import Dataset
from gen.scenario import default_spec
from invariant.conservation import check_conservation
from reserve.ledger import ReserveHold, outstanding_balance, overdue_holds, reserve_delta, run_periods

DEFAULT_RESERVE_BPS = 500      # 5% -- a common rolling-reserve rate
DEFAULT_HOLD_DAYS = 60         # released after two 30-day periods


def _period_dataset(seed: int) -> Dataset:
    return generate_clean(default_spec(seed=seed))


def _withhold_from_bank(ds: Dataset, net_delta: int) -> Dataset:
    """Actually removes the period's net reserve movement from the money
    that reached the bank.

    This is the step that makes the demonstration mean anything, and
    leaving it out was a real error in the first draft: without it, every
    period's dataset was CLEAN, conservation closed fine on its own, and
    supplying a reserve delta *broke* a previously-balanced identity --
    the exact opposite of the claim being made. The numbers looked
    plausible and proved nothing.

    A positive delta shrinks the bank credit (more withheld than
    returned); a negative one grows it (past holds came back). Applied to
    the single largest credit line rather than spread across all of them:
    reserve is withheld from the payout as one aggregate movement, and
    smearing it proportionally would imply a per-line withholding that
    does not happen. The line is chosen deterministically (largest, ties
    broken by id) so the same seed always produces the same dataset.
    """
    if net_delta == 0:
        return ds

    credit_lines = [b for b in ds.bank_lines if b.credit > 0]
    if not credit_lines:
        return ds

    target = max(credit_lines, key=lambda b: (b.credit, b.line_id))
    target.credit = max(0, target.credit - net_delta)
    return ds


def build_reserve_journey(
    n_periods: int = 4,
    reserve_bps: int = DEFAULT_RESERVE_BPS,
    hold_days: int = DEFAULT_HOLD_DAYS,
    seed: int = 300,
) -> dict:
    """Runs several consecutive settlement periods with a rolling reserve.

    Each period is a real generated dataset (a different seed, so the
    amounts genuinely differ rather than repeating one month N times).
    Reserve is withheld on each period's settled total and returned
    `hold_days` later, which with the defaults means period 1's hold comes
    back during period 3 -- so the demonstration actually contains a
    release, not just an ever-growing balance.
    """
    if not 2 <= n_periods <= 12:
        raise ValueError("n_periods must be between 2 and 12")

    base = date(2026, 1, 31)
    period_inputs = []
    datasets: list[Dataset] = []

    for i in range(n_periods):
        ds = _period_dataset(seed + i)
        datasets.append(ds)
        end = base + timedelta(days=30 * i)
        start = end - timedelta(days=29)
        gross = sum_paise(s.amount for s in ds.settlements if s.amount > 0)
        period_inputs.append((f"Period {i + 1}", start, end, gross, f"setl_period_{i + 1}"))

    summaries, holds = run_periods(period_inputs, reserve_bps, hold_days)

    periods = []
    for (label, start, end, _gross, _sid), summary, ds in zip(period_inputs, summaries, datasets):
        delta = reserve_delta(holds, start, end)

        # The reserve is genuinely withheld from this period's bank
        # credit BEFORE either check runs -- so the money really is
        # absent, not merely declared absent.
        _withhold_from_bank(ds, delta)
        ds.reserve_holds = [h for h in holds if start <= h.held_on <= end]

        # THE comparison. Same data, same function, one argument different.
        without = check_conservation(ds)
        with_ledger = check_conservation(ds, reserve_delta=delta)

        periods.append(
            {
                **asdict(summary),
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "reserve_delta_paise": delta,
                "conservation_without_ledger": {
                    "residual_paise": without.residual,
                    "closes": without.closes,
                },
                "conservation_with_ledger": {
                    "residual_paise": with_ledger.residual,
                    "closes": with_ledger.closes,
                },
            }
        )

    final_date = period_inputs[-1][2]
    return {
        "config": {
            "n_periods": n_periods,
            "reserve_bps": reserve_bps,
            "reserve_pct": reserve_bps / 100,
            "hold_days": hold_days,
            "seed": seed,
        },
        "periods": periods,
        "totals": {
            "total_held_paise": sum_paise(p["held"] for p in periods),
            "total_released_paise": sum_paise(p["released"] for p in periods),
            "outstanding_balance_paise": outstanding_balance(holds, final_date),
            "overdue_count": len(overdue_holds(holds, final_date)),
        },
        "holds": [
            {
                "hold_id": h.hold_id,
                "settlement_id": h.settlement_id,
                "amount_paise": h.amount,
                "held_on": h.held_on.isoformat(),
                "release_due_on": h.release_due_on.isoformat(),
                "released_on": h.released_on.isoformat() if h.released_on else None,
                "outstanding": h.is_outstanding,
            }
            for h in holds
        ],
        # Stated as a claim the numbers above either support or refute,
        # rather than as narration asserted independently of them.
        "verdict": _verdict(periods),
    }


def _verdict(periods: list[dict]) -> dict:
    released_periods = [p for p in periods if p["released"] > 0]
    return {
        "any_release_happened": bool(released_periods),
        "release_period_labels": [p["label"] for p in released_periods],
        "single_period_view_would_mislead": bool(released_periods),
        "explanation": (
            "A release period receives more money than its own sales explain. Looking at that "
            "period alone, the surplus is an unexplained credit; looking at the period where the "
            "money was withheld, the same rupees look like a shortfall. Only a ledger spanning "
            "both can say they are one movement, deferred."
            if released_periods
            else "No release has fallen due yet within the periods simulated -- the reserve balance "
            "is still accumulating. Extend the number of periods to see a return."
        ),
    }
