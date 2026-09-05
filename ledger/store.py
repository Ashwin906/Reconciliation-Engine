"""
Append-only ledger. See DECISIONS.md D-16: no UPDATE, no DELETE. A wrong
decision is corrected by appending a new row referencing the one it
corrects (`corrects`), never by editing the original. That is what makes
"why did you decide this in September" a question with a real, checkable
answer, not just a plausible-sounding one.

Append-only is enforced two ways here, deliberately: (1) structurally --
this module's public API offers append(), by_subject(), and replay(),
and NOTHING that updates or deletes a row. There is no update_entry() or
delete_entry() function to accidentally reach for. (2) A real database
trigger rejecting UPDATE/DELETE at the schema level is the stronger
guarantee, and core/schema.sql already notes it as deferred to when a
live Postgres instance exists (Phase 11) -- same [UNVERIFIED] status as
that file's other Postgres-specific claims. This module's own tests
prove (1); they cannot yet prove (2).

[TESTING NOTE, same shape as ingest/store.py] Tested against Python's
stdlib sqlite3, not Postgres, for the same reason: no live Postgres was
available while building this. The `paramstyle` parameter exists for the
same reason it does in ingest/store.py -- swap to "pyformat" against a
real psycopg connection without rewriting this module.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


class DBConnection(Protocol):
    def execute(self, sql: str, params: tuple) -> Any: ...
    def commit(self) -> None: ...


def _placeholder(paramstyle: str) -> str:
    if paramstyle == "qmark":
        return "?"
    if paramstyle == "pyformat":
        return "%s"
    raise ValueError(f"unsupported paramstyle: {paramstyle!r}")


def hash_inputs(data: dict) -> str:
    """Deterministic sha256 of whatever raw-input dict the caller hands
    in -- e.g. {"payments": [...], "settlement": {...}, "bank_line": {...}}
    for the specific records a decision looked at. sort_keys=True makes
    the hash independent of dict insertion order; default=str handles
    datetime/Decimal/etc without requiring the caller to pre-serialize.
    Deliberately generic rather than Dataset-aware -- this module has no
    opinion on WHAT a decision looked at, only that whatever it was gets
    hashed the same way every time."""
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    merchant_id: str
    subject: str
    inputs_hash: str
    tier: str
    verifier_result: str          # 'ACCEPT' | 'REJECT' | 'UNVERIFIABLE' (no Proof
                                   # could be built at all -- nothing was there to
                                   # run verify_proof on, distinct from a proof
                                   # that ran and failed)
    residual: int
    gate: str                     # 'AUTO' | 'HUMAN' | 'REJECT'
    status: str                   # 'PENDING_APPROVAL' | 'APPROVED' | 'REJECTED' | 'APPLIED'
    hypothesis: str | None = None
    evidence: dict | None = None
    corrects: str | None = None   # id of the ledger entry this one corrects, if any
    id: str | None = None         # set by append(); None before insertion
    ts: str | None = None         # set by append(); ISO 8601 UTC


def append(conn: DBConnection, entry: LedgerEntry, paramstyle: str = "qmark") -> str:
    """Inserts a new ledger row. ALWAYS an insert -- there is no
    corresponding update function in this module. Returns the generated
    entry id."""
    ph = _placeholder(paramstyle)
    entry_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    evidence_json = json.dumps(entry.evidence, sort_keys=True) if entry.evidence is not None else None

    sql = f"""
        INSERT INTO ledger
            (id, merchant_id, ts, subject, inputs_hash, tier, hypothesis,
             evidence, verifier_result, residual, gate, status, corrects)
        VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})
    """
    conn.execute(
        sql,
        (
            entry_id, entry.merchant_id, ts, entry.subject, entry.inputs_hash,
            entry.tier, entry.hypothesis, evidence_json, entry.verifier_result,
            entry.residual, entry.gate, entry.status, entry.corrects,
        ),
    )
    conn.commit()
    return entry_id


def _row_to_entry(row: tuple) -> LedgerEntry:
    (id_, merchant_id, ts, subject, inputs_hash, tier, hypothesis, evidence_json,
     verifier_result, residual, gate, status, corrects) = row
    evidence = json.loads(evidence_json) if evidence_json is not None else None
    return LedgerEntry(
        id=id_, merchant_id=merchant_id, ts=ts, subject=subject, inputs_hash=inputs_hash,
        tier=tier, hypothesis=hypothesis, evidence=evidence, verifier_result=verifier_result,
        residual=residual, gate=gate, status=status, corrects=corrects,
    )


_COLUMNS = (
    "id, merchant_id, ts, subject, inputs_hash, tier, hypothesis, evidence, "
    "verifier_result, residual, gate, status, corrects"
)


def by_subject(conn: DBConnection, merchant_id: str, subject: str, paramstyle: str = "qmark") -> list[LedgerEntry]:
    """All ledger entries for one subject (e.g. a settlement_id), oldest
    first -- the full decision history for that subject, including any
    corrections layered on top of an original entry.

    [KNOWN MICRO-RISK] Ordered by `ts` (ISO 8601 UTC, microsecond
    resolution) alone. Two appends within the same microsecond would tie
    and their relative order would then depend on the database's
    unspecified tie-breaking behaviour. Not addressed with a rowid-based
    tiebreaker here because that's SQLite-specific and wouldn't carry
    over to the real Postgres target; a production version should use a
    monotonic sequence column instead. Named rather than silently risked."""
    ph = _placeholder(paramstyle)
    cursor = conn.execute(
        f"SELECT {_COLUMNS} FROM ledger WHERE merchant_id = {ph} AND subject = {ph} ORDER BY ts",
        (merchant_id, subject),
    )
    return [_row_to_entry(row) for row in cursor.fetchall()]


def all_entries(conn: DBConnection, merchant_id: str, paramstyle: str = "qmark") -> list[LedgerEntry]:
    """Every ledger entry for a merchant, newest first -- the raw feed
    the dashboard's Ledger screen (Phase 12) renders. Newest-first
    (unlike by_subject's oldest-first) because a permanent audit feed is
    read like a commit log: most recent activity first."""
    ph = _placeholder(paramstyle)
    cursor = conn.execute(
        f"SELECT {_COLUMNS} FROM ledger WHERE merchant_id = {ph} ORDER BY ts DESC", (merchant_id,)
    )
    return [_row_to_entry(row) for row in cursor.fetchall()]


def get(conn: DBConnection, entry_id: str, paramstyle: str = "qmark") -> LedgerEntry | None:
    ph = _placeholder(paramstyle)
    cursor = conn.execute(f"SELECT {_COLUMNS} FROM ledger WHERE id = {ph}", (entry_id,))
    row = cursor.fetchone()
    return _row_to_entry(row) if row else None


@dataclass(frozen=True)
class ReplayResult:
    entry_id: str
    found: bool
    original_inputs_hash: str | None
    current_inputs_hash: str
    inputs_match: bool


def replay(conn: DBConnection, entry_id: str, raw_loader, paramstyle: str = "qmark") -> ReplayResult:
    """Re-fetches the recorded entry and re-hashes the CURRENT raw inputs
    via raw_loader() (a zero-arg callable returning the same shape of
    dict originally passed to hash_inputs), comparing against the
    inputs_hash stored at decision time.

    SCOPED, NOT THE FULL GUARANTEE YET -- stated plainly rather than
    oversold. True replay (re-running the actual decision pipeline and
    confirming an IDENTICAL OUTCOME, per DECISIONS.md D-16's stated
    standard) needs the real orchestrator, gate, and agent machinery from
    Phases 9-11, none of which exist yet. What this function proves is a
    necessary precondition for that: are the raw inputs this decision
    was based on still exactly what they were when it was made. If
    inputs_match is False, no amount of re-running any future decision
    logic could reproduce the original outcome, because the inputs
    themselves have changed -- catching that early is worth having on
    its own, even before full decision replay exists to build on top of it.
    """
    entry = get(conn, entry_id, paramstyle=paramstyle)
    current_hash = hash_inputs(raw_loader())

    if entry is None:
        return ReplayResult(
            entry_id=entry_id, found=False, original_inputs_hash=None,
            current_inputs_hash=current_hash, inputs_match=False,
        )

    return ReplayResult(
        entry_id=entry_id,
        found=True,
        original_inputs_hash=entry.inputs_hash,
        current_inputs_hash=current_hash,
        inputs_match=entry.inputs_hash == current_hash,
    )
