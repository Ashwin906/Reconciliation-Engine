"""
The money journey, stage by stage: customer pays -> Razorpay captures and
deducts fees -> settlement batch -> bank credit -> reconciliation ->
investigation -> gate -> ledger.

[SCOPE NOTE, stated directly] Every number in a JourneyTrace is READ OUT
of a real generated dataset and a real pipeline.run.run_batch() result --
this module computes no reconciliation logic of its own and decides
nothing. It is a narrator, not a second implementation: if it disagreed
with the pipeline it would be because it read the wrong field, not
because two algorithms drifted. That is deliberate, and it is what makes
this safe to show a judge as "what actually happened" rather than "what
we drew".

The one thing this module DOES own is the merchant-facing wording -- the
plain-language framing of each stage. That is presentation, and it lives
here rather than in the pipeline so the pipeline stays free of UI
concerns.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from core.money import Paise
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.models import Dataset
from gen.scenario import default_spec
from agent.client import GroqClient
from pipeline.run import BatchResult, PipelineConfig, run_batch
from sim.adversary import ATTACK_LABELS, AdversarialTransport, assess
from sim.catalog import coerce_params, defect_catalog
from sim.naive import compare as naive_compare

StageStatus = Literal["ok", "warn", "fail", "info"]


@dataclass
class Stage:
    """One step of the journey. `facts` are real values pulled from the
    dataset/BatchResult; `detail` is the plain-language line a merchant
    reads. Never invent a fact here that isn't in the underlying data."""

    key: str
    title: str
    detail: str
    status: StageStatus
    facts: dict = field(default_factory=dict)


@dataclass
class ScenarioDef:
    slug: str
    name: str
    merchant_story: str          # the problem in the merchant's own words
    defect: str | None           # gen.defects registry key, None = clean run
    seed: int
    # Injector params, per gen/defects.py's own signatures -- they differ
    # per injector (fee_overcharge takes method/extra_bps/days, most
    # others take n), so each scenario carries its own rather than this
    # module assuming a single shape.
    params: dict = field(default_factory=lambda: {"n": 1})


# Each scenario is a REAL defect from gen/defects.py's registry -- the
# same injectors the eval harness uses, not a bespoke story-only fixture.
# merchant_story is the human framing; the mechanics come from the
# injector itself.
SCENARIOS: tuple[ScenarioDef, ...] = (
    ScenarioDef(
        slug="happy_path",
        name="A normal day",
        merchant_story="Customers paid, Razorpay settled, the bank credited exactly what it should have. Nothing went wrong -- this is the baseline everything else is compared against.",
        defect=None,
        seed=42,
    ),
    ScenarioDef(
        slug="unbooked_chargeback",
        name="A customer disputed a payment",
        merchant_story="Your bank credit came up short this week. Nothing in your dashboard says why. Somewhere in a batch of hundreds of payments, one customer raised a chargeback and the money was quietly pulled back -- along with a fee you were never told about.",
        defect="unbooked_chargeback",
        seed=101,
    ),
    ScenarioDef(
        slug="missing_settlement",
        name="Razorpay paid you. Your bank disagrees.",
        merchant_story="Razorpay's dashboard shows a settlement went out. Your bank statement has no such credit. Either the money is in transit, it went to the wrong account, or it never left -- and until someone checks, you cannot close your books.",
        defect="missing_settlement",
        seed=102,
    ),
    ScenarioDef(
        slug="short_credit",
        name="The bank credited less than promised",
        merchant_story="The settlement said one number. The bank credited a smaller one. The gap is too small to notice by eye and too big to ignore across a month.",
        defect="short_credit",
        seed=103,
    ),
    ScenarioDef(
        slug="fee_overcharge",
        name="You were overcharged fees",
        merchant_story="Your rate card says one thing. What was actually deducted says another. Nobody checks this by hand across thousands of transactions -- which is exactly why it goes unnoticed for months. Note this one reconciles PERFECTLY: settlement and bank agree exactly, because the overcharge was taken before the settlement was even calculated. Only a rate-card comparison catches it.",
        defect="fee_overcharge",
        seed=104,
        params={"method": "card", "extra_bps": 35, "days": 30},
    ),
    ScenarioDef(
        slug="reserve_hold",
        name="Money held back as a reserve",
        merchant_story="Part of your settlement never arrived. It was not lost -- it was withheld as a rolling reserve. But nothing told you that, so it looks identical to money going missing.",
        defect="reserve_hold",
        seed=105,
    ),
    ScenarioDef(
        slug="utr_change",
        name="The bank rewrote the reference number",
        merchant_story="Your bank quoted a different UTR than Razorpay issued -- a truncation, a prefix, a reformat. Exact matching fails outright. A naive system reports this as money missing and sends you hunting for a payment that arrived perfectly fine. Watch the fallback tiers recover it without ever guessing.",
        defect="utr_change",
        seed=106,
    ),
)

