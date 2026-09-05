"""
BankAdapter: the interface every bank's statement format is normalised
through. See DECISIONS.md D-19 -- the real deployment obstacle here isn't
Razorpay (clean API), it's that Indian bank statements have no common
format. One adapter interface, pluggable per bank, is what keeps adding a
new bank a config/mapping change instead of a code change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from gen.models import BankLine


class BankAdapter(Protocol):
    name: str

    def sniff(self, headers: list[str]) -> float:
        """Confidence (0..1) that this adapter can parse a file with
        these column headers. Used to auto-select an adapter for an
        unrecognised upload before falling back to the LLM column
        mapper (ingest/bank/column_mapper.py)."""
        ...

    def parse(self, file: Path) -> list[BankLine]:
        """Parses the file into normalised BankLine records."""
        ...
