import sqlite3

import pytest

from ingest.store import upsert_payments

SCHEMA = """
CREATE TABLE payments (
    merchant_id TEXT NOT NULL,
    payment_id TEXT NOT NULL,
    order_id TEXT,
    amount INTEGER NOT NULL,
    method TEXT NOT NULL,
    fee INTEGER NOT NULL DEFAULT 0,
    tax INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    captured_at TEXT,
    settlement_id TEXT,
    UNIQUE (merchant_id, payment_id)
);
"""


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.execute(SCHEMA)
    yield connection
    connection.close()


def _row_count(conn, merchant_id="M1"):
    return conn.execute(
        "SELECT COUNT(*) FROM payments WHERE merchant_id = ?", (merchant_id,)
    ).fetchone()[0]


class TestUpsertPayments:
    def test_first_run_inserts_all(self, conn):
        rows = [
            {"payment_id": "pay_1", "order_id": "order_1", "amount": 100000, "method": "upi", "status": "captured"},
            {"payment_id": "pay_2", "order_id": "order_2", "amount": 200000, "method": "card", "fee": 4000, "tax": 720, "status": "captured"},
        ]
        stats = upsert_payments(conn, "M1", rows)

        assert stats.inserted == 2
        assert stats.updated == 0
        assert stats.unchanged == 0
        assert _row_count(conn) == 2

    def test_running_twice_with_same_data_is_a_true_no_op(self, conn):
        """THE core contract: idempotent ingestion. A webhook redelivery
        or the daily sweep re-processing the same period must not create
        duplicates or spuriously touch rows."""
        rows = [
            {"payment_id": "pay_1", "order_id": "order_1", "amount": 100000, "method": "upi", "status": "captured"},
        ]
        first = upsert_payments(conn, "M1", rows)
        second = upsert_payments(conn, "M1", rows)

        assert first.inserted == 1
        assert second.inserted == 0
        assert second.unchanged == 1
        assert second.updated == 0
        assert _row_count(conn) == 1  # NOT 2 -- no duplicate row

    def test_changed_field_is_detected_as_update_not_unchanged(self, conn):
        rows = [
            {"payment_id": "pay_1", "order_id": "order_1", "amount": 100000, "method": "upi", "status": "captured"},
        ]
        upsert_payments(conn, "M1", rows)

        rows[0]["status"] = "refunded"
        stats = upsert_payments(conn, "M1", rows)

        assert stats.updated == 1
        assert stats.unchanged == 0
        assert stats.inserted == 0
        current_status = conn.execute(
            "SELECT status FROM payments WHERE payment_id = ?", ("pay_1",)
        ).fetchone()[0]
        assert current_status == "refunded"
        assert _row_count(conn) == 1  # still one row, updated in place not duplicated

    def test_mixed_insert_and_unchanged_in_one_call(self, conn):
        upsert_payments(
            conn,
            "M1",
            [{"payment_id": "pay_1", "amount": 100000, "method": "upi", "status": "captured"}],
        )
        stats = upsert_payments(
            conn,
            "M1",
            [
                {"payment_id": "pay_1", "amount": 100000, "method": "upi", "status": "captured"},  # unchanged
                {"payment_id": "pay_2", "amount": 200000, "method": "card", "status": "captured"},  # new
            ],
        )
        assert stats.unchanged == 1
        assert stats.inserted == 1
        assert _row_count(conn) == 2

    def test_same_payment_id_different_merchant_does_not_collide(self, conn):
        # (merchant_id, payment_id) is the real key -- a payment_id alone
        # must not be treated as globally unique across merchants
        upsert_payments(
            conn, "M1", [{"payment_id": "pay_1", "amount": 100000, "method": "upi", "status": "captured"}]
        )
        upsert_payments(
            conn, "M2", [{"payment_id": "pay_1", "amount": 999999, "method": "card", "status": "captured"}]
        )
        assert _row_count(conn, "M1") == 1
        assert _row_count(conn, "M2") == 1

        m1_amount = conn.execute(
            "SELECT amount FROM payments WHERE merchant_id = 'M1' AND payment_id = 'pay_1'"
        ).fetchone()[0]
        assert m1_amount == 100000  # untouched by M2's insert