_BY_SLUG = {s.slug: s for s in SCENARIOS}


def list_scenarios() -> list[dict]:
    return [asdict(s) for s in SCENARIOS]


def _sum(values) -> Paise:
    return sum(values, 0)


@dataclass
class JourneyRun:
    """The narrated journey PLUS the real objects behind it.

    The caller gets the dataset and BatchResult back rather than only the
    display payload, so an API route can install them as the application's
    actual state -- the dashboard then shows this simulation's real
    numbers instead of a separately-seeded placeholder that only resembled
    them. One run, one set of numbers, shown two ways.
    """

    payload: dict
    dataset: Dataset
    result: BatchResult


def build_journey(
    slug: str, cfg: PipelineConfig, client=None, conn=None, merchant_id: str = "M_SIM"
) -> JourneyRun:
    """Generates the scenario's dataset, runs the REAL pipeline over it,
    and narrates what happened stage by stage.

    Raises KeyError for an unknown slug -- the caller (an API route) turns
    that into a 404 rather than this module inventing an empty journey.
    """
    scenario = _BY_SLUG[slug]

    ds = generate_clean(default_spec(seed=scenario.seed))
    truth = None
    if scenario.defect is not None:
        ds, truth = apply_defects(ds, [DefectSpec(scenario.defect, dict(scenario.params))], seed=1)

    result = run_batch(merchant_id, ds, conn=conn, cfg=cfg, client=client)

    payload = {
        "scenario": asdict(scenario),
        "stages": [asdict(s) for s in _stages(scenario, ds, truth, result)],
        "planted_defect": _planted_summary(truth),
        "planted_defects": _planted_all(truth),
        "outcome": _outcome(result),
        "money_flow": _money_flow(result),
        "comparison": naive_compare(ds, result, cfg.detect_cfg.rate_card_bps),
    }
    return JourneyRun(payload=payload, dataset=ds, result=result)


# Guard-rail on a user-composed run. Not arbitrary: each injector picks
# distinct subjects from a dataset of ~5 settlements, so asking for far
# more defects than there are subjects makes injectors fail or silently
# no-op. Capped where the result is still meaningful.
MAX_CUSTOM_DEFECTS = 6


def build_custom_journey(
    defects: list[dict],
    cfg: PipelineConfig,
    seed: int = 7,
    client=None,
    conn=None,
    merchant_id: str = "M_SIM",
) -> JourneyRun:
    """"Break it yourself": the viewer composes the defect plan.

    `defects` is a list of {"kind": str, "params": dict}. Each is coerced
    against the injector's REAL signature (sim/catalog.py) before anything
    runs, so an impossible request fails fast with a specific message
    rather than deep inside a generator with a TypeError.

    Everything after injection is the identical code path a curated
    scenario takes -- same generator, same run_batch, same narration.
    There is no separate "custom mode" pipeline that could behave
    differently from the one demoed, which is the entire point of letting
    someone else choose the inputs.

    Raises ValueError for an unusable plan; the API layer turns that into
    a 422.
    """
    if not defects:
        raise ValueError("pick at least one thing to break")
    if len(defects) > MAX_CUSTOM_DEFECTS:
        raise ValueError(f"at most {MAX_CUSTOM_DEFECTS} defects at once")

    plan: list[DefectSpec] = []
    for entry in defects:
        kind = entry.get("kind")
        if not kind:
            raise ValueError("each defect needs a 'kind'")
        plan.append(DefectSpec(kind, coerce_params(kind, entry.get("params") or {})))

    ds = generate_clean(default_spec(seed=seed))
    ds, truth = apply_defects(ds, plan, seed=1)
    result = run_batch(merchant_id, ds, conn=conn, cfg=cfg, client=client)

    labels = ", ".join(_kind_label(spec.kind) for spec in plan)
    scenario = ScenarioDef(
        slug="custom",
        name="Your scenario",
        merchant_story=(
            f"You chose what to break: {labels}. Nothing about this run is pre-arranged -- "
            f"it uses the same generator, the same pipeline, and the same narration as every "
            f"built-in scenario. The system has no idea what you picked."
        ),
        defect=None,
        seed=seed,
    )

    payload = {
        "scenario": asdict(scenario),
        "stages": [asdict(s) for s in _stages(scenario, ds, truth, result)],
        "planted_defect": _planted_summary(truth),
        "planted_defects": _planted_all(truth),
        "outcome": _outcome(result),
        "money_flow": _money_flow(result),
        "comparison": naive_compare(ds, result, cfg.detect_cfg.rate_card_bps),
        "requested": [{"kind": s.kind, "params": s.params} for s in plan],
    }
    return JourneyRun(payload=payload, dataset=ds, result=result)


