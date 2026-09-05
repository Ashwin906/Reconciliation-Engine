"""
Proof objects: every match's arithmetic, written down as re-derivable
steps rather than asserted as a bare conclusion. See DECISIONS.md D-01.

Lives in core/, not match/ -- moved here after DECISIONS.md D-02's own
independence guard (verify/test_independence.py) caught verify/recompute.py
needing to import Proof/ProofStep as types and flagged it as a forbidden
match/ import. The real distinction D-02 protects is LOGIC, not vocabulary:
Proof/ProofStep are pure, inert data shapes -- a frozen dataclass and two
derived properties, no business rule about HOW to compute a settlement
amount lives here. That computation (expected_settlement_amount) stays in
match/t2_set.py, which is exactly the kind of logic verify/ must never
import. Proof itself is the CONTRACT between the producer (match/) and
the checker (verify/), so it belongs in the neutral layer both depend on
-- not owned by either side.

A Proof produced by match/ is a CLAIM. verify/ independently re-derives
it from raw source data and either accepts or rejects it -- this module
only defines the shape of a claim, it contains no logic that builds or
checks one.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.money import Paise


@dataclass(frozen=True)
class ProofStep:
    op: str                    # 'sum' | 'subtract' | 'add'
    source: str                # e.g. "payments[settlement_id=setl_1]" -- must
                                # be enough for a human, or verify/, to know
                                # exactly what raw data to re-query
    source_ids: tuple[str, ...]
    value: Paise


@dataclass(frozen=True)
class Proof:
    claim: str
    steps: tuple[ProofStep, ...]
    computed: Paise
    observed: Paise
    observed_source: str

    @property
    def residual(self) -> Paise:
        return self.computed - self.observed

    @property
    def ties(self) -> bool:
        return self.residual == 0


def build_settlement_proof(
    settlement_id: str,
    gross: Paise,
    fees: Paise,
    tax: Paise,
    refund_total: Paise,
    dispute_total: Paise,
    member_payment_ids: list[str],
    refund_ids: list[str],
    dispute_ids: list[str],
    observed: Paise,
    observed_source: str,
) -> Proof:
    """Assembles the standard settlement-arithmetic proof shape used by
    T2 (match/t2_set.py). Factored out as its own function -- rather than
    inlined in t2_set.py -- so the exact same step-construction shape can
    be independently re-derived by verify/recompute.py by walking
    `steps`, without importing this function at all."""
    computed = gross - fees - tax - refund_total - dispute_total
    steps = (
        ProofStep("sum", f"payments[settlement_id={settlement_id}]", tuple(member_payment_ids), gross),
        ProofStep("subtract", "fees", tuple(member_payment_ids), fees),
        ProofStep("subtract", "gst_on_fees", tuple(member_payment_ids), tax),
        ProofStep("subtract", "refunds", tuple(refund_ids), refund_total),
        ProofStep("subtract", "disputes_plus_fees", tuple(dispute_ids), dispute_total),
    )
    return Proof(
        claim=f"bank credit == settlement {settlement_id}",
        steps=steps,
        computed=computed,
        observed=observed,
        observed_source=observed_source,
    )
