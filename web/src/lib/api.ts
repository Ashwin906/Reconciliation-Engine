// Typed client for the FastAPI backend (api/routes.py). Real fetch
// calls against a real running server -- no mock data. Backend runs on
// :8000 in dev (see .claude/launch.json), this dashboard on :5173.

const API_BASE = import.meta.env.VITE_API_BASE ?? (import.meta.env.DEV ? "http://localhost:8000" : "");

export interface Finding {
  rule: string;
  severity: "P0" | "P1" | "P2" | "P3";
  impact_paise: number;
}

export interface RunSummary {
  run_id: string;
  residue_count: number;
  findings_count: number;
  conservation_closes: boolean;
}

export interface RunDetail {
  run_id: string;
  duration_s: number;
  t1_matches: number;
  t2_matches: number;
  t3_matches: number;
  residue: string[];
  verify_accepted: number;
  verify_rejected: number;
  conservation_closes: boolean;
  conservation_residual_paise: number;
  findings: Finding[];
}

export interface StatusSummary {
  status?: string;
  last_run_id?: string;
  books_close?: boolean;
  residue_count?: number;
  open_findings?: number;
}

export interface ExceptionItem {
  settlement_id: string;
  reason: string;
  detail: Record<string, unknown>;
}

export interface LedgerRow {
  id: string;
  ts: string;
  subject: string;
  tier: string;
  hypothesis: string | null;
  evidence: Record<string, unknown> | null;
  verifier_result: "ACCEPT" | "REJECT" | "UNVERIFIABLE";
  residual: number;
  gate: "AUTO" | "HUMAN" | "REJECT";
  status: string;
}

export interface RunListItem {
  run_id: string;
  merchant_id: string;
  residue_count: number;
  conservation_closes: boolean;
  match_rate_pct: number;
}

export interface ExportHistoryItem {
  run_id: string;
  generated_at: string;
  residue_count: number;
}

export interface SimScenario {
  slug: string;
  name: string;
  merchant_story: string;
  defect: string | null;
  seed: number;
}

export interface SimStage {
  key: string;
  title: string;
  detail: string;
  status: "ok" | "warn" | "fail" | "info";
  facts: Record<string, unknown>;
}

export interface DefectParam {
  name: string;
  label: string;
  required: boolean;
  default: number | string;
  min: number;
  max: number;
  step: number;
  unit: string;
  choices: string[] | null;
}

export interface DefectKind {
  kind: string;
  label: string;
  description: string;
  costs_money: boolean;
  params: DefectParam[];
}

export interface MoneyFlow {
  collected_paise: number;
  reached_bank_paise: number;
  withheld_paise: number;
  residual_paise: number;
  closes: boolean;
  deductions: { label: string; paise: number }[];
}

export interface Comparison {
  naive: {
    matched: number;
    unresolved: number;
    match_rate_pct: number;
    flagged_missing_paise: number;
    blind_spot_paise: number;
    items: { id: string; reason: string; amount_paise: number }[];
    finds_fee_leaks: boolean;
    proves_books_balance: boolean;
    verifies_independently: boolean;
  };
  real: {
    matched: number;
    unresolved: number;
    match_rate_pct: number;
    t1: number;
    t2: number;
    t3: number;
    findings: Finding[];
    conservation_closes: boolean;
    finds_fee_leaks: boolean;
    proves_books_balance: boolean;
    verifies_independently: boolean;
  };
  difference: {
    false_alarms_avoided: number;
    money_falsely_flagged_paise: number;
    leak_money_naive_would_miss_paise: number;
  };
}

export interface PlantedDefect {
  kind: string;
  subject_id: string;
  impact_paise: number;
  should_be_detected: boolean;
  note: string;
}

export interface AttackKind {
  attack: string;
  label: string;
  attempt: string;
  defence: string;
}

export interface AdversarialReport {
  attack: string;
  attempts: {
    subject_id: string;
    attack: string;
    hypothesis: string;
    evidence_ids: string[];
    claimed_paise: number;
    true_paise: number;
    confidence: number;
  }[];
  investigated: number;
  auto_applied: number;
  rejected_by_gate: number;
  sent_to_human: number;
  stopped_before_gate: number;
  money_moved_without_a_human: number;
  held: boolean;
  defence_that_caught_it: string;
}

export interface ReservePeriod {
  label: string;
  period_start: string;
  period_end: string;
  settled_gross: number;
  held: number;
  released: number;
  net_delta: number;
  closing_balance: number;
  overdue_count: number;
  reserve_delta_paise: number;
  conservation_without_ledger: { residual_paise: number; closes: boolean };
  conservation_with_ledger: { residual_paise: number; closes: boolean };
}

