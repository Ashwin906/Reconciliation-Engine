import sqlite3

import pytest

from ledger.store import LedgerEntry, append, by_subject, get, hash_inputs, replay

SCHEMA = """
CREATE TABLE ledger (
    id TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    subject TEXT NOT NULL,
    inputs_hash TEXT NOT NULL,
    tier TEXT NOT NULL,
    hypothesis TEXT,
    evidence TEXT,
    verifier_result TEXT NOT NULL,
    residual INTEGER NOT NULL,
    gate TEXT NOT NULL,
    status TEXT NOT NULL,
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
        merchant_id="M1",
        subject="setl_1",
        inputs_hash=hash_inputs({"a": 1}),
        tier="T2",
        verifier_result="ACCEPT",
        residual=0,
        gate="AUTO",
        status="APPLIED",
    )
    defaults.update(overrides)
    return LedgerEntry(**defaults)


class TestAppendAndRetrieve:
    def test_append_returns_an_id_and_row_is_retrievable(self, conn):
        entry_id = append(conn, _entry())
        assert entry_id

        fetched = get(conn, entry_id)
        assert fetched is not None
        assert fetched.merchant_id == "M1"
        assert fetched.subject == "setl_1"
        assert fetched.ts is not None  # set by append()

    def test_get_unknown_id_returns_none(self, conn):
        assert get(conn, "does-not-exist") is None

    def test_evidence_dict_round_trips_through_json(self, conn):
        entry_id = append(conn, _entry(evidence={"dispute_id": "disp_1", "amount": 40000}))
        fetched = get(conn, entry_id)
        assert fetched.evidence == {"dispute_id": "disp_1", "amount": 40000}

    def test_null_evidence_stays_none(self, conn):
        entry_id = append(conn, _entry(evidence=None))
        fetched = get(conn, entry_id)
        assert fetched.evidence is None


class TestByfSubject:
    def test_returns_only_matching_merchant_and_subject(self, conn):
        append(conn, _entry(merchant_id="M1", subject="setl_1"))
        append(conn, _entry(merchant_id="M1", subject="setl_2"))
        append(conn, _entry(merchant_id="M2", subject="setl_1"))

        results = by_subject(conn, "M1", "setl_1")
        assert len(results) == 1
        assert results[0].merchant_id == "M1"
        assert results[0].subject == "setl_1"

    def test_ordered_oldest_first(self, conn):
        first_id = append(conn, _entry(subject="setl_1", tier="T1"))
        second_id = append(conn, _entry(subject="setl_1", tier="T2"))

        results = by_subject(conn, "M1", "setl_1")
        assert [r.id for r in results] == [first_id, second_id]

    def test_empty_when_nothing_matches(self, conn):
        assert by_subject(conn, "M1", "setl_unknown") == []


class TestCorrections:
    def test_a_correction_references_the_original_and_both_remain(self, conn):
        """DECISIONS.md D-16: a wrong decision is corrected by APPENDING,
        never by rewriting the original. Both rows must exist afterward."""
        original_id = append(conn, _entry(subject="setl_1", status="APPLIED", residual=0))
        correction_id = append(
            conn,
            _entry(subject="setl_1", status="APPLIED", residual=1000, corrects=original_id),
        )

        history = by_subject(conn, "M1", "setl_1")
        assert len(history) == 2  # original NOT overwritten
        ids = [h.id for h in history]
        assert original_id in ids
        assert correction_id in ids

        correction = next(h for h in history if h.id == correction_id)
        assert correction.corrects == original_id


class TestAppendOnlyStructurally:
    def test_module_provides_no_update_or_delete_function(self):
        """The public API itself offers no way to violate append-only --
        no update_entry, no delete_entry, no generic 'save' that could
        overwrite an existing row."""
        import ledger.store as store_module

        public_names = [n for n in dir(store_module) if not n.startswith("_")]
        forbidden_substrings = ("update", "delete", "remove", "overwrite")
        violations = [
            name for name in public_names
            if any(bad in name.lower() for bad in forbidden_substrings)
        ]
        assert violations == [], f"ledger.store exposes forbidden mutation function(s): {violations}"


class TestHashInputs:
    def test_deterministic_for_same_data(self):
        data = {"payments": ["pay_1", "pay_2"], "amount": 100000}
        assert hash_inputs(data) == hash_inputs(data)

    def test_independent_of_key_order(self):
        a = {"x": 1, "y": 2}
        b = {"y": 2, "x": 1}
        assert hash_inputs(a) == hash_inputs(b)

    def test_different_data_different_hash(self):
        assert hash_inputs({"a": 1}) != hash_inputs({"a": 2})

    def test_handles_non_json_native_types_via_default_str(self):
        from datetime import date
        # must not raise -- datetime/date objects are common in raw payloads
        result = hash_inputs({"d": date(2026, 9, 2)})
        assert isinstance(result, str) and len(result) == 64  # sha256 hex digest length


class TestReplay:
    def test_matching_inputs_report_inputs_match_true(self, conn):
        raw = {"payments": ["pay_1"], "amount": 100000}
        entry_id = append(conn, _entry(inputs_hash=hash_inputs(raw)))

        result = replay(conn, entry_id, raw_loader=lambda: raw)
        assert result.found is True
        assert result.inputs_match is True

    def test_changed_inputs_report_inputs_match_false(self, conn):
        original_raw = {"payments": ["pay_1"], "amount": 100000}
        entry_id = append(conn, _entry(inputs_hash=hash_inputs(original_raw)))

        changed_raw = {"payments": ["pay_1"], "amount": 999999}  # amount mutated since
        result = replay(conn, entry_id, raw_loader=lambda: changed_raw)
        assert result.found is True
        assert result.inputs_match is False
        assert result.original_inputs_hash != result.current_inputs_hash

    def test_unknown_entry_id_reports_not_found(self, conn):
        result = replay(conn, "does-not-exist", raw_loader=lambda: {"a": 1})
        assert result.found is False
        assert result.inputs_match is False
