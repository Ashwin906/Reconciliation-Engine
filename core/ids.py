"""
Razorpay-style entity ID validation.

Razorpay IDs are prefixed by entity type: pay_XXXX, order_XXXX, rfnd_XXXX,
setl_XXXX, disp_XXXX. See DOMAIN_KNOWLEDGE.md section 15.
"""

from __future__ import annotations

KNOWN_PREFIXES = frozenset({"order", "pay", "rfnd", "setl", "disp"})


def parse_prefix(entity_id: str) -> str:
    """'pay_ABC123' -> 'pay'. Raises if there's no underscore to split on --
    a malformed id should fail loudly here, not three functions later as
    a confusing KeyError somewhere in the matcher."""
    if "_" not in entity_id:
        raise ValueError(f"not a valid Razorpay-style id (no prefix): {entity_id!r}")
    prefix, _, rest = entity_id.partition("_")
    if not prefix or not rest:
        raise ValueError(f"not a valid Razorpay-style id: {entity_id!r}")
    return prefix


def validate(entity_id: str, expect: str) -> None:
    """Raises ValueError if entity_id's prefix doesn't match expect.
    Used at ingestion boundaries to catch a payment_id accidentally
    passed where a settlement_id was expected -- a mistake that would
    otherwise silently produce a wrong (but well-typed) match."""
    if expect not in KNOWN_PREFIXES:
        raise ValueError(f"unknown expected prefix: {expect!r}")
    actual = parse_prefix(entity_id)
    if actual != expect:
        raise ValueError(f"expected a {expect!r} id, got {entity_id!r} (prefix {actual!r})")
