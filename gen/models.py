"""
Entity dataclasses mirroring core/schema.sql. Kept separate from the DB
layer on purpose -- the generator and the matcher both need to build and
pass around these objects in memory, long before (or entirely without)
touching Postgres. A dataclass with no ORM dependency is testable in
isolation and importable by core/, match/, verify/ alike without any of
them needing a database connection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from core.money import Paise


@dataclass
class Order:
    order_id: str
    amount: Paise
    status: str
    created_at: datetime
    currency: str = "INR"
    invoice_no: str | None = None


@dataclass
class Payment:
    payment_id: str
    order_id: str | None
    amount: Paise
    method: str
    status: str                      # created|authorized|captured|refunded|failed
    fee: Paise = 0
    tax: Paise = 0
    captured_at: datetime | None = None
    settlement_id: str | None = None


@dataclass
class Refund:
    refund_id: str
    payment_id: str
    amount: Paise
    created_at: datetime


@dataclass
class Dispute:
    dispute_id: str
    payment_id: str
    amount: Paise
    fee: Paise
    status: str                      # raised|won|lost|closed
    raised_at: datetime


@dataclass
class Settlement:
    settlement_id: str
    amount: Paise
    utr: str
    created_at: datetime


@dataclass
class BankLine:
    line_id: str
    txn_date: date
    narration: str
    credit: Paise = 0
    debit: Paise = 0
    value_date: date | None = None
    ref_no: str | None = None
    balance: Paise | None = None
    source_file: str | None = None


@dataclass
class Dataset:
    """The four-source universe for one merchant. Everything downstream
    (matcher, verifier, invariants, detectors) reads from this."""

    merchant_id: str
    orders: list[Order] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)
    disputes: list[Dispute] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    bank_lines: list[BankLine] = field(default_factory=list)
    # Rolling-reserve holds with real dates (Phase 16, reserve/ledger.py).
    # Optional and default-empty: a Dataset from an ingest path that knows
    # nothing about reserve is still valid, and every consumer treats an
    # empty list as "reserve is not tracked here" rather than "no reserve
    # exists" -- a distinction detect_reserve_stuck depends on.
    reserve_holds: list = field(default_factory=list)

    def payment_by_id(self, payment_id: str) -> Payment | None:
        return next((p for p in self.payments if p.payment_id == payment_id), None)

    def refunds_for(self, payment_id: str) -> list[Refund]:
        return [r for r in self.refunds if r.payment_id == payment_id]

    def disputes_for(self, payment_id: str) -> list[Dispute]:
        return [d for d in self.disputes if d.payment_id == payment_id]

    def payments_in_settlement(self, settlement_id: str) -> list[Payment]:
        return [p for p in self.payments if p.settlement_id == settlement_id]
