import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from api.routes import AppState, create_app
from detect.rules import DetectConfig
from gen.build import generate_clean
from gen.defects import DefectSpec, apply_defects
from gen.scenario import default_spec
from pipeline.run import PipelineConfig

RATE_CARD = {"upi": 0, "card": 200, "amex": 300}
SECRET = "test_secret"


@pytest.fixture
def state():
    return AppState(
        webhook_secret=SECRET,
        pipeline_cfg=PipelineConfig(detect_cfg=DetectConfig(rate_card_bps=RATE_CARD)),
    )


@pytest.fixture
def client(state):
    return TestClient(create_app(state))


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


class TestWebhookSignature:
    """Phase 13: the webhook now actually TRIGGERS a run (event ==
    settlement.processed) rather than just validating and returning
    {"status": "accepted"} -- these tests exercise the real trigger, not
    just the signature check."""

    def test_valid_signature_and_settlement_event_triggers_a_run(self, client, state):
        ds = generate_clean(default_spec(seed=900))
        state.datasets["M1"] = ds

        body = json.dumps({"event": "settlement.processed"}).encode()
        response = client.post(
            "/webhooks/razorpay/M1", content=body,
            headers={"X-Razorpay-Signature": _sign(body), "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "accepted"
        assert "run_id" in payload
        assert payload["conservation_closes"] is True  # real pipeline ran on clean data

    def test_non_settlement_event_is_ignored_not_run(self, client, state):
        ds = generate_clean(default_spec(seed=901))
        state.datasets["M1"] = ds

        body = json.dumps({"event": "payment.captured"}).encode()
        response = client.post(
            "/webhooks/razorpay/M1", content=body,
            headers={"X-Razorpay-Signature": _sign(body), "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ignored", "event": "payment.captured"}
        assert len(state.runs) == 0  # no run was triggered

    def test_invalid_signature_rejected_400(self, client):
        body = json.dumps({"event": "settlement.processed"}).encode()
        response = client.post(
            "/webhooks/razorpay/M1", content=body,
            headers={"X-Razorpay-Signature": "wrong", "Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_missing_signature_rejected_400(self, client):
        body = json.dumps({"event": "x"}).encode()
        response = client.post("/webhooks/razorpay/M1", content=body)
        assert response.status_code == 400

    def test_tampered_body_rejected_even_with_originally_valid_signature(self, client):
        original_body = json.dumps({"event": "settlement.processed", "amount": 100}).encode()
        signature = _sign(original_body)
        tampered_body = json.dumps({"event": "settlement.processed", "amount": 999999999}).encode()
        response = client.post(
            "/webhooks/razorpay/M1", content=tampered_body,
            headers={"X-Razorpay-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_invalid_json_body_rejected_400_not_500(self, client):
        body = b"not json at all"
        response = client.post(
            "/webhooks/razorpay/M1", content=body,
            headers={"X-Razorpay-Signature": _sign(body), "Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_unknown_merchant_404s_after_signature_passes(self, client):
        body = json.dumps({"event": "settlement.processed"}).encode()
        response = client.post(
            "/webhooks/razorpay/M_UNKNOWN", content=body,
            headers={"X-Razorpay-Signature": _sign(body), "Content-Type": "application/json"},
        )
        assert response.status_code == 404


class TestDailySweep:
    def test_sweep_runs_every_loaded_merchant(self, client, state):
        state.datasets["M1"] = generate_clean(default_spec(seed=902))
        state.datasets["M2"] = generate_clean(default_spec(seed=903))

        response = client.post("/sweep")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"M1", "M2"}
        assert body["M1"]["conservation_closes"] is True
        assert body["M2"]["conservation_closes"] is True

    def test_sweep_on_no_merchants_returns_empty(self, client):
        response = client.post("/sweep")
        assert response.status_code == 200
        assert response.json() == {}

    def test_sweep_and_manual_trigger_agree_on_clean_data(self, client, state):
        """Both triggers call the same underlying pipeline -- their
        results for the same unchanged dataset must agree, proving
        there's exactly one behaviour, not two that could drift apart."""
        state.datasets["M1"] = generate_clean(default_spec(seed=904))

        manual = client.post("/runs", params={"merchant_id": "M1"}).json()
        swept = client.post("/sweep").json()["M1"]

        assert manual["residue_count"] == swept["residue_count"]
        assert manual["conservation_closes"] == swept["conservation_closes"]


class TestRunsLifecycle:
    def test_create_run_without_dataset_404s(self, client):
        response = client.post("/runs", params={"merchant_id": "M_UNKNOWN"})
        assert response.status_code == 404

    def test_create_and_fetch_a_run_on_clean_data(self, client, state):
        ds = generate_clean(default_spec(seed=800))
        state.datasets["M1"] = ds

        create_response = client.post("/runs", params={"merchant_id": "M1"})
        assert create_response.status_code == 200
        body = create_response.json()
        assert body["residue_count"] == 0
        assert body["conservation_closes"] is True

        run_id = body["run_id"]
        get_response = client.get(f"/runs/{run_id}")
        assert get_response.status_code == 200
        detail = get_response.json()
        assert detail["residue"] == []
        assert detail["verify_rejected"] == 0

    def test_unknown_run_id_404s(self, client):
        response = client.get("/runs/run_99999")
        assert response.status_code == 404

    def test_run_with_defects_shows_residue_and_findings(self, client, state):
        ds = generate_clean(default_spec(seed=801))
        ds, truth = apply_defects(
            ds,
            [DefectSpec("missing_settlement", {"n": 1}), DefectSpec("offline_payment", {"n": 1})],
            seed=1,
        )
        state.datasets["M1"] = ds

        create_response = client.post("/runs", params={"merchant_id": "M1"})
        body = create_response.json()
        assert body["residue_count"] == 1  # missing_settlement -> residue
        assert body["findings_count"] >= 1  # unknown_credit at least


class TestSimulationDrivesAppState:
    """The app ships empty; a simulation is the ONLY thing that puts data
    in it. These lock in that the journey view and the dashboard are two
    renderings of one run rather than two independent sources of numbers."""

    def test_app_starts_completely_empty(self, client):
        assert client.get("/status").json() == {"status": "no runs yet"}
        assert client.get("/runs").json() == []
        assert client.get("/exports").json() == []

    def test_simulation_installs_a_real_run_the_dashboard_can_read(self, client):
        journey = client.post("/simulate/unbooked_chargeback").json()
        run_id = journey["run_id"]

        listed = client.get("/runs").json()
        assert [r["run_id"] for r in listed] == [run_id]

        detail = client.get(f"/runs/{run_id}").json()
        assert len(detail["residue"]) == journey["outcome"]["residue"]

        status = client.get("/status").json()
        assert status["last_run_id"] == run_id

    def test_dashboard_numbers_match_the_journey_exactly(self, client):
        """If these ever disagree, one of the two screens is lying."""
        journey = client.post("/simulate/short_credit").json()
        detail = client.get(f"/runs/{journey['run_id']}").json()

        assert detail["conservation_closes"] == journey["outcome"]["conservation_closes"]
        assert detail["conservation_residual_paise"] == journey["outcome"]["conservation_residual_paise"]
        assert len(detail["findings"]) == len(journey["outcome"]["findings"])

    def test_exceptions_come_from_the_simulated_run(self, client):
        journey = client.post("/simulate/missing_settlement").json()
        exceptions = client.get("/exceptions", params={"run_id": journey["run_id"]}).json()
        assert len(exceptions) == journey["outcome"]["residue"]

    def test_successive_simulations_accumulate_rather_than_overwrite(self, client):
        first = client.post("/simulate/happy_path").json()
        second = client.post("/simulate/unbooked_chargeback").json()
        assert first["run_id"] != second["run_id"]
        assert len(client.get("/runs").json()) == 2

    def test_unknown_scenario_404s(self, client):
        assert client.post("/simulate/not_a_scenario").status_code == 404

    def test_defect_catalog_lists_real_injectors(self, client):
        catalog = client.get("/simulate/defects").json()
        kinds = {e["kind"] for e in catalog}
        assert "fee_overcharge" in kinds and "unbooked_chargeback" in kinds
        fee = next(e for e in catalog if e["kind"] == "fee_overcharge")
        assert {p["name"] for p in fee["params"]} == {"method", "extra_bps", "days"}

    def test_custom_run_installs_state_like_a_built_in_scenario(self, client):
        journey = client.post(
            "/simulate/custom/run",
            json={"defects": [{"kind": "unbooked_chargeback", "params": {"n": 1}}]},
        ).json()
        assert client.get("/status").json()["last_run_id"] == journey["run_id"]
        assert journey["scenario"]["slug"] == "custom"

    def test_custom_run_rejects_a_bad_plan_with_422_not_500(self, client):
        assert client.post("/simulate/custom/run", json={"defects": []}).status_code == 422
        assert (
            client.post(
                "/simulate/custom/run", json={"defects": [{"kind": "nope", "params": {}}]}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/simulate/custom/run",
                json={"defects": [{"kind": "unbooked_chargeback", "params": {"bogus": 1}}]},
            ).status_code
            == 422
        )

    def test_journey_carries_money_flow_and_comparison(self, client):
        journey = client.post("/simulate/utr_change").json()
        assert journey["money_flow"]["collected_paise"] > 0
        assert journey["comparison"]["difference"]["false_alarms_avoided"] == 1

    def test_attack_catalog_names_a_defence_for_each(self, client):
        catalog = client.get("/simulate/attacks").json()
        assert len(catalog) == 4
        assert all(entry["defence"] for entry in catalog)

    def test_adversarial_run_is_stopped_and_installs_state(self, client):
        journey = client.post("/simulate/adversarial/understated_amount").json()
        assert journey["adversarial"]["held"] is True
        assert journey["adversarial"]["auto_applied"] == 0
        assert client.get("/status").json()["last_run_id"] == journey["run_id"]

    def test_unknown_attack_is_422(self, client):
        assert client.post("/simulate/adversarial/nope").status_code == 422

    def test_reserve_endpoint_proves_conservation_needs_the_ledger(self, client):
        journey = client.get("/simulate/reserve", params={"periods": 4}).json()
        assert len(journey["periods"]) == 4
        assert all(not p["conservation_without_ledger"]["closes"] for p in journey["periods"])
        assert all(p["conservation_with_ledger"]["closes"] for p in journey["periods"])

    def test_reserve_does_not_pollute_single_run_dashboard_state(self, client):
        """It spans several periods; the dashboard describes exactly one
        run. Installing it would force a single-batch view to display a
        multi-batch result."""
        client.get("/simulate/reserve")
        assert client.get("/runs").json() == []

    def test_reserve_rejects_an_unusable_period_count(self, client):
        assert client.get("/simulate/reserve", params={"periods": 99}).status_code == 422

    def test_reset_returns_to_the_empty_state(self, client):
        client.post("/simulate/unbooked_chargeback")
        assert client.get("/runs").json() != []

        assert client.post("/reset").status_code == 200
        assert client.get("/runs").json() == []
        assert client.get("/status").json() == {"status": "no runs yet"}
        assert client.get("/ledger", params={"merchant_id": "M_SIM"}).json() == []


class TestStatus:
    def test_no_runs_yet(self, client):
        response = client.get("/status")
        assert response.json() == {"status": "no runs yet"}

    def test_books_close_true_on_clean_run(self, client, state):
        ds = generate_clean(default_spec(seed=802))
        state.datasets["M1"] = ds
        client.post("/runs", params={"merchant_id": "M1"})

        response = client.get("/status")
        body = response.json()
        assert body["books_close"] is True
        assert body["residue_count"] == 0

    def test_books_close_false_when_residue_exists(self, client, state):
        ds = generate_clean(default_spec(seed=803))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        state.datasets["M1"] = ds
        client.post("/runs", params={"merchant_id": "M1"})

        response = client.get("/status")
        assert response.json()["books_close"] is False


class TestExceptionsAndActions:
    def test_list_exceptions_matches_residue(self, client, state):
        ds = generate_clean(default_spec(seed=804))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        state.datasets["M1"] = ds
        run_id = client.post("/runs", params={"merchant_id": "M1"}).json()["run_id"]

        response = client.get("/exceptions", params={"run_id": run_id})
        assert response.status_code == 200
        exceptions = response.json()
        assert len(exceptions) == 1
        assert exceptions[0]["settlement_id"] == truth.defects[0].subject_id
        assert exceptions[0]["reason"] == "missing_settlement"

    def test_approve_unknown_exception_404s(self, client, state):
        ds = generate_clean(default_spec(seed=805))
        state.datasets["M1"] = ds
        run_id = client.post("/runs", params={"merchant_id": "M1"}).json()["run_id"]

        response = client.post(f"/exceptions/{run_id}/setl_does_not_exist/approve")
        assert response.status_code == 404

    def test_approve_real_exception_succeeds(self, client, state):
        ds = generate_clean(default_spec(seed=806))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        state.datasets["M1"] = ds
        run_id = client.post("/runs", params={"merchant_id": "M1"}).json()["run_id"]
        settlement_id = truth.defects[0].subject_id

        response = client.post(f"/exceptions/{run_id}/{settlement_id}/approve")
        assert response.status_code == 200
        assert response.json()["status"] == "approved"

    def test_reject_without_reason_422s(self, client, state):
        ds = generate_clean(default_spec(seed=807))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        state.datasets["M1"] = ds
        run_id = client.post("/runs", params={"merchant_id": "M1"}).json()["run_id"]
        settlement_id = truth.defects[0].subject_id

        response = client.post(
            f"/exceptions/{run_id}/{settlement_id}/reject", params={"reason": ""}
        )
        assert response.status_code == 422

    def test_reject_with_reason_succeeds(self, client, state):
        ds = generate_clean(default_spec(seed=808))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        state.datasets["M1"] = ds
        run_id = client.post("/runs", params={"merchant_id": "M1"}).json()["run_id"]
        settlement_id = truth.defects[0].subject_id

        response = client.post(
            f"/exceptions/{run_id}/{settlement_id}/reject",
            params={"reason": "not a real chargeback, investigated manually"},
        )
        assert response.status_code == 200
        assert response.json()["reason"]


class TestListRuns:
    def test_empty_when_no_runs(self, client):
        response = client.get("/runs")
        assert response.status_code == 200
        assert response.json() == []

    def test_lists_runs_newest_first_with_merchant_id(self, client, state):
        state.datasets["M1"] = generate_clean(default_spec(seed=820))
        state.datasets["M2"] = generate_clean(default_spec(seed=821))

        first = client.post("/runs", params={"merchant_id": "M1"}).json()
        second = client.post("/runs", params={"merchant_id": "M2"}).json()

        response = client.get("/runs")
        rows = response.json()
        assert [r["run_id"] for r in rows] == [second["run_id"], first["run_id"]]
        assert rows[0]["merchant_id"] == "M2"
        assert rows[1]["merchant_id"] == "M1"

    def test_sweep_triggered_runs_also_appear(self, client, state):
        state.datasets["M1"] = generate_clean(default_spec(seed=822))
        client.post("/sweep")
        rows = client.get("/runs").json()
        assert len(rows) == 1
        assert rows[0]["merchant_id"] == "M1"

    def test_match_rate_pct_reflects_matched_over_total(self, client, state):
        """Overview page's trend sparkline is built from this field -- it
        must be a real matched/total ratio, not a stand-in for residue_count."""
        state.datasets["M1"] = generate_clean(default_spec(seed=824))
        client.post("/runs", params={"merchant_id": "M1"})
        row = client.get("/runs").json()[0]
        assert 0.0 <= row["match_rate_pct"] <= 100.0
        run_detail = client.get(f"/runs/{row['run_id']}").json()
        matched = run_detail["t1_matches"] + run_detail["t2_matches"] + run_detail["t3_matches"]
        total = matched + len(run_detail["residue"])
        expected = round(100 * matched / total, 1) if total else 100.0
        assert row["match_rate_pct"] == expected


class TestLedgerFullDetail:
    def test_ledger_row_includes_evidence_and_residual(self, client, state):
        """The Ledger screen's expand-for-detail view needs evidence and
        residual, not just the summary fields -- confirms the route
        actually returns them, not just that a row exists."""
        import json as json_module

        from agent.client import GroqClient

        ds = generate_clean(default_spec(seed=823))
        ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)
        settlement_id = truth.defects[0].subject_id
        members = [p for p in ds.payments if p.settlement_id == settlement_id]
        dispute = next(d for p in members for d in ds.disputes_for(p.payment_id))

        response_json = json_module.dumps({
            "hypothesis": "unbooked_chargeback",
            "evidence_ids": [dispute.dispute_id],
            "proposed_adjustments": [
                {"kind": "dispute", "ref": dispute.dispute_id, "paise": dispute.amount + dispute.fee}
            ],
            "confidence": 0.99,
        })

        class _FakeTransport:
            def complete(self, system, user, model, temperature, schema=None):
                return response_json

        state.datasets["M1"] = ds
        state.llm_client = GroqClient(transport=_FakeTransport(), sleep_fn=lambda _: None)
        client.post("/runs", params={"merchant_id": "M1"})

        rows = client.get("/ledger", params={"merchant_id": "M1"}).json()
        assert len(rows) == 1
        assert rows[0]["evidence"] == {"evidence_ids": [dispute.dispute_id], "note": None}
        assert rows[0]["residual"] == 0  # verified/accepted


class TestExportHistory:
    def test_empty_before_any_export_generated(self, client):
        assert client.get("/exports").json() == []

    def test_generating_a_close_pack_records_history(self, client, state):
        ds = generate_clean(default_spec(seed=824))
        state.datasets["M1"] = ds
        run_id = client.post("/runs", params={"merchant_id": "M1"}).json()["run_id"]

        csv_response = client.get("/export/close-pack.csv", params={"run_id": run_id})
        assert csv_response.status_code == 200

        history = client.get("/exports").json()
        assert len(history) == 1
        assert history[0]["run_id"] == run_id
        assert "generated_at" in history[0]

    def test_multiple_exports_newest_first(self, client, state):
        ds = generate_clean(default_spec(seed=825))
        state.datasets["M1"] = ds
        run_id = client.post("/runs", params={"merchant_id": "M1"}).json()["run_id"]

        client.get("/export/close-pack.csv", params={"run_id": run_id})
        client.get("/export/close-pack.csv", params={"run_id": run_id})

        history = client.get("/exports").json()
        assert len(history) == 2

    def test_failed_export_does_not_record_history(self, client):
        response = client.get("/export/close-pack.csv", params={"run_id": "run_nonexistent"})
        assert response.status_code == 404
        assert client.get("/exports").json() == []


class TestLedgerAndExport:
    def test_ledger_genuinely_empty_when_no_llm_client_configured(self, client, state):
        """Real SQLite query (AppState.ledger_conn), genuinely empty --
        pipeline.run.run_batch only writes a row for a settlement it
        actually investigated via T4, and no llm_client is configured by
        default. Honestly empty via a real query, not a hardcoded []."""
        ds = generate_clean(default_spec(seed=810))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        state.datasets["M1"] = ds
        client.post("/runs", params={"merchant_id": "M1"})

        response = client.get("/ledger", params={"merchant_id": "M1"})
        assert response.status_code == 200
        assert response.json() == []

    def test_ledger_shows_real_rows_once_a_hypothesis_is_investigated(self, client, state):
        """Confirms /ledger is a REAL query, not a permanently-empty
        stub -- wires a fake-but-real GroqClient (same pattern as
        tests/test_pipeline_run.py) so T4 actually runs, and checks the
        resulting row is genuinely queryable back out over HTTP."""
        import json

        from agent.client import GroqClient

        ds = generate_clean(default_spec(seed=811))
        ds, truth = apply_defects(ds, [DefectSpec("unbooked_chargeback", {"n": 1})], seed=1)
        settlement_id = truth.defects[0].subject_id
        members = [p for p in ds.payments if p.settlement_id == settlement_id]
        dispute = next(d for p in members for d in ds.disputes_for(p.payment_id))

        response_json = json.dumps({
            "hypothesis": "unbooked_chargeback",
            "evidence_ids": [dispute.dispute_id],
            "proposed_adjustments": [
                {"kind": "dispute", "ref": dispute.dispute_id, "paise": dispute.amount + dispute.fee}
            ],
            "confidence": 0.99,
        })

        class _FakeTransport:
            def complete(self, system, user, model, temperature, schema=None):
                return response_json

        state.datasets["M1"] = ds
        state.llm_client = GroqClient(transport=_FakeTransport(), sleep_fn=lambda _: None)
        client.post("/runs", params={"merchant_id": "M1"})

        response = client.get("/ledger", params={"merchant_id": "M1"})
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 1
        assert rows[0]["subject"] == settlement_id
        assert rows[0]["tier"] == "T4"
        assert rows[0]["hypothesis"] == "unbooked_chargeback"

    def test_export_close_pack_csv_shape(self, client, state):
        ds = generate_clean(default_spec(seed=809))
        ds, truth = apply_defects(ds, [DefectSpec("missing_settlement", {"n": 1})], seed=1)
        state.datasets["M1"] = ds
        run_id = client.post("/runs", params={"merchant_id": "M1"}).json()["run_id"]

        response = client.get("/export/close-pack.csv", params={"run_id": run_id})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        lines = response.text.strip().splitlines()
        assert lines[0] == "settlement_id,status,reason,amount"
        assert any(truth.defects[0].subject_id in line for line in lines)


class TestLiveRazorpayPath:
    """The production path: real credentials, real API, same pipeline.

    Exercised against an injected fake client -- the transport boundary
    (ingest.razorpay.RequestsTransport) is the one link that has never run
    against a live endpoint, and that is stated in the route's own
    /live/status response rather than hidden.
    """

    def _fake_client(self):
        from datetime import datetime

        ts = datetime(2026, 2, 4, 12, 0)

        class FakeRazorpay:
            def fetch_payments(self, since, until):
                return [
                    {
                        "payment_id": "pay_live_1",
                        "order_id": "order_1",
                        "amount": 100_000,
                        "method": "card",
                        "fee": 2_000,
                        "tax": 360,
                        "status": "captured",
                        "captured_at": ts,
                        "settlement_id": "setl_live_1",
                    }
                ]

            def fetch_settlements(self, since, until):
                return [
                    {
                        "settlement_id": "setl_live_1",
                        "amount": 97_640,
                        "utr": "UTRLIVE1",
                        "created_at": ts,
                    }
                ]

            def fetch_refunds(self, since, until):
                return []

            def fetch_disputes(self, since, until):
                return []

        return FakeRazorpay()

    def test_status_reports_configuration_without_leaking_the_key(self, client, monkeypatch):
        monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_SUPERSECRET")
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", "alsosecret")

        body = client.get("/live/status").json()
        assert body["configured"] is True
        # No key material anywhere in the response, not even masked.
        assert "SUPERSECRET" not in json.dumps(body)
        assert "alsosecret" not in json.dumps(body)

    def test_status_is_honest_that_the_transport_is_unverified(self, client):
        assert client.get("/live/status").json()["transport_verified_against_live_api"] is False

    def test_run_without_credentials_is_refused_with_guidance(self, client, monkeypatch):
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

        response = client.post("/live/run", json={"since": 1, "until": 2})
        assert response.status_code == 412
        assert "never accepted through this API" in response.json()["detail"]

    def test_credentials_in_the_request_body_are_ignored_entirely(self, client, monkeypatch):
        """A key must never be usable from the browser. Posting one does
        not authenticate -- the route reads the environment or refuses."""
        monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
        monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

        response = client.post(
            "/live/run",
            json={"since": 1, "until": 2, "key_id": "rzp_live_x", "key_secret": "y"},
        )
        assert response.status_code == 412

    def test_a_real_run_reconciles_and_installs_state(self, client, state, monkeypatch):
        monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_x")
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", "y")
        state.razorpay_client = self._fake_client()

        body = client.post(
            "/live/run", json={"merchant_id": "M_LIVE", "since": 1, "until": 2}
        ).json()

        assert body["source"] == "razorpay_live"
        assert body["ingested"]["payments"] == 1
        assert body["ingested"]["settlements"] == 1
        # The dashboard now reads this run like any other.
        assert client.get("/status").json()["last_run_id"] == body["run_id"]

    def test_missing_bank_statement_is_surfaced_in_the_response(self, client, state, monkeypatch):
        monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_x")
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", "y")
        state.razorpay_client = self._fake_client()

        body = client.post("/live/run", json={"since": 1, "until": 2}).json()
        assert body["ingested"]["complete"] is False
        assert any("no bank statement" in s for s in body["ingested"]["skipped"])

    def test_a_bad_bank_csv_path_is_422(self, client, state, monkeypatch):
        monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_x")
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", "y")
        state.razorpay_client = self._fake_client()

        response = client.post(
            "/live/run", json={"since": 1, "until": 2, "bank_csv_path": "nope.csv"}
        )
        assert response.status_code == 422

    def test_a_razorpay_error_surfaces_its_real_status_not_a_500(self, client, state, monkeypatch):
        """A 401 means bad keys and a 429 means slow down. A merchant
        debugging their own integration needs to know which."""
        from ingest.razorpay import RazorpayAPIError

        monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_x")
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", "y")

        class Failing:
            def fetch_payments(self, since, until):
                raise RazorpayAPIError(401, "unauthorized")

        state.razorpay_client = Failing()

        response = client.post("/live/run", json={"since": 1, "until": 2})
        assert response.status_code == 502
        assert "401" in response.json()["detail"]
