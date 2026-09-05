import { useEffect, useState } from "react";
import { CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { client, type RunDetail } from "@/lib/api";
import { formatPaise } from "@/lib/money";

export function StatusPage({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        setLoading(true);
        const detail = await client.getRun(runId);
        if (!cancelled) setRun(detail);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-8 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Running reconciliation…
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 text-destructive text-sm space-y-1">
        <p>Could not reach the backend at localhost:8000 — {error}</p>
        <p className="text-muted-foreground">
          Start it with <code className="font-mono">uvicorn api.main:app --reload</code>, then reload this page.
        </p>
      </div>
    );
  }

  if (!run) return null;

  const allClosed = run.residue.length === 0 && run.conservation_closes;
  const totalMatched = run.t1_matches + run.t2_matches + run.t3_matches;
  const matchRate = totalMatched > 0 ? (run.t1_matches + run.t3_matches) / (run.t1_matches + run.t3_matches + run.residue.length) : 1;

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      {/* status banner */}
      <div
        className={`flex flex-wrap items-center gap-3 rounded-md border px-4 py-3 ${
          allClosed
            ? "bg-success/10 border-success/30 text-success"
            : "bg-warning/10 border-warning/30 text-warning"
        }`}
      >
        {allClosed ? (
          <CheckCircle2 className="h-5 w-5 shrink-0" aria-hidden="true" />
        ) : (
          <AlertTriangle className="h-5 w-5 shrink-0" aria-hidden="true" />
        )}
        <span className="text-sm font-medium">
          {allClosed
            ? `Books closed. ₹0.00 residual. Run ${run.run_id}.`
            : `${run.residue.length} item(s) need attention. Conservation residual ₹${formatPaise(
                Math.abs(run.conservation_residual_paise)
              )}.`}
        </span>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Match rate" value={`${(matchRate * 100).toFixed(0)}%`} />
        <StatCard label="Proofs verified" value={`${run.verify_accepted}`} sub={run.verify_rejected > 0 ? `${run.verify_rejected} rejected` : undefined} />
        <StatCard label="Open exceptions" value={`${run.residue.length}`} tone={run.residue.length > 0 ? "warning" : "default"} />
        <StatCard label="Findings" value={`${run.findings.length}`} tone={run.findings.length > 0 ? "warning" : "default"} />
      </div>

      {/* tier breakdown */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Matching tiers</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm tabular-nums">
            <TierChip label="T1 exact" n={run.t1_matches} />
            <TierChip label="T2 internal check" n={run.t2_matches} />
            <TierChip label="T3 fuzzy" n={run.t3_matches} />
            <TierChip label="Unresolved" n={run.residue.length} warn={run.residue.length > 0} />
          </div>
        </CardContent>
      </Card>

      {/* findings */}
      {run.findings.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Findings</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {run.findings.map((f, i) => (
              <div key={i} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-sm border-b last:border-0 pb-2 last:pb-0">
                <div className="flex items-center gap-2 min-w-0">
                  <Badge variant={f.severity === "P0" ? "destructive" : "secondary"}>{f.severity}</Badge>
                  <span className="font-mono text-xs truncate">{f.rule}</span>
                </div>
                <span className="tabular-nums font-medium shrink-0">
                  {f.impact_paise > 0 ? `₹${formatPaise(f.impact_paise)}` : "—"}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function StatCard({ label, value, sub, tone = "default" }: { label: string; value: string; sub?: string; tone?: "default" | "warning" }) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className={`text-2xl font-semibold tabular-nums ${tone === "warning" ? "text-warning" : ""}`}>{value}</div>
        <div className="text-xs text-muted-foreground mt-1">{label}</div>
        {sub && <div className="text-xs text-destructive mt-1">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function TierChip({ label, n, warn }: { label: string; n: number; warn?: boolean }) {
  return (
    <div className={`min-w-0 rounded-md border px-3 py-2 ${warn && n > 0 ? "border-warning/40 bg-warning/5" : ""}`}>
      <div className={`text-lg font-semibold tabular-nums ${warn && n > 0 ? "text-warning" : ""}`}>{n}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}
