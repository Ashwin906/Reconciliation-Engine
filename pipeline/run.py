"""
The real orchestrator: match -> verify -> invariants -> detectors ->
T4 investigation of residue -> gate -> ledger. evalh/pipeline.py's
run_full_pipeline (Phase 6) was an explicitly thin, eval-scoped stand-in
for this -- this is the real thing that stand-in was standing in for.

T4 investigation is OPTIONAL: `client=None` skips it entirely and every
residue item stays unresolved with no hypothesis attempted. This is not
a degraded mode to apologize for -- no GROQ_API_KEY is configured in
this environment, so `client=None` is this pipeline's actually-exercised
path in every test in this repository, and the whole architecture
(DECISIONS.md D-08) is built so that being unable to reach the LLM
degrades to "more honest residue," never to an unsafe fallback.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.money import Paise
from detect.finding import Finding
from detect.rules import DetectConfig, run_all as run_detectors
from gen.models import Dataset
from invariant.conservation import ConservationResult, check_conservation
from ledger.store import DBConnection, LedgerEntry, append, hash_inputs
from match.run import MatchContext, MatchResult, run_matching
from match.types import Unmatched
from verify.recompute import VerifyReport, verify_all

from agent.client import GroqClient
from agent.investigate import NoHypothesis, ProposedResolution, investigate
from agent.packet import PacketConfig
from gate.policy import GateConfig, decide, from_investigation


@dataclass(frozen=True)
class PipelineConfig:
    detect_cfg: DetectConfig
    packet_cfg: PacketConfig = field(default_factory=PacketConfig)
    gate_cfg: GateConfig = field(default_factory=GateConfig)


@dataclass
class InvestigationOutcome:
    unmatched: Unmatched
    resolution: ProposedResolution | NoHypothesis
    gate_decision: str | None       # None if resolution is NoHypothesis -- nothing to gate
    ledger_entry_id: str | None     # None if nothing was written (NoHypothesis, or REJECT)


@dataclass
class BatchResult:
    match_result: MatchResult
    verify_report: VerifyReport
    conservation: ConservationResult
    findings: list[Finding]
    investigations: list[InvestigationOutcome]
    duration_s: float


def run_batch(
    merchant_id: str,
    ds: Dataset,
    conn: DBConnection | None,
    cfg: PipelineConfig,
    client: GroqClient | None = None,
) -> BatchResult:
    """Runs the full loop over one merchant's dataset for one batch.

    conn=None skips ledger writes entirely (useful for a dry run / a
    caller that only wants findings and doesn't have a database handy);
    when conn is provided, every gated investigation outcome -- AUTO,
    HUMAN, and REJECT alike -- is appended to the ledger (DECISIONS.md
    D-16: even a rejected proposal is a decision worth a permanent
    record, not just the ones that succeeded).
    """
    start = time.perf_counter()

    match_result = run_matching(MatchContext(dataset=ds))

    t2_proofs = [m.proof for m in match_result.t2_matches if m.proof is not None]
    verify_report = verify_all(t2_proofs, ds)

    conservation = check_conservation(ds)
    findings = run_detectors(ds, cfg.detect_cfg)

    investigations: list[InvestigationOutcome] = []
    for unmatched in match_result.residue:
        outcome = _investigate_and_gate(merchant_id, unmatched, ds, conn, client, cfg)
        investigations.append(outcome)

    duration_s = time.perf_counter() - start

    return BatchResult(
        match_result=match_result,
        verify_report=verify_report,
        conservation=conservation,
        findings=findings,
        investigations=investigations,
        duration_s=duration_s,
    )


def _investigate_and_gate(
    merchant_id: str,
    unmatched: Unmatched,
    ds: Dataset,
    conn: DBConnection | None,
    client: GroqClient | None,
    cfg: PipelineConfig,
) -> InvestigationOutcome:
    if client is None:
        return InvestigationOutcome(
            unmatched=unmatched,
            resolution=NoHypothesis(unmatched=unmatched, reason="no LLM client configured for this run"),
            gate_decision=None,
            ledger_entry_id=None,
        )

    resolution = investigate(unmatched, ds, client, cfg.packet_cfg)

    if isinstance(resolution, NoHypothesis):
        return InvestigationOutcome(
            unmatched=unmatched, resolution=resolution, gate_decision=None, ledger_entry_id=None
        )

    proof = resolution.to_proof(ds)
    if proof is None:
        # A hypothesis with no independently-verifiable adjustment --
        # ProposedResolution.to_proof's own docstring promises this "is
        # still recorded (the ledger keeps it)", but until this fix that
        # promise wasn't kept: the function returned here with
        # ledger_entry_id=None and nothing was ever written. Caught live
        # in a demo run where both real residue items were exactly this
        # case (missing_settlement, amount_short_unexplained) and the
        # Activity log stayed silently empty despite the model producing
        # real, schema-valid hypotheses.
        #
        # gate.policy.decide() requires a verified bool it has no way to
        # produce here (there is no Proof to run verify_proof on) -- so
        # this is never routed through decide(). It is unconditionally
        # HUMAN: unverifiable by construction, so AUTO (which requires
        # verified=True) and REJECT (which means verification actively
        # failed) are both the wrong story for "nobody could check this
        # either way." verifier_result gets its own honest third value
        # for the same reason -- 'REJECT' would misreport that
        # verification ran and failed, when it never ran at all.
        ledger_entry_id = None
        if conn is not None:
            entry = LedgerEntry(
                merchant_id=merchant_id,
                subject=unmatched.id,
                inputs_hash=hash_inputs({"packet": _packet_summary_for_hash(unmatched, ds)}),
                tier="T4",
                hypothesis=resolution.hypothesis,
                evidence={"evidence_ids": list(resolution.evidence_ids), "note": resolution.note},
                verifier_result="UNVERIFIABLE",
                residual=0,
                gate="HUMAN",
                status="PENDING_APPROVAL",
            )
            ledger_entry_id = append(conn, entry)
        return InvestigationOutcome(
            unmatched=unmatched, resolution=resolution, gate_decision="HUMAN", ledger_entry_id=ledger_entry_id
        )

    gate_resolution = from_investigation(resolution, proof, ds)
    gate_decision = decide(gate_resolution, cfg.gate_cfg)

    ledger_entry_id = None
    if conn is not None:
        entry = LedgerEntry(
            merchant_id=merchant_id,
            subject=unmatched.id,
            inputs_hash=hash_inputs({"packet": _packet_summary_for_hash(unmatched, ds)}),
            tier="T4",
            hypothesis=resolution.hypothesis,
            evidence={"evidence_ids": list(resolution.evidence_ids), "note": resolution.note},
            verifier_result="ACCEPT" if gate_resolution.verified else "REJECT",
            residual=0 if gate_resolution.verified else gate_resolution.paise,
            gate=gate_decision,
            status="APPLIED" if gate_decision == "AUTO" else (
                "PENDING_APPROVAL" if gate_decision == "HUMAN" else "REJECTED"
            ),
        )
        ledger_entry_id = append(conn, entry)

    return InvestigationOutcome(
        unmatched=unmatched,
        resolution=resolution,
        gate_decision=gate_decision,
        ledger_entry_id=ledger_entry_id,
    )


def daily_sweep(
    merchant_datasets: dict[str, Dataset],
    conn: DBConnection | None,
    cfg: PipelineConfig,
    client: GroqClient | None = None,
) -> dict[str, BatchResult]:
    """Runs run_batch for every merchant with a loaded dataset -- the
    belt-and-braces trigger from DOMAIN_KNOWLEDGE.md section 8's trigger
    design ("webhooks get dropped, so a daily sweep also just goes and
    checks"). Not a background scheduler itself -- that's ops/cron's job
    (or a real task queue in production); this is the unit of work a
    scheduler would call once a day, exposed as a plain function so it's
    testable without needing an actual clock or queue.

    "Idempotent" here means: running this twice on an UNCHANGED dataset
    produces deterministic match/verify/conservation/detect results both
    times (already proven exhaustively since Phase 3) and never corrupts
    or duplicates the underlying data (ingest.store.upsert_payments'
    idempotency contract, Phase 2, is what actually guarantees that at
    the ingestion layer -- this function doesn't re-prove it, it relies
    on it). Each call DOES produce a fresh BatchResult and, if T4 runs,
    fresh ledger rows -- a sweep is a new decision-making pass, not a
    no-op; "idempotent" describes the DATA underneath staying correct
    under repetition, not the ledger staying empty.
    """
    return {
        merchant_id: run_batch(merchant_id, ds, conn, cfg, client)
        for merchant_id, ds in merchant_datasets.items()
    }


def _packet_summary_for_hash(unmatched: Unmatched, ds: Dataset) -> dict:
    """A stable, minimal summary of what this investigation looked at --
    used only to produce inputs_hash for the ledger, not the actual
    packet sent to the model. Kept separate and simple rather than
    reusing agent.packet.build_packet's full output, so a change to the
    model-facing packet format doesn't silently change what
    ledger.store.replay compares against."""
    members = [p.payment_id for p in ds.payments if p.settlement_id == unmatched.id]
    return {"subject": unmatched.id, "reason": unmatched.reason, "member_payment_ids": sorted(members)}
