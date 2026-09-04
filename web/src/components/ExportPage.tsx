import { useEffect, useState } from "react";
import { Download, FileText } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  client,
  type ExportHistoryItem,
  type RunDetail,
  type RunListItem,
} from "@/lib/api";
import { cn } from "@/lib/utils";

export function ExportPage({ runId }: { runId: string }) {
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string>(runId);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [history, setHistory] = useState<ExportHistoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Real periods: DOMAIN_KNOWLEDGE.md / this dashboard's honest scope --
  // a "period" here IS a run (one batch reconciled against one dataset
  // snapshot), not a calendar month, since the demo generates a single
  // batch rather than months of accumulated data. Listing real runs
  // rather than faking a month picker with nothing real behind it.
  useEffect(() => {
    client.listRuns().then(setRuns).catch((e) => setError(String(e)));
    refreshHistory();
  }, []);

  useEffect(() => {
    client.getRun(selectedRunId).then(setRun).catch((e) => setError(String(e)));
  }, [selectedRunId]);

  function refreshHistory() {
    client.listExports().then(setHistory).catch((e) => setError(String(e)));
  }

  if (error) return <div className="p-8 text-destructive text-sm">{error}</div>;

  const matched = run ? run.t1_matches + run.t2_matches + run.t3_matches : 0;
  const total = run ? matched + run.residue.length || 1 : 1;
  const matchRate = run ? ((run.t1_matches + run.t3_matches) / total) * 100 : 0;

  return (
    <div className="max-w-2xl mx-auto px-5 py-6 lg:px-8 lg:py-8 space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Generate close pack</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="period-select" className="text-sm font-medium">
              Period
            </label>
            {/* Select's onValueChange can emit null (cleared); this picker
                has no cleared state, so an explicit guard keeps the
                run id a string rather than widening it to string | null
                everywhere downstream. */}
            <Select value={selectedRunId} onValueChange={(v) => v !== null && setSelectedRunId(v)}>
              <SelectTrigger id="period-select" className="w-full">
                <SelectValue placeholder="Select a run" />
              </SelectTrigger>
              <SelectContent>
                {(runs ?? []).map((r) => (
                  <SelectItem key={r.run_id} value={r.run_id}>
                    {r.run_id} — {r.merchant_id} ({r.residue_count} open)
                  </SelectItem>
                ))}
                {runs && runs.every((r) => r.run_id !== runId) && (
                  <SelectItem value={runId}>{runId} (current)</SelectItem>
                )}
              </SelectContent>
            </Select>
          </div>

          {run && (
            <div className="rounded-md border p-4 space-y-2 bg-muted/30">
              <SummaryRow label="Match rate" value={`${matchRate.toFixed(0)}%`} />
              <SummaryRow label="Open exceptions" value={`${run.residue.length}`} />
              <SummaryRow
                label="Conservation"
                value={run.conservation_closes ? "Closes" : "Does not close"}
                warn={!run.conservation_closes}
              />
            </div>
          )}

          {/* Web Interface Guidelines: a <button> nested inside an <a> is
              invalid HTML (interactive content inside interactive
              content). Style the anchor directly with the button's own
              variant classes instead of wrapping a real <button>. */}
          <a
            href={client.exportCloseCsvUrl(selectedRunId)}
            download={`close-pack-${selectedRunId}.csv`}
            onClick={() => setTimeout(refreshHistory, 500)}
            className={cn(buttonVariants(), "w-full")}
          >
            <Download className="h-4 w-4 mr-2" aria-hidden="true" />
            Generate close pack (CSV)
          </a>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Previously generated</CardTitle>
        </CardHeader>
        <CardContent>
          {!history || history.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No exports generated yet. They'll appear here after you generate one above.
            </p>
          ) : (
            <ul className="divide-y">
              {history.map((h, i) => (
                <li key={i} className="flex items-center justify-between gap-3 py-2 text-sm">
                  <div className="flex items-center gap-2 min-w-0">
                    <FileText className="h-4 w-4 text-muted-foreground shrink-0" aria-hidden="true" />
                    <span className="font-mono text-xs truncate">{h.run_id}</span>
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      {new Date(h.generated_at).toLocaleString()}
                    </span>
                  </div>
                  <a
                    href={client.exportCloseCsvUrl(h.run_id)}
                    download={`close-pack-${h.run_id}.csv`}
                    className={cn(buttonVariants({ variant: "outline", size: "sm" }), "shrink-0")}
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden="true" />
                    <span className="sr-only">Download {h.run_id}</span>
                  </a>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SummaryRow({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-medium tabular-nums ${warn ? "text-warning" : ""}`}>{value}</span>
    </div>
  );
}
