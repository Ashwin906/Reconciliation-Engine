import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Building2,
  CalendarClock,
  CheckCircle2,
  CreditCard,
  Info,
  Landmark,
  Loader2,
  Play,
  PlayCircle,
  Plug,
  RotateCcw,
  Scale,
  Search,
  ShieldCheck,
  Swords,
  Users,
  Wrench,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AdversarialControls, AdversarialResult } from "@/components/AdversarialPanel";
import { BreakItYourself } from "@/components/BreakItYourself";
import { ComparisonPanel } from "@/components/ComparisonPanel";
import { LivePanel } from "@/components/LivePanel";
import { MoneyFlowDiagram } from "@/components/MoneyFlow";
import { ReservePanel } from "@/components/ReservePanel";
import { client, type SimJourney, type SimScenario, type SimStage } from "@/lib/api";
import { formatPaise } from "@/lib/money";
import { cn } from "@/lib/utils";

const STAGE_ICONS: Record<string, typeof Users> = {
  customer_pays: Users,
  razorpay_captures: CreditCard,
  settlement_created: Building2,
  bank_credits: Landmark,
  matching: Search,
  residue: AlertTriangle,
  conservation: Scale,
  findings: ShieldCheck,
};

const STATUS_STYLES = {
  ok: { ring: "border-success/40 bg-success/5", text: "text-success", Icon: CheckCircle2 },
  warn: { ring: "border-warning/40 bg-warning/5", text: "text-warning", Icon: AlertTriangle },
  fail: { ring: "border-destructive/40 bg-destructive/5", text: "text-destructive", Icon: XCircle },
  info: { ring: "border-border bg-muted/30", text: "text-muted-foreground", Icon: Info },
} as const;

/** Stages appear one at a time so a viewer follows the money rather than
 *  being handed a finished wall of text. Purely presentational -- the whole
 *  journey is computed server-side before the first stage renders. */
const STAGE_DELAY_MS = 550;

/** The five things you can run. Modelled as modes rather than five stacked
 *  cards: previously all five choosers sat on the page at once and the
 *  result landed below all of them, so picking the first thing meant
 *  scrolling past four panels to see what happened. */
const MODES = [
  { id: "scenarios", label: "Scenarios", icon: PlayCircle, blurb: "Real problems merchants hit" },
  { id: "custom", label: "Build your own", icon: Wrench, blurb: "Choose what breaks" },
  { id: "attack", label: "Attack it", icon: Swords, blurb: "Make the AI lie" },
  { id: "reserve", label: "Across months", icon: CalendarClock, blurb: "Rolling reserve" },
  { id: "live", label: "Real account", icon: Plug, blurb: "Your own Razorpay data" },
] as const;
type ModeId = (typeof MODES)[number]["id"];

type ResultTab = "journey" | "flow" | "compare";