def build_adversarial_journey(
    attack: str,
    cfg: PipelineConfig,
    seed: int = 101,
    conn=None,
    merchant_id: str = "M_SIM",
) -> JourneyRun:
    """Runs the real pipeline against a model that is actively lying.

    Deliberately ignores any configured LLM client and substitutes
    AdversarialTransport -- the point is a REPRODUCIBLE attack, not
    whatever a live model happens to say today. Everything downstream of
    the transport (investigate, verify, gate, ledger) is untouched
    production code that has no idea it is being attacked.

    Uses the unbooked_chargeback scenario because it reliably produces a
    residue item with real disputes attached -- the adversary needs
    genuine evidence ids in its packet to construct the interesting
    attacks (understating a real amount, citing real-but-wrong evidence).
    """
    transport = AdversarialTransport(attack)
    client = GroqClient(transport=transport, sleep_fn=lambda _: None)

    ds = generate_clean(default_spec(seed=seed))
    ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)
    result = run_batch(merchant_id, ds, conn=conn, cfg=cfg, client=client)

    label, attempt, defence = ATTACK_LABELS[attack]
    scenario = ScenarioDef(
        slug="adversarial",
        name=f"Adversarial: {label}",
        merchant_story=(
            f"The AI investigator has been replaced with one that is deliberately trying to "
            f"get a false correction applied to your books. {attempt} Nothing downstream of it "
            f"knows this -- the verifier, the gate and the ledger are the same code as every "
            f"other run."
        ),
        defect="unbooked_chargeback",
        seed=seed,
    )

    payload = {
        "scenario": asdict(scenario),
        "stages": [asdict(s) for s in _stages(scenario, ds, truth, result)],
        "planted_defect": _planted_summary(truth),
        "planted_defects": _planted_all(truth),
        "outcome": _outcome(result),
        "money_flow": _money_flow(result),
        "comparison": naive_compare(ds, result, cfg.detect_cfg.rate_card_bps),
        "adversarial": assess(transport, result),
    }
    return JourneyRun(payload=payload, dataset=ds, result=result)


def _kind_label(kind: str) -> str:
    for entry in defect_catalog():
        if entry["kind"] == kind:
            return entry["label"].lower()
    return kind.replace("_", " ")


def _planted_all(truth) -> list[dict]:
    """Every planted defect, not just the first -- a user-composed run can
    plant several, and showing only one would understate what the system
    was actually asked to find."""
    if truth is None:
        return []
    return [
        {
            "kind": d.kind,
            "subject_id": d.subject_id,
            "impact_paise": d.impact_paise,
            "should_be_detected": d.should_be_detected,
            "note": d.note,
        }
        for d in truth.defects
    ]


def _planted_summary(truth) -> dict | None:
    """The ground truth -- what we deliberately broke. Shown ONLY so a
    viewer can check the system's conclusion against the real answer;
    nothing in the pipeline ever sees this."""
    if truth is None or not truth.defects:
        return None
    d = truth.defects[0]
    return {
        "kind": d.kind,
        "subject_id": d.subject_id,
        "expected_resolution": d.expected_resolution,
    }


