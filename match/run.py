"""
Orchestrates T0 -> T1 -> T2 -> T3 over a full Dataset. Each tier's own
correctness is tested in isolation (test_match_t0/t1/t2/t3.py); this
module's job is just the wiring -- which tier's leftovers feed the next,
and what counts as final residue for T4 (Phase 9, not yet built) to pick
up.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gen.models import Dataset
from match.t0_key import group_payments_by_settlement, link_orders_to_payments
from match.t1_utr import index_bank_by_ref, match_settlement_to_bank
from match.t2_set import match_group_to_settlement
from match.t3_fuzzy import match_fuzzy
from match.types import MatchCandidate, Unmatched


@dataclass
class MatchContext:
    dataset: Dataset


@dataclass
class MatchResult:
    t0_groups: dict[str, list] = field(default_factory=dict)
    t1_matches: list[MatchCandidate] = field(default_factory=list)
    t1_unmatched: list[Unmatched] = field(default_factory=list)
    t2_matches: list[MatchCandidate] = field(default_factory=list)
    t2_unmatched: list[Unmatched] = field(default_factory=list)
    t3_matches: list[MatchCandidate] = field(default_factory=list)
    residue: list[Unmatched] = field(default_factory=list)

    def all_matches(self) -> list[MatchCandidate]:
        return self.t1_matches + self.t2_matches + self.t3_matches

    def tier_counts(self) -> dict[str, int]:
        counts = {"T1": len(self.t1_matches), "T2": len(self.t2_matches), "T3": len(self.t3_matches)}
        counts["unresolved"] = len(self.residue)
        return counts


def run_matching(ctx: MatchContext) -> MatchResult:
    """Runs settlement<->bank matching through T1 (exact UTR), T2
    (settlement-internal-consistency, run against every settlement
    independently of T1's outcome -- it answers a different question, see
    match/t2_set.py's module docstring), and T3 (fuzzy, only over what T1
    could not resolve by exact UTR). Whatever T3 still can't resolve
    becomes `residue` -- the final unmatched list, meant for T4 in a
    later phase.
    """
    ds = ctx.dataset
    t0_groups = group_payments_by_settlement(ds.payments)

    bank_index = index_bank_by_ref(ds.bank_lines)
    t1_matches, t1_unmatched = match_settlement_to_bank(ds.settlements, bank_index)

    t2_matches: list[MatchCandidate] = []
    t2_unmatched: list[Unmatched] = []
    for settlement in ds.settlements:
        members = t0_groups.get(settlement.settlement_id, [])
        result = match_group_to_settlement(settlement, members, ds.refunds, ds.disputes)
        if isinstance(result, MatchCandidate):
            t2_matches.append(result)
        else:
            t2_unmatched.append(result)

    # T3 only operates on settlements T1 could not resolve by exact UTR,
    # against bank lines T1 did not already claim -- reusing a bank line
    # T1 already matched elsewhere would be a double-count bug
    t1_matched_settlement_ids = {m.left.id for m in t1_matches}
    t1_matched_bank_line_ids = {m.right.id for m in t1_matches}

    unresolved_settlements = [
        s for s in ds.settlements if s.settlement_id not in t1_matched_settlement_ids
    ]
    remaining_bank_lines = [
        b for b in ds.bank_lines if b.line_id not in t1_matched_bank_line_ids
    ]

    t3_matches, t3_unmatched = match_fuzzy(unresolved_settlements, remaining_bank_lines)

    # Final residue should carry the ORIGINAL, more specific T1 reason
    # (missing_settlement / amount_mismatch) rather than T3's generic
    # "no_fuzzy_match" -- a real gap caught while sanity-checking
    # evalh/to_exceptions_md before trusting its output: every settlement
    # that reaches T3 got there BECAUSE it was in t1_unmatched, so that
    # reason is always available and always more informative than "fuzzy
    # matching also failed," which is true but says nothing about WHY.
    # This matters concretely: exceptions.md is the honest exception list
    # the track explicitly asks for, and "no_fuzzy_match" alone tells a
    # human nothing they can act on.
    t1_reason_by_id = {u.id: u for u in t1_unmatched}
    residue = []
    for u in t3_unmatched:
        original = t1_reason_by_id.get(u.id)
        if original is not None:
            residue.append(
                Unmatched(
                    kind=original.kind,
                    id=original.id,
                    reason=original.reason,
                    detail={**original.detail, "fuzzy_match_also_failed": True},
                )
            )
        else:
            residue.append(u)

    return MatchResult(
        t0_groups=t0_groups,
        t1_matches=t1_matches,
        t1_unmatched=t1_unmatched,
        t2_matches=t2_matches,
        t2_unmatched=t2_unmatched,
        t3_matches=t3_matches,
        residue=residue,
    )
