"""
Defect injection: mutates a clean Dataset (gen/build.py) into one with
known, labeled problems. This is what turns "we matched some records" into
a measurable claim -- every injector returns the ground truth of what it
broke, so gen/evalh (Phase 6) can score the pipeline against a known
answer instead of an assumed one. See DECISIONS.md D-04.

Every injector has signature (ds, rng, **kwargs) -> list[PlantedDefect]
and MUTATES ds IN PLACE, mirroring gen.build._batch_settlements' own
mutate-in-place style. Returning the defect list separately (rather than
attaching truth directly onto the entities) keeps the ground truth in one
place, immutable once created, uninfluenced by anything the pipeline does
to the dataset afterwards.

should_be_detected=False is used for two different reasons, both folded
into one flag deliberately:
  1. genuinely-nothing-wrong timing cases (holiday_shift, late_capture)
     from D-05 -- alerting on these IS the bug.
  2. identity-only mismatches with zero money at risk (utr_change,
     narration_truncation) -- these must resolve quietly via fuzzy
     matching, not land in a human's queue. A system that pesters a human
     over a cosmetic UTR change has the same trust cost as one that misses
     a real loss: attention spent on nothing.
Both share the same operational meaning -- "a correct system stays quiet
here" -- which is why one flag covers both, tracked in FUNCTION_REFERENCE.md
against the function that established it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import timedelta

from core.calendar_ist import HOLIDAYS
from core.money import apply_rate, gst_on, sum_paise
from gen.models import BankLine, Dataset, Dispute, Order


@dataclass(frozen=True)
class PlantedDefect:
    defect_id: str
    kind: str
    subject_id: str
    impact_paise: int
    should_be_detected: bool
    expected_resolution: str
    note: str = ""


@dataclass(frozen=True)
class DefectSpec:
    kind: str
    params: dict = field(default_factory=dict)


@dataclass
class TruthSet:
    defects: list[PlantedDefect] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[PlantedDefect]:
        return [d for d in self.defects if d.kind == kind]

    def should_alert(self) -> list[PlantedDefect]:
        return [d for d in self.defects if d.should_be_detected]

    def should_stay_quiet(self) -> list[PlantedDefect]:
        return [d for d in self.defects if not d.should_be_detected]

    def total_impact(self) -> int:
        return sum_paise(d.impact_paise for d in self.defects)


_counter = 0


def _next_id(kind: str) -> str:
    global _counter
    _counter += 1
    return f"defect_{kind}_{_counter:04d}"


def _pick(pool: list, rng: random.Random, n: int) -> list:
    """Sample without replacement, capped at pool size -- asking for more
    defects than there is material to inject is a spec error, not a
    crash; callers checking len() of the return value see it honestly."""
    k = min(n, len(pool))
    return rng.sample(pool, k)


def _bank_line_for_utr(ds: Dataset, utr: str) -> BankLine | None:
    return next((b for b in ds.bank_lines if b.ref_no == utr), None)


# ---------------------------------------------------------------------------
# Real losses -- money actually leaves incorrectly, detection matters.
# ---------------------------------------------------------------------------


def inject_fee_overcharge(
    ds: Dataset, rng: random.Random, method: str, extra_bps: int, days: int
) -> list[PlantedDefect]:
    """Charges `extra_bps` above the contracted rate on every captured
    payment of `method` in the last `days` of the dataset. The extra fee
    is REAL money -- it is deducted from the settlement and the bank
    credit, exactly as it would be in reality. The defect is invisible to
    settlement<->bank tie-out; it is only visible by comparing the
    effective per-method rate to the rate card (Phase 8 detect_fee_overcharge).
    This mirrors DOMAIN_KNOWLEDGE.md section 11's leak-detection example."""
    if not ds.payments:
        return []
    cutoff_date = max(p.captured_at for p in ds.payments if p.captured_at).date() - timedelta(
        days=days
    )
    targets = [
        p
        for p in ds.payments
        if p.status == "captured"
        and p.method == method
        and p.captured_at
        and p.captured_at.date() >= cutoff_date
    ]

    defects: list[PlantedDefect] = []
    for payment in targets:
        extra_fee = apply_rate(payment.amount, extra_bps)
        if extra_fee <= 0:
            continue

        # GST is charged on whatever the fee actually was -- a real
        # overcharge increases GST too, not just the fee itself. Two
        # rounding-related bugs were caught here in sequence while
        # building Phase 4's independent verifier, both real, both
        # instructive:
        #
        # (1) The first version only adjusted payment.fee, leaving
        #     payment.tax inconsistent with the new fee (GST on the OLD,
        #     smaller fee). match/t2_set.py's tie-out still passed
        #     because it trusts stored .tax consistently on both sides --
        #     but verify/recompute.py correctly recomputes GST fresh from
        #     .fee (never trusts a stored value the matcher already
        #     trusted) and caught the inconsistency.
        #
        # (2) The fix for (1) computed extra_tax = gst_on(extra_fee)
        #     separately and added it to the existing tax. That is STILL
        #     wrong by up to a paisa, because gst_on(a) + gst_on(b) is not
        #     always equal to gst_on(a + b) -- each call rounds
        #     independently. The verifier recomputes GST on the payment's
        #     FINAL total fee in one call, so the injector must produce
        #     tax the same way: round once, on the combined fee, not
        #     twice and sum.
        #
        # Both bugs would have shown up as "fee_overcharge defect wrongly
        # rejected by the verifier" -- undermining the exact domain claim
        # this defect exists to demonstrate (DOMAIN_KNOWLEDGE.md section
        # 11: fee overcharge is invisible to reconciliation, only
        # detectable via rate comparison). Neither was a real money
        # problem; both were the injector computing tax differently than
        # the system it's supposed to be simulating.
        new_fee = payment.fee + extra_fee
        new_tax = gst_on(new_fee)
        total_extra = (new_fee - payment.fee) + (new_tax - payment.tax)
        payment.fee = new_fee
        payment.tax = new_tax

        settlement = next(
            (s for s in ds.settlements if s.settlement_id == payment.settlement_id), None
        )
        if settlement is None:
            continue
        settlement.amount -= total_extra
        bank_line = _bank_line_for_utr(ds, settlement.utr)
        if bank_line is not None:
            bank_line.credit -= total_extra

        defects.append(
            PlantedDefect(
                defect_id=_next_id("fee_overcharge"),
                kind="fee_overcharge",
                subject_id=payment.payment_id,
                impact_paise=total_extra,
                should_be_detected=True,
                expected_resolution="fee_overcharge",
                note=f"{method} overcharged by {extra_bps}bps (incl. GST on the excess)",
            )
        )
    return defects


