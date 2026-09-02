"""
T4 investigation orchestration. The agent PROPOSES; it never decides
(DECISIONS.md D-07). Every returned ProposedResolution has NO AUTHORITY
of its own -- verify.recompute.verify_proof (Phase 4) is the only thing
that can turn a hypothesis into something a gate is allowed to act on.

DECISIONS.md D-12: hallucinated evidence ids are rejected before any
arithmetic runs -- the cheapest possible check, catching the single most
common model failure mode, and it needs no LLM call to perform.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import requests

from core.money import Paise, sum_paise
from core.proof import Proof, ProofStep
from gen.models import Dataset
from match.types import Unmatched

from agent.client import GroqClient, SchemaValidationError
from agent.packet import PacketConfig, build_packet
from agent.schema import HYPOTHESIS_SCHEMA

SYSTEM_PROMPT = (
    "You investigate payment-reconciliation exceptions for an Indian merchant. "
    "You will be given an unexplained settlement<->bank discrepancy plus the "
    "relevant payments, refunds, and disputes. Classify the cause using ONLY "
    "the allowed hypothesis values, cite ONLY evidence ids that appear in the "
    "given context, and propose adjustments (refund/dispute/dispute_fee/"
    "fee_correction/reserve_release/other) that would explain the gap. If "
    "nothing in the given context explains it, use hypothesis "
    "'other_unexplained' and an empty evidence_ids/proposed_adjustments list "
    "rather than guessing. In 'note', state in one short sentence what you "
    "actually observed -- especially when using 'other_unexplained', so a "
    "human reviewer sees what you noticed instead of only that you could not "
    "classify it. Never put a hypothesis in the note that is not in the "
    "allowed list. Respond with JSON matching the given schema only."
)

# Adjustment kinds recompute_step (verify/recompute.py) can independently
# re-derive from raw data right now. An adjustment of any OTHER kind
# cannot be turned into a verifiable Proof -- see ProposedResolution.to_proof.
_VERIFIABLE_ADJUSTMENT_KINDS = {"refund": "refunds", "dispute": "disputes_plus_fees", "dispute_fee": "disputes_plus_fees"}


@dataclass(frozen=True)
class NoHypothesis:
    unmatched: Unmatched
    reason: str
    # The model's own short observation, when it had one. Advisory only,
    # never parsed or acted on -- it exists so a novel failure mode the
    # enum can't express reaches a human carrying WHAT the model actually
    # noticed, instead of an information-free "could not classify".
    note: str | None = None


@dataclass(frozen=True)
class ProposedResolution:
    unmatched: Unmatched
    hypothesis: str
    evidence_ids: tuple[str, ...]
    proposed_adjustments: tuple[dict, ...]
    confidence: float
    note: str | None = None

    def to_proof(self, raw: Dataset) -> Proof | None:
        """Builds a Proof from the proposed adjustments -- ONLY if every
        adjustment is of a kind the independent verifier can actually
        re-derive (refund/dispute/dispute_fee). If ANY adjustment is of
        an unverifiable kind (fee_correction, reserve_release, other),
        returns None rather than a partially-trustworthy Proof: this
        hypothesis is still recorded (the ledger keeps it), but it can
        never be auto-applied, only reviewed by a human, because there
        is no way for the verifier to independently confirm it.

        Returns None also if this Unmatched isn't about a settlement, or
        the settlement/its bank line can't be found -- a Proof means
        nothing without something concrete to tie against.
        """
        settlement = next(
            (s for s in raw.settlements if s.settlement_id == self.unmatched.id), None
        )
        if settlement is None:
            return None
        bank_line = next(
            (b for b in raw.bank_lines if b.ref_no == settlement.utr), None
        )
        if bank_line is None:
            return None

        refund_ids: set[str] = set()
        dispute_ids: set[str] = set()
        for adj in self.proposed_adjustments:
            kind = adj.get("kind")
            if kind not in _VERIFIABLE_ADJUSTMENT_KINDS:
                return None  # any single unverifiable adjustment voids the whole proof
            if kind == "refund":
                refund_ids.add(adj["ref"])
            else:
                dispute_ids.add(adj["ref"])

        refund_total = sum_paise(r.amount for r in raw.refunds if r.refund_id in refund_ids)
        dispute_total = sum_paise(
            d.amount + d.fee for d in raw.disputes if d.dispute_id in dispute_ids
        )
        computed = settlement.amount - refund_total - dispute_total
        observed = -bank_line.debit if settlement.amount < 0 else bank_line.credit

        steps = [
            ProofStep("sum", "settlement_amount", (settlement.settlement_id,), settlement.amount),
        ]
        if refund_ids:
            steps.append(ProofStep("subtract", "refunds", tuple(refund_ids), refund_total))
        if dispute_ids:
            steps.append(
                ProofStep("subtract", "disputes_plus_fees", tuple(dispute_ids), dispute_total)
            )

        return Proof(
            claim=f"T4 hypothesis '{self.hypothesis}' explains the gap for {settlement.settlement_id}",
            steps=tuple(steps),
            computed=computed,
            observed=observed,
            observed_source=f"bank_line:{bank_line.line_id}",
        )


def _known_evidence_ids(raw: Dataset) -> set[str]:
    ids: set[str] = set()
    ids |= {p.payment_id for p in raw.payments}
    ids |= {r.refund_id for r in raw.refunds}
    ids |= {d.dispute_id for d in raw.disputes}
    ids |= {s.settlement_id for s in raw.settlements}
    ids |= {b.line_id for b in raw.bank_lines}
    return ids


def investigate(
    unmatched: Unmatched, raw: Dataset, client: GroqClient, cfg: PacketConfig
) -> ProposedResolution | NoHypothesis:
    """1. build packet (deterministic, agent/packet.py)
    2. call the model, structured output validated against HYPOTHESIS_SCHEMA
    3. reject hallucinated evidence ids BEFORE any arithmetic (D-12)
    4. return the hypothesis if usable, NoHypothesis otherwise

    Never raises on a model failure (schema-invalid output, hallucinated
    evidence, an honest 'other_unexplained') -- every failure mode
    degrades to NoHypothesis, which the caller routes to exceptions.md,
    not to an exception that would crash a batch over one bad case.
    """
    packet = build_packet(unmatched, raw, cfg)
    import json

    user_message = json.dumps(packet, sort_keys=True)

    try:
        result = client.complete_json(SYSTEM_PROMPT, user_message, HYPOTHESIS_SCHEMA, temperature=0.0)
    except SchemaValidationError as exc:
        return NoHypothesis(unmatched=unmatched, reason=f"model output invalid: {exc}")
    except requests.exceptions.RequestException as exc:
        # A transient transport failure (rate limit, timeout, network
        # blip) is not a reason to crash the whole batch -- same
        # graceful-degradation contract as a schema failure above. The
        # residue item just stays uninvestigated instead of investigated.
        return NoHypothesis(unmatched=unmatched, reason=f"LLM call failed: {exc}")

    note = (result.get("note") or "").strip() or None

    if result["hypothesis"] == "other_unexplained":
        # Carries the model's observation forward. Still NoHypothesis --
        # the enum was not widened and nothing here is acted on -- but a
        # human now sees what the model actually noticed rather than only
        # that it gave up.
        return NoHypothesis(
            unmatched=unmatched,
            reason="model could not classify this into a known cause",
            note=note,
        )

    known_ids = _known_evidence_ids(raw)
    hallucinated = [eid for eid in result["evidence_ids"] if eid not in known_ids]
    if hallucinated:
        return NoHypothesis(
            unmatched=unmatched,
            reason=f"model cited evidence id(s) not present in the raw data: {hallucinated}",
            note=note,
        )

    return ProposedResolution(
        unmatched=unmatched,
        hypothesis=result["hypothesis"],
        evidence_ids=tuple(result["evidence_ids"]),
        proposed_adjustments=tuple(result["proposed_adjustments"]),
        confidence=result["confidence"],
        note=note,
    )
