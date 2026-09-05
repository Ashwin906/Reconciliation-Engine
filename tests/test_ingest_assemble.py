from datetime import datetime

from gen.models import BankLine
from ingest.assemble import assemble_dataset, fetch_and_assemble

TS = datetime(2026, 2, 4, 12, 0)


def _payment(pid="pay_1", **over):
    base = {
        "payment_id": pid,
        "order_id": "order_1",
        "amount": 100_000,
        "method": "card",
        "fee": 2_000,
        "tax": 360,
        "status": "captured",
        "captured_at": TS,
        "settlement_id": "setl_1",
    }
    base.update(over)
    return base


def _settlement(sid="setl_1", **over):
    base = {"settlement_id": sid, "amount": 97_640, "utr": "UTR123", "created_at": TS}
    base.update(over)
    return base


class TestAssemblyProducesARealDataset:
    def test_maps_every_entity_onto_the_pipelines_own_shape(self):
        dataset, report = assemble_dataset(
            merchant_id="M_LIVE",
            payments=[_payment()],
            settlements=[_settlement()],
            refunds=[{"refund_id": "rfnd_1", "payment_id": "pay_1", "amount": 500, "created_at": TS}],
            disputes=[
                {
                    "dispute_id": "disp_1",
                    "payment_id": "pay_1",
                    "amount": 1_000,
                    "fee": 200,
                    "status": "raised",
                    "raised_at": TS,
                }
            ],
            bank_lines=[BankLine("bnk_1", TS.date(), "SETTLEMENT", credit=97_640, ref_no="UTR123")],
        )

        assert dataset.merchant_id == "M_LIVE"
        assert dataset.payments[0].payment_id == "pay_1"
        assert dataset.payments[0].amount == 100_000
        assert dataset.settlements[0].utr == "UTR123"
        assert dataset.refunds[0].refund_id == "rfnd_1"
        assert dataset.disputes[0].fee == 200
        assert report.is_complete

    def test_the_assembled_dataset_actually_runs_through_the_real_pipeline(self):
        """The point of this module: real-shaped data must be consumable
        by the same matcher/verifier/invariant path generated data uses.
        If this failed, the whole 'swap in real data' claim would be
        untrue no matter how clean the mapping looked."""
        from detect.rules import DetectConfig
        from pipeline.run import PipelineConfig, run_batch

        dataset, _ = assemble_dataset(
            merchant_id="M_LIVE",
            payments=[_payment()],
            settlements=[_settlement()],
            refunds=[],
            disputes=[],
            bank_lines=[BankLine("bnk_1", TS.date(), "SETTLEMENT", credit=97_640, ref_no="UTR123")],
        )

        result = run_batch(
            "M_LIVE",
            dataset,
            conn=None,
            cfg=PipelineConfig(detect_cfg=DetectConfig(rate_card_bps={"card": 200})),
        )
        assert len(result.match_result.t1_matches) == 1
        assert result.match_result.residue == []


class TestTheReportIsHonestAboutGaps:
    def test_missing_bank_statement_is_reported_not_silently_zero(self):
        """A merchant seeing 0% matched must be told it is because no
        statement was supplied, not because their money vanished."""
        _, report = assemble_dataset("M_LIVE", [_payment()], [_settlement()], [], [], bank_lines=[])
        assert not report.is_complete
        assert any("no bank statement" in s for s in report.skipped)

    def test_a_settlement_with_no_timestamp_is_skipped_and_named(self):
        _, report = assemble_dataset(
            "M_LIVE", [], [_settlement(created_at=None)], [], [], bank_lines=[BankLine("b", TS.date(), "x")]
        )
        assert any("setl_1" in s for s in report.skipped)

    def test_a_settlement_with_no_utr_is_kept_not_dropped(self):
        """It is real money Razorpay says it sent. Dropping it would turn
        a reconciliation failure into a clean report."""
        dataset, _ = assemble_dataset(
            "M_LIVE", [], [_settlement(utr=None)], [], [], bank_lines=[BankLine("b", TS.date(), "x")]
        )
        assert len(dataset.settlements) == 1
        assert dataset.settlements[0].utr is None

    def test_an_uncaptured_payment_is_kept(self):
        """Still real money in the collected total -- dropping it would
        silently break conservation."""
        dataset, _ = assemble_dataset(
            "M_LIVE",
            [_payment(captured_at=None, status="authorized")],
            [],
            [],
            [],
            bank_lines=[BankLine("b", TS.date(), "x")],
        )
        assert len(dataset.payments) == 1


class TestLiveFetchPath:
    def test_fetches_every_entity_type_for_the_window(self):
        calls = []

        class FakeClient:
            def fetch_payments(self, since, until):
                calls.append(("payments", since, until))
                return [_payment()]

            def fetch_settlements(self, since, until):
                calls.append(("settlements", since, until))
                return [_settlement()]

            def fetch_refunds(self, since, until):
                calls.append(("refunds", since, until))
                return []

            def fetch_disputes(self, since, until):
                calls.append(("disputes", since, until))
                return []

        dataset, report = fetch_and_assemble(
            FakeClient(),
            merchant_id="M_LIVE",
            since=1_700_000_000,
            until=1_700_600_000,
            bank_lines=[BankLine("b", TS.date(), "x", credit=1)],
        )

        assert {c[0] for c in calls} == {"payments", "settlements", "refunds", "disputes"}
        assert all(c[1] == 1_700_000_000 and c[2] == 1_700_600_000 for c in calls)
        assert report.payments == 1 and report.settlements == 1
        assert dataset.merchant_id == "M_LIVE"
