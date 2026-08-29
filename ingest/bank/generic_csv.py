"""
The default bank adapter: works when a CSV export already roughly
matches the common Indian bank statement convention (Date, Narration,
Chq/Ref No, Debit, Credit, Balance -- the SBI/ICICI/HDFC-family layout).
Column headers are matched case-insensitively against a small set of
known aliases. When a file's headers don't match well enough (sniff()
returns low confidence), the caller falls back to
ingest.bank.column_mapper's LLM-assisted, once-then-cached mapping
(DECISIONS.md D-19) instead of this adapter.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ingest.normalise import clean_narration, normalise_amount, normalise_date
from gen.models import BankLine

_ALIASES: dict[str, tuple[str, ...]] = {
    "txn_date": ("date", "txn date", "transaction date", "value date"),
    "narration": ("narration", "description", "particulars", "remarks"),
    "ref_no": ("ref no", "ref no.", "reference", "chq/ref no", "chq./ref.no."),
    "debit": ("debit", "withdrawal amt", "withdrawal"),
    "credit": ("credit", "deposit amt", "deposit"),
    "balance": ("balance", "closing balance"),
}


def _find_column(headers: list[str], canonical: str) -> str | None:
    normalised_headers = {h.strip().lower(): h for h in headers}
    for alias in _ALIASES[canonical]:
        if alias in normalised_headers:
            return normalised_headers[alias]
    return None


class GenericCsvAdapter:
    name = "generic_csv"

    def sniff(self, headers: list[str]) -> float:
        """Fraction of canonical fields found -- date/narration/at-least-one-
        of-debit-or-credit are required; balance/ref_no are bonus points.
        A file missing date or narration entirely can't be parsed by this
        adapter no matter how confident the rest looks, so those are
        weighted as hard requirements, not just two more points out of six."""
        found = {
            field: _find_column(headers, field) is not None for field in _ALIASES
        }
        if not found["txn_date"] or not found["narration"]:
            return 0.0
        if not (found["debit"] or found["credit"]):
            return 0.0
        return sum(found.values()) / len(found)

    def parse(self, file: Path) -> list[BankLine]:
        with open(file, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            columns = {field: _find_column(headers, field) for field in _ALIASES}

            if not columns["txn_date"] or not columns["narration"]:
                raise ValueError(
                    f"{file}: cannot parse -- missing required date/narration "
                    f"column. Found headers: {headers}. "
                    "Consider ingest.bank.column_mapper for an unrecognised format."
                )

            lines: list[BankLine] = []
            for i, row in enumerate(reader, start=1):
                debit_raw = row.get(columns["debit"], "") if columns["debit"] else ""
                credit_raw = row.get(columns["credit"], "") if columns["credit"] else ""
                balance_raw = row.get(columns["balance"], "") if columns["balance"] else ""
                ref_raw = row.get(columns["ref_no"], "") if columns["ref_no"] else ""

                lines.append(
                    BankLine(
                        line_id=f"bnk_{file.stem}_{i:05d}",
                        txn_date=normalise_date(row[columns["txn_date"]]),
                        narration=clean_narration(row[columns["narration"]]),
                        debit=normalise_amount(debit_raw) if debit_raw.strip() else 0,
                        credit=normalise_amount(credit_raw) if credit_raw.strip() else 0,
                        balance=normalise_amount(balance_raw) if balance_raw.strip() else None,
                        ref_no=ref_raw.strip() or None,
                        source_file=str(file),
                    )
                )
            return lines
