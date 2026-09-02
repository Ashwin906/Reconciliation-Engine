"""
Deterministic context retrieval for T4. DECISIONS.md D-11: fixed rules
decide what goes in the packet -- the model does not choose what to look
at. Agentic (model-driven) retrieval is unbounded in cost and
non-reproducible: two runs could see different evidence and reach
different conclusions from identical raw data. Fixed retrieval means the
same case always gets the same file, a precondition for
ledger.store.replay meaning anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from gen.models import Dataset
from match.types import Unmatched


@dataclass(frozen=True)
class PacketConfig:
    window_days: int = 7
    rate_card_bps: dict[str, int] = field(default_factory=dict)


def build_packet(unmatched: Unmatched, raw: Dataset, cfg: PacketConfig) -> dict:
    """Builds the exact context handed to the model for one unresolved
    settlement. Retrieval is precise where possible (payments that are
    genuinely members of this settlement, via settlement_id -- not a
    date-window guess) and additionally windowed by date for evidence
    that isn't structurally linked (nearby unclaimed bank lines), so the
    model sees both what's definitely relevant and what might be."""
    settlement = next(
        (s for s in raw.settlements if s.settlement_id == unmatched.id), None
    )

    members = (
        [p for p in raw.payments if p.settlement_id == unmatched.id]
        if settlement
        else []
    )
    member_ids = {p.payment_id for p in members}

    refunds_on_members = [r for r in raw.refunds if r.payment_id in member_ids]
    disputes_on_members = [d for d in raw.disputes if d.payment_id in member_ids]

    window = timedelta(days=cfg.window_days)
    ref_date = settlement.created_at.date() if settlement else None
    nearby_bank_lines = []
    if ref_date is not None:
        nearby_bank_lines = [
            b
            for b in raw.bank_lines
            if abs((b.txn_date - ref_date).days) <= cfg.window_days
        ]

    return {
        "unexplained": {
            "kind": unmatched.kind,
            "id": unmatched.id,
            "reason": unmatched.reason,
            "detail": unmatched.detail,
        },
        "settlement": (
            {
                "settlement_id": settlement.settlement_id,
                "amount_paise": settlement.amount,
                "utr": settlement.utr,
                "created_at": settlement.created_at.isoformat(),
            }
            if settlement
            else None
        ),
        "member_payments": [
            {
                "payment_id": p.payment_id,
                "amount_paise": p.amount,
                "method": p.method,
                "fee_paise": p.fee,
                "tax_paise": p.tax,
            }
            for p in members
        ],
        "refunds_on_members": [
            {"refund_id": r.refund_id, "payment_id": r.payment_id, "amount_paise": r.amount}
            for r in refunds_on_members
        ],
        "disputes_on_members": [
            {
                "dispute_id": d.dispute_id, "payment_id": d.payment_id,
                "amount_paise": d.amount, "fee_paise": d.fee, "status": d.status,
            }
            for d in disputes_on_members
        ],
        "nearby_bank_lines": [
            {
                "line_id": b.line_id, "txn_date": b.txn_date.isoformat(),
                "narration": b.narration, "credit_paise": b.credit,
                "debit_paise": b.debit, "ref_no": b.ref_no,
            }
            for b in nearby_bank_lines
        ],
        "rate_card_bps": dict(cfg.rate_card_bps),
        "window_days": cfg.window_days,
    }
