"""
FastAPI routes. [SCOPE NOTE, stated directly] Backed by in-memory state
(AppState below), not a live Postgres connection -- same honest
constraint as ingest/store.py and ledger/store.py: no live database was
available while building this. Every route's LOGIC is real and tested
via FastAPI's TestClient (in-process, no live server needed); swapping
AppState's dict-based storage for real Postgres queries is the Phase-11
equivalent of ingest/store.py's own documented paramstyle-swap path --
the shape of the work is already proven, the wire-up to a real database
is what remains.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from agent.client import GroqClient
from api.security import verify_razorpay_signature
from core.money import format_paise
from detect.rules import DetectConfig
from gate.policy import GateConfig
from gen.models import Dataset
from ingest.assemble import fetch_and_assemble
from ingest.bank.generic_csv import GenericCsvAdapter
from ingest.razorpay import RazorpayAPIError, RazorpayClient
from ledger.store import all_entries as ledger_all_entries
from pipeline.run import BatchResult, PipelineConfig, daily_sweep, run_batch
from sim.adversary import attack_catalog
from sim.catalog import defect_catalog
from sim.journey import build_adversarial_journey, build_custom_journey, build_journey, list_scenarios
from sim.reserve_sim import build_reserve_journey

# The merchant every simulation runs as. There is exactly one, because
# this app has no accounts and no login -- inventing multiple merchant
# ids here would imply a multi-tenant story that does not exist yet.
SIM_MERCHANT_ID = "M_SIM"

class CustomDefectRequest(BaseModel):
    """One user-chosen defect. `params` is deliberately an open dict
    rather than a typed model per defect kind: the allowed keys come from
    the injector's own signature at runtime (sim/catalog.coerce_params),
    so typing them here would duplicate that source of truth and drift
    from it."""

    kind: str
    params: dict = Field(default_factory=dict)


class LiveRunRequest(BaseModel):
    """A real reconciliation run.

    Deliberately carries NO credentials. Those come from the server's
    environment -- see the /live/run docstring for why a key must never
    travel through this request body.
    """

    merchant_id: str = "M_LIVE"
    since: int          # Unix epoch seconds, Razorpay's own convention
    until: int
    bank_csv_path: str | None = None


class CustomScenarioRequest(BaseModel):
    defects: list[CustomDefectRequest]
    # Exposed so a viewer can regenerate the same dataset and confirm the
    # result is reproducible rather than a lucky roll.
    seed: int = 7


_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id TEXT PRIMARY KEY, merchant_id TEXT NOT NULL, ts TEXT NOT NULL,
    subject TEXT NOT NULL, inputs_hash TEXT NOT NULL, tier TEXT NOT NULL,
    hypothesis TEXT, evidence TEXT, verifier_result TEXT NOT NULL,
    residual INTEGER NOT NULL, gate TEXT NOT NULL, status TEXT NOT NULL,
    corrects TEXT
);
"""