export interface ReserveJourney {
  config: {
    n_periods: number;
    reserve_bps: number;
    reserve_pct: number;
    hold_days: number;
    seed: number;
  };
  periods: ReservePeriod[];
  totals: {
    total_held_paise: number;
    total_released_paise: number;
    outstanding_balance_paise: number;
    overdue_count: number;
  };
  holds: {
    hold_id: string;
    settlement_id: string;
    amount_paise: number;
    held_on: string;
    release_due_on: string;
    released_on: string | null;
    outstanding: boolean;
  }[];
  verdict: {
    any_release_happened: boolean;
    release_period_labels: string[];
    single_period_view_would_mislead: boolean;
    explanation: string;
  };
}

export interface LiveStatus {
  configured: boolean;
  mode: string;
  transport_verified_against_live_api: boolean;
}

export interface LiveRunResult {
  run_id: string;
  merchant_id: string;
  source: string;
  ingested: {
    payments: number;
    settlements: number;
    refunds: number;
    disputes: number;
    bank_lines: number;
    skipped_count: number;
    skipped: string[];
    complete: boolean;
  };
  residue_count: number;
  findings_count: number;
  conservation_closes: boolean;
}

export interface SimJourney {
  run_id: string;
  merchant_id: string;
  adversarial?: AdversarialReport;
  scenario: SimScenario;
  stages: SimStage[];
  planted_defect: { kind: string; subject_id: string; expected_resolution: string } | null;
  planted_defects: PlantedDefect[];
  money_flow: MoneyFlow;
  comparison: Comparison;
  outcome: {
    matched: number;
    residue: number;
    match_rate_pct: number;
    conservation_closes: boolean;
    conservation_residual_paise: number;
    findings: Finding[];
    duration_s: number;
  };
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${init?.method ?? "GET"} ${path} -> ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export const client = {
  createRun: (merchantId: string) =>
    api<RunSummary>(`/runs?merchant_id=${encodeURIComponent(merchantId)}`, { method: "POST" }),

  getRun: (runId: string) => api<RunDetail>(`/runs/${runId}`),

  getStatus: () => api<StatusSummary>("/status"),

  listExceptions: (runId: string) =>
    api<ExceptionItem[]>(`/exceptions?run_id=${encodeURIComponent(runId)}`),

  approveException: (runId: string, settlementId: string) =>
    api<{ status: string }>(`/exceptions/${runId}/${settlementId}/approve`, { method: "POST" }),

  rejectException: (runId: string, settlementId: string, reason: string) =>
    api<{ status: string }>(
      `/exceptions/${runId}/${settlementId}/reject?reason=${encodeURIComponent(reason)}`,
      { method: "POST" }
    ),

  getLedger: (merchantId: string) =>
    api<LedgerRow[]>(`/ledger?merchant_id=${encodeURIComponent(merchantId)}`),

  listRuns: () => api<RunListItem[]>("/runs"),

  listExports: () => api<ExportHistoryItem[]>("/exports"),

  exportCloseCsvUrl: (runId: string) => `${API_BASE}/export/close-pack.csv?run_id=${runId}`,

  listScenarios: () => api<SimScenario[]>("/simulate/scenarios"),

  simulate: (slug: string) =>
    api<SimJourney>(`/simulate/${encodeURIComponent(slug)}`, { method: "POST" }),

  reset: () => api<{ status: string }>("/reset", { method: "POST" }),

  listDefects: () => api<DefectKind[]>("/simulate/defects"),

  listAttacks: () => api<AttackKind[]>("/simulate/attacks"),

  liveStatus: () => api<LiveStatus>("/live/status"),

  // No credentials here on purpose -- the server reads them from its own
  // environment. See api/routes.py's /live/run docstring.
  liveRun: (since: number, until: number, bankCsvPath?: string, merchantId = "M_LIVE") =>
    api<LiveRunResult>("/live/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        merchant_id: merchantId,
        since,
        until,
        bank_csv_path: bankCsvPath || null,
      }),
    }),

  simulateAdversarial: (attack: string) =>
    api<SimJourney>(`/simulate/adversarial/${encodeURIComponent(attack)}`, { method: "POST" }),

  simulateReserve: (periods = 4) => api<ReserveJourney>(`/simulate/reserve?periods=${periods}`),

  simulateCustom: (defects: { kind: string; params: Record<string, number | string> }[], seed = 7) =>
    api<SimJourney>("/simulate/custom/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ defects, seed }),
    }),
};
