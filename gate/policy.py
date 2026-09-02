"""
The policy gate. DECISIONS.md D-17: this is the component deciding how
much power the software has over someone's books, so it must be readable
in ten seconds and behave identically every time. Plain `if` statements,
no I/O, no LLM, exhaustively tested per branch below.

Scope, stated directly: decide() is for CORRECTIONS -- a proposed
adjustment to a settlement's books (typically originating from
agent/investigate.py's T4 hypotheses, once independently verified). It
is not consulted for T0/T1/T3 matches, which are already-resolved ties
with nothing to gate; there is no "apply/don't apply" decision to make
about a match that already tied exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.money import Paise

GateDecision = Literal["AUTO", "HUMAN", "REJECT"]


@dataclass(frozen=True)
class GateConfig:
    auto_apply_max_paise: int = 10_000          # Rs 100 -- [VERIFY against a real risk appetite before production use]
    min_confidence: float = 0.95
    always_human: frozenset[str] = field(
        default_factory=lambda: frozenset({"unknown_credit", "negative_settlement"})
    )


@dataclass(frozen=True)
class Resolution:
    """The minimal input this gate needs to decide -- deliberately NOT
    agent.investigate.ProposedResolution or verify.recompute.VerifyResult
    directly, so decide() itself stays free of any dependency on how a
    resolution was produced. from_investigation() below is the (optional)
    bridge for the common case of wiring T4's actual output into this
    shape; decide() has no idea that function exists."""

    kind: str
    paise: Paise          # absolute impact -- always >= 0
    confidence: float
    verified: bool         # did independent verification (verify_proof) accept this?


def decide(resolution: Resolution, cfg: GateConfig = GateConfig()) -> GateDecision:
    """
    REJECT  -- verification did not accept this resolution. There is
               nothing to approve: an unverified claim is simply wrong,
               not a candidate for human review of THIS specific fix.
               The underlying case stays in residue/exceptions.md for a
               human to investigate fresh, not to approve or deny a
               proposal that doesn't tie.
    HUMAN   -- verified, but either: the kind is in always_human
               (regardless of amount or confidence -- e.g. unknown_credit
               has no source document to auto-trust no matter how small),
               or the amount exceeds auto_apply_max_paise, or confidence
               is below min_confidence.
    AUTO    -- verified, small, high-confidence, and not in a category
               that always requires a human regardless.

    Order matters for readability, not for correctness: verified is
    checked first because everything else is meaningless if the proof
    itself doesn't hold up.
    """
    if not resolution.verified:
        return "REJECT"
    if resolution.kind in cfg.always_human:
        return "HUMAN"
    if resolution.paise > cfg.auto_apply_max_paise:
        return "HUMAN"
    if resolution.confidence < cfg.min_confidence:
        return "HUMAN"
    return "AUTO"


def from_investigation(proposed, proof, raw) -> Resolution:
    """Bridges agent.investigate.ProposedResolution + the Proof it built
    into the Resolution shape decide() actually needs -- verifying the
    proof itself and independently recomputing the correction's SIZE in
    the same call, so nothing here ever trusts a number the model claimed.

    A real bug was caught while writing this function's first draft: it
    computed `paise` (the amount the gate compares against
    auto_apply_max_paise) from
    `sum(adj["paise"] for adj in proposed.proposed_adjustments)` -- the
    MODEL's own claimed adjustment size. That directly contradicts the
    whole point of this system: a model could understate an adjustment's
    size to sneak a large correction under the AUTO threshold, and the
    gate would have no way to know, because it never independently
    checked. Fixed by recomputing the adjustment total via
    verify.recompute.recompute_step -- the SAME independent, raw-data-only
    re-derivation Phase 4's verifier itself uses -- over every proof step
    except the settlement_amount base, rather than reading anything the
    model asserted. If proof.steps' independent recomputation disagrees
    with what the model claimed, this function never notices or cares --
    it was never going to trust the model's number in the first place.
    """
    from verify.recompute import recompute_step, verify_proof

    verify_result = verify_proof(proof, raw)
    if not verify_result.accepted:
        return Resolution(kind=proposed.hypothesis, paise=0, confidence=proposed.confidence, verified=False)

    adjustment_paise = sum(
        recompute_step(step, raw) for step in proof.steps if step.source != "settlement_amount"
    )
    return Resolution(
        kind=proposed.hypothesis,
        paise=abs(adjustment_paise),
        confidence=proposed.confidence,
        verified=True,
    )
