import json
import sqlite3

import pytest

from agent.client import GroqClient
from detect.rules import DetectConfig
from gate.policy import GateConfig
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from pipeline.run import NoHypothesis, PipelineConfig, run_batch

LEDGER_SCHEMA = """
CREATE TABLE ledger (
    id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, ts TEXT NOT NULL,
    subject TEXT NOT NULL, inputs_hash TEXT NOT NULL, tier TEXT NOT NULL,
    hypothesis TEXT, evidence TEXT, verifier_result TEXT NOT NULL,
    residual INTEGER NOT NULL, gate TEXT NOT NULL, status TEXT NOT NULL,
    corrects TEXT
);
"""

RATE_CARD = {"upi": 0, "card": 200, "amex": 300}


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.execute(LEDGER_SCHEMA)
    yield connection
    connection.close()


def _cfg(**overrides) -> PipelineConfig:
    return PipelineConfig(detect_cfg=DetectConfig(rate_card_bps=RATE_CARD), **overrides)


class FakeLLMTransport:
    def __init__(self, responses):
        self.responses = list(responses)

    def complete(self, system, user, model, temperature, schema=None):
        return self.responses.pop(0)


class TestRunBatchWithoutClient:
    def test_clean_data_no_residue_no_investigations(self, conn):
        ds = generate_clean(default_spec(seed=700))
        result = run_batch("M1", ds, conn, _cfg())

        assert result.match_result.residue == []
        assert result.investigations == []
        assert result.verify_report.rejected == 0
        assert result.conservation.closes

    def test_residue_without_client_becomes_no_hypothesis_no_ledger_writes(self, conn):
        ds = generate_clean(default_spec(seed=701))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)

        result = run_batch("M1", ds, conn, _cfg(), client=None)

        assert len(result.investigations) == 1
        outcome = result.investigations[0]
        assert isinstance(outcome.resolution, NoHypothesis)
        assert "no LLM client" in outcome.resolution.reason
        assert outcome.gate_decision is None
        assert outcome.ledger_entry_id is None

        # nothing written to the ledger at all -- no client means no investigation attempted
        rows = conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        assert rows == 0

    def test_findings_still_run_without_a_client(self, conn):
        """Detectors (Phase 8) are independent of T4 -- must still work
        with no LLM configured."""
        ds = generate_clean(default_spec(seed=702))
        ds, truth = apply_defects(ds, [DefectSpec("offline_payment", {"n": 1})], seed=1)
        result = run_batch("M1", ds, conn, _cfg(), client=None)
        assert any(f.rule == "unknown_credit" for f in result.findings)


class TestRunBatchWithClientAutoPath:
    def test_small_confident_correct_hypothesis_auto_applies_and_writes_ledger(self, conn):
        ds = generate_clean(default_spec(seed=703))
        ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)
        settlement_id = truth.defects[0].subject_id
        members = [p for p in ds.payments if p.settlement_id == settlement_id]
        dispute = next(d for p in members for d in ds.disputes_for(p.payment_id))

        response = json.dumps({
            "hypothesis": "unbooked_chargeback",
            "evidence_ids": [dispute.dispute_id],
            "proposed_adjustments": [
                {"kind": "dispute", "ref": dispute.dispute_id, "paise": dispute.amount + dispute.fee}
            ],
            "confidence": 0.99,
        })
        client = GroqClient(transport=FakeLLMTransport([response]), sleep_fn=lambda _: None)

        gate_cfg = GateConfig(auto_apply_max_paise=10_000_000, min_confidence=0.5, always_human=frozenset())
        result = run_batch("M1", ds, conn, _cfg(gate_cfg=gate_cfg), client=client)

        assert len(result.investigations) == 1
        outcome = result.investigations[0]
        assert outcome.gate_decision == "AUTO"
        assert outcome.ledger_entry_id is not None

        row = conn.execute(
            "SELECT tier, hypothesis, gate, status, verifier_result FROM ledger WHERE id = ?",
            (outcome.ledger_entry_id,),
        ).fetchone()
        assert row == ("T4", "unbooked_chargeback", "AUTO", "APPLIED", "ACCEPT")


