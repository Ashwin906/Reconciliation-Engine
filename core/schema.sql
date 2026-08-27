-- [UNVERIFIED] Not yet executed against a live Postgres instance (Docker
-- daemon unavailable at time of writing). Written to spec, not yet proven
-- to run. Must be validated the first time Phase 2 (ingest/) needs a real
-- database -- do not assume this is bug-free SQL until then.
--
-- Reconciliation engine schema.
-- Every money column is BIGINT paise (see core/money.py, DECISIONS.md D-06).
-- Every table carries merchant_id (see DECISIONS.md D-20 — multi-tenant
-- from the first migration, even with one demo merchant).
-- ledger is APPEND-ONLY: no UPDATE, no DELETE (see DECISIONS.md D-16).

CREATE TABLE merchants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- rate_cards: per-merchant, per-method contracted fee rate, in basis points.
-- effective_from/to lets a rate change be recorded without deleting the
-- old rate — needed because fee_overcharge detection (Phase 8) compares
-- each transaction against the rate that was contracted AT THAT TIME.
CREATE TABLE rate_cards (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id     UUID NOT NULL REFERENCES merchants(id),
    method          TEXT NOT NULL,          -- 'upi' | 'card' | 'amex' | 'netbanking' | ...
    rate_bps        INTEGER NOT NULL,       -- 200 = 2.00%
    effective_from  DATE NOT NULL,
    effective_to    DATE,                   -- NULL = still in effect
    UNIQUE (merchant_id, method, effective_from)
);

CREATE TABLE orders (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id  UUID NOT NULL REFERENCES merchants(id),
    order_id     TEXT NOT NULL,             -- Razorpay-style: order_XXXX
    amount       BIGINT NOT NULL,           -- paise
    currency     TEXT NOT NULL DEFAULT 'INR',
    status       TEXT NOT NULL,
    invoice_no   TEXT,
    created_at   TIMESTAMPTZ NOT NULL,
    UNIQUE (merchant_id, order_id)
);

CREATE TABLE payments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id     UUID NOT NULL REFERENCES merchants(id),
    payment_id      TEXT NOT NULL,          -- pay_XXXX
    order_id        TEXT,                   -- may be null for stray/offline
    amount          BIGINT NOT NULL,        -- paise, captured amount
    method          TEXT NOT NULL,
    fee             BIGINT NOT NULL DEFAULT 0,
    tax             BIGINT NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,          -- created|authorized|captured|refunded|failed
    captured_at     TIMESTAMPTZ,
    settlement_id   TEXT,                   -- setl_XXXX, null until settled
    UNIQUE (merchant_id, payment_id)
);

CREATE TABLE refunds (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id  UUID NOT NULL REFERENCES merchants(id),
    refund_id    TEXT NOT NULL,             -- rfnd_XXXX
    payment_id   TEXT NOT NULL,
    amount       BIGINT NOT NULL,           -- paise
    created_at   TIMESTAMPTZ NOT NULL,
    UNIQUE (merchant_id, refund_id)
);

CREATE TABLE disputes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id  UUID NOT NULL REFERENCES merchants(id),
    dispute_id   TEXT NOT NULL,             -- disp_XXXX
    payment_id   TEXT NOT NULL,
    amount       BIGINT NOT NULL,           -- chargeback amount, paise
    fee          BIGINT NOT NULL DEFAULT 0, -- dispute fee, paise
    status       TEXT NOT NULL,             -- raised|won|lost|closed
    raised_at    TIMESTAMPTZ NOT NULL,
    UNIQUE (merchant_id, dispute_id)
);

CREATE TABLE settlements (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id    UUID NOT NULL REFERENCES merchants(id),
    settlement_id  TEXT NOT NULL,           -- setl_XXXX
    amount         BIGINT NOT NULL,         -- paise, what Razorpay says it sent
    utr            TEXT,
    created_at     TIMESTAMPTZ NOT NULL,
    UNIQUE (merchant_id, settlement_id)
);

CREATE TABLE bank_lines (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id  UUID NOT NULL REFERENCES merchants(id),
    line_id      TEXT NOT NULL,             -- our own generated id for this row
    txn_date     DATE NOT NULL,
    value_date   DATE,
    narration    TEXT NOT NULL,             -- free text, bank-specific
    ref_no       TEXT,                      -- often the UTR, sometimes truncated
    debit        BIGINT NOT NULL DEFAULT 0, -- paise
    credit       BIGINT NOT NULL DEFAULT 0, -- paise
    balance      BIGINT,                    -- paise, running balance if provided
    source_file  TEXT,                      -- which uploaded statement this came from
    UNIQUE (merchant_id, line_id)
);

-- ledger: append-only. No UPDATE, no DELETE, enforced by convention here
-- and by a Postgres trigger once ledger/store.py exists (Phase 7).
-- A correction is a NEW row that references the entry it corrects, never
-- an edit to the original.
CREATE TABLE ledger (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id      UUID NOT NULL REFERENCES merchants(id),
    ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject          TEXT NOT NULL,          -- e.g. a settlement_id or payment_id
    inputs_hash      TEXT NOT NULL,          -- sha256 of the raw data this decision saw
    tier             TEXT NOT NULL,          -- T0|T1|T2|T3|T4
    hypothesis       TEXT,                   -- set only for T4 (agent) entries
    evidence         JSONB,                  -- ids of supporting records
    verifier_result  TEXT NOT NULL,          -- ACCEPT|REJECT
    residual         BIGINT NOT NULL,        -- paise; must be 0 for ACCEPT
    gate             TEXT NOT NULL,          -- AUTO|HUMAN|REJECT
    status           TEXT NOT NULL,          -- PENDING_APPROVAL|APPROVED|REJECTED|APPLIED
    corrects          UUID REFERENCES ledger(id)  -- set if this row corrects a prior one
);

CREATE INDEX idx_payments_settlement ON payments (merchant_id, settlement_id);
CREATE INDEX idx_bank_lines_ref ON bank_lines (merchant_id, ref_no);
CREATE INDEX idx_ledger_subject ON ledger (merchant_id, subject);