def inject_unbooked_chargeback(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """Adds a genuine Dispute for n already-settled captured payments, then
    shorts the corresponding bank credit by the dispute amount + fee --
    WITHOUT touching the settlement report's stated amount. This is the
    canonical worked example from DOMAIN_KNOWLEDGE.md section 17: the
    settlement report and the bank line disagree, and the true cause is
    a chargeback that landed after the report was generated."""
    candidates = [
        p
        for p in ds.payments
        if p.status == "captured"
        and p.settlement_id
        and not ds.disputes_for(p.payment_id)
    ]
    picked = _pick(candidates, rng, n)

    defects: list[PlantedDefect] = []
    for i, payment in enumerate(picked):
        dispute_amount = payment.amount // 2 or payment.amount  # partial or full
        dispute_fee = 10_000  # Rs 100 flat [VERIFY against real dispute fee]
        dispute_id = f"disp_defect_{len(ds.disputes) + i + 1:05d}"
        ds.disputes.append(
            _make_dispute(dispute_id, payment.payment_id, dispute_amount, dispute_fee, payment)
        )

        settlement = next(
            (s for s in ds.settlements if s.settlement_id == payment.settlement_id), None
        )
        if settlement is None:
            continue
        bank_line = _bank_line_for_utr(ds, settlement.utr)
        if bank_line is None:
            continue
        shortfall = dispute_amount + dispute_fee
        bank_line.credit -= shortfall

        defects.append(
            PlantedDefect(
                defect_id=_next_id("unbooked_chargeback"),
                kind="unbooked_chargeback",
                subject_id=settlement.settlement_id,
                impact_paise=shortfall,
                should_be_detected=True,
                expected_resolution="unbooked_chargeback",
                note=f"dispute {dispute_id} not reflected in settlement report",
            )
        )
    return defects


def _make_dispute(dispute_id, payment_id, amount, fee, payment):
    return Dispute(
        dispute_id=dispute_id,
        payment_id=payment_id,
        amount=amount,
        fee=fee,
        status="raised",
        raised_at=(payment.captured_at + timedelta(days=1)) if payment.captured_at else None,
    )


def inject_missing_settlement(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """Deletes the bank line for n settlements entirely -- the payout was
    promised and never arrived. The most severe case in the taxonomy."""
    picked = _pick(list(ds.settlements), rng, n)
    defects: list[PlantedDefect] = []
    for settlement in picked:
        bank_line = _bank_line_for_utr(ds, settlement.utr)
        if bank_line is not None:
            ds.bank_lines.remove(bank_line)
        defects.append(
            PlantedDefect(
                defect_id=_next_id("missing_settlement"),
                kind="missing_settlement",
                subject_id=settlement.settlement_id,
                impact_paise=settlement.amount,
                should_be_detected=True,
                expected_resolution="missing_settlement",
            )
        )
    return defects


def inject_short_credit(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """Shorts n bank lines by an amount with NO traceable cause anywhere
    in the dataset -- the genuinely-unresolvable case from the taxonomy.
    A correct system reports this in exceptions.md rather than guessing;
    this is the defect that tests that the system is willing to say
    'I don't know' instead of fabricating a plausible-sounding answer."""
    eligible = [b for b in ds.bank_lines if b.credit > 0]
    picked = _pick(eligible, rng, n)
    defects: list[PlantedDefect] = []
    for bank_line in picked:
        shortfall = min(bank_line.credit, rng.randint(5_000, 50_000))
        bank_line.credit -= shortfall
        defects.append(
            PlantedDefect(
                defect_id=_next_id("short_credit"),
                kind="short_credit",
                subject_id=bank_line.line_id,
                impact_paise=shortfall,
                should_be_detected=True,
                expected_resolution="unresolved",
                note="deliberately untraceable -- correct system cannot explain this",
            )
        )
    return defects


# ---------------------------------------------------------------------------
# Identity-only mismatches -- no money at risk, must resolve WITHOUT
# alerting a human. should_be_detected=False for the "stay quiet" reason.
# ---------------------------------------------------------------------------


def inject_utr_change(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """Simulates a failed transfer retried under a new UTR: the bank
    line's ref_no no longer matches the settlement's utr, though the
    amount still ties exactly. T0/T1 (exact id/UTR match) will miss this;
    T3 (fuzzy, amount+date) must catch it. No money is at risk, so a
    correct system resolves this silently -- should_be_detected=False."""
    eligible = [s for s in ds.settlements if _bank_line_for_utr(ds, s.utr) is not None]
    picked = _pick(eligible, rng, n)
    defects: list[PlantedDefect] = []
    for i, settlement in enumerate(picked):
        bank_line = _bank_line_for_utr(ds, settlement.utr)
        new_utr = f"UTR{900000 + i:06d}"
        bank_line.ref_no = new_utr
        defects.append(
            PlantedDefect(
                defect_id=_next_id("utr_change"),
                kind="utr_change",
                subject_id=settlement.settlement_id,
                impact_paise=0,
                should_be_detected=False,
                expected_resolution="utr_changed",
                note=f"bank line UTR changed to {new_utr}, amount unchanged",
            )
        )
    return defects


def inject_narration_truncation(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """Truncates a bank line's narration AND blanks its ref_no, forcing
    resolution through fuzzy amount+date+narration matching (T3) instead
    of the exact UTR lookup (T1). No money at risk -- should stay quiet."""
    eligible = [b for b in ds.bank_lines if b.ref_no is not None]
    picked = _pick(eligible, rng, n)
    defects: list[PlantedDefect] = []
    for bank_line in picked:
        original_ref = bank_line.ref_no
        bank_line.narration = bank_line.narration[:10]
        bank_line.ref_no = None
        defects.append(
            PlantedDefect(
                defect_id=_next_id("narration_truncation"),
                kind="narration_truncation",
                subject_id=bank_line.line_id,
                impact_paise=0,
                should_be_detected=False,
                expected_resolution="narration_truncated",
                note=f"ref_no blanked (was {original_ref}), narration cut to 10 chars",
            )
        )
    return defects


def inject_duplicate_order_id(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """Duplicates n orders under the same order_id -- a client-side retry
    bug in the MERCHANT's own system, not a money problem. Flagged as a
    data-integrity finding, not a loss."""
    picked = _pick(list(ds.orders), rng, n)
    defects: list[PlantedDefect] = []
    for order in picked:
        duplicate = Order(
            order_id=order.order_id,
            amount=order.amount,
            status=order.status,
            created_at=order.created_at + timedelta(seconds=rng.randint(1, 30)),
        )
        ds.orders.append(duplicate)
        defects.append(
            PlantedDefect(
                defect_id=_next_id("duplicate_order_id"),
                kind="duplicate_order_id",
                subject_id=order.order_id,
                impact_paise=0,
                should_be_detected=True,
                expected_resolution="duplicate_order_id",
                note="merchant-side data quality issue, not a fund loss",
            )
        )
    return defects


# ---------------------------------------------------------------------------
# Structural / netting defects -- the hard matcher cases.
# ---------------------------------------------------------------------------


def inject_cross_period_refund(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """Moves n refunds' actual deduction out of their originating payment's
    settlement and into a LATER settlement -- the exact scenario in
    DOMAIN_KNOWLEDGE.md section 4: 'a payout on 2 April can contain a
    refund for an order from 10 March.' The source settlement is credited
    back the refund amount (as if it were never deducted there); the
    target settlement is debited instead. This is the hardest case in the
    dataset -- it requires joining across time, not just within a batch."""
    settlements_by_date = sorted(ds.settlements, key=lambda s: s.created_at)
    if len(settlements_by_date) < 2:
        return []

    eligible_refunds = [
        r
        for r in ds.refunds
        if (p := ds.payment_by_id(r.payment_id)) is not None and p.settlement_id
    ]
    picked = _pick(eligible_refunds, rng, n)

    defects: list[PlantedDefect] = []
    for refund in picked:
        payment = ds.payment_by_id(refund.payment_id)
        source = next(
            (s for s in ds.settlements if s.settlement_id == payment.settlement_id), None
        )
        if source is None:
            continue
        later = [s for s in settlements_by_date if s.created_at > source.created_at]
        if not later:
            continue
        target = later[0]

        source.amount += refund.amount
        source_bank = _bank_line_for_utr(ds, source.utr)
        if source_bank:
            source_bank.credit += refund.amount

        target.amount -= refund.amount
        target_bank = _bank_line_for_utr(ds, target.utr)
        if target_bank:
            target_bank.credit -= refund.amount

        defects.append(
            PlantedDefect(
                defect_id=_next_id("cross_period_refund"),
                kind="cross_period_refund",
                subject_id=refund.refund_id,
                impact_paise=refund.amount,
                should_be_detected=True,
                expected_resolution="cross_period_refund",
                note=(
                    f"originating payment settled in {source.settlement_id}, "
                    f"actual deduction moved to {target.settlement_id}"
                ),
            )
        )
    return defects


def inject_reserve_hold(ds: Dataset, rng: random.Random, n: int, reserve_bps: int = 500) -> list[PlantedDefect]:
    """Withholds reserve_bps (default 5%) of n settlements as a risk
    reserve: the settlement report states the full amount, but the bank
    credit is short by the reserve. Simplified model -- see note in
    FUNCTION_REFERENCE.md: no standalone reserve-ledger table exists yet,
    the withheld amount is only visible as a settlement<->bank gap plus
    this defect's own metadata."""
    picked = _pick(list(ds.settlements), rng, n)
    defects: list[PlantedDefect] = []
    for settlement in picked:
        reserve_amount = apply_rate(settlement.amount, reserve_bps)
        if reserve_amount <= 0:
            continue
        bank_line = _bank_line_for_utr(ds, settlement.utr)
        if bank_line is None:
            continue
        bank_line.credit -= reserve_amount
        defects.append(
            PlantedDefect(
                defect_id=_next_id("reserve_hold"),
                kind="reserve_hold",
                subject_id=settlement.settlement_id,
                impact_paise=reserve_amount,
                should_be_detected=True,
                expected_resolution="reserve_hold",
                note=f"{reserve_bps}bps withheld as risk reserve",
            )
        )
    return defects


def inject_negative_settlement(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """Forces n settlements negative -- refunds/chargebacks exceeded
    sales, so Razorpay DEBITS the merchant instead of crediting them.
    Bank line flips from credit to debit accordingly."""
    picked = _pick(list(ds.settlements), rng, n)
    defects: list[PlantedDefect] = []
    for settlement in picked:
        new_amount = -abs(settlement.amount) - rng.randint(1_000, 10_000)
        settlement.amount = new_amount

        bank_line = _bank_line_for_utr(ds, settlement.utr)
        if bank_line is not None:
            bank_line.credit = 0
            bank_line.debit = abs(new_amount)
            bank_line.narration = bank_line.narration.replace(" CR ", " DR ")

        defects.append(
            PlantedDefect(
                defect_id=_next_id("negative_settlement"),
                kind="negative_settlement",
                subject_id=settlement.settlement_id,
                impact_paise=abs(new_amount),
                should_be_detected=True,
                expected_resolution="negative_settlement",
            )
        )
    return defects


def inject_offline_payment(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """Adds n bank credits with no matching settlement anywhere --
    unexplained money, the case that must ALWAYS go to a human
    regardless of confidence (see gate/policy.py, D-17 in DECISIONS.md:
    'unknown_credit' is in the always_human set)."""
    defects: list[PlantedDefect] = []
    base_date = ds.bank_lines[0].txn_date if ds.bank_lines else None
    for i in range(n):
        amount = rng.randint(50_000, 500_000)
        line_id = f"bnk_offline_{i + 1:05d}"
        ds.bank_lines.append(
            BankLine(
                line_id=line_id,
                txn_date=base_date,
                narration="NEFT CR UNKNOWN THIRD PARTY",
                credit=amount,
                ref_no=None,
            )
        )
        defects.append(
            PlantedDefect(
                defect_id=_next_id("offline_payment"),
                kind="offline_payment",
                subject_id=line_id,
                impact_paise=amount,
                should_be_detected=True,
                expected_resolution="unknown_credit",
                note="not a loss -- unexplained gain that still requires a human to source",
            )
        )
    return defects


# ---------------------------------------------------------------------------
# The false-positive guards. should_be_detected=False for the "nothing is
# actually wrong" reason -- see D-05.
# ---------------------------------------------------------------------------


def inject_holiday_shift(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """MARKER ONLY -- mutates nothing. Scans for settlements whose
    capture-to-credit window spans a real holiday (core.calendar_ist
    already accounts for this correctly via expected_settlement_date), and
    tags up to n of them. Purpose: the eval harness (Phase 6) asserts NO
    alert fires for these -- proving the expectation engine, not just the
    data, treats a holiday-lengthened gap as normal. Honestly returns an
    empty list if the scenario's date range contains no holiday -- this is
    not a bug, it means this defect had nothing to mark."""
    candidates = []
    for settlement in ds.settlements:
        members = ds.payments_in_settlement(settlement.settlement_id)
        if not members:
            continue
        earliest_capture = min(p.captured_at for p in members if p.captured_at)
        span_start, span_end = earliest_capture.date(), settlement.created_at.date()
        if any(span_start <= h <= span_end for h in HOLIDAYS):
            candidates.append(settlement)

    picked = _pick(candidates, rng, n)
    return [
        PlantedDefect(
            defect_id=_next_id("holiday_shift"),
            kind="holiday_shift",
            subject_id=s.settlement_id,
            impact_paise=0,
            should_be_detected=False,
            expected_resolution="none -- holiday-adjusted timing is correct behaviour",
            note="settlement window spans a real holiday; must not trigger a false alert",
        )
        for s in picked
    ]


def inject_late_capture(ds: Dataset, rng: random.Random, n: int) -> list[PlantedDefect]:
    """MARKER ONLY -- mutates nothing. Tags up to n payments captured in
    the last hour of the business day (gen/build.py's business hours run
    9am-9pm, so hour 21 is the tail). Purpose: confirms the eval harness
    checks that a late-in-the-day capture, correctly rolled into the next
    settlement batch by expected_settlement_date, is not mistaken for a
    delayed or missing settlement.

    NOTE: an earlier version of this function compared against literal
    midnight (minutes_to_midnight <= 90), which -- given gen/build.py's
    9am-9pm business hours and <=30min capture delay -- could NEVER be
    reached (latest possible capture is 22:29, threshold needed 22:30+).
    That version was not 'rare', it was silently impossible, which is a
    worse failure mode than an honest empty result: it would have looked
    like a working defect that just never happened to fire. Fixed to use
    the business-hour boundary the generator actually produces."""
    near_cutoff = [
        p
        for p in ds.payments
        if p.status == "captured" and p.captured_at is not None and p.captured_at.hour >= 21
    ]

    picked = _pick(near_cutoff, rng, n)
    return [
        PlantedDefect(
            defect_id=_next_id("late_capture"),
            kind="late_capture",
            subject_id=p.payment_id,
            impact_paise=0,
            should_be_detected=False,
            expected_resolution="none -- correctly rolled to the next batch",
            note="capture occurred close to cutoff; settlement date already accounts for it",
        )
        for p in picked
    ]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_REGISTRY = {
    "fee_overcharge": inject_fee_overcharge,
    "unbooked_chargeback": inject_unbooked_chargeback,
    "missing_settlement": inject_missing_settlement,
    "short_credit": inject_short_credit,
    "utr_change": inject_utr_change,
    "narration_truncation": inject_narration_truncation,
    "duplicate_order_id": inject_duplicate_order_id,
    "cross_period_refund": inject_cross_period_refund,
    "reserve_hold": inject_reserve_hold,
    "negative_settlement": inject_negative_settlement,
    "offline_payment": inject_offline_payment,
    "holiday_shift": inject_holiday_shift,
    "late_capture": inject_late_capture,
}


def apply_defects(
    ds: Dataset, plan: list[DefectSpec], seed: int = 1
) -> tuple[Dataset, TruthSet]:
    """Applies each DefectSpec in plan, in order, to ds (mutated in
    place), and returns (ds, TruthSet) -- the mutated dataset plus the
    complete, immutable record of every defect actually injected.

    Order matters and is preserved exactly as given in plan -- defects
    are not randomly shuffled, because some are more naturally applied
    before others (e.g. missing_settlement removing a bank line should
    not be the same settlement cross_period_refund tries to net into).
    Callers composing a plan are responsible for a sensible order; this
    function does not attempt to detect or prevent defect interactions.
    """
    rng = random.Random(seed)
    truth = TruthSet()
    for spec in plan:
        if spec.kind not in _REGISTRY:
            raise ValueError(f"unknown defect kind: {spec.kind!r}")
        injector = _REGISTRY[spec.kind]
        defects = injector(ds, rng, **spec.params)
        truth.defects.extend(defects)
    return ds, truth