class TestRunBatchWithClientHumanPath:
    def test_default_gate_config_routes_large_correct_hypothesis_to_human(self, conn):
        ds = generate_clean(default_spec(seed=704))
        ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)
        settlement_id = truth.defects[0].subject_id
        members = [p for p in ds.payments if p.settlement_id == settlement_id]
        dispute = next(d for p in members for d in ds.disputes_for(p.payment_id))

        response = json.dumps({
            "hypothesis": "unbooked_chargeback",
            "evidence_ids": [dispute.dispute_id],
            "proposed_adjustments": [
                {"kind": "dispute", "ref": dispute.dispute_id, "paise": dispute.amount + dispute.fee}
            ],
            "confidence": 0.99,
        })
        client = GroqClient(transport=FakeLLMTransport([response]), sleep_fn=lambda _: None)

        # default GateConfig: auto_apply_max_paise=10_000 (Rs 100) --
        # a real dispute+fee shortfall will almost always exceed this
        result = run_batch("M1", ds, conn, _cfg(), client=client)  # default gate_cfg

        outcome = result.investigations[0]
        assert outcome.gate_decision == "HUMAN"
        row = conn.execute(
            "SELECT gate, status FROM ledger WHERE id = ?", (outcome.ledger_entry_id,)
        ).fetchone()
        assert row == ("HUMAN", "PENDING_APPROVAL")


class TestRunBatchWithClientRejectPath:
    def test_wrong_hypothesis_gets_rejected_and_recorded(self, conn):
        from datetime import datetime

        from gen.models import Dispute

        ds = generate_clean(default_spec(seed=705))
        ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)
        settlement_id = truth.defects[0].subject_id

        decoy = Dispute("disp_decoy_pipeline", "pay_00001", 2, 2, "raised", datetime(2026, 2, 3))
        ds.disputes.append(decoy)

        response = json.dumps({
            "hypothesis": "unbooked_chargeback",
            "evidence_ids": [decoy.dispute_id],
            "proposed_adjustments": [{"kind": "dispute", "ref": decoy.dispute_id, "paise": 4}],
            "confidence": 0.99,
        })
        client = GroqClient(transport=FakeLLMTransport([response]), sleep_fn=lambda _: None)

        result = run_batch("M1", ds, conn, _cfg(), client=client)
        outcome = result.investigations[0]

        assert outcome.gate_decision == "REJECT"
        assert outcome.ledger_entry_id is not None  # still recorded -- D-16, a decision worth a permanent record

        row = conn.execute(
            "SELECT gate, status, verifier_result FROM ledger WHERE id = ?",
            (outcome.ledger_entry_id,),
        ).fetchone()
        assert row == ("REJECT", "REJECTED", "REJECT")


class TestRunBatchUnverifiableHypothesis:
    def test_unverifiable_adjustment_kind_still_gets_a_ledger_row(self, conn):
        """Caught live: agent.investigate.ProposedResolution.to_proof's
        docstring promises an unverifiable hypothesis 'is still recorded
        (the ledger keeps it)', but the pipeline used to return
        ledger_entry_id=None and never call ledger.store.append at all --
        the Activity log stayed silently empty even when the model
        produced a real, schema-valid hypothesis. Must now write a HUMAN/
        PENDING_APPROVAL/UNVERIFIABLE row instead of dropping it."""
        ds = generate_clean(default_spec(seed=707))
        ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)

        response = json.dumps({
            "hypothesis": "fee_overcharge",
            "evidence_ids": [],
            "proposed_adjustments": [{"kind": "fee_correction", "ref": "pay_00001", "paise": 500}],
            "confidence": 0.8,
        })
        client = GroqClient(transport=FakeLLMTransport([response]), sleep_fn=lambda _: None)

        result = run_batch("M1", ds, conn, _cfg(), client=client)
        outcome = result.investigations[0]

        assert outcome.gate_decision == "HUMAN"
        assert outcome.ledger_entry_id is not None

        row = conn.execute(
            "SELECT gate, status, verifier_result, hypothesis FROM ledger WHERE id = ?",
            (outcome.ledger_entry_id,),
        ).fetchone()
        assert row == ("HUMAN", "PENDING_APPROVAL", "UNVERIFIABLE", "fee_overcharge")


class TestRunBatchDryRun:
    def test_conn_none_skips_ledger_but_still_investigates(self):
        ds = generate_clean(default_spec(seed=706))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        result = run_batch("M1", ds, conn=None, cfg=_cfg(), client=None)

        assert len(result.investigations) == 1
        assert result.investigations[0].ledger_entry_id is None