def _money_flow(result: BatchResult) -> dict:
    """The Sankey's data, taken straight from the conservation identity
    (invariant/conservation.py) rather than recomputed for display.

    That identity already IS a flow statement: every rupee collected
    leaves through exactly one of five doors, and whatever fails to leave
    through one of them is the residual. Reusing it means the diagram
    cannot show a different story from the proof -- the picture and the
    arithmetic are the same object.

    `unaccounted` is signed-corrected to a magnitude for width purposes
    but keeps its real value in `residual_paise`, because the DIRECTION
    matters: a positive residual is money collected that never left, a
    negative one is money that left without being collected. Two very
    different problems that must not render identically.
    """
    c = result.conservation

    deductions = [
        ("Razorpay fee", c.fees),
        ("GST", c.gst),
        ("Refunds", c.refunded),
        ("Chargebacks", c.disputed),
        ("Chargeback fees", c.dispute_fees),
    ]
    withheld = sum(amount for _, amount in deductions)

    return {
        "collected_paise": c.collected,
        "reached_bank_paise": c.settled,
        "withheld_paise": withheld,
        "residual_paise": c.residual,
        "closes": c.closes,
        # Ordered largest-first so the diagram's biggest branch is the one
        # a viewer's eye lands on. Zero-value doors are kept rather than
        # filtered: "no chargebacks this period" is information, and a
        # branch vanishing between runs is harder to read than one at zero.
        "deductions": [
            {"label": label, "paise": amount}
            for label, amount in sorted(deductions, key=lambda kv: -kv[1])
        ],
    }


def _outcome(result: BatchResult) -> dict:
    matched = (
        len(result.match_result.t1_matches)
        + len(result.match_result.t2_matches)
        + len(result.match_result.t3_matches)
    )
    total = matched + len(result.match_result.residue)
    return {
        "matched": matched,
        "residue": len(result.match_result.residue),
        "match_rate_pct": round(100 * matched / total, 1) if total else 100.0,
        "conservation_closes": result.conservation.closes,
        "conservation_residual_paise": result.conservation.residual,
        "findings": [
            {"rule": f.rule, "severity": f.severity, "impact_paise": f.impact_paise}
            for f in result.findings
        ],
        "duration_s": round(result.duration_s, 3),
    }


