"""
Eleven fixed leak-detection rules -- DOMAIN_KNOWLEDGE.md section 11.
Each is a pure function (raw: Dataset, cfg: DetectConfig) -> list[Finding].
No LLM anywhere in this file: every rule here is a deterministic
comparison, on purpose (DECISIONS.md D-14 for the fee-rate rule
specifically, and the same principle applies to all eleven).

HONESTY NOTE, read before trusting a "0 findings" result from any of
these against generate_clean() output: several of these rules check a
precondition the current data model (gen/models.py) or generator
(gen/build.py) does not yet produce -- a modeled reserve ledger, a
payment stuck in 'authorized' status, a dispute that reached 'won'. Each
such rule is implemented CORRECTLY and marked in its own docstring with
exactly what it needs to ever fire, and is tested against a hand-built
minimal fixture rather than generate_clean()'s output, so "this rule is
untested against real generator data" is never confused with "this rule
doesn't work." See FUNCTION_REFERENCE.md for the full accounting.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from datetime import time as _time

from core.calendar_ist import expected_settlement_date
from core.money import Paise, apply_rate, format_paise, sum_paise
from detect.finding import Finding
from gen.models import Dataset
from reserve.ledger import overdue_holds


@dataclass(frozen=True)
class DetectConfig:
    rate_card_bps: dict[str, int]
    fee_tolerance_paise: int = 100      # Rs 1 rounding slack before flagging --
                                         # NOT basis points; this is compared
                                         # directly against a summed paise total,
                                         # so it must be a paise value itself.
                                         # An earlier version was named/typed as
                                         # bps and compared straight against paise
                                         # -- a real units bug caught before any
                                         # test ran, fixed here.
    settlement_t_plus: int = 2
    reserve_release_days: int = 30
    capture_gap_hours: int = 24
    double_refund_tolerance: int = 0    # paise


def _bank_line_for_utr(raw: Dataset, utr: str | None):
    if utr is None:
        return None
    return next((b for b in raw.bank_lines if b.ref_no == utr), None)


def detect_missing_settlement(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """A settlement was reported but no bank line carries its UTR.
    Fires against inject_missing_settlement (gen/defects.py)."""
    findings = []
    for s in raw.settlements:
        if _bank_line_for_utr(raw, s.utr) is None:
            findings.append(
                Finding(
                    rule="missing_settlement",
                    severity="P0",
                    impact_paise=abs(s.amount),
                    subjects=(s.settlement_id,),
                    evidence={"expected_amount": s.amount, "utr": s.utr},
                    confidence=1.0,
                    explanation=f"Settlement {s.settlement_id} expected but no matching bank credit found.",
                )
            )
    return findings


def detect_amount_short(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """The bank line for a settlement exists but disagrees on the amount.
    Fires against inject_unbooked_chargeback, inject_short_credit,
    inject_reserve_hold, inject_negative_settlement (all reduce the bank
    credit relative to the reported amount)."""
    findings = []
    for s in raw.settlements:
        bank_line = _bank_line_for_utr(raw, s.utr)
        if bank_line is None:
            continue
        observed = -bank_line.debit if s.amount < 0 else bank_line.credit
        gap = s.amount - observed
        if gap != 0:
            findings.append(
                Finding(
                    rule="amount_short",
                    severity="P1",
                    impact_paise=abs(gap),
                    subjects=(s.settlement_id,),
                    evidence={"expected": s.amount, "observed": observed, "gap": gap},
                    confidence=1.0,
                    explanation=f"Settlement {s.settlement_id} bank credit differs from reported amount by {gap} paise.",
                )
            )
    return findings


def _per_method_fee_stats(raw: Dataset, rate_card_bps: dict[str, int]) -> dict[str, dict]:
    """Shared aggregation: for each contracted method, actual fee total
    vs expected fee total (at the contracted rate), and per-transaction
    overcharge count -- used by BOTH detect_fee_overcharge (this file,
    single-run check) and detect.patterns.scan_fee_drift (the stricter,
    cross-batch, noise-resistant version). DOMAIN_KNOWLEDGE.md section
    11 / DECISIONS.md D-14: PER METHOD, never blended."""
    stats: dict[str, dict] = {}
    for method, rate_bps in rate_card_bps.items():
        payments = [
            p for p in raw.payments if p.status == "captured" and p.method == method
        ]
        if not payments:
            continue
        actual_fee_total = sum_paise(p.fee for p in payments)
        expected_fee_total = sum_paise(apply_rate(p.amount, rate_bps) for p in payments)
        overcharged_count = sum(
            1 for p in payments if p.fee > apply_rate(p.amount, rate_bps)
        )
        stats[method] = {
            "n": len(payments),
            "actual_fee_total": actual_fee_total,
            "expected_fee_total": expected_fee_total,
            "overcharge": actual_fee_total - expected_fee_total,
            "overcharged_count": overcharged_count,
        }
    return stats


def detect_fee_overcharge(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """Single-run, lenient check: any contracted method whose actual fee
    total exceeds the contracted-rate expectation, beyond a small
    tolerance, is flagged -- even from a single transaction. This is the
    rule that finally gives gen.defects.inject_fee_overcharge (planted
    since Phase 1, honestly reported as 'no detector yet' in Phase 6's
    eval harness) a real detection mechanism. Contrast with
    detect.patterns.scan_fee_drift, which additionally requires many
    samples and high consistency before reporting -- the noise-resistant
    version meant for a real trend, not a one-off.
    """
    findings = []
    stats = _per_method_fee_stats(raw, cfg.rate_card_bps)
    for method, s in stats.items():
        if s["overcharge"] > cfg.fee_tolerance_paise:
            findings.append(
                Finding(
                    rule="fee_overcharge",
                    severity="P1",
                    impact_paise=s["overcharge"],
                    subjects=(method,),
                    evidence={
                        "method": method, "n": s["n"],
                        "actual_fee_total": s["actual_fee_total"],
                        "expected_fee_total": s["expected_fee_total"],
                    },
                    confidence=1.0 if s["overcharged_count"] == s["n"] else 0.7,
                    explanation=(
                        f"{method} payments charged {s['actual_fee_total']} paise in fees, "
                        f"contracted rate implies {s['expected_fee_total']} paise "
                        f"({s['overcharged_count']}/{s['n']} transactions over rate)."
                    ),
                )
            )
    return findings


def detect_orphan_refund(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """A refund exists for a payment_id not present in raw.payments at
    all -- a data-integrity anomaly, not producible by any current
    injector (every gen/defects.py refund-related mutation still points
    at a real payment). Implemented and tested via a hand-built fixture."""
    known_payment_ids = {p.payment_id for p in raw.payments}
    findings = []
    for r in raw.refunds:
        if r.payment_id not in known_payment_ids:
            findings.append(
                Finding(
                    rule="orphan_refund",
                    severity="P1",
                    impact_paise=r.amount,
                    subjects=(r.refund_id,),
                    evidence={"payment_id": r.payment_id},
                    confidence=1.0,
                    explanation=f"Refund {r.refund_id} references unknown payment {r.payment_id}.",
                )
            )
    return findings


def detect_double_refund(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """Total refunded against a payment exceeds the payment's own
    amount -- legitimate sequential partial refunds summing exactly to
    the payment amount are NOT flagged, only a total that overshoots it."""
    by_payment: dict[str, list] = {}
    for r in raw.refunds:
        by_payment.setdefault(r.payment_id, []).append(r)

    findings = []
    for payment_id, refunds in by_payment.items():
        if len(refunds) < 2:
            continue
        payment = raw.payment_by_id(payment_id)
        if payment is None:
            continue
        total_refunded = sum_paise(r.amount for r in refunds)
        excess = total_refunded - payment.amount - cfg.double_refund_tolerance
        if excess > 0:
            findings.append(
                Finding(
                    rule="double_refund",
                    severity="P0",
                    impact_paise=excess,
                    subjects=tuple(r.refund_id for r in refunds),
                    evidence={"payment_amount": payment.amount, "total_refunded": total_refunded},
                    confidence=1.0,
                    explanation=f"Payment {payment_id} refunded {total_refunded} paise against an amount of {payment.amount}.",
                )
            )
    return findings


def detect_stale_chargeback(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """A dispute marked 'won' whose deduction was never reversed anywhere
    in the data. [CANNOT FIRE AGAINST generate_clean()/gen.defects.py
    OUTPUT] -- every dispute those modules produce has status='raised';
    nothing in the current generator ever advances a dispute to 'won'.
    Implemented per spec and tested via a hand-built Dispute fixture with
    status='won', because the rule itself is correct even though no
    planted-defect scenario currently exercises it -- named here rather
    than silently absent from the REGISTRY."""
    findings = []
    for d in raw.disputes:
        if d.status == "won":
            findings.append(
                Finding(
                    rule="stale_chargeback",
                    severity="P1",
                    impact_paise=d.amount + d.fee,
                    subjects=(d.dispute_id,),
                    evidence={"payment_id": d.payment_id, "status": d.status},
                    confidence=0.8,
                    explanation=f"Dispute {d.dispute_id} was won but its deduction appears unreversed.",
                )
            )
    return findings


def detect_reserve_stuck(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """Reserve held past its release date.

    [WAS A STRUCTURAL NO-OP UNTIL PHASE 16.] This function previously
    returned [] unconditionally, with a comment stating its precondition
    could not exist: nothing in this system modeled a reserve hold with a
    release date. reserve/ledger.py now does, so the rule is real.

    It reads `raw.reserve_holds` -- optional, because a Dataset built by
    an ingest path that knows nothing about reserve simply will not have
    it, and that must degrade to "no findings" rather than an error. An
    empty result here still means one of two genuinely different things
    (no reserve is overdue / no reserve is tracked), which is why the
    evidence names the ledger explicitly when it does fire.
    """
    holds = getattr(raw, "reserve_holds", None)
    if not holds:
        return []

    as_of = max((s.created_at.date() for s in raw.settlements), default=None)
    if as_of is None:
        return []

    findings: list[Finding] = []
    for hold in overdue_holds(list(holds), as_of):
        days_late = (as_of - hold.release_due_on).days
        findings.append(
            Finding(
                rule="reserve_stuck",
                severity="P1",
                impact_paise=hold.amount,
                subjects=(hold.settlement_id,),
                evidence={
                    "hold_id": hold.hold_id,
                    "held_on": hold.held_on.isoformat(),
                    "release_due_on": hold.release_due_on.isoformat(),
                    "days_overdue": days_late,
                },
                confidence=0.9,
                explanation=(
                    f"Reserve of {format_paise(hold.amount)} on {hold.settlement_id} was due back "
                    f"on {hold.release_due_on.isoformat()} and is {days_late} day(s) overdue."
                ),
            )
        )
    return findings


def detect_unknown_credit(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """A bank credit with no settlement behind it at all. THE one
    detector explicitly designed to catch what invariant.conservation
    (Phase 5) proved it structurally cannot: an unexplained EXTRA credit
    (see that module's own scope-boundary note). Fires against
    inject_offline_payment. Per DECISIONS.md D-17, this category always
    routes to a human regardless of confidence -- reflected here by
    setting severity P0 unconditionally."""
    known_utrs = {s.utr for s in raw.settlements if s.utr}
    findings = []
    for b in raw.bank_lines:
        if b.credit <= 0:
            continue
        if b.ref_no is None or b.ref_no not in known_utrs:
            findings.append(
                Finding(
                    rule="unknown_credit",
                    severity="P0",
                    impact_paise=b.credit,
                    subjects=(b.line_id,),
                    evidence={"ref_no": b.ref_no, "narration": b.narration},
                    confidence=1.0,
                    explanation=f"Bank line {b.line_id} (Rs credited) has no matching settlement.",
                )
            )
    return findings


def detect_duplicate_settlement(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """The same settlement_id recorded more than once."""
    counts = Counter(s.settlement_id for s in raw.settlements)
    findings = []
    for settlement_id, count in counts.items():
        if count > 1:
            total_amount = sum_paise(
                s.amount for s in raw.settlements if s.settlement_id == settlement_id
            )
            findings.append(
                Finding(
                    rule="duplicate_settlement",
                    severity="P0",
                    impact_paise=total_amount,
                    subjects=(settlement_id,),
                    evidence={"occurrences": count},
                    confidence=1.0,
                    explanation=f"Settlement {settlement_id} recorded {count} times.",
                )
            )
    return findings


def detect_capture_gap(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """A payment authorised but never captured, past an expiry window.
    [CANNOT FIRE AGAINST generate_clean()/gen.defects.py OUTPUT] --
    gen.build._make_payments only ever produces status in {'captured',
    'failed'}; no generated payment is ever left in an 'authorized' limbo
    state. Implemented per spec, tested via a hand-built Payment fixture
    with status='authorized' and an old captured_at=None timestamp
    context, for the same reason as detect_stale_chargeback above."""
    findings = []
    for p in raw.payments:
        if p.status == "authorized":
            findings.append(
                Finding(
                    rule="capture_gap",
                    severity="P2",
                    impact_paise=p.amount,
                    subjects=(p.payment_id,),
                    evidence={"status": p.status},
                    confidence=0.9,
                    explanation=f"Payment {p.payment_id} authorised but never captured.",
                )
            )
    return findings


def detect_cycle_drift(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    """Settlement timing silently drifted longer than the contracted
    T+n cycle, across MULTIPLE settlements (a systematic shift, not one
    holiday-adjusted outlier). [CANNOT FIRE AGAINST generate_clean()
    OUTPUT] -- gen.build._batch_settlements always computes settlement
    dates via core.calendar_ist.expected_settlement_date at the
    configured t_plus consistently; nothing in the generator introduces
    drift on its own. Implemented per spec, tested via hand-built
    Settlement/Payment fixtures with an artificially delayed
    settlement.created_at.

    A real false-positive bug was caught here before any test was
    written: the first version compared RAW CALENDAR DAYS elapsed
    against t_plus (`actual_days > t_plus + 1`). A payment captured on a
    Friday legitimately settles the following Tuesday under T+2 --
    DOMAIN_KNOWLEDGE.md section 4's own example -- which is 4 calendar
    days, not 2, and the naive check flagged EVERY such settlement as
    "drift" even in a fully clean dataset. Fixed to compare against
    core.calendar_ist.expected_settlement_date (the same
    working-day-and-holiday-aware function gen/build.py itself uses to
    schedule settlements), and to flag only settlements that actually
    landed LATER than that calendar-correct expectation -- not merely
    "more than N calendar days after capture."
    """
    findings = []
    drifted = []
    for s in raw.settlements:
        members = [p for p in raw.payments if p.settlement_id == s.settlement_id and p.captured_at]
        if not members:
            continue
        earliest_capture = min(p.captured_at for p in members)
        expected = expected_settlement_date(earliest_capture, cfg.settlement_t_plus, _time(23, 59, 59))
        actual = s.created_at.date()
        overshoot_days = (actual - expected).days
        if overshoot_days > 0:
            drifted.append((s, overshoot_days))

    if len(drifted) >= 2:  # a systematic pattern, not one outlier
        for s, overshoot_days in drifted:
            findings.append(
                Finding(
                    rule="cycle_drift",
                    severity="P2",
                    impact_paise=0,
                    subjects=(s.settlement_id,),
                    evidence={"expected_t_plus": cfg.settlement_t_plus, "overshoot_days": overshoot_days},
                    confidence=0.7,
                    explanation=(
                        f"Settlement {s.settlement_id} landed {overshoot_days} day(s) later than "
                        f"T+{cfg.settlement_t_plus} accounting for weekends/holidays."
                    ),
                )
            )
    return findings


REGISTRY = [
    detect_missing_settlement,
    detect_amount_short,
    detect_fee_overcharge,
    detect_orphan_refund,
    detect_double_refund,
    detect_stale_chargeback,
    detect_reserve_stuck,
    detect_unknown_credit,
    detect_duplicate_settlement,
    detect_capture_gap,
    detect_cycle_drift,
]


def run_all(raw: Dataset, cfg: DetectConfig) -> list[Finding]:
    findings: list[Finding] = []
    for rule_fn in REGISTRY:
        findings.extend(rule_fn(raw, cfg))
    return findings
