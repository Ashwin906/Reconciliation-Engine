"""
T0: exact-id matching. A dictionary lookup, zero ambiguity possible.
Cheapest tier, tried first -- see DOMAIN_KNOWLEDGE.md section 10.
"""

from __future__ import annotations

from collections import Counter

from gen.models import Order, Payment


def group_payments_by_settlement(payments: list[Payment]) -> dict[str, list[Payment]]:
    """Groups captured payments by their settlement_id. A payment with
    status != 'captured' is correctly excluded -- a failed payment can
    never settle, and including it here would make it look like a
    payment mysteriously missing from its settlement rather than the
    normal, expected case it actually is."""
    groups: dict[str, list[Payment]] = {}
    for payment in payments:
        if payment.status != "captured":
            continue
        if payment.settlement_id is None:
            continue
        groups.setdefault(payment.settlement_id, []).append(payment)
    return groups


def link_orders_to_payments(
    orders: list[Order], payments: list[Payment]
) -> tuple[dict[str, Payment], list[Order], list[Payment]]:
    """Maps order_id -> its payment where one exists. Returns
    (mapping, orphan_orders, orphan_payments).

    orphan_orders: orders that need attention for either of two distinct
    reasons, both included here rather than as separate return values --
    (a) no payment attempt was ever recorded against this order_id at
    all, or (b) the order_id appears MORE THAN ONCE among orders (both
    the original and the duplicate are included, since from the data
    alone there is no way to tell which one is "real" -- that
    determination is a human/business-process question, not this
    function's to make).

    Reason (b) exists specifically because of a bug caught while testing
    this function: inject_duplicate_order_id (gen/defects.py) creates a
    second Order sharing an order_id that DOES have a real payment behind
    it. An earlier version of this function only checked "does a payment
    exist for this order_id at all" -- which it does, so BOTH the
    original and duplicate order silently passed as fine, and the whole
    point of the defect (a merchant-side data integrity problem) would
    never have surfaced. Checking for duplicate order_ids directly, not
    just missing payments, is what catches it.

    orphan_payments: payments whose order_id doesn't match any known
    order. Doesn't occur in this dataset's generator, but a real
    ingestion pipeline receiving payments and orders from two different
    systems (Razorpay vs. the merchant's own DB) could plausibly see
    this if one feed lags the other -- handled here rather than assumed
    away.
    """
    payment_by_order: dict[str, Payment] = {}
    for payment in payments:
        if payment.order_id is not None:
            # first payment wins if somehow more than one payment shares
            # an order_id -- not expected in this dataset, but a
            # dict.setdefault keeps this deterministic rather than
            # silently letting the last one clobber the first
            payment_by_order.setdefault(payment.order_id, payment)

    known_order_ids = {o.order_id for o in orders}
    order_id_counts = Counter(o.order_id for o in orders)

    orphan_orders = [
        o
        for o in orders
        if o.order_id not in payment_by_order or order_id_counts[o.order_id] > 1
    ]
    orphan_payments = [
        p for p in payments if p.order_id is not None and p.order_id not in known_order_ids
    ]

    mapping = {oid: p for oid, p in payment_by_order.items() if oid in known_order_ids}
    return mapping, orphan_orders, orphan_payments
