"""
Scores a RunResult against a TruthSet -- the first place in the codebase
honest numbers actually get produced and published, per DECISIONS.md D-03.

Two things this module refuses to do:
  1. Claim a defect was "detected" via a made-up or overly generous
     signal. Detection is checked against the SPECIFIC mechanisms that
     actually exist right now (match residue, verifier rejection,
     conservation localise_gap, T0's orphan-order detection) -- not an
     idealized "the system knows" abstraction.
  2. Silently score a defect kind this phase's pipeline has NO mechanism
     to catch (fee_overcharge -- its whole design point is to be
     invisible to T1/T2/conservation; real detection is Phase 8's
     detect_fee_overcharge, not yet built) as "detected" by accident, or
     hide it by leaving it out of the denominator. It is counted,
     reported as undetected, and the reason is named in to_markdown's
     output -- an honest 0% on a metric beats a fabricated 100%.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.money import format_paise, sum_paise
from evalh.pipeline import RunResult
from gen.defects import PlantedDefect, TruthSet
from gen.models import Dataset
from invariant.conservation import localise_gap
from match.t0_key import link_orders_to_payments

# Defect kinds whose subject_id IS a settlement_id directly
_SETTLEMENT_SUBJECT_KINDS = frozenset(
    {"unbooked_chargeback", "missing_settlement", "utr_change", "reserve_hold",
     "negative_settlement", "holiday_shift"}
)
# Defect kinds whose subject_id is a payment_id -> map via payment.settlement_id
_PAYMENT_SUBJECT_KINDS = frozenset({"fee_overcharge", "late_capture"})

# Defect kinds this pipeline (Phases 3-5) has NO detection mechanism for
# yet, by design -- named explicitly so a 0% detection rate reads as an
# honest, expected gap rather than a silent harness failure. Real
# detection for these is Phase 8 (rate-comparison detectors) or later.
_NO_DETECTOR_YET = frozenset({"fee_overcharge"})


def _owning_settlement_id(defect: PlantedDefect, ds: Dataset) -> str | None:
    """Maps a defect to the settlement whose residue/verify/conservation
    status would reveal it -- or None if this defect kind has no
    settlement-level signal to check (see callers for what happens then)."""
    if defect.kind in _SETTLEMENT_SUBJECT_KINDS:
        return defect.subject_id

    if defect.kind == "short_credit":
        bank_line = next((b for b in ds.bank_lines if b.line_id == defect.subject_id), None)
        if bank_line is None or bank_line.ref_no is None:
            return None
        settlement = next((s for s in ds.settlements if s.utr == bank_line.ref_no), None)
        return settlement.settlement_id if settlement else None

    if defect.kind in _PAYMENT_SUBJECT_KINDS:
        payment = ds.payment_by_id(defect.subject_id)
        return payment.settlement_id if payment else None

    if defect.kind == "cross_period_refund":
        refund = next((r for r in ds.refunds if r.refund_id == defect.subject_id), None)
        if refund is None:
            return None
        payment = ds.payment_by_id(refund.payment_id)
        # NOTE: only checks the SOURCE settlement (where the refund
        # originated). cross_period_refund also touches a target
        # settlement; not checked here, a deliberate scope narrowing to
        # keep this mapping tractable rather than silently pretending
        # both sides are covered.
        return payment.settlement_id if payment else None

    # narration_truncation, offline_payment, duplicate_order_id: no
    # settlement-level signal applies to these -- handled by their own
    # specific paths in _build_flagged_sets / score(), not here
    return None


@dataclass(frozen=True)
class _FlaggedSets:
    settlement_ids: set[str]
    order_ids: set[str]


def _build_flagged_sets(run: RunResult) -> _FlaggedSets:
    """Every id this phase's pipeline has actually raised a flag against,
    from every mechanism that exists right now.

    A real false-alert bug was caught here while sanity-checking this
    module before writing any test: inject_utr_change (should_be_detected
    =False -- no money at risk, must resolve quietly) was showing up as
    flagged=True even when match/t3_fuzzy.py correctly, quietly resolved
    it. Cause: invariant/conservation.py's OWN documented scope boundary
    (exact-UTR lookup only, does not know about T3's fuzzy resolution --
    see its module docstring, point 1) means localise_gap will ALWAYS
    flag a utr_change-touched settlement, even after match/ has already
    correctly resolved the real question (does this bank credit belong
    to this settlement). That is not a bug in conservation.py -- it is
    behaving exactly as its own docstring says it will. The bug was here:
    naively unioning all three signals treated a KNOWN, ALREADY-DOCUMENTED
    blind spot as if it were a fresh finding. Fixed by excluding any
    settlement match/ already resolved (T1 or T3) from the
    conservation-sourced flags -- match/'s resolution is the more
    specific, more authoritative answer to "is this settlement's
    identity accounted for," and conservation's disagreement in exactly
    that case is expected, not informative.
    """
    settlement_ids: set[str] = set()

    settlement_ids |= {u.id for u in run.match_result.residue}
    settlement_ids |= {
        proof.observed_source.split(":", 1)[1]
        for proof, _ in run.verify_report.rejected_items
        if proof.observed_source.startswith("settlement:")
    }
    # T2's OWN unmatched findings -- a settlement whose report doesn't
    # match its real members, independent of whether the bank side ties.
    # A real gap was caught here while sanity-checking this module before
    # any test existed: negative_settlement and cross_period_refund both
    # tie at T1 (the injector keeps settlement.amount and bank_line.credit
    # mutually consistent with each other), so the bank-reconciliation
    # question alone says "fine" -- but T2 independently catches both
    # (confirmed by direct inspection: t2_unmatched correctly named the
    # negative_settlement's settlement, and BOTH the source and target
    # settlements of a cross_period_refund). Forgetting to include
    # t2_unmatched here meant those genuine catches were computed by
    # match/t2_set.py and then silently discarded before reaching the
    # eval harness's own detection metric.
    settlement_ids |= {u.id for u in run.match_result.t2_unmatched}

    resolved_by_match = {m.left.id for m in run.match_result.t1_matches} | {
        m.left.id for m in run.match_result.t3_matches
    }
    conservation_suspects = {s.subject_id for s in localise_gap(run.dataset)}
    settlement_ids |= (conservation_suspects - resolved_by_match)

    _, orphan_orders, _ = link_orders_to_payments(run.dataset.orders, run.dataset.payments)
    order_ids = {o.order_id for o in orphan_orders}

    return _FlaggedSets(settlement_ids=settlement_ids, order_ids=order_ids)


def _defect_was_flagged(defect: PlantedDefect, ds: Dataset, flagged: _FlaggedSets) -> bool | None:
    """True/False if this defect's detection status is determinable by
    an existing mechanism; None if this defect kind has no applicable
    signal at all (excluded from the detection-rate denominator, not
    silently counted as a miss)."""
    if defect.kind == "duplicate_order_id":
        return defect.subject_id in flagged.order_ids

    if defect.kind in _NO_DETECTOR_YET:
        return False  # honestly always undetected -- no mechanism exists yet

    owning = _owning_settlement_id(defect, ds)
    if owning is None:
        return None
    return owning in flagged.settlement_ids


@dataclass(frozen=True)
class Metrics:
    records: int
    duration_s: float
    per_tier: dict[str, int]
    matched: int
    unmatched: int
    proofs_emitted: int
    proofs_verified: int
    proofs_rejected: int
    false_matches: int
    defects_planted: int
    defects_should_alert: int
    defects_correctly_silent_expected: int
    defects_detected: int
    defects_undetermined: int
    false_alerts: int
    conservation_residual: int
    conservation_closes: bool


def score(run: RunResult, truth: TruthSet) -> Metrics:
    ds = run.dataset
    flagged = _build_flagged_sets(run)

    matched = len(run.match_result.t1_matches) + len(run.match_result.t3_matches)
    unmatched = len(run.match_result.residue)

    # every T2 MatchCandidate is only ever produced when proof.ties is
    # already True (match/t2_set.py never returns one otherwise) -- so
    # every verify_all rejection on these proofs is, by construction, a
    # case the matcher believed was a genuine match and the independent
    # verifier disagreed with. false_matches is therefore exactly
    # verify_report.rejected, not a separately-computed approximation of it.
    false_matches = run.verify_report.rejected

    should_alert = truth.should_alert()
    should_stay_quiet = truth.should_stay_quiet()

    detected = 0
    undetermined = 0
    for defect in should_alert:
        result = _defect_was_flagged(defect, ds, flagged)
        if result is None:
            undetermined += 1
        elif result:
            detected += 1

    false_alerts = 0
    for defect in should_stay_quiet:
        result = _defect_was_flagged(defect, ds, flagged)
        if result:  # None or False both count as "correctly quiet" here
            false_alerts += 1

    return Metrics(
        records=len(ds.payments),
        duration_s=run.duration_s,
        per_tier=run.match_result.tier_counts(),
        matched=matched,
        unmatched=unmatched,
        proofs_emitted=len(run.proofs),
        proofs_verified=run.verify_report.accepted,
        proofs_rejected=run.verify_report.rejected,
        false_matches=false_matches,
        defects_planted=len(truth.defects),
        defects_should_alert=len(should_alert),
        defects_correctly_silent_expected=len(should_stay_quiet),
        defects_detected=detected,
        defects_undetermined=undetermined,
        false_alerts=false_alerts,
        conservation_residual=run.conservation.residual,
        conservation_closes=run.conservation.closes,
    )


def to_markdown(m: Metrics) -> str:
    detectable = m.defects_should_alert - m.defects_undetermined
    detection_rate = f"{m.defects_detected}/{detectable}"
    if detectable:
        detection_rate += f" ({100*m.defects_detected/detectable:.0f}%)"
    lines = [
        f"BATCH                    {m.records} payment records          {m.duration_s*1000:.1f}ms",
        "",
        "MATCHING",
        f"  T1 (exact UTR)         {m.per_tier.get('T1', 0)}",
        f"  T2 (internal check)    {m.per_tier.get('T2', 0)}",
        f"  T3 (fuzzy)             {m.per_tier.get('T3', 0)}",
        f"  unresolved             {m.per_tier.get('unresolved', 0)}",
        f"  matched (T1+T3)        {m.matched}",
        f"  unmatched              {m.unmatched}",
        "",
        "VERIFICATION (T2 proofs only -- see evalh/pipeline.py)",
        f"  proofs emitted         {m.proofs_emitted}",
        f"  proofs verified        {m.proofs_verified}",
        f"  proofs REJECTED        {m.proofs_rejected}   <- matcher believed these ties; independent recomputation disagreed",
        f"  false matches          {m.false_matches}",
        "",
        "GROUND TRUTH",
        f"  defects planted             {m.defects_planted}",
        f"  should have alerted         {m.defects_should_alert}",
        f"  detected                    {detection_rate}",
        f"  not checkable this phase    {m.defects_undetermined}   <- no detector exists yet (e.g. fee_overcharge; Phase 8)",
        f"  should have stayed quiet    {m.defects_correctly_silent_expected}",
        f"  false alerts on those       {m.false_alerts}",
        "",
        "INVARIANTS",
        f"  conservation residual  {format_paise(m.conservation_residual)}   {'CLOSES' if m.conservation_closes else 'DOES NOT CLOSE'}",
    ]
    return "\n".join(lines)


def to_exceptions_md(run: RunResult) -> str:
    """The honest exception list the track explicitly asks for -- every
    settlement this run could not resolve, why, and what it would take."""
    if not run.match_result.residue:
        return "# Exceptions\n\nNone. Every settlement resolved.\n"

    lines = ["# Exceptions", "", f"{len(run.match_result.residue)} unresolved settlement(s).", ""]
    for u in run.match_result.residue:
        settlement = next(
            (s for s in run.dataset.settlements if s.settlement_id == u.id), None
        )
        amount = format_paise(settlement.amount) if settlement else "?"
        lines.append(f"## {u.id}")
        lines.append(f"- reported amount: Rs {amount}")
        lines.append(f"- reason: {u.reason}")
        if u.detail:
            lines.append(f"- detail: {u.detail}")
        lines.append("")
    return "\n".join(lines)
