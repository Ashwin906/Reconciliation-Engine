"""
run_full_pipeline: wires match/ -> verify/ -> invariant/ together into
one RunResult, for the eval harness to score.

Not named in BUILD_PLAN.md's original Phase 6 file list (only score.py
and cli.py were) -- added because the harness needs an actual run to
measure, and pipeline/run.py (the REAL orchestrator, Phase 11) doesn't
exist yet. This is deliberately a thin, eval-scoped stand-in, not a
preview of Phase 11's full pipeline: no ledger writes, no gate, no
webhooks, no agent investigation of residue. It runs match -> verify ->
conservation and stops -- exactly what Phase 6 needs to produce a
scoreable result, no more.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.proof import Proof
from gen.models import Dataset
from invariant.conservation import ConservationResult, check_conservation
from match.run import MatchContext, MatchResult, run_matching
from verify.recompute import VerifyReport, verify_all


@dataclass
class RunResult:
    dataset: Dataset
    match_result: MatchResult
    proofs: list[Proof]           # only T2 proofs -- see module docstring on why
    verify_report: VerifyReport
    conservation: ConservationResult
    duration_s: float


def run_full_pipeline(ds: Dataset) -> RunResult:
    """T2 is the only tier whose MatchCandidate carries a Proof object
    (match/t1_utr.py and match/t3_fuzzy.py's candidates have proof=None
    -- exact-id and fuzzy-score matches don't build a step-by-step
    arithmetic claim the way T2's settlement-arithmetic check does). So
    "proofs" verified here means T2's proofs specifically; T1/T3 matches
    are certain-by-construction (exact lookup) or confidence-scored
    (fuzzy), not independently re-derivable in the same sense. This is
    stated explicitly rather than left as an implicit assumption, because
    it directly shapes what proofs_emitted/verified/rejected mean in the
    Metrics this run feeds into (evalh/score.py).
    """
    start = time.perf_counter()

    match_result = run_matching(MatchContext(dataset=ds))
    proofs = [m.proof for m in match_result.t2_matches if m.proof is not None]
    verify_report = verify_all(proofs, ds)
    conservation = check_conservation(ds)

    duration_s = time.perf_counter() - start

    return RunResult(
        dataset=ds,
        match_result=match_result,
        proofs=proofs,
        verify_report=verify_report,
        conservation=conservation,
        duration_s=duration_s,
    )
