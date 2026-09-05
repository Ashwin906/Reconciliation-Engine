# Reconciliation Engine

**An automated, mathematically rigorous proof engine for merchant payment reconciliation.**

Built for the Razorpay Buildathon (Track 04: AI Finance Controller).

[![Status](https://img.shields.io/badge/status-active-success.svg)](#)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](#)

---

## Overview

For a merchant, proving that the bulk settlement deposited into their bank account perfectly matches the individual orders they sold is a complex, opaque process. Legitimate deductions like payment gateway fees, GST, refunds, and chargebacks obscure the math.

The Reconciliation Engine solves this by automatically rebuilding the entire financial journey. It connects a merchant's sales, Razorpay's captured payments, batch settlements, and the final bank credit lines to prove that every single rupee is accounted for.

## Key Features

- **Tiered Deterministic Matching:** Isolates noise from actual financial variance.
  - *Tier 1:* Exact UTR (Unique Transaction Reference) matching.
  - *Tier 2:* Mathematical reconstruction of bulk settlements from component fees and deductions.
  - *Tier 3:* Fuzzy logic to resolve identity noise (e.g., truncated bank narrations), enforcing strict rules that never bridge a material financial gap.
- **Propose and Verify AI Architecture (Tier 4):** Utilizes an LLM to investigate the remaining unexplained edge cases. The AI acts solely as an investigator to propose solutions, which are then passed through a strict, deterministic mathematical verifier before any action is taken. This eliminates the risk of AI hallucination in financial records.
- **Conservation Proofs:** The system sums every rupee collected against every possible outflow, closing to exactly ₹0.00 as a proof of completeness.
- **Rolling Reserve Ledger:** Accurately models and tracks funds withheld in one period and released in another, preventing false-positive shortfall alerts.
- **"Watch It Happen" Simulation Dashboard:** A live, interactive environment that generates realistic financial traffic and intentional adversaries (e.g., dropped fees, rewritten UTRs) to test the pipeline against guaranteed truth data.

## Architecture

The engine is built with a clear separation of concerns, ensuring that matching logic and verification logic share no code:

- `core/`: Fundamental types (Money in integer paise), calendar utilities, and the Proof contract.
- `ingest/`: Adapters for Razorpay API payloads and Bank CSV statements.
- `match/`: The deterministic tiered matching pipeline (T0 -> T1 -> T2 -> T3).
- `verify/`: Independent re-derivation and verification engine.
- `agent/`: LLM-backed (Groq) investigation for unresolved residue.
- `gate/`: Deterministic decision engine (Auto-apply, Hold for Human, or Reject).
- `sim/`: Scenario catalog and defect injectors for the live dashboard.
- `api/` & `web/`: FastAPI backend and React/Vite frontend dashboard.

## Quick Start

### Using Docker (Recommended)

The easiest way to get the Reconciliation Engine running is via Docker Compose:

```bash
docker compose up
```

- **Backend / API:** `http://localhost:8000` (Interactive docs at `/docs`)
- **Frontend Dashboard:** `http://localhost:5173`

### Manual Setup

If you prefer to run the services directly on your machine:

1. **Install dependencies and start the backend:**
   ```bash
   pip install .
   uvicorn api.main:app --reload --port 8000
   ```

2. **Start the frontend:**
   ```bash
   cd web
   npm install
   npm run dev
   ```

*Note: The application starts empty by design. Open the dashboard at `http://localhost:5173`, navigate to **Watch it happen**, and run a simulation scenario to populate the system with live, traceable data.*

## Documentation

For deep dives into the engineering decisions and build process, please refer to:
- **[DOMAIN_KNOWLEDGE.md](DOMAIN_KNOWLEDGE.md)**: Background on payment aggregation and reconciliation.
- **[DECISIONS.md](DECISIONS.md)**: Rationale behind non-obvious architecture and design choices.
- **[FAILURES.md](FAILURES.md)**: A log of real bugs caught during development and how they shaped the final system.

## License

MIT License
