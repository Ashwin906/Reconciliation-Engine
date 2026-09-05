import sqlite3

import pytest

from detect.rules import DetectConfig
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from pipeline.run import PipelineConfig, daily_sweep

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


def _cfg() -> PipelineConfig:
    return PipelineConfig(detect_cfg=DetectConfig(rate_card_bps=RATE_CARD))


class TestDailySweep:
    def test_sweeps_every_merchant_in_the_dict(self, conn):
        datasets = {
            "M1": generate_clean(default_spec(seed=910)),
            "M2": generate_clean(default_spec(seed=911)),
            "M3": generate_clean(default_spec(seed=912)),
        }
        results = daily_sweep(datasets, conn, _cfg())
        assert set(results.keys()) == {"M1", "M2", "M3"}
        assert all(r.conservation.closes for r in results.values())

    def test_empty_dict_returns_empty_dict(self, conn):
        assert daily_sweep({}, conn, _cfg()) == {}

    def test_repeated_sweeps_on_unchanged_data_agree(self, conn):
        """The idempotency property that matters: re-running on
        unchanged data produces the SAME match/verify/conservation
        result every time (not the same run_id or object -- a fresh
        BatchResult each call is expected and correct, per the
        function's own docstring)."""
        ds = generate_clean(default_spec(seed=913))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        datasets = {"M1": ds}

        first = daily_sweep(datasets, conn, _cfg())["M1"]
        second = daily_sweep(datasets, conn, _cfg())["M1"]

        assert [u.id for u in first.match_result.residue] == [u.id for u in second.match_result.residue]
        assert first.conservation.residual == second.conservation.residual
        assert first.conservation.closes == second.conservation.closes
        assert len(first.findings) == len(second.findings)

    def test_one_merchants_defects_do_not_affect_another(self, conn):
        clean_ds = generate_clean(default_spec(seed=914))
        broken_ds = generate_clean(default_spec(seed=915))
        broken_ds, _ = apply_defects(broken_ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)

        results = daily_sweep({"CLEAN": clean_ds, "BROKEN": broken_ds}, conn, _cfg())

        assert results["CLEAN"].conservation.closes
        assert not results["BROKEN"].conservation.closes
