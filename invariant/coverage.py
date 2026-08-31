"""
Three cheap structural sanity checks, separate from the conservation
identity -- these ask "does the data even make basic sense" before
conservation asks "does the money add up". Deliberately simple: each is a
single pass over one or two lists, not a re-implementation of anything
in match/.
"""

from __future__ import annotations

from collections import Counter

from gen.models import Dataset, Order, Payment


def check_every_order_terminal(raw: Dataset) -> list[Order]:
    """Returns orders with no payment attempt recorded against their
    order_id at all -- every order should reach SOME terminal payment
    state (captured or failed); one with neither is a data gap, not a
    business-as-usual case."""
    orders_with_a_payment = {p.order_id for p in raw.payments if p.order_id is not None}
    return [o for o in raw.orders if o.order_id not in orders_with_a_payment]


def check_every_capture_settled_or_pending(raw: Dataset) -> list[Payment]:
    """Returns captured payments with no settlement_id at all.

    [KNOWN GAP, stated rather than silently assumed away] A real system
    needs a grace window here -- a payment captured minutes ago hasn't
    had time to settle yet, and flagging it would be a false positive.
    This synthetic dataset has no "current time" concept distinct from
    its own generation, so every captured payment is expected to have
    settled by the time this check runs; a genuinely time-aware version
    (captured_at + t_plus vs. "now") belongs with the real pipeline
    (Phase 11), not this offline check.
    """
    return [
        p
        for p in raw.payments
        if p.status == "captured" and p.settlement_id is None
    ]


def check_no_duplicate_settlement(raw: Dataset) -> list[str]:
    """Returns settlement_ids that appear more than once in
    raw.settlements -- guards against the same settlement being recorded
    twice (e.g. a duplicate webhook delivery that wasn't deduplicated
    upstream by ingest.store.upsert_payments' idempotency contract)."""
    counts = Counter(s.settlement_id for s in raw.settlements)
    return sorted(sid for sid, count in counts.items() if count > 1)
