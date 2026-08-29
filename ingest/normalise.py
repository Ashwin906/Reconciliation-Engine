"""
Normalisation at the ingestion boundary -- the point where messy external
data (Razorpay's JSON, a bank's CSV) becomes the clean paise-int,
IST-aware types the rest of the system assumes everywhere else. Every
external field passes through exactly one of these functions before it
touches anything else in the codebase.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from core.money import Paise, to_paise

IST = ZoneInfo("Asia/Kolkata")

_CURRENCY_NOISE = re.compile(r"[₹,\s]")

# Bank statements vary in date format by bank. Tried in order; first
# match wins. [VERIFY against real exported statements from the specific
# banks this is deployed against -- this list covers common Indian bank
# CSV export conventions, not an exhaustive set.]
_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%Y-%m-%d")


def normalise_amount(raw: str | int) -> Paise:
    """Handles the two shapes amounts actually arrive in:

    - Razorpay's API already returns amounts as integer paise (see
      DOMAIN_KNOWLEDGE.md section 15) -- an int input is trusted as
      already-correct paise and passed through unchanged, not re-scaled.
    - A bank CSV gives a rupee string, often with a currency symbol and
      thousands separators ('₹1,000.50', '1,000.50') -- these are
      stripped, then the remainder goes through core.money.to_paise,
      which is where the actual decimal-precision safety lives.

    Deliberately does NOT accept float -- same reasoning as
    core.money.to_paise (DECISIONS.md D-06): a float amount at the
    ingestion boundary is already corrupted before this function even
    sees it, and no amount of cleanup here can recover the lost precision.
    """
    if isinstance(raw, float):
        raise TypeError(
            f"normalise_amount() rejects float input ({raw!r}) -- "
            "the source system should have given an int (paise) or a str (rupees)."
        )
    if isinstance(raw, int):
        return raw
    cleaned = _CURRENCY_NOISE.sub("", raw).strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        # some bank exports show debits as (1,234.50) instead of -1234.50
        cleaned = "-" + cleaned[1:-1]
    return to_paise(Decimal(cleaned))


def normalise_ts(raw: str | int) -> datetime:
    """Handles the two shapes timestamps actually arrive in:

    - Razorpay's API gives Unix epoch seconds as an int -- converted
      directly to an IST-aware datetime.
    - A bank CSV gives a date string with no time component in one of
      several common formats -- parsed as a naive date, then localised
      to IST at midnight (bank statements report the transaction DATE,
      not a timestamp; treating it as midnight IST is the least-wrong
      assumption when only a date is given, and is explicit here rather
      than silently defaulting).

    Raises ValueError if a string matches none of the known formats,
    rather than guessing -- a date the system can't confidently parse
    must fail loudly at ingestion, not silently become 1970-01-01 or
    some other wrong-but-valid-looking date three steps downstream.
    """
    if isinstance(raw, int):
        return datetime.fromtimestamp(raw, tz=IST)

    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw.strip(), fmt)
            return parsed.replace(tzinfo=IST)
        except ValueError:
            continue
    raise ValueError(
        f"could not parse timestamp {raw!r} against any known format {_DATE_FORMATS}"
    )


def normalise_date(raw: str | int) -> date:
    """Same as normalise_ts but returns just the date -- for fields like
    a bank line's txn_date where there genuinely is no time component to
    preserve, rather than an IST-midnight datetime nobody should compare
    a real timestamp against."""
    return normalise_ts(raw).date()


def clean_narration(raw: str) -> str:
    """Collapses a bank narration to a comparable form: whitespace
    collapsed to single spaces, trimmed, uppercased. Used before any
    narration-similarity fuzzy matching (Phase 3, T3) so 'Razorpay  Software'
    and 'RAZORPAY SOFTWARE' compare as the same thing rather than differing
    only in incidental formatting."""
    return re.sub(r"\s+", " ", raw).strip().upper()
