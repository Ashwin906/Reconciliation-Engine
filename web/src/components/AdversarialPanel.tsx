import { useEffect, useState } from "react";
import {
  BadgeCheck,
  Ghost,
  ShieldAlert,
  ShieldCheck,
  Shuffle as ShuffleIcon,
  Swords,
  TrendingDown,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { client, type AdversarialReport, type AttackKind } from "@/lib/api";
import { formatPaise } from "@/lib/money";
import { cn } from "@/lib/utils";

/** Icon + short headline per attack, so four choices are distinguishable at
 *  a glance rather than four paragraphs to read. Keyed by the backend's own
 *  slug; an unknown slug falls back to the generic icon and the
 *  server-supplied label, so a new attack renders before anyone writes copy. */
const ATTACK_UI: Record<string, { Icon: typeof Swords; headline: string }> = {
  fabricated_evidence: { Icon: Ghost, headline: "Invent evidence" },
  understated_amount: { Icon: TrendingDown, headline: "Understate the amount" },
  wrong_but_real: { Icon: ShuffleIcon, headline: "Cite the wrong cause" },
  overconfident: { Icon: BadgeCheck, headline: "Fake certainty" },
};

/**
 * Controls only. The RESULT renders separately (AdversarialResult) because
 * the two live in different columns of the page -- input on the left,
 * outcome on the right -- so nobody has to scroll past the chooser to find
 * out what happened.
 */
export function AdversarialControls({
  onRun,
  running,
  selected,
}: {
  onRun: (attack: string) => void;
  running: boolean;
  selected: string | null;
}) {
  const [attacks, setAttacks] = useState<AttackKind[] | null>(null);

  useEffect(() => {
    client.listAttacks().then(setAttacks).catch(() => setAttacks([]));
  }, []);

  if (!attacks) return <p className="text-sm text-muted-foreground">Loading…</p>;

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Replace the investigator with one actively trying to get a false correction onto your books.
        Each attack targets a different defence.
      </p>

      <div className="space-y-1.5">
        {attacks.map((attack) => {
          const { Icon, headline } = ATTACK_UI[attack.attack] ?? {
            Icon: Swords,
            headline: attack.label,
          };
          const active = selected === attack.attack;
          return (
            <button
              key={attack.attack}
              onClick={() => onRun(attack.attack)}
              disabled={running}
              aria-pressed={active}
              className={cn(
                "w-full text-left rounded-md border px-3 py-2.5 transition-colors cursor-pointer",
                "hover:bg-accent/50 focus-visible:outline-2 focus-visible:outline-ring",
                "disabled:opacity-60 disabled:cursor-not-allowed",
                active && "border-primary bg-accent/40"
              )}
            >
              <span className="flex items-center gap-2">
                <Icon className="h-4 w-4 text-primary shrink-0" aria-hidden="true" />
                <span className="text-sm font-medium">{headline}</span>
              </span>
            </button>
          );
        })}
      </div>

      <p className="text-[11px] leading-relaxed text-muted-foreground border-t pt-2.5">
        The attacks are constructed; the defences are not. Nothing downstream of the model knows this
        mode exists.
      </p>
    </div>
  );
}

export function AdversarialResult({ report }: { report: AdversarialReport }) {
  const attempt = report.attempts[0];

  return (
    <div
      className={cn(
        "rounded-lg border-2 p-4 space-y-3",
        report.held ? "border-success/40 bg-success/5" : "border-destructive/50 bg-destructive/5"
      )}
    >
      <div className="flex items-start gap-3">
        {report.held ? (
          <ShieldCheck className="h-6 w-6 text-success shrink-0" aria-hidden="true" />
        ) : (
          <ShieldAlert className="h-6 w-6 text-destructive shrink-0" aria-hidden="true" />
        )}
        <div className="min-w-0">
          <p className={cn("font-semibold", report.held ? "text-success" : "text-destructive")}>
            {report.held
              ? "Held. Nothing reached your books without a human."
              : `${report.money_moved_without_a_human} false correction(s) were applied automatically.`}
          </p>
          <p className="text-sm text-muted-foreground">Caught by: {report.defence_that_caught_it}</p>
        </div>
      </div>

      {attempt && (
        <div className="rounded-md bg-background/70 border p-3 text-xs space-y-1.5">
          <p className="font-medium">What the model actually claimed</p>
          <Row label="Suggested cause" value={attempt.hypothesis.replace(/_/g, " ")} />
          <Row label="Evidence cited" value={attempt.evidence_ids.join(", ") || "—"} mono />
          <Row label="Amount it claimed" value={`₹${formatPaise(attempt.claimed_paise)}`} tone="bad" />
          {attempt.true_paise > 0 && (
            <Row label="Actual amount" value={`₹${formatPaise(attempt.true_paise)}`} tone="good" />
          )}
          <Row label="Its stated confidence" value={`${(attempt.confidence * 100).toFixed(0)}%`} />
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Badge variant={report.auto_applied > 0 ? "destructive" : "outline"}>
          Auto-applied: {report.auto_applied}
        </Badge>
        <Badge variant="secondary">Stopped before the gate: {report.stopped_before_gate}</Badge>
        <Badge variant="secondary">Rejected by the gate: {report.rejected_by_gate}</Badge>
        <Badge variant="secondary">Sent to a human: {report.sent_to_human}</Badge>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
  tone,
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: "good" | "bad";
}) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span
        className={cn(
          "text-right min-w-0 break-words",
          mono && "font-mono",
          tone === "bad" && "text-destructive font-medium",
          tone === "good" && "text-success font-medium"
        )}
      >
        {value}
      </span>
    </div>
  );
}
