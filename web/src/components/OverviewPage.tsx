import { useEffect, useState } from "react";
import { CheckCircle2, AlertTriangle, ArrowRight } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";
import { client, type RunDetail, type RunListItem, type ExceptionItem } from "@/lib/api";
import { formatPaise } from "@/lib/money";
import { cn } from "@/lib/utils";
import { reasonLabel } from "@/lib/labels";

export function OverviewPage({ runId, onNavigate }: { runId: string; onNavigate: (tab: string) => void }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [history, setHistory] = useState<RunListItem[] | null>(null);
  const [exceptions, setExceptions] = useState<ExceptionItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    client.getRun(runId).then(setRun).catch((e) => setError(String(e)));
    client.listRuns().then(setHistory).catch((e) => setError(String(e)));
    client.listExceptions(runId).then(setExceptions).catch((e) => setError(String(e)));
  }, [runId]);

  if (error) return <div className="p-8 text-destructive text-sm">{error}</div>;
  if (!run || !exceptions) return <div className="p-8 text-muted-foreground">Loading…</div>;

  const allClear = run.residue.length === 0 && run.conservation_closes;
  const matched = run.t1_matches + run.t2_matches + run.t3_matches;
  const total = matched + run.residue.length;
  const matchRate = total > 0 ? (matched / total) * 100 : 100;
  const preview = [...exceptions]
    .sort((a, b) => (impact(b) ?? 0) - (impact(a) ?? 0))
    .slice(0, 3);

  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

  // Two DIFFERENT problems, previously conflated into one sentence that
  // could read "You're missing ₹0.00":
  //   - a conservation gap: money genuinely unaccounted for; and
  //   - residue: items that failed to match, while the books still balance.
  // A chargeback run is exactly the second case -- the money is all
  // present and traceable, one settlement just cannot be tied to its bank
  // line automatically. Saying "you're missing ₹0.00" there is both
  // alarming and false, so the two cases now get their own wording, and
  // the at-risk figure comes from the exceptions themselves rather than
  // from a conservation residual that is legitimately zero.
  const conservationGap = Math.abs(run.conservation_residual_paise);
  const atRisk = exceptions.reduce((sum, e) => sum + (impact(e) ?? 0), 0);
  const itemWord = `${run.residue.length} item${run.residue.length === 1 ? "" : "s"}`;

  const headline = allClear
    ? "You're all set. Nothing needs your attention."
    : conservationGap > 0
      ? `You're missing ₹${formatPaise(conservationGap)}.`
      : atRisk > 0
        ? `₹${formatPaise(atRisk)} needs checking.`
        : `${itemWord} need${run.residue.length === 1 ? "s" : ""} a look.`;

  const subline = allClear
    ? "Every payment from your last check matched up correctly."
    : conservationGap > 0
      ? `${itemWord} couldn't be explained, and your books don't balance yet.`
      : `Your books balance — but ${itemWord} couldn't be matched automatically, so someone should confirm ${run.residue.length === 1 ? "it" : "them"}.`;

  return (
    <div className="max-w-4xl mx-auto px-5 py-6 lg:px-8 lg:py-8 space-y-6">
      <div className="space-y-1">
        <h1 className="text-xl font-bold tracking-tight">{greeting}.</h1>
        <p className="text-sm text-muted-foreground">Here's where your money stands today.</p>
      </div>

      {/* The one thing a merchant with no finance team actually needs to
          know first -- an unambiguous plain-language answer, not a metric
          they have to interpret. Loudest element on the page on purpose. */}
      <div
        className={cn(
          "flex flex-col sm:flex-row sm:items-center gap-4 rounded-lg border-2 px-5 py-5",
          allClear ? "bg-success/10 border-success/40" : "bg-warning/10 border-warning/40"
        )}
      >
        <div className="flex items-start gap-3 flex-1 min-w-0">
          {allClear ? (
            <CheckCircle2 className="h-8 w-8 text-success shrink-0" aria-hidden="true" />
          ) : (
            <AlertTriangle className="h-8 w-8 text-warning shrink-0" aria-hidden="true" />
          )}
          <div className="min-w-0">
            <p className={cn("text-lg font-semibold", allClear ? "text-success" : "text-warning")}>
              {headline}
            </p>
            <p className="text-sm text-muted-foreground">{subline}</p>
          </div>
        </div>
        {!allClear && (
          <button
            onClick={() => onNavigate("exceptions")}
            className={cn(buttonVariants(), "shrink-0 w-full sm:w-auto")}
          >
            Review now
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
      </div>

      {/* plain-language tiles, not raw pipeline terminology */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <MetricTile label="Matched automatically" value={`${matchRate.toFixed(0)}%`} />
        <MetricTile label="Items that need you" value={`${run.residue.length}`} warn={run.residue.length > 0} />
        <MetricTile label="Checked and confirmed" value={`${run.verify_accepted}`} />
      </div>

      {history && history.length > 1 && <TrendCard history={history} />}

      {preview.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">What needs your attention</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {preview.map((exc) => (
              <div
                key={exc.settlement_id}
                className="flex items-center justify-between gap-3 text-sm border-b last:border-0 pb-2 last:pb-0"
              >
                <div className="min-w-0">
                  <p className="truncate">{reasonLabel(exc.reason)}</p>
                  <p className="text-xs text-muted-foreground font-mono truncate">{exc.settlement_id}</p>
                </div>
                <span className="tabular-nums font-medium shrink-0">
                  {impact(exc) != null ? `₹${formatPaise(impact(exc)!)}` : "—"}
                </span>
              </div>
            ))}
            <button
              onClick={() => onNavigate("exceptions")}
              className={cn(buttonVariants({ variant: "outline", size: "sm" }), "mt-1")}
            >
              See all {exceptions.length}
              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function MetricTile({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className={cn("text-2xl font-semibold tabular-nums", warn && "text-warning")}>{value}</div>
        <div className="text-xs text-muted-foreground mt-1">{label}</div>
      </CardContent>
    </Card>
  );
}

// Real history from GET /runs, oldest to newest -- not a fabricated
// trend. With one run there is nothing to trend, so the caller hides
// this card entirely rather than drawing a single dot as if it means
// something.
function TrendCard({ history }: { history: RunListItem[] }) {
  const points = [...history].reverse();
  const w = 400;
  const h = 64;
  const step = points.length > 1 ? w / (points.length - 1) : 0;
  const path = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${i * step} ${h - (p.match_rate_pct / 100) * h}`)
    .join(" ");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">How you've been doing</CardTitle>
      </CardHeader>
      <CardContent>
        <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16" preserveAspectRatio="none" role="img" aria-label="Match rate across recent checks">
          <path d={path} fill="none" stroke="var(--chart-1)" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <p className="text-xs text-muted-foreground mt-1">
          Last {points.length} checks — {points[points.length - 1].match_rate_pct.toFixed(0)}% matched most recently.
        </p>
      </CardContent>
    </Card>
  );
}

function impact(exc: ExceptionItem): number | null {
  const d = exc.detail as Record<string, unknown>;
  for (const key of ["expected_amount", "gap", "expected"]) {
    const v = d[key];
    if (typeof v === "number") return Math.abs(v);
  }
  return null;
}
