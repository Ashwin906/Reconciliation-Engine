"""
Command-line entry point producing the first honest numbers -- Phase 6's
exit criterion from BUILD_PLAN.md: one command, a metrics table, an
exceptions list.

Usage:
    python -m evalh.cli --out eval_results --seed 42

DEVIATION FROM BUILD_PLAN.md, noted rather than silently made: the
original plan specified `--scenario configs/eval_v1.yaml`, a YAML config
file. This CLI instead defines its evaluation scenario directly in code
(build_eval_scenario() below) and takes only --seed and --out. Reason:
a YAML-driven config adds a new dependency (PyYAML) and a file format to
validate for a benefit -- swappable scenarios -- that isn't needed yet,
since there is currently exactly one evaluation scenario. Functionally
equivalent for what Phase 6 actually needs; revisit if/when multiple
named scenarios are actually required.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from evalh.pipeline import run_full_pipeline
from evalh.score import score, to_exceptions_md, to_markdown
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec


def build_eval_scenario(seed: int):
    """One representative scenario + defect plan, covering every defect
    kind this phase's pipeline can meaningfully be scored against. Kept
    deliberately small (5 days, ~15-25 orders/day) so the metrics report
    stays readable rather than needing a spreadsheet to interpret."""
    spec = default_spec(seed=seed)
    plan = [
        DefectSpec("missing_settlement", {"n": 1}),
        DefectSpec("short_credit", {"n": 1}),
        DefectSpec("unbooked_chargeback", {"n": 1}),
        DefectSpec("utr_change", {"n": 1}),
        DefectSpec("narration_truncation", {"n": 1}),
        DefectSpec("reserve_hold", {"n": 1}),
        DefectSpec("negative_settlement", {"n": 1}),
        DefectSpec("duplicate_order_id", {"n": 1}),
        DefectSpec("cross_period_refund", {"n": 1}),
        DefectSpec("offline_payment", {"n": 1}),
        # fee_overcharge deliberately excluded from the default scenario:
        # with days=30 against a 5-day dataset it touches most of one
        # payment method and dominates the defect count (46 defects were
        # observed from one call during manual testing), swamping every
        # other signal in the report. A reader wanting to see it can add
        # DefectSpec("fee_overcharge", {"method": "card", "extra_bps": 36,
        # "days": 1}) -- a 1-day window keeps it proportionate.
    ]
    return spec, plan


def run_eval(seed: int, out_dir: Path) -> None:
    spec, plan = build_eval_scenario(seed)
    ds = generate_clean(spec)
    ds, truth = apply_defects(ds, plan, seed=seed)

    run = run_full_pipeline(ds)
    metrics = score(run, truth)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.md").write_text(to_markdown(metrics), encoding="utf-8")
    (out_dir / "exceptions.md").write_text(to_exceptions_md(run), encoding="utf-8")

    print(to_markdown(metrics))
    print()
    print(f"Written: {out_dir / 'metrics.md'}")
    print(f"Written: {out_dir / 'exceptions.md'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the reconciliation eval harness.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("eval_results"))
    args = parser.parse_args()
    run_eval(args.seed, args.out)


if __name__ == "__main__":
    main()
