import { formatPaise } from "@/lib/money";
import { cn } from "@/lib/utils";

/**
 * Renders a raw detail object as a readable field grid instead of a JSON
 * dump.
 *
 * Previously these screens printed `JSON.stringify(detail, null, 2)`.
 * That is fine for a developer and intimidating for the merchant this
 * product is actually for -- and worse, it renders money as raw paise
 * integers (`"expected": 5573576`), which is the one thing a finance
 * screen must never make someone decode in their head.
 *
 * Money keys are an EXPLICIT list, not a heuristic. Guessing "any large
 * integer is probably paise" would eventually format a line id or a
 * threshold as rupees, which is a worse failure than showing a bare
 * number: it would be confidently wrong about an amount. Anything not on
 * the list renders as-is.
 */
const MONEY_KEYS = new Set([
  "expected",
  "observed",
  "gap",
  "expected_amount",
  "residual",
  "computed",
  "reported",
  "impact_paise",
  "amount_paise",
  "claimed_paise",
  "true_paise",
]);

const LABELS: Record<string, string> = {
  expected: "Expected",
  observed: "Actually received",
  gap: "Difference",
  expected_amount: "Expected amount",
  utr: "Bank reference (UTR)",
  bank_line_id: "Bank statement line",
  residual: "Left unexplained",
  computed: "What the records add up to",
  reported: "What the settlement claimed",
  threshold: "Match confidence needed",
  fuzzy_match_also_failed: "Closest-fit matching also failed",
  evidence_ids: "Evidence referenced",
  hypothesis: "Suggested cause",
  verifier_result: "Independent check",
  gate: "Decision route",
  status: "Status",
  tier: "Matched at",
  subject: "Settlement",
  inputs_hash: "Input fingerprint",
  ts: "Recorded at",
  id: "Entry id",
  corrects: "Corrects entry",
};

function label(key: string): string {
  return LABELS[key] ?? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function isMoney(key: string): boolean {
  return MONEY_KEYS.has(key) || key.endsWith("_paise");
}

/**
 * Enum values are internal vocabulary too.
 *
 * Formatting the KEYS while leaving `PENDING_APPROVAL` and
 * `unbooked_chargeback` in the values only solves half the problem -- the
 * row still reads as a database dump. Keyed per-field rather than
 * globally, because the same token can mean different things in different
 * columns: `REJECT` as a verifier result means the arithmetic did not
 * tie, while `REJECT` as a gate decision means the correction was
 * refused. Collapsing them into one lookup would blur that.
 */
const VALUE_LABELS: Record<string, Record<string, string>> = {
  verifier_result: {
    ACCEPT: "Checked and confirmed",
    REJECT: "Failed the independent check",
    UNVERIFIABLE: "Nothing to check it against",
  },
  gate: {
    AUTO: "Applied automatically",
    HUMAN: "Sent for human review",
    REJECT: "Refused",
  },
  status: {
    PENDING_APPROVAL: "Waiting for review",
    APPLIED: "Applied",
    APPROVED: "Approved",
    REJECTED: "Rejected",
  },
  tier: {
    T1: "Exact match",
    T2: "Internal consistency check",
    T3: "Closest fit",
    T4: "AI investigation",
  },
};

/** ISO timestamps are unreadable and are never what a person wants. */
function isTimestamp(key: string, value: unknown): boolean {
  return (
    typeof value === "string" &&
    /(^ts$|_at$|^held_on$|^release_due_on$|^released_on$)/.test(key) &&
    !Number.isNaN(Date.parse(value))
  );
}

function renderValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);

  if (typeof value === "number" && isMoney(key)) {
    return `${value < 0 ? "−" : ""}₹${formatPaise(Math.abs(value))}`;
  }

  if (isTimestamp(key, value)) return new Date(value as string).toLocaleString();

  const mapped = VALUE_LABELS[key]?.[String(value)];
  if (mapped) return mapped;

  // Any remaining snake_case slug (hypothesis kinds, reasons) reads as a
  // sentence rather than an identifier.
  if (typeof value === "string" && /^[a-z][a-z0-9]*(_[a-z0-9]+)+$/.test(value)) {
    return value.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  }

  return String(value);
}

/**
 * The expected-vs-observed comparison, when the object carries both.
 *
 * This is the single most common shape in this system (a settlement's
 * reported amount against what the bank actually credited) and it
 * deserves better than two rows in a list -- the whole question a
 * merchant has is "how far apart are these, and in which direction".
 */
function VarianceRow({
  expected,
  observed,
  gap,
}: {
  expected: number;
  observed: number;
  gap?: number;
}) {
  const difference = gap ?? expected - observed;
  const short = difference > 0;

  return (
    <div className="rounded-md border bg-background p-3">
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <div className="text-xs text-muted-foreground">Expected</div>
          <div className="tabular-nums font-medium">₹{formatPaise(expected)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Received</div>
          <div className="tabular-nums font-medium">₹{formatPaise(observed)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Difference</div>
          <div
            className={cn(
              "tabular-nums font-semibold",
              difference === 0 ? "text-success" : short ? "text-destructive" : "text-warning"
            )}
          >
            {difference === 0 ? "₹0.00" : `${short ? "−" : "+"}₹${formatPaise(Math.abs(difference))}`}
          </div>
        </div>
      </div>
      {difference !== 0 && (
        <p className="text-xs text-muted-foreground text-center mt-2">
          {short
            ? `You received ₹${formatPaise(Math.abs(difference))} less than the settlement said you would.`
            : `You received ₹${formatPaise(Math.abs(difference))} more than the settlement said you would.`}
        </p>
      )}
    </div>
  );
}

export function DetailFields({
  data,
  className,
}: {
  data: Record<string, unknown>;
  className?: string;
}) {
  const entries = Object.entries(data ?? {});
  if (entries.length === 0) {
    return (
      <p className={cn("text-sm text-muted-foreground", className)}>
        No extra detail was recorded for this item.
      </p>
    );
  }

  const expected = data.expected ?? data.reported;
  const observed = data.observed ?? data.computed;
  const showVariance = typeof expected === "number" && typeof observed === "number";

  // Fields already shown by the variance panel are not repeated below it.
  const shown = showVariance
    ? entries.filter(([k]) => !["expected", "observed", "gap", "reported", "computed"].includes(k))
    : entries;

  return (
    <div className={cn("space-y-3", className)}>
      {showVariance && (
        <VarianceRow
          expected={expected as number}
          observed={observed as number}
          gap={typeof data.gap === "number" ? data.gap : undefined}
        />
      )}

      {shown.length > 0 && (
        <dl className="divide-y rounded-md border bg-background">
          {shown.map(([key, value]) => (
            <div key={key} className="flex justify-between gap-4 px-3 py-2 text-sm">
              <dt className="text-muted-foreground shrink-0">{label(key)}</dt>
              <dd
                className={cn(
                  "text-right min-w-0 break-words",
                  (isMoney(key) || typeof value === "number") && "tabular-nums",
                  // ids stay monospaced -- they are meant to be compared
                  // character by character against another system's screen
                  /(_id|^id$|utr|hash)/.test(key) && "font-mono text-xs"
                )}
              >
                {renderValue(key, value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
