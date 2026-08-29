"""
The missing layer between "real Razorpay data" and "this pipeline".

[WHAT THIS CLOSES] `ingest/razorpay.py` could already fetch and normalise
live payments, settlements, refunds and disputes; `ingest/bank/` could
already parse a real bank CSV. Everything downstream -- match, verify,
invariant, detect, gate, ledger -- operates on `gen.models.Dataset` and
does not care where it came from. Nothing connected the two. This module
is that connection, and it is the whole difference between "runs on
generated data" and "runs on a real merchant's account".

[NAMING, stated directly] `Dataset` lives in `gen/` for historical
reasons -- the generator was the first thing that needed it (Phase 1).
It is not a synthetic-only type: it is this system's universal input
shape, and the fact that real data now populates the same structure is
what makes the generated-data testing meaningful rather than a parallel
universe. Moving it to `core/` would be tidier and would touch every
module; the honest note is cheaper than the churn.

[WHAT IS AND IS NOT VERIFIED] The assembly logic below is fully tested
against recorded Razorpay-shaped payloads. What has never run against a
live endpoint is `ingest.razorpay.RequestsTransport` itself -- no real
API keys existed in the environment this was built in. That boundary is
one class, it is injected, and it is the only unverified link in the
chain. Said plainly here rather than discovered by whoever tries it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from gen.models import Dataset, Dispute, Payment, Refund, Settlement


@dataclass
class AssemblyReport:
    """What actually came back, and what could not be used.

    Returned alongside the Dataset rather than logged, because a merchant
    connecting a real account needs to know that (say) 12 payments were
    skipped for a missing capture timestamp BEFORE they trust a match
    rate computed without them. Silence here would make an incomplete
    dataset indistinguishable from a complete one.
    """

    payments: int = 0
    settlements: int = 0
    refunds: int = 0
    disputes: int = 0
    bank_lines: int = 0
    skipped: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.skipped is None:
            self.skipped = []

    @property
    def is_complete(self) -> bool:
        return not self.skipped

    def as_dict(self) -> dict:
        return {
            "payments": self.payments,
            "settlements": self.settlements,
            "refunds": self.refunds,
            "disputes": self.disputes,
            "bank_lines": self.bank_lines,
            "skipped_count": len(self.skipped),
            "skipped": self.skipped[:20],
            "complete": self.is_complete,
        }


def _payment(raw: dict, report: AssemblyReport) -> Payment | None:
    # captured_at is genuinely optional in Razorpay's payload (an
    # uncaptured payment has no capture time), and core.calendar_ist's
    # settlement-date logic needs it. Kept rather than dropped: a payment
    # with no capture time is still real money in the collected total,
    # and dropping it would silently break conservation.
    return Payment(
        payment_id=raw["payment_id"],
        order_id=raw.get("order_id"),
        amount=raw["amount"],
        method=raw.get("method", "unknown"),
        status=raw["status"],
        fee=raw.get("fee", 0),
        tax=raw.get("tax", 0),
        captured_at=raw.get("captured_at"),
        settlement_id=raw.get("settlement_id"),
    )


def _settlement(raw: dict, report: AssemblyReport) -> Settlement | None:
    # A settlement with no UTR cannot be tied to a bank line by T1 at all.
    # It is NOT dropped -- it is real money Razorpay says it sent, and
    # hiding it would turn a reconciliation failure into a clean report.
    # It flows through and surfaces as residue, which is the honest
    # outcome.
    created = raw.get("created_at")
    if created is None:
        report.skipped.append(f"settlement {raw.get('settlement_id')}: no created_at")
        return None
    return Settlement(
        settlement_id=raw["settlement_id"],
        amount=raw["amount"],
        utr=raw.get("utr"),
        created_at=created,
    )


def assemble_dataset(
    merchant_id: str,
    payments: list[dict],
    settlements: list[dict],
    refunds: list[dict],
    disputes: list[dict],
    bank_lines: list | None = None,
) -> tuple[Dataset, AssemblyReport]:
    """Builds a Dataset from `ingest.razorpay.RazorpayClient` output plus
    parsed bank lines.

    Takes already-normalised dicts rather than a client, so this is
    testable without any transport at all and can be fed from a recorded
    payload, a live fetch, or a CSV export equally.

    Orders are deliberately left empty: Razorpay's order object adds
    nothing this pipeline reads (no rule, matcher or invariant touches
    `Dataset.orders`), and fetching them would be a per-payment API call
    for data nothing consumes.
    """
    report = AssemblyReport()

    built_payments = []
    for raw in payments:
        payment = _payment(raw, report)
        if payment is not None:
            built_payments.append(payment)

    built_settlements = []
    for raw in settlements:
        settlement = _settlement(raw, report)
        if settlement is not None:
            built_settlements.append(settlement)

    built_refunds = [
        Refund(
            refund_id=raw["refund_id"],
            payment_id=raw["payment_id"],
            amount=raw["amount"],
            created_at=raw["created_at"],
        )
        for raw in refunds
    ]

    built_disputes = [
        Dispute(
            dispute_id=raw["dispute_id"],
            payment_id=raw["payment_id"],
            amount=raw["amount"],
            fee=raw.get("fee", 0),
            status=raw["status"],
            raised_at=raw["raised_at"],
        )
        for raw in disputes
    ]

    lines = list(bank_lines or [])

    report.payments = len(built_payments)
    report.settlements = len(built_settlements)
    report.refunds = len(built_refunds)
    report.disputes = len(built_disputes)
    report.bank_lines = len(lines)

    if not lines:
        # Stated as a skipped item, not merely absent: without bank data
        # there is nothing to reconcile settlements AGAINST, and every
        # settlement would report as unmatched. A merchant seeing 0%
        # matched must be told it is because no statement was supplied,
        # not because their money is missing.
        report.skipped.append(
            "no bank statement supplied -- settlements cannot be matched against bank credits"
        )

    dataset = Dataset(
        merchant_id=merchant_id,
        orders=[],
        payments=built_payments,
        refunds=built_refunds,
        disputes=built_disputes,
        settlements=built_settlements,
        bank_lines=lines,
    )
    return dataset, report


def fetch_and_assemble(
    client,
    merchant_id: str,
    since: int,
    until: int,
    bank_lines: list | None = None,
) -> tuple[Dataset, AssemblyReport]:
    """Live path: pull a date range from Razorpay and assemble it.

    `client` is anything with the `RazorpayClient` fetch methods -- so a
    test injects a fake and production injects the real one, the same
    edge-injection pattern the rest of `ingest/` uses.

    Read-only by construction: every call underneath is a GET. This
    system never writes to a merchant's Razorpay account, and there is no
    code path here that could.
    """
    return assemble_dataset(
        merchant_id=merchant_id,
        payments=client.fetch_payments(since, until),
        settlements=client.fetch_settlements(since, until),
        refunds=client.fetch_refunds(since, until),
        disputes=client.fetch_disputes(since, until),
        bank_lines=bank_lines,
    )