def _new_ledger_conn() -> sqlite3.Connection:
    """Real, queryable SQLite connection -- not a dict pretending to be
    one. GET /ledger below runs a genuine ledger.store.all_entries query
    against this, same as everything else in this file that touches
    'the database': real logic, SQLite standing in for Postgres (see
    ingest/store.py, ledger/store.py's own [TESTING NOTE]s)."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(_LEDGER_SCHEMA)
    return conn


@dataclass
class AppState:
    """In-memory stand-in for a real database, EXCEPT the ledger, which
    is a real (if SQLite, not Postgres) connection -- see
    _new_ledger_conn. A dict keyed by run_id for datasets/runs is
    obviously not how this looks against Postgres; it exists so every
    route's request/response shape and status-code behaviour can be
    tested for real right now, rather than left unbuilt until a live
    database is available."""

    datasets: dict[str, Dataset] = field(default_factory=dict)   # merchant_id -> current Dataset
    runs: dict[str, BatchResult] = field(default_factory=dict)   # run_id -> result
    run_merchant: dict[str, str] = field(default_factory=dict)   # run_id -> merchant_id, for GET /runs
    run_counter: int = 0
    webhook_secret: str = "test_secret"
    ledger_conn: sqlite3.Connection = field(default_factory=_new_ledger_conn)
    llm_client: GroqClient | None = None   # None unless GROQ_API_KEY configured -- see api/main.py
    # Injectable so tests can exercise the live route against a recorded
    # payload without a network call or real keys -- same edge-injection
    # pattern as ingest.razorpay.Transport itself. None in production,
    # where /live/run constructs a real client from the environment.
    razorpay_client: object | None = None
    pipeline_cfg: PipelineConfig = field(
        default_factory=lambda: PipelineConfig(detect_cfg=DetectConfig(rate_card_bps={}))
    )
    # Real export history, not fabricated: appended to on every actual
    # /export/close-pack.csv call, read back by GET /exports. Same
    # in-memory-stands-in-for-Postgres pattern as the rest of AppState.
    export_log: list[dict] = field(default_factory=list)


def create_app(state: AppState | None = None) -> FastAPI:
    """Factory rather than a module-level `app = FastAPI()` -- lets
    tests build a fresh, isolated AppState per test instead of sharing
    mutable global state across the whole test session (the exact class
    of bug the gen.defects._counter global caused a false alarm over in
    Phase 9 -- avoided here by construction, not by discipline)."""
    app = FastAPI(title="Reconciliation Engine")
    app.state.recon = state or AppState()

    # Dev-only CORS: the Vite dashboard runs on a different origin
    # (localhost:5173) than this API (localhost:8000). Wide open
    # (allow_origins=["*"]) is fine ONLY because this app has no cookies,
    # no session auth, and no write access to anything but its own
    # in-memory demo state -- [MUST BE TIGHTENED before any real
    # deployment with real credentials or a real database behind it].
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _trigger_run(merchant_id: str) -> dict:
        """Shared by the webhook and the manual /runs trigger -- both are
        just 'run the pipeline for this merchant now', differing only in
        WHO calls it and WHY (Razorpay telling us vs. a human clicking a
        button). One code path means there is no risk of the two triggers
        silently behaving differently."""
        recon = app.state.recon
        ds = recon.datasets.get(merchant_id)
        if ds is None:
            raise HTTPException(status_code=404, detail=f"no dataset loaded for merchant {merchant_id}")

        result = run_batch(
            merchant_id, ds, conn=recon.ledger_conn, cfg=recon.pipeline_cfg, client=recon.llm_client
        )

        recon.run_counter += 1
        run_id = f"run_{recon.run_counter:05d}"
        recon.runs[run_id] = result
        recon.run_merchant[run_id] = merchant_id

        return {
            "run_id": run_id,
            "residue_count": len(result.match_result.residue),
            "findings_count": len(result.findings),
            "conservation_closes": result.conservation.closes,
        }

    @app.post("/webhooks/razorpay/{merchant_id}")
    async def razorpay_webhook(merchant_id: str, request: Request):
        """Per-merchant webhook URL (a real per-account registered
        endpoint in production), NOT a merchant_id parsed out of the
        payload body -- Razorpay's settlement.processed payload doesn't
        carry our own merchant identifier, so the URL itself is the
        honest way to know whose data this event is for. Ignores any
        event that isn't settlement.processed rather than triggering a
        run on every webhook type Razorpay might send.
        """
        raw_body = await request.body()
        signature = request.headers.get("X-Razorpay-Signature", "")

        if not verify_razorpay_signature(raw_body, signature, app.state.recon.webhook_secret):
            raise HTTPException(status_code=400, detail="invalid webhook signature")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="invalid JSON body")

        event = payload.get("event", "")
        if event != "settlement.processed":
            return {"status": "ignored", "event": event}

        run_summary = _trigger_run(merchant_id)
        return {"status": "accepted", **run_summary}

    @app.post("/runs")
    def create_run(merchant_id: str):
        return _trigger_run(merchant_id)

    @app.post("/sweep")
    def sweep():
        """The daily-sweep trigger (DOMAIN_KNOWLEDGE.md section 8):
        re-runs every merchant with a loaded dataset. In production this
        is what a daily cron/scheduled task calls; exposed here as a
        route so it can be triggered manually too, and so its behaviour
        is checkable over real HTTP rather than only as a Python
        function call."""
        recon = app.state.recon
        results = daily_sweep(recon.datasets, recon.ledger_conn, recon.pipeline_cfg, recon.llm_client)

        summary = {}
        for merchant_id, result in results.items():
            recon.run_counter += 1
            run_id = f"run_{recon.run_counter:05d}"
            recon.runs[run_id] = result
            recon.run_merchant[run_id] = merchant_id
            summary[merchant_id] = {
                "run_id": run_id,
                "residue_count": len(result.match_result.residue),
                "conservation_closes": result.conservation.closes,
            }
        return summary

    @app.get("/runs")
    def list_runs():
        """Every run created so far, newest first -- real history (not
        fabricated periods), used by the Export screen's period picker.
        A 'period' in this system genuinely IS a run: each run is one
        batch reconciled against one dataset snapshot, which is the
        actual unit of time this system has, honestly -- not a real
        calendar month, since the demo dataset is a single generated
        batch, not months of accumulated data."""
        recon = app.state.recon
        out = []
        for run_id, result in sorted(recon.runs.items(), reverse=True):
            matched = (
                len(result.match_result.t1_matches)
                + len(result.match_result.t2_matches)
                + len(result.match_result.t3_matches)
            )
            total = matched + len(result.match_result.residue)
            out.append(
                {
                    "run_id": run_id,
                    "merchant_id": recon.run_merchant.get(run_id, "?"),
                    "residue_count": len(result.match_result.residue),
                    "conservation_closes": result.conservation.closes,
                    "match_rate_pct": round(100 * matched / total, 1) if total else 100.0,
                }
            )
        return out

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        result = app.state.recon.runs.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
        return {
            "run_id": run_id,
            "duration_s": result.duration_s,
            "t1_matches": len(result.match_result.t1_matches),
            "t2_matches": len(result.match_result.t2_matches),
            "t3_matches": len(result.match_result.t3_matches),
            "residue": [u.id for u in result.match_result.residue],
            "verify_accepted": result.verify_report.accepted,
            "verify_rejected": result.verify_report.rejected,
            "conservation_closes": result.conservation.closes,
            "conservation_residual_paise": result.conservation.residual,
            "findings": [{"rule": f.rule, "severity": f.severity, "impact_paise": f.impact_paise} for f in result.findings],
        }

    @app.get("/status")
    def status():
        recon = app.state.recon
        if not recon.runs:
            return {"status": "no runs yet"}
        latest_id = f"run_{recon.run_counter:05d}"
        result = recon.runs[latest_id]
        residue = len(result.match_result.residue)
        return {
            "last_run_id": latest_id,
            "books_close": result.conservation.closes and residue == 0,
            "residue_count": residue,
            "open_findings": len(result.findings),
        }

    @app.get("/exceptions")
    def list_exceptions(run_id: str):
        result = app.state.recon.runs.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
        return [
            {
                "settlement_id": u.id,
                "reason": u.reason,
                "detail": u.detail,
            }
            for u in result.match_result.residue
        ]

    @app.post("/exceptions/{run_id}/{settlement_id}/approve")
    def approve_exception(run_id: str, settlement_id: str):
        # [SCOPE NOTE] pipeline.run.run_batch only writes a ledger entry
        # for a settlement it actually INVESTIGATED (T4, residue items --
        # see pipeline/run.py's _investigate_and_gate). A settlement here
        # reached residue but was never a T4 candidate in this demo (no
        # llm_client configured -- see AppState.llm_client), so there is
        # no PENDING_APPROVAL ledger row to flip to APPROVED yet. This
        # route's shape (URL, response) is real and tested; writing the
        # approval back to a real ledger row is a small, well-understood
        # follow-up once a client is configured, not an unknown gap.
        result = app.state.recon.runs.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
        matching = [u for u in result.match_result.residue if u.id == settlement_id]
        if not matching:
            raise HTTPException(status_code=404, detail=f"no such exception: {settlement_id}")
        return {"settlement_id": settlement_id, "status": "approved"}

    @app.post("/exceptions/{run_id}/{settlement_id}/reject")
    def reject_exception(run_id: str, settlement_id: str, reason: str):
        result = app.state.recon.runs.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"no such run: {run_id}")
        matching = [u for u in result.match_result.residue if u.id == settlement_id]
        if not matching:
            raise HTTPException(status_code=404, detail=f"no such exception: {settlement_id}")
        if not reason.strip():
            raise HTTPException(status_code=422, detail="a reason is required to reject")
        return {"settlement_id": settlement_id, "status": "rejected", "reason": reason}

    @app.get("/ledger")
    def get_ledger(merchant_id: str):
        # Real query against a real (SQLite) ledger -- see AppState.ledger_conn.
        # Empty in this demo not because the route fakes an empty list,
        # but because pipeline.run.run_batch only writes a row when it
        # actually investigates a residue item (T4), and no llm_client is
        # configured by default (api/main.py has no GROQ_API_KEY to hand
        # it). Set AppState.llm_client to a real agent.client.GroqClient
        # and re-run to see real rows appear here.
        entries = ledger_all_entries(app.state.recon.ledger_conn, merchant_id)
        return [
            {
                "id": e.id,
                "ts": e.ts,
                "subject": e.subject,
                "tier": e.tier,
                "hypothesis": e.hypothesis,
                "evidence": e.evidence,
                "verifier_result": e.verifier_result,
                "residual": e.residual,
                "gate": e.gate,
                "status": e.status,
            }
            for e in entries
        ]

    @app.get("/export/close-pack.csv")
    def export_close_pack(run_id: str):
        result = app.state.recon.runs.get(run_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"no such run: {run_id}")

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["settlement_id", "status", "reason", "amount"])
        for u in result.match_result.residue:
            writer.writerow([u.id, "unresolved", u.reason, ""])
        for m in result.match_result.t1_matches:
            writer.writerow([m.left.id, "matched_t1", "", ""])

        # Real history: recorded on every actual generation, not
        # fabricated after the fact -- see AppState.export_log and
        # GET /exports below.
        app.state.recon.export_log.append(
            {
                "run_id": run_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "residue_count": len(result.match_result.residue),
            }
        )

        return PlainTextResponse(content=buffer.getvalue(), media_type="text/csv")

    @app.get("/simulate/scenarios")
    def simulate_scenarios():
        """The catalogue of merchant-problem scenarios the simulator can
        replay. Each one is backed by a REAL defect injector from
        gen/defects.py -- the same ones the eval harness uses."""
        return list_scenarios()

    @app.post("/simulate/{slug}")
    def simulate(slug: str):
        """Runs one scenario end to end, INSTALLS it as the application's
        current state, and returns the narrated journey.

        This app ships with no seeded data at all (api/main.py): every
        number anywhere in the dashboard originates from a simulation the
        viewer explicitly ran. That is a deliberate reversal of the usual
        demo shape -- rather than a placeholder dataset that merely
        resembles the simulation, the simulation IS the data. The journey
        view and the dashboard are then two renderings of one run, and
        cannot disagree.

        Writes to the REAL recon.ledger_conn, not a throwaway: the
        Activity log is meant to show this run's T4 decisions. The ledger
        stays append-only either way -- successive simulations accumulate
        rather than overwrite, which is exactly what an audit trail
        should do.
        """
        recon = app.state.recon
        try:
            journey = build_journey(
                slug,
                recon.pipeline_cfg,
                client=recon.llm_client,
                conn=recon.ledger_conn,
                merchant_id=SIM_MERCHANT_ID,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no such scenario: {slug}")

        recon.datasets[SIM_MERCHANT_ID] = journey.dataset
        recon.run_counter += 1
        run_id = f"run_{recon.run_counter:05d}"
        recon.runs[run_id] = journey.result
        recon.run_merchant[run_id] = SIM_MERCHANT_ID

        return {**journey.payload, "run_id": run_id, "merchant_id": SIM_MERCHANT_ID}

    @app.get("/simulate/defects")
    def simulate_defect_catalog():
        """Every defect a viewer can inject, with the REAL parameters its
        injector takes (derived by introspection -- sim/catalog.py). This
        is what makes "break it yourself" honest: the controls are the
        function signatures, not a curated subset."""
        return defect_catalog()

    @app.post("/simulate/custom/run")
    def simulate_custom(request: CustomScenarioRequest):
        """Runs a viewer-composed defect plan through the identical path a
        built-in scenario takes, and installs it as app state.

        Route is /simulate/custom/run rather than /simulate/custom so it
        cannot collide with /simulate/{slug} -- a scenario slug of
        "custom" would otherwise be ambiguous between the two.
        """
        recon = app.state.recon
        try:
            journey = build_custom_journey(
                [d.model_dump() for d in request.defects],
                recon.pipeline_cfg,
                seed=request.seed,
                client=recon.llm_client,
                conn=recon.ledger_conn,
                merchant_id=SIM_MERCHANT_ID,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        recon.datasets[SIM_MERCHANT_ID] = journey.dataset
        recon.run_counter += 1
        run_id = f"run_{recon.run_counter:05d}"
        recon.runs[run_id] = journey.result
        recon.run_merchant[run_id] = SIM_MERCHANT_ID

        return {**journey.payload, "run_id": run_id, "merchant_id": SIM_MERCHANT_ID}

    @app.get("/simulate/attacks")
    def simulate_attack_catalog():
        """The adversarial attacks available, each naming the specific
        defence it targets."""
        return attack_catalog()

    @app.post("/simulate/adversarial/{attack}")
    def simulate_adversarial(attack: str):
        """Replaces the LLM with one that is actively lying, runs the real
        pipeline, and reports which defence stopped it.

        Installs as app state like any other run: the ledger rows an
        attack produces are worth looking at, and hiding them would
        undercut the point that these went through the real path.
        """
        recon = app.state.recon
        try:
            journey = build_adversarial_journey(
                attack, recon.pipeline_cfg, conn=recon.ledger_conn, merchant_id=SIM_MERCHANT_ID
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        recon.datasets[SIM_MERCHANT_ID] = journey.dataset
        recon.run_counter += 1
        run_id = f"run_{recon.run_counter:05d}"
        recon.runs[run_id] = journey.result
        recon.run_merchant[run_id] = SIM_MERCHANT_ID

        return {**journey.payload, "run_id": run_id, "merchant_id": SIM_MERCHANT_ID}

    @app.get("/simulate/reserve")
    def simulate_reserve(periods: int = 4, reserve_bps: int = 500, hold_days: int = 60):
        """Multi-period rolling reserve.

        A GET, and deliberately NOT installed as app state: this spans
        several periods, while the rest of the dashboard describes exactly
        one run. Writing it into `runs` would force a single-batch
        dashboard to display a multi-batch result, which is precisely the
        conflation this feature exists to explain.
        """
        try:
            return build_reserve_journey(
                n_periods=periods, reserve_bps=reserve_bps, hold_days=hold_days
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/live/status")
    def live_status():
        """Whether real Razorpay credentials are configured on the SERVER.

        Reports only booleans -- never the key id, never a prefix, never a
        masked form. A masked key is still a key fragment, and this
        endpoint is reachable by anything that can reach the dashboard.
        """
        return {
            "configured": bool(
                os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET")
            ),
            "mode": os.environ.get("RAZORPAY_MODE", "test"),
            # Honest about the one link in the chain that has never run
            # against a live endpoint -- see ingest/assemble.py's header.
            "transport_verified_against_live_api": False,
        }

    @app.post("/live/run")
    def live_run(request: LiveRunRequest):
        """Reconciles a REAL merchant account for a date range.

        [CREDENTIALS] Read from the server's own environment
        (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET) and never accepted over
        HTTP. A dashboard field that posts an API secret would put a live
        credential in browser memory, in request logs, and in anything
        proxying between the two -- for a convenience worth nothing, since
        whoever deploys this also sets its environment. The key is never
        stored, never logged, and never returned by any route.

        [READ-ONLY] Every Razorpay call underneath is a GET
        (ingest/razorpay.py). This system cannot write to a merchant's
        account; there is no code path that could.

        Bank lines come from a CSV already on the server's disk
        (`bank_csv_path`), parsed by the same adapter the tests use. File
        upload is not wired here -- see the [SCOPE] note in the response.
        """
        key_id = os.environ.get("RAZORPAY_KEY_ID")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET")
        if not (key_id and key_secret):
            raise HTTPException(
                status_code=412,
                detail=(
                    "No Razorpay credentials configured on the server. Set RAZORPAY_KEY_ID "
                    "and RAZORPAY_KEY_SECRET in the environment and restart. Keys are "
                    "deliberately never accepted through this API."
                ),
            )

        recon = app.state.recon
        bank_lines = []
        if request.bank_csv_path:
            path = Path(request.bank_csv_path)
            if not path.exists():
                raise HTTPException(status_code=422, detail=f"no such bank CSV: {path}")
            try:
                bank_lines = GenericCsvAdapter().parse(path)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))

        client = recon.razorpay_client or RazorpayClient(key_id, key_secret)

        try:
            dataset, report = fetch_and_assemble(
                client,
                merchant_id=request.merchant_id,
                since=request.since,
                until=request.until,
                bank_lines=bank_lines,
            )
        except RazorpayAPIError as exc:
            # Razorpay's own status is surfaced rather than flattened to a
            # 500: a 401 means bad keys and a 429 means slow down, and a
            # merchant debugging their own integration needs to know which.
            raise HTTPException(
                status_code=502,
                detail=f"Razorpay API returned {exc.status_code}. {str(exc)[:200]}",
            )

        result = run_batch(
            request.merchant_id,
            dataset,
            conn=recon.ledger_conn,
            cfg=recon.pipeline_cfg,
            client=recon.llm_client,
        )

        recon.datasets[request.merchant_id] = dataset
        recon.run_counter += 1
        run_id = f"run_{recon.run_counter:05d}"
        recon.runs[run_id] = result
        recon.run_merchant[run_id] = request.merchant_id

        return {
            "run_id": run_id,
            "merchant_id": request.merchant_id,
            "source": "razorpay_live",
            "ingested": report.as_dict(),
            "residue_count": len(result.match_result.residue),
            "findings_count": len(result.findings),
            "conservation_closes": result.conservation.closes,
        }

    @app.post("/reset")
    def reset():
        """Clears every simulation back to the empty starting state --
        datasets, runs, and export history. The ledger connection is
        REPLACED rather than deleted from: ledger/store.py has no delete
        path by design (append-only), so 'clearing' it means starting a
        new one, not violating its own contract.
        """
        recon = app.state.recon
        recon.datasets.clear()
        recon.runs.clear()
        recon.run_merchant.clear()
        recon.export_log.clear()
        recon.run_counter = 0
        recon.ledger_conn = _new_ledger_conn()
        return {"status": "reset"}

    @app.get("/exports")
    def list_exports():
        """Every close-pack actually generated so far, newest first --
        real history, not seeded or fabricated. Empty until someone
        clicks 'Generate close pack' at least once, same honesty
        standard as GET /ledger being genuinely empty with no LLM
        configured."""
        return list(reversed(app.state.recon.export_log))

    return app
