"""
LLM-assisted column mapping for a bank CSV format GenericCsvAdapter
couldn't confidently parse. See DECISIONS.md D-19: this is called ONCE
per unseen format, and the result is meant to be cached by the caller as
config -- this module has no cache of its own, deliberately, so the
caching policy (where, how long, invalidation) is an ingestion-layer
decision, not baked in here.

The LLM client is injected (LLMClient protocol), matching the same
boundary-injection pattern as ingest/razorpay.py's Transport -- this
module's mapping-application logic (apply_mapping) is fully testable
without any LLM at all, and learn_mapping's prompt-building and
response-validation are testable with a FakeLLMClient.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from gen.models import BankLine
from ingest.normalise import clean_narration, normalise_amount, normalise_date

_REQUIRED_FIELDS = ("txn_date", "narration")
# 'signed_amount' is the alternative to the debit/credit pair: one column
# carrying both directions by sign (negative = money out, positive = money
# in). Common in real Indian bank exports, and previously an unsupported
# KNOWN GAP here -- a format with no separate debit/credit columns simply
# could not be mapped.
_OPTIONAL_FIELDS = ("ref_no", "debit", "credit", "signed_amount", "balance")
_ALL_FIELDS = _REQUIRED_FIELDS + _OPTIONAL_FIELDS


@dataclass(frozen=True)
class ColumnMap:
    """canonical field name -> actual column header in the source file.
    A field absent from the dict means that column doesn't exist in this
    bank's export.

    Money direction is expressed one of two ways, never both: either a
    debit/credit column PAIR, or a single 'signed_amount' column whose
    sign carries the direction. apply_mapping splits a signed amount back
    into BankLine's debit/credit fields so everything downstream sees one
    consistent shape regardless of which format the bank exported."""

    fields: dict[str, str]

    def __post_init__(self) -> None:
        missing_required = [f for f in _REQUIRED_FIELDS if f not in self.fields]
        if missing_required:
            raise ValueError(
                f"ColumnMap missing required field(s): {missing_required}"
            )
        unknown = set(self.fields) - set(_ALL_FIELDS)
        if unknown:
            raise ValueError(f"ColumnMap has unknown field(s): {unknown}")

        # Both shapes at once is ambiguous -- which one wins for a row
        # where they disagree? Rejected up front rather than resolved by
        # a silent precedence rule nobody would remember.
        if "signed_amount" in self.fields and (
            "debit" in self.fields or "credit" in self.fields
        ):
            raise ValueError(
                "ColumnMap has both 'signed_amount' and a debit/credit column -- "
                "a bank statement uses one convention or the other, not both"
            )


class LLMClient(Protocol):
    def map_columns(self, headers: list[str], sample_rows: list[list[str]]) -> dict[str, str]:
        """Returns {canonical_field: source_header}. Implementations call
        an actual LLM; tests inject a fake. See agent/client.py (Phase 9)
        for the real Groq-backed implementation this is expected to share
        once that module exists -- not yet wired here to avoid Phase 2
        depending on Phase 9 before Phase 9 is built."""
        ...


class NotConfiguredLLMClient:
    """Default LLMClient when no real one is wired -- fails loudly and
    specifically, rather than silently returning a bogus mapping. Exists
    so learn_mapping() has something to call without every caller needing
    to check 'is a client configured' themselves first."""

    def map_columns(self, headers: list[str], sample_rows: list[list[str]]) -> dict[str, str]:
        raise RuntimeError(
            "No LLM client configured for column mapping. "
            "Wire a real client (e.g. from agent/client.py once Phase 9 exists, "
            "or GROQ_API_KEY-backed) via learn_mapping(..., client=...)."
        )


def learn_mapping(
    headers: list[str],
    sample_rows: list[list[str]],
    client: LLMClient | None = None,
) -> ColumnMap:
    """Asks the LLM client to map this file's headers to our canonical
    field names, once. Validates the response shape (required fields
    present, no invented field names) before returning -- an LLM
    inventing a field name here would be a silent, hard-to-trace bug in
    every row parsed with the resulting map, so it's rejected immediately
    rather than trusted."""
    active_client = client or NotConfiguredLLMClient()
    raw_mapping = active_client.map_columns(headers, sample_rows)

    unknown = set(raw_mapping) - set(_ALL_FIELDS)
    if unknown:
        raise ValueError(
            f"LLM column mapping invented unknown field name(s): {unknown} "
            f"-- not one of {_ALL_FIELDS}"
        )
    for canonical, source_header in raw_mapping.items():
        if source_header not in headers:
            raise ValueError(
                f"LLM column mapping points '{canonical}' at {source_header!r}, "
                f"which is not one of the actual file headers: {headers}"
            )

    return ColumnMap(fields=raw_mapping)


def apply_mapping(rows: list[dict[str, str]], mapping: ColumnMap) -> list[BankLine]:
    """Turns raw CSV rows (as dicts, header -> value) into BankLine
    records using a previously learned or hand-authored ColumnMap. No LLM
    involved here at all -- this is the part that runs on every row,
    every time, and must be as deterministic as GenericCsvAdapter.parse."""
    lines: list[BankLine] = []
    date_col = mapping.fields["txn_date"]
    narration_col = mapping.fields["narration"]
    ref_col = mapping.fields.get("ref_no")
    debit_col = mapping.fields.get("debit")
    credit_col = mapping.fields.get("credit")
    signed_col = mapping.fields.get("signed_amount")
    balance_col = mapping.fields.get("balance")

    for i, row in enumerate(rows, start=1):
        balance_raw = row.get(balance_col, "") if balance_col else ""
        ref_raw = row.get(ref_col, "") if ref_col else ""

        if signed_col:
            # One column, sign carries direction. Split back into the
            # debit/credit pair BankLine (and everything downstream of it)
            # expects, so a signed-amount bank looks identical to a
            # debit/credit bank from here on. Stored as magnitudes:
            # BankLine.debit/.credit are both non-negative by convention,
            # which is why the sign is consumed here and not passed along.
            signed_raw = row.get(signed_col, "")
            signed = normalise_amount(signed_raw) if signed_raw.strip() else 0
            debit = -signed if signed < 0 else 0
            credit = signed if signed > 0 else 0
        else:
            debit_raw = row.get(debit_col, "") if debit_col else ""
            credit_raw = row.get(credit_col, "") if credit_col else ""
            debit = normalise_amount(debit_raw) if debit_raw.strip() else 0
            credit = normalise_amount(credit_raw) if credit_raw.strip() else 0

        lines.append(
            BankLine(
                line_id=f"bnk_mapped_{i:05d}",
                txn_date=normalise_date(row[date_col]),
                narration=clean_narration(row[narration_col]),
                debit=debit,
                credit=credit,
                balance=normalise_amount(balance_raw) if balance_raw.strip() else None,
                ref_no=ref_raw.strip() or None,
            )
        )
    return lines
