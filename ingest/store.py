"""
Idempotent writes into the payments table. See DECISIONS.md D-06/D-20 for
why every amount column is BIGINT paise and every table carries
merchant_id.

[TESTING NOTE] These functions are written and tested against Python's
stdlib sqlite3, not Postgres -- no live Postgres instance was available
in this environment (see core/schema.sql's own [UNVERIFIED] note). SQLite
supports the same "INSERT ... ON CONFLICT ... DO UPDATE" syntax used here,
so the IDEMPOTENCY LOGIC is genuinely tested and proven, not just
inspected by eye. What is NOT proven here: Postgres-specific behaviour
(UUID generation via gen_random_uuid(), JSONB columns, exact type
coercion). The `paramstyle` argument exists specifically so a real
psycopg-backed connection (paramstyle="pyformat", using %s placeholders)
can reuse this same function without a rewrite -- swap the paramstyle,
point conn at Postgres, and re-run this same test suite against it as the
first thing Phase 11 (api/) does when it needs a real database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class DBConnection(Protocol):
    """Minimal DB-API-shaped interface -- deliberately not tied to
    sqlite3 or psycopg2 specifically, so tests can use sqlite3 today and
    production can swap in psycopg without this module changing."""

    def execute(self, sql: str, params: tuple) -> Any: ...
    def commit(self) -> None: ...


@dataclass
class IngestStats:
    inserted: int
    updated: int
    unchanged: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


def _placeholder(paramstyle: str, position: int) -> str:
    if paramstyle == "qmark":
        return "?"
    if paramstyle == "pyformat":
        return "%s"
    raise ValueError(f"unsupported paramstyle: {paramstyle!r}")


def upsert_payments(
    conn: DBConnection,
    merchant_id: str,
    rows: list[dict],
    paramstyle: str = "qmark",
) -> IngestStats:
    """Inserts or updates payments, keyed on (merchant_id, payment_id).
    Running this twice with the same rows must leave the database in the
    same state as running it once -- this is what makes webhook
    redelivery and the daily sweep's re-processing safe (DECISIONS.md
    D-06's engineering-rules table: "idempotent ingestion").

    Uses ON CONFLICT ... DO UPDATE rather than a SELECT-then-INSERT-or-
    UPDATE round trip -- the conflict resolution is atomic at the database
    level, so two concurrent ingestions (a webhook firing while the daily
    sweep is also running) can't race into a duplicate row the way a
    check-then-act pattern could.

    Returns counts, not just success/failure -- an ingestion run silently
    updating 4,000 unchanged rows and inserting 3 new ones is a very
    different event from inserting 4,000 new ones, and the caller (or a
    human watching a log) should be able to tell them apart.
    """
    ph = lambda i: _placeholder(paramstyle, i)

    inserted = 0
    updated = 0
    unchanged = 0

    for row in rows:
        existing = conn.execute(
            f"SELECT amount, fee, tax, status, settlement_id FROM payments "
            f"WHERE merchant_id = {ph(1)} AND payment_id = {ph(2)}",
            (merchant_id, row["payment_id"]),
        ).fetchone()

        sql = f"""
            INSERT INTO payments
                (merchant_id, payment_id, order_id, amount, method, fee, tax,
                 status, captured_at, settlement_id)
            VALUES ({ph(1)}, {ph(2)}, {ph(3)}, {ph(4)}, {ph(5)}, {ph(6)}, {ph(7)}, {ph(8)}, {ph(9)}, {ph(10)})
            ON CONFLICT (merchant_id, payment_id) DO UPDATE SET
                order_id = excluded.order_id,
                amount = excluded.amount,
                method = excluded.method,
                fee = excluded.fee,
                tax = excluded.tax,
                status = excluded.status,
                captured_at = excluded.captured_at,
                settlement_id = excluded.settlement_id
        """
        params = (
            merchant_id,
            row["payment_id"],
            row.get("order_id"),
            row["amount"],
            row["method"],
            row.get("fee", 0),
            row.get("tax", 0),
            row["status"],
            row.get("captured_at"),
            row.get("settlement_id"),
        )
        conn.execute(sql, params)

        if existing is None:
            inserted += 1
        else:
            existing_tuple = tuple(existing)
            new_tuple = (
                row["amount"],
                row.get("fee", 0),
                row.get("tax", 0),
                row["status"],
                row.get("settlement_id"),
            )
            if existing_tuple == new_tuple:
                unchanged += 1
            else:
                updated += 1

    conn.commit()
    return IngestStats(inserted=inserted, updated=updated, unchanged=unchanged)
