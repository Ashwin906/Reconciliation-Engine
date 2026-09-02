"""
Cross-batch pattern scans -- the "no human would ever spot this by eye"
half of DOMAIN_KNOWLEDGE.md section 11. Every individual transaction can
look almost right; only the aggregate reveals a systematic leak. Reuses
detect.rules._per_method_fee_stats (the same underlying computation as
detect_fee_overcharge) but adds the statistical gate a single-run rule
doesn't have: enough samples, and consistent enough across them, to be a
real pattern rather than one-off noise.
"""

from __future__ import annotations

from datetime import time as _time

from core.calendar_ist import expected_settlement_date
from detect.finding import Finding
from detect.rules import DetectConfig, _per_method_fee_stats
from gen.models import Dataset


def scan_fee_drift(
    raw: Dataset,
    cfg: DetectConfig,
    min_samples: int = 20,
    min_consistency: float = 0.9,
) -> list[Finding]:
    """Flags a method only if BOTH hold: at least min_samples captured
    transactions of that method, AND at least min_consistency (default
    90%) of them individually show an overcharge -- not just a positive
    total (which a handful of large outliers could produce on their
    own). This is what turns 'the total looks a bit high' into 'this is
    a systematic, structural rate problem,' the confidence a merchant
    would need before raising a formal dispute with evidence."""
    findings = []
    stats = _per_method_fee_stats(raw, cfg.rate_card_bps)
    for method, s in stats.items():
        if s["n"] < min_samples:
            continue
        consistency = s["overcharged_count"] / s["n"]
        if consistency < min_consistency:
            continue
        if s["overcharge"] <= cfg.fee_tolerance_paise:
            continue

        findings.append(
            Finding(
                rule="fee_drift_pattern",
                severity="P1",
                impact_paise=s["overcharge"],
                subjects=(method,),
                evidence={
                    "method": method,
                    "n": s["n"],
                    "overcharged_count": s["overcharged_count"],
                    "consistency": round(consistency, 3),
                },
                confidence=min(0.99, consistency),
                explanation=(
                    f"{method} overcharged in {s['overcharged_count']}/{s['n']} "
                    f"transactions ({consistency:.0%}) -- a systematic rate "
                    f"discrepancy, not isolated noise. Total impact "
                    f"{s['overcharge']} paise."
                ),
            )
        )
    return findings


def scan_settlement_cycle_drift(
    raw: Dataset, cfg: DetectConfig, window_settlements: int = 3
) -> list[Finding]:
    """Flags a systematic lengthening of the settlement cycle (e.g.
    T+2 quietly becoming T+3) across the most recent window_settlements
    settlements, sorted by date -- distinct from detect_cycle_drift
    (detect/rules.py), which flags individual drifted settlements
    without requiring them to be the MOST RECENT ones. This scan asks a
    narrower, trend-shaped question: is the cycle getting worse lately,
    right now, not just 'did some settlements drift at some point.'

    Uses core.calendar_ist.expected_settlement_date, not raw calendar
    days, for the same reason detect_cycle_drift does -- see that
    function's docstring for the false-positive bug (every
    weekend-crossing settlement in a fully clean dataset flagged as
    "drift") this shares the exact same fix for, caught in the same pass
    rather than shipped once and found twice.
    """
    settlements_by_date = sorted(raw.settlements, key=lambda s: s.created_at)
    if len(settlements_by_date) < window_settlements:
        return []

    recent = settlements_by_date[-window_settlements:]
    overshoots = []
    for s in recent:
        members = [p for p in raw.payments if p.settlement_id == s.settlement_id and p.captured_at]
        if not members:
            continue
        earliest = min(p.captured_at for p in members)
        expected = expected_settlement_date(earliest, cfg.settlement_t_plus, _time(23, 59, 59))
        overshoots.append((s.created_at.date() - expected).days)

    if len(overshoots) < window_settlements:
        return []

    if all(g > 0 for g in overshoots):
        return [
            Finding(
                rule="settlement_cycle_drift",
                severity="P2",
                impact_paise=0,
                subjects=tuple(s.settlement_id for s in recent),
                evidence={"expected_t_plus": cfg.settlement_t_plus, "recent_overshoot_days": overshoots},
                confidence=0.75,
                explanation=(
                    f"The last {window_settlements} settlements all landed later than "
                    f"T+{cfg.settlement_t_plus} accounting for weekends/holidays: "
                    f"{overshoots} day(s) over."
                ),
            )
        ]
    return []
