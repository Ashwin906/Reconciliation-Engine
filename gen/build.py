"""
Synthetic dataset generator -- the CLEAN case only. No defects here; see
gen/defects.py for the mutation layer that injects known-answer problems.

Contract this module must uphold: generate_clean(spec) must ALWAYS produce
a dataset where every settlement ties exactly to its bank line and the
conservation invariant (Phase 5) closes to zero. If a clean dataset does
not reconcile 100%, the GENERATOR is wrong, not the matcher -- this file
is the ground truth everything else is measured against.

SIMPLIFICATION, documented rather than hidden: refunds and disputes here
are always netted into the SAME settlement batch as their originating
payment (i.e. modeled as if they occurred before that batch's cutoff).
Real-world cross-period refunds -- a refund landing in a LATER settlement
than the original payment -- are deliberately NOT modeled as "clean"
behaviour. They are a known, common, correct pattern (see
DOMAIN_KNOWLEDGE.md section 4, "netting"), and are injected on purpose by
gen/defects.py's inject_cross_period_refund, where they are still a
"defect" only in the sense that they are the hard case the matcher must
handle -- not because cross-period refunds are actually wrong in reality.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from core.calendar_ist import expected_settlement_date
from core.money import apply_rate, gst_on, sum_paise
from gen.models import BankLine, Dataset, Dispute, Order, Payment, Refund, Settlement
from gen.scenario import ScenarioSpec

_BUSINESS_HOUR_START = 9
_BUSINESS_HOUR_END = 21


def _make_orders(spec: ScenarioSpec, rng: random.Random) -> list[Order]:
    orders: list[Order] = []
    counter = 0
    for day_offset in range(spec.days):
        day = spec.start_date + timedelta(days=day_offset)
        n = rng.randint(*spec.orders_per_day)
        for _ in range(n):
            counter += 1
            amount = rng.randint(10_000, 500_000)  # Rs 100 to Rs 5,000, in paise
            hour = rng.randint(_BUSINESS_HOUR_START, _BUSINESS_HOUR_END)
            minute = rng.randint(0, 59)
            created_at = datetime.combine(day, datetime.min.time()) + timedelta(
                hours=hour, minutes=minute
            )
            orders.append(
                Order(
                    order_id=f"order_{counter:05d}",
                    amount=amount,
                    status="created",
                    created_at=created_at,
                )
            )
    return orders


def _pick_method(spec: ScenarioSpec, rng: random.Random) -> str:
    methods = list(spec.method_mix.keys())
    weights = list(spec.method_mix.values())
    return rng.choices(methods, weights=weights, k=1)[0]


def _make_payments(
    orders: list[Order], spec: ScenarioSpec, rng: random.Random
) -> list[Payment]:
    payments: list[Payment] = []
    for i, order in enumerate(orders, start=1):
        method = _pick_method(spec, rng)
        failed = rng.random() < spec.failure_rate
        payment_id = f"pay_{i:05d}"

        if failed:
            payments.append(
                Payment(
                    payment_id=payment_id,
                    order_id=order.order_id,
                    amount=order.amount,
                    method=method,
                    status="failed",
                )
            )
            continue

        # small auth->capture delay in minutes, kept short so it does not
        # accidentally cross the cutoff and create an unintended late-capture
        # case in what is supposed to be the clean dataset
        capture_delay = timedelta(minutes=rng.randint(0, 30))
        captured_at = order.created_at + capture_delay

        rate_bps = spec.rate_card_bps[method]
        fee = apply_rate(order.amount, rate_bps)
        tax = gst_on(fee)

        payments.append(
            Payment(
                payment_id=payment_id,
                order_id=order.order_id,
                amount=order.amount,
                method=method,
                status="captured",
                fee=fee,
                tax=tax,
                captured_at=captured_at,
            )
        )
    return payments


def _make_refunds(
    payments: list[Payment], spec: ScenarioSpec, rng: random.Random
) -> list[Refund]:
    refunds: list[Refund] = []
    counter = 0
    for payment in payments:
        if payment.status != "captured":
            continue
        if rng.random() < spec.refund_rate:
            counter += 1
            # full refund only in the clean generator; partial refunds are
            # a documented defect scenario, not a clean-path behaviour
            refunds.append(
                Refund(
                    refund_id=f"rfnd_{counter:05d}",
                    payment_id=payment.payment_id,
                    amount=payment.amount,
                    created_at=payment.captured_at + timedelta(hours=rng.randint(1, 12)),
                )
            )
    return refunds


def _make_disputes(
    payments: list[Payment],
    refunded_payment_ids: set[str],
    spec: ScenarioSpec,
    rng: random.Random,
) -> list[Dispute]:
    disputes: list[Dispute] = []
    counter = 0
    for payment in payments:
        if payment.status != "captured":
            continue
        if payment.payment_id in refunded_payment_ids:
            # a payment already fully refunded is not also disputed in the
            # clean generator -- avoids modelling a double-deduction that
            # would need its own reconciliation rule to be "clean"
            continue
        if rng.random() < spec.dispute_rate:
            counter += 1
            disputes.append(
                Dispute(
                    dispute_id=f"disp_{counter:05d}",
                    payment_id=payment.payment_id,
                    amount=payment.amount,
                    fee=spec.dispute_fee,
                    status="raised",
                    raised_at=payment.captured_at + timedelta(days=rng.randint(1, 3)),
                )
            )
    return disputes


def _batch_settlements(
    payments: list[Payment],
    refunds: list[Refund],
    disputes: list[Dispute],
    spec: ScenarioSpec,
) -> list[Settlement]:
    """Groups captured payments by their expected settlement date and
    produces one Settlement per group, mutating each payment's
    settlement_id in place. Nets refunds and disputes belonging to
    payments in that group -- see module docstring for the same-batch
    simplification this relies on."""
    refunds_by_payment: dict[str, list[Refund]] = {}
    for r in refunds:
        refunds_by_payment.setdefault(r.payment_id, []).append(r)

    disputes_by_payment: dict[str, list[Dispute]] = {}
    for d in disputes:
        disputes_by_payment.setdefault(d.payment_id, []).append(d)

    groups: dict[str, list[Payment]] = {}
    for payment in payments:
        if payment.status != "captured":
            continue
        settle_date = expected_settlement_date(
            payment.captured_at, spec.t_plus, spec.cutoff
        )
        groups.setdefault(settle_date.isoformat(), []).append(payment)

    settlements: list[Settlement] = []
    for settle_date_str in sorted(groups.keys()):
        group = groups[settle_date_str]
        settlement_id = f"setl_{settle_date_str}"

        gross = sum_paise(p.amount for p in group)
        fees = sum_paise(p.fee for p in group)
        tax = sum_paise(p.tax for p in group)
        refund_total = sum_paise(
            r.amount
            for p in group
            for r in refunds_by_payment.get(p.payment_id, [])
        )
        dispute_total = sum_paise(
            d.amount + d.fee
            for p in group
            for d in disputes_by_payment.get(p.payment_id, [])
        )
        net = gross - fees - tax - refund_total - dispute_total

        for payment in group:
            payment.settlement_id = settlement_id

        settlements.append(
            Settlement(
                settlement_id=settlement_id,
                amount=net,
                utr=f"UTR{len(settlements) + 1:06d}",
                created_at=datetime.fromisoformat(settle_date_str + "T11:00:00"),
            )
        )
    return settlements


def _make_bank_lines(
    settlements: list[Settlement], spec: ScenarioSpec, rng: random.Random
) -> list[BankLine]:
    """One bank line per settlement, exact match by construction -- this
    is what makes generate_clean's output 100%-reconcilable at T1."""
    lines: list[BankLine] = []
    for i, settlement in enumerate(settlements, start=1):
        lines.append(
            BankLine(
                line_id=f"bnk_{i:05d}",
                txn_date=settlement.created_at.date(),
                value_date=settlement.created_at.date(),
                narration=f"NEFT CR RAZORPAY SOFTWARE {settlement.utr}",
                credit=settlement.amount,
                ref_no=settlement.utr,
            )
        )
    return lines


def generate_clean(spec: ScenarioSpec, merchant_id: str = "M_DEMO") -> Dataset:
    """Produces a fully self-consistent synthetic dataset: every order
    resolves, every settlement's arithmetic is internally correct, every
    settlement has exactly one matching bank line. Deterministic in
    spec.seed -- same spec always produces byte-identical output, which
    is what makes ground-truth-based scoring (Phase 6) meaningful."""
    rng = random.Random(spec.seed)

    orders = _make_orders(spec, rng)
    payments = _make_payments(orders, spec, rng)
    refunds = _make_refunds(payments, spec, rng)
    refunded_payment_ids = {r.payment_id for r in refunds}
    disputes = _make_disputes(payments, refunded_payment_ids, spec, rng)
    settlements = _batch_settlements(payments, refunds, disputes, spec)
    bank_lines = _make_bank_lines(settlements, spec, rng)

    return Dataset(
        merchant_id=merchant_id,
        orders=orders,
        payments=payments,
        refunds=refunds,
        disputes=disputes,
        settlements=settlements,
        bank_lines=bank_lines,
    )
