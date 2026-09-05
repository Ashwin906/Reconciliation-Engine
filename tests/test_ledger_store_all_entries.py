import sqlite3

import pytest

from ledger.store import LedgerEntry, all_entries, append, hash_inputs

SCHEMA = """
CREATE TABLE ledger (
    id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, ts TEXT NOT NULL,
    subject TEXT NOT NULL, inputs_hash TEXT NOT NULL, tier TEXT NOT NULL,
    hypothesis TEXT, evidence TEXT, verifier_result TEXT NOT NULL,
    residual INTEGER NOT NULL, gate TEXT NOT NULL, status TEXT NOT NULL,
    corrects TEXT
);
"""


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.execute(SCHEMA)
    yield connection
    connection.close()


def _entry(**overrides) -> LedgerEntry:
    defaults = dict(
        merchant_id="M1", subject="setl_1", inputs_hash=hash_inputs({"a": 1}),
        tier="T2", verifier_result="ACCEPT", residual=0, gate="AUTO", status="APPLIED",
    )
    defaults.update(overrides)
    return LedgerEntry(**defaults)


class TestAllEntries:
    def test_empty_when_nothing_recorded(self, conn):
        assert all_entries(conn, "M1") == []

    def test_returns_only_this_merchant(self, conn):
        append(conn, _entry(merchant_id="M1"))
        append(conn, _entry(merchant_id="M2"))
        results = all_entries(conn, "M1")
        assert len(results) == 1
        assert results[0].merchant_id == "M1"

    def test_newest_first(self, conn):
        first_id = append(conn, _entry(subject="setl_1"))
        second_id = append(conn, _entry(subject="setl_2"))
        results = all_entries(conn, "M1")
        assert [r.id for r in results] == [second_id, first_id]
