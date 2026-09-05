import { useEffect, useState } from "react";
import { AlertTriangle, KeyRound, Plug, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { client, type LiveRunResult, type LiveStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Connects a REAL Razorpay account.
 *
 * There is deliberately no field here for an API key. Credentials are
 * read from the server's own environment and never travel through the
 * browser -- a key typed into this page would end up in browser memory,
 * in request logs, and in anything proxying between the two, for a
 * convenience worth nothing since whoever deploys this also sets its
 * environment. This panel can only *trigger* a run and report what came
 * back.
 */
function isoToEpoch(value: string): number {
  return Math.floor(new Date(value).getTime() / 1000);
}

function defaultRange(): { from: string; to: string } {
  const to = new Date();
  const from = new Date(to.getTime() - 30 * 24 * 60 * 60 * 1000);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { from: fmt(from), to: fmt(to) };
}

export function LivePanel({ onRun }: { onRun: (runId: string) => void }) {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [range, setRange] = useState(defaultRange);
  const [bankCsv, setBankCsv] = useState("");
  const [result, setResult] = useState<LiveRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    client.liveStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  async function run() {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const out = await client.liveRun(
        isoToEpoch(range.from),
        isoToEpoch(range.to),
        bankCsv.trim() || undefined
      );
      setResult(out);
      onRun(out.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <Plug className="h-4 w-4" aria-hidden="true" />
          Use your real Razorpay account
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={status.configured ? "default" : "secondary"}>
            {status.configured ? "Credentials configured" : "No credentials configured"}
          </Badge>
          <Badge variant="outline">{status.mode} mode</Badge>
        </div>

        {!status.configured ? (
          <div className="rounded-md border p-3 space-y-2 text-sm">
            <p className="flex items-center gap-2 font-medium">
              <KeyRound className="h-4 w-4" aria-hidden="true" />
              Set your keys on the server, not here
            </p>
            <p className="text-muted-foreground text-xs">
              This page has no field for an API secret on purpose. Add them to the server's
              environment and restart:
            </p>
            <pre className="text-xs font-mono bg-muted rounded p-2 overflow-x-auto">
              RAZORPAY_KEY_ID=rzp_test_xxxx{"\n"}RAZORPAY_KEY_SECRET=xxxx
            </pre>
            <p className="text-muted-foreground text-xs">
              Start with <strong>test-mode</strong> keys. Every call this system makes to Razorpay is
              a read (GET) — it can never move money or change anything in your account.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field
                id="live-from"
                label="From"
                type="date"
                value={range.from}
                onChange={(v) => setRange((r) => ({ ...r, from: v }))}
              />
              <Field
                id="live-to"
                label="To"
                type="date"
                value={range.to}
                onChange={(v) => setRange((r) => ({ ...r, to: v }))}
              />
            </div>

            <Field
              id="live-bank"
              label="Bank statement CSV (path on the server)"
              type="text"
              placeholder="/data/statement.csv"
              value={bankCsv}
              onChange={setBankCsv}
              hint="Without a statement there is nothing to match settlements against — the run will say so rather than report 0%."
            />

            <Button onClick={run} disabled={busy}>
              {busy ? "Reconciling…" : "Reconcile my account"}
            </Button>
          </div>
        )}

        {error && (
          <p className="text-sm text-destructive break-words">{error}</p>
        )}

        {result && <LiveResult result={result} />}

        <p className="text-xs text-muted-foreground border-t pt-3 flex gap-2">
          <ShieldCheck className="h-4 w-4 shrink-0 mt-0.5" aria-hidden="true" />
          <span>
            Read-only and credential-free by design: keys live in the server environment, never in
            this page or in any request. {" "}
            {!status.transport_verified_against_live_api && (
              <strong>
                Stated honestly: the HTTP layer that calls Razorpay has never been run against a live
                endpoint — no real keys existed while this was built. Everything either side of it is
                tested. Try test-mode keys first.
              </strong>
            )}
          </span>
        </p>
      </CardContent>
    </Card>
  );
}

function LiveResult({ result }: { result: LiveRunResult }) {
  const i = result.ingested;
  return (
    <div
      className={cn(
        "rounded-md border p-3 space-y-2",
        i.complete ? "border-success/40 bg-success/5" : "border-warning/40 bg-warning/5"
      )}
    >
      <p className="text-sm font-medium">Pulled from your account</p>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-sm">
        <Stat label="Payments" value={i.payments} />
        <Stat label="Settlements" value={i.settlements} />
        <Stat label="Refunds" value={i.refunds} />
        <Stat label="Disputes" value={i.disputes} />
        <Stat label="Bank lines" value={i.bank_lines} />
      </div>

      {!i.complete && (
        <div className="text-xs space-y-1">
          <p className="flex items-center gap-1.5 text-warning font-medium">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            {i.skipped_count} thing{i.skipped_count === 1 ? "" : "s"} could not be used
          </p>
          <ul className="list-disc pl-5 text-muted-foreground">
            {i.skipped.map((s, n) => (
              <li key={n}>{s}</li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-sm">
        {result.residue_count} unexplained item{result.residue_count === 1 ? "" : "s"},{" "}
        {result.findings_count} finding{result.findings_count === 1 ? "" : "s"}, books{" "}
        {result.conservation_closes ? "balance" : "do not balance"}. Every other screen now shows this
        run.
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="tabular-nums font-semibold">{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  type,
  placeholder,
  hint,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  type: string;
  placeholder?: string;
  hint?: string;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 w-full rounded-md border bg-transparent px-3 text-sm"
      />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