export function SimulatePage({
  onSimulated,
  onReset,
}: {
  onSimulated: (runId: string, residueCount: number) => void;
  onReset: () => void;
}) {
  const [scenarios, setScenarios] = useState<SimScenario[] | null>(null);
  const [mode, setMode] = useState<ModeId>("scenarios");
  const [selected, setSelected] = useState<string | null>(null);
  const [journey, setJourney] = useState<SimJourney | null>(null);
  const [visibleCount, setVisibleCount] = useState(0);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<ResultTab>("journey");
  const timers = useRef<number[]>([]);
  const resultsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    client.listScenarios().then(setScenarios).catch((e) => setError(String(e)));
    return () => timers.current.forEach(clearTimeout);
  }, []);

  function clearTimers() {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }

  /** Shared by every journey mode -- all of them hit the identical backend
   *  pipeline, so they end in the identical animate-and-install flow. */
  async function runJourney(load: () => Promise<SimJourney>, label: string | null) {
    clearTimers();
    setSelected(label);
    setRunning(true);
    setJourney(null);
    setVisibleCount(0);
    setError(null);
    setTab("journey");

    // On a narrow screen the results sit BELOW the controls, so bring them
    // into view rather than leaving someone looking at an unchanged screen.
    if (window.matchMedia("(max-width: 1023px)").matches) {
      requestAnimationFrame(() =>
        resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })
      );
    }

    try {
      const result = await load();
      setJourney(result);
      onSimulated(result.run_id, result.outcome.residue);
      result.stages.forEach((_, i) => {
        timers.current.push(
          window.setTimeout(() => setVisibleCount(i + 1), i * STAGE_DELAY_MS)
        );
      });
      timers.current.push(
        window.setTimeout(() => setRunning(false), result.stages.length * STAGE_DELAY_MS)
      );
    } catch (e) {
      setError(String(e));
      setRunning(false);
    }
  }

  const run = (slug: string) => runJourney(() => client.simulate(slug), slug);
  const runCustom = (
    defects: { kind: string; params: Record<string, number | string> }[],
    seed: number
  ) => runJourney(() => client.simulateCustom(defects, seed), null);
  const runAdversarial = (attack: string) =>
    runJourney(() => client.simulateAdversarial(attack), attack);

  function replay() {
    if (!journey) return;
    clearTimers();
    setVisibleCount(0);
    setRunning(true);
    setTab("journey");
    journey.stages.forEach((_, i) => {
      timers.current.push(window.setTimeout(() => setVisibleCount(i + 1), i * STAGE_DELAY_MS));
    });
    timers.current.push(
      window.setTimeout(() => setRunning(false), journey.stages.length * STAGE_DELAY_MS)
    );
  }

  async function handleReset() {
    clearTimers();
    await client.reset();
    setJourney(null);
    setSelected(null);
    setVisibleCount(0);
    setRunning(false);
    onReset();
  }

  if (error) return <div className="p-8 text-destructive text-sm">{error}</div>;

  const done = journey !== null && visibleCount >= journey.stages.length;
  const isJourneyMode = mode === "scenarios" || mode === "custom" || mode === "attack";

  return (
    <div className="max-w-6xl mx-auto px-5 py-6 lg:px-8 lg:py-8 space-y-6">
      {/* ---------- page header ---------- */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="text-xl font-bold tracking-tight">Watch it happen</h1>
          <p className="text-sm text-muted-foreground max-w-2xl">
            Run something and follow the money from the customer's payment to your bank. Whatever you
            run here becomes the live data on every other screen.
          </p>
        </div>
        {journey && (
          <Button size="sm" variant="ghost" onClick={handleReset} disabled={running}>
            Clear all
          </Button>
        )}
      </div>

      {/* ---------- controls | results ---------- */}
      <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)] items-start">
        {/* left rail: what to run */}
        <div className="space-y-4 lg:sticky lg:top-6">
          <nav aria-label="What to run" className="grid grid-cols-2 lg:grid-cols-1 gap-1.5">
            {MODES.map((m) => {
              const Icon = m.icon;
              const active = mode === m.id;
              return (
                <button
                  key={m.id}
                  onClick={() => setMode(m.id)}
                  aria-current={active ? "true" : undefined}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md border px-3 py-2 text-left transition-colors cursor-pointer",
                    active
                      ? "border-primary bg-accent text-accent-foreground"
                      : "border-transparent hover:bg-accent/50 text-muted-foreground"
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium truncate">{m.label}</span>
                    <span className="block text-[11px] opacity-70 truncate">{m.blurb}</span>
                  </span>
                </button>
              );
            })}
          </nav>

          <div className="rounded-lg border bg-card p-4">
            {mode === "scenarios" && (
              <ScenarioList
                scenarios={scenarios}
                selected={selected}
                running={running}
                onRun={run}
              />
            )}
            {mode === "custom" && <BreakItYourself onRun={runCustom} running={running} />}
            {mode === "attack" && (
              <AdversarialControls onRun={runAdversarial} running={running} selected={selected} />
            )}
            {mode === "reserve" && (
              <p className="text-xs text-muted-foreground">
                Money withheld one month and returned another is the same money. Run it on the right
                to see why a single-month view raises a false alarm twice.
              </p>
            )}
            {mode === "live" && (
              <p className="text-xs text-muted-foreground">
                Reconcile a real Razorpay account. Credentials are read from the server, never typed
                into this page.
              </p>
            )}
          </div>
        </div>

        {/* right: results */}
        <div ref={resultsRef} className="min-w-0 space-y-4">
          {mode === "reserve" && <ReservePanel />}
          {mode === "live" && <LivePanel onRun={(runId) => onSimulated(runId, 0)} />}

          {isJourneyMode && !journey && !running && <ResultsPlaceholder mode={mode} />}

          {isJourneyMode && (running || journey) && (
            <>
              {journey && (
                <div className="rounded-lg border bg-card p-4 space-y-2">
                  <div className="flex items-start justify-between gap-3">
                    <h2 className="text-base font-semibold">{journey.scenario.name}</h2>
                    <Button size="sm" variant="outline" onClick={replay} disabled={running}>
                      <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                      Replay
                    </Button>
                  </div>
                  <p className="text-sm text-muted-foreground">{journey.scenario.merchant_story}</p>
                </div>
              )}

              {done && (
                <div className="flex gap-1 border-b" role="tablist" aria-label="Result views">
                  <ResultTabButton id="journey" active={tab} onSelect={setTab}>
                    What happened
                  </ResultTabButton>
                  <ResultTabButton id="flow" active={tab} onSelect={setTab}>
                    Money flow
                  </ResultTabButton>
                  <ResultTabButton id="compare" active={tab} onSelect={setTab}>
                    vs. simple matching
                  </ResultTabButton>
                </div>
              )}

              {(!done || tab === "journey") && (
                <div className="space-y-4">
                  {journey?.adversarial && done && (
                    <AdversarialResult report={journey.adversarial} />
                  )}

                  <div
                    role="status"
                    aria-live="polite"
                    aria-label={`${visibleCount} of ${journey?.stages.length ?? 0} stages shown`}
                  >
                    {journey?.stages.slice(0, visibleCount).map((stage, i) => (
                      <StageCard
                        key={stage.key}
                        stage={stage}
                        isLast={i === journey.stages.length - 1}
                      />
                    ))}
                    {running && (
                      <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                        Following the money…
                      </div>
                    )}
                  </div>

                  {journey && done && <Verdict journey={journey} />}
                  {journey && done && <GroundTruth journey={journey} />}
                </div>
              )}

              {done && journey && tab === "flow" && <MoneyFlowDiagram flow={journey.money_flow} />}
              {done && journey && tab === "compare" && (
                <ComparisonPanel comparison={journey.comparison} />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------- left rail: scenarios ---------------- */

function ScenarioList({
  scenarios,
  selected,
  running,
  onRun,
}: {
  scenarios: SimScenario[] | null;
  selected: string | null;
  running: boolean;
  onRun: (slug: string) => void;
}) {
  if (!scenarios) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-1.5">
      {scenarios.map((s) => {
        const active = selected === s.slug;
        return (
          <button
            key={s.slug}
            onClick={() => onRun(s.slug)}
            disabled={running}
            aria-pressed={active}
            className={cn(
              "w-full text-left rounded-md border px-3 py-2.5 transition-colors cursor-pointer",
              "hover:bg-accent/50 focus-visible:outline-2 focus-visible:outline-ring",
              "disabled:opacity-60 disabled:cursor-not-allowed",
              active ? "border-primary bg-accent/40" : "border-border"
            )}
          >
            <span className="flex items-center gap-2">
              <Play className="h-3.5 w-3.5 text-primary shrink-0" aria-hidden="true" />
              <span className="text-sm font-medium">{s.name}</span>
            </span>
            {s.defect === null && (
              <Badge variant="secondary" className="mt-1.5">
                baseline
              </Badge>
            )}
          </button>
        );
      })}
    </div>
  );
}

/* ---------------- right: placeholder + tabs ---------------- */

function ResultsPlaceholder({ mode }: { mode: ModeId }) {
  const copy: Record<string, string> = {
    scenarios: "Pick a scenario on the left and the whole money journey plays out here.",
    custom: "Choose what to break on the left, then run it. Results appear here.",
    attack: "Pick an attack on the left and watch which defence stops it.",
  };
  return (
    <div className="rounded-lg border border-dashed bg-card/50 px-6 py-14 text-center">
      <PlayCircle className="h-6 w-6 text-primary mx-auto mb-3" aria-hidden="true" />
      <p className="text-sm font-medium">Nothing running yet</p>
      <p className="text-sm text-muted-foreground mt-1 max-w-sm mx-auto">{copy[mode]}</p>
    </div>
  );
}

function ResultTabButton({
  id,
  active,
  onSelect,
  children,
}: {
  id: ResultTab;
  active: ResultTab;
  onSelect: (t: ResultTab) => void;
  children: React.ReactNode;
}) {
  const on = active === id;
  return (
    <button
      role="tab"
      aria-selected={on}
      onClick={() => onSelect(id)}
      className={cn(
        "px-3 py-2 text-sm border-b-2 -mb-px transition-colors cursor-pointer",
        on
          ? "border-primary text-foreground font-medium"
          : "border-transparent text-muted-foreground hover:text-foreground"
      )}
    >
      {children}
    </button>
  );
}

/* ---------------- stages ---------------- */

function StageCard({ stage, isLast }: { stage: SimStage; isLast: boolean }) {
  const style = STATUS_STYLES[stage.status];
  const Icon = STAGE_ICONS[stage.key] ?? style.Icon;

  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center shrink-0">
        <div className={cn("rounded-full p-2 border", style.ring)}>
          <Icon className={cn("h-4 w-4", style.text)} aria-hidden="true" />
        </div>
        {!isLast && <div className="w-px flex-1 bg-border my-1" aria-hidden="true" />}
      </div>

      <div className={cn("flex-1 min-w-0 rounded-lg border p-4 mb-3", style.ring)}>
        <h3 className="text-sm font-semibold">{stage.title}</h3>
        <p className="text-sm text-muted-foreground mt-1">{stage.detail}</p>
        <FactList facts={stage.facts} />
      </div>
    </div>
  );
}

/** Renders only the facts worth reading at a glance. Money fields (any key
 *  ending _paise) are formatted as rupees; nested structures are left out
 *  rather than dumped as raw JSON. */
function FactList({ facts }: { facts: Record<string, unknown> }) {
  const scalar = Object.entries(facts).filter(
    ([, v]) => typeof v === "number" || typeof v === "boolean" || typeof v === "string"
  );
  if (scalar.length === 0) return null;

  return (
    <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
      {scalar.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
            {key.replace(/_paise$/, "").replace(/_/g, " ")}
          </dt>
          <dd className="text-sm font-medium tabular-nums">{formatFact(key, value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function formatFact(key: string, value: unknown): string {
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number" && key.endsWith("_paise")) return `₹${formatPaise(Math.abs(value))}`;
  return String(value);
}

/* ---------------- verdict + ground truth ---------------- */

function Verdict({ journey }: { journey: SimJourney }) {
  const { outcome, planted_defects } = journey;
  const caught = outcome.residue > 0 || outcome.findings.length > 0;
  // "Everything reconciled" must also mean nothing was FOUND. A fee
  // overcharge reconciles perfectly and still steals real money.
  const clean =
    outcome.residue === 0 && outcome.conservation_closes && outcome.findings.length === 0;

  const plantedCount = planted_defects.length;
  const plantedKinds = [...new Set(planted_defects.map((d) => d.kind))];
  const plantedLabel =
    plantedCount === 0
      ? ""
      : plantedCount === 1
        ? `A ${plantedKinds[0].replace(/_/g, " ")} defect was`
        : `${plantedCount} defects (${plantedKinds.map((k) => k.replace(/_/g, " ")).join(", ")}) were`;
  const leaked = outcome.findings.reduce((sum, f) => sum + Math.max(0, f.impact_paise), 0);

  return (
    <div
      className={cn(
        "rounded-lg border-2 p-4 space-y-3",
        caught ? "border-primary/40 bg-accent/20" : "border-success/40 bg-success/5"
      )}
    >
      <h2 className="text-base font-semibold">What the system concluded</h2>

      <p className="text-sm">
        {plantedCount > 0
          ? caught
            ? `${plantedLabel} planted. The system flagged it — ${outcome.residue} unexplained item(s) and ${outcome.findings.length} rule finding(s) — without ever being told what to look for.`
            : `${plantedLabel} planted, and the system did NOT surface it. Stated honestly rather than hidden.`
          : `Nothing was broken in this run, and the system correctly found nothing wrong — ${outcome.match_rate_pct}% matched, books balanced. A system that cries wolf on a clean day is useless.`}
      </p>

      {outcome.residue === 0 && outcome.conservation_closes && outcome.findings.length > 0 && (
        <p className="text-sm text-warning">
          Note what just happened: every settlement tied to the bank exactly and the books balanced —
          and ₹{formatPaise(leaked)} was still taken. Matching alone would have called this a clean run.
        </p>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Fact label="Matched" value={`${outcome.match_rate_pct}%`} />
        <Fact label="Unexplained" value={`${outcome.residue}`} warn={outcome.residue > 0} />
        <Fact
          label="Books balance"
          value={outcome.conservation_closes ? "Yes" : "No"}
          warn={!outcome.conservation_closes}
        />
        <Fact label="Took" value={`${(outcome.duration_s * 1000).toFixed(0)}ms`} />
      </div>

      {!outcome.conservation_closes && (
        <p className="text-sm text-warning">
          Conservation gap: ₹{formatPaise(Math.abs(outcome.conservation_residual_paise))}. Money in does
          not equal money accounted for — this is arithmetic, not an opinion.
        </p>
      )}

      {outcome.findings.length > 0 && (
        <div className="space-y-1.5 pt-1 border-t">
          {outcome.findings.map((f, i) => (
            <div key={i} className="flex items-center justify-between gap-3 text-sm pt-1.5">
              <div className="flex items-center gap-2 min-w-0">
                <Badge variant={f.severity === "P0" ? "destructive" : "secondary"}>{f.severity}</Badge>
                <span className="truncate">{f.rule.replace(/_/g, " ")}</span>
              </div>
              <span className="tabular-nums shrink-0">
                {f.impact_paise > 0 ? `₹${formatPaise(f.impact_paise)}` : "—"}
              </span>
            </div>
          ))}
        </div>
      )}

      {clean && (
        <p className="text-sm text-success flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
          Everything reconciled and the books balance.
        </p>
      )}

      <p className="text-xs text-muted-foreground border-t pt-3">
        This run is now the live data on every other screen — Overview, Needs attention, Activity log
        and Reports all show these exact figures, not a copy.
      </p>
    </div>
  );
}

/** The planted defects, shown AFTER the verdict on purpose: it is the
 *  answer key, and putting it above would let a reader check the system's
 *  homework before seeing what it concluded on its own. */
function GroundTruth({ journey }: { journey: SimJourney }) {
  const [open, setOpen] = useState(false);
  if (journey.planted_defects.length === 0) return null;

  return (
    <div className="rounded-lg border border-dashed p-4">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="text-sm font-medium cursor-pointer hover:text-primary transition-colors"
      >
        {open ? "Hide" : "Show"} the answer key — {journey.planted_defects.length} defect
        {journey.planted_defects.length === 1 ? "" : "s"} planted
      </button>

      {open && (
        <div className="mt-3 space-y-2 text-xs">
          <ul className="space-y-1">
            {journey.planted_defects.slice(0, 6).map((d, i) => (
              <li key={i} className="flex flex-wrap gap-x-2">
                <span>{d.kind.replace(/_/g, " ")}</span>
                <span className="text-muted-foreground">on</span>
                <span className="font-mono">{d.subject_id}</span>
                {d.impact_paise > 0 && (
                  <span className="tabular-nums">₹{formatPaise(d.impact_paise)}</span>
                )}
              </li>
            ))}
            {journey.planted_defects.length > 6 && (
              <li className="text-muted-foreground">
                …and {journey.planted_defects.length - 6} more
              </li>
            )}
          </ul>
          <p className="text-muted-foreground">
            The pipeline never sees any of this — it is here so you can check its conclusion against
            the real answer.
          </p>
        </div>
      )}
    </div>
  );
}

function Fact({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div>
      <div className={cn("text-lg font-semibold tabular-nums", warn && "text-warning")}>{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}