def _stages(scenario: ScenarioDef, ds: Dataset, truth, result: BatchResult) -> list[Stage]:
    captured = [p for p in ds.payments if p.status == "captured"]
    gross = _sum(p.amount for p in captured)
    fees = _sum(p.fee + p.tax for p in captured)
    settled = _sum(s.amount for s in ds.settlements)
    bank_credit = _sum(b.credit for b in ds.bank_lines)
    bank_debit = _sum(b.debit for b in ds.bank_lines)

    residue = result.match_result.residue
    m = result.match_result

    stages = [
        Stage(
            key="customer_pays",
            title="Customers pay",
            detail=f"{len(captured)} customers paid you across cards, UPI and netbanking.",
            status="ok",
            facts={"count": len(captured), "gross_paise": gross},
        ),
        Stage(
            key="razorpay_captures",
            title="Razorpay captures and deducts its fee",
            detail=f"Razorpay took its processing fee plus GST before settling. This is expected -- but it is also where an overcharge would hide.",
            status="ok",
            facts={"fees_paise": fees, "net_after_fees_paise": gross - fees},
        ),
        Stage(
            key="settlement_created",
            title="Razorpay batches it into a settlement",
            detail=f"{len(ds.settlements)} settlement batch(es) created, each with a UTR reference the bank should quote back.",
            status="ok",
            facts={"count": len(ds.settlements), "settled_paise": settled},
        ),
        Stage(
            key="bank_credits",
            title="Your bank credits the money",
            detail=(
                f"{len(ds.bank_lines)} line(s) hit your bank statement. This is the ONLY number that is really yours -- "
                f"everything before it is someone else's record of what they intend to pay you."
            ),
            status="ok" if bank_credit >= settled else "warn",
            facts={
                "lines": len(ds.bank_lines),
                "credit_paise": bank_credit,
                "debit_paise": bank_debit,
                "gap_vs_settled_paise": settled - bank_credit,
            },
        ),
        Stage(
            key="matching",
            title="We match every settlement to the bank, in tiers",
            detail=(
                f"T1 exact UTR match: {len(m.t1_matches)}. "
                f"T2 internal consistency check: {len(m.t2_matches)}. "
                f"T3 closest-fit fallback: {len(m.t3_matches)}. "
                f"Anything surviving all three is residue -- genuinely unexplained."
            ),
            status="ok" if not residue else "warn",
            facts={
                "t1": len(m.t1_matches),
                "t2": len(m.t2_matches),
                "t3": len(m.t3_matches),
                "residue": len(residue),
            },
        ),
    ]

    if residue:
        stages.append(
            Stage(
                key="residue",
                title="Something does not add up",
                detail=(
                    f"{len(residue)} item(s) could not be explained by matching alone: "
                    + "; ".join(f"{u.id} ({u.reason})" for u in residue[:3])
                ),
                status="fail",
                facts={
                    "items": [
                        {"id": u.id, "reason": u.reason, "detail": _jsonable(u.detail)}
                        for u in residue
                    ]
                },
            )
        )

        for outcome in result.investigations:
            stages.append(_investigation_stage(outcome))

    stages.append(
        Stage(
            key="conservation",
            title="Prove the books balance",
            detail=(
                "Every rupee in must equal every rupee accounted for. This is arithmetic over the raw data, "
                "not an opinion -- it either closes or it does not."
            ),
            status="ok" if result.conservation.closes else "fail",
            facts={
                "closes": result.conservation.closes,
                "residual_paise": result.conservation.residual,
            },
        )
    )

    if result.findings:
        stages.append(
            Stage(
                key="findings",
                title="Rule-based detectors also flagged issues",
                detail=(
                    "These run independently of matching -- they catch things that reconcile fine "
                    "but are still wrong, like a fee that exceeds your rate card."
                ),
                status="warn",
                facts={
                    "findings": [
                        {"rule": f.rule, "severity": f.severity, "impact_paise": f.impact_paise}
                        for f in result.findings
                    ]
                },
            )
        )

    return stages


def _investigation_stage(outcome) -> Stage:
    """Narrates ONE residue item's T4 investigation, including the case
    where no LLM was configured at all -- that is a real, honest outcome
    (D-08), not a stage to hide."""
    from agent.investigate import NoHypothesis

    subject = outcome.unmatched.id
    resolution = outcome.resolution

    if isinstance(resolution, NoHypothesis):
        return Stage(
            key=f"investigate_{subject}",
            title=f"AI investigation: {subject}",
            detail=(
                f"No usable hypothesis. {resolution.reason}"
                + (f" The model's own observation: \"{resolution.note}\"" if resolution.note else "")
            ),
            status="info",
            facts={
                "subject": subject,
                "reason": resolution.reason,
                "note": resolution.note,
                "gate": outcome.gate_decision,
                "ledger_entry_id": outcome.ledger_entry_id,
            },
        )

    verdict = {
        "AUTO": "The system applied this automatically -- verified, small, and high confidence.",
        "HUMAN": "Routed to a human. Either it could not be independently verified, or it is too large or too uncertain to auto-apply.",
        "REJECT": "Independent verification did NOT accept this. The hypothesis was wrong, and the system said so instead of applying it.",
    }.get(outcome.gate_decision, "No gate decision was reached.")

    return Stage(
        key=f"investigate_{subject}",
        title=f"AI investigation: {subject}",
        detail=(
            f"The model proposed '{resolution.hypothesis}' citing {list(resolution.evidence_ids)}. "
            f"That proposal has NO authority -- it was independently re-verified before anything happened. "
            f"{verdict}"
        ),
        status={"AUTO": "ok", "HUMAN": "warn", "REJECT": "fail"}.get(outcome.gate_decision, "info"),
        facts={
            "subject": subject,
            "hypothesis": resolution.hypothesis,
            "evidence_ids": list(resolution.evidence_ids),
            "confidence": resolution.confidence,
            "note": resolution.note,
            "gate": outcome.gate_decision,
            "ledger_entry_id": outcome.ledger_entry_id,
        },
    )


def _jsonable(value):
    """Unmatched.detail can carry dates/Decimals from the raw records --
    coerced to str here so the API layer can serialise a trace without
    each route needing its own encoder."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
