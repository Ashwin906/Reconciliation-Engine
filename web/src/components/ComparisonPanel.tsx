import { Check, X } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Comparison } from "@/lib/api";
import { formatPaise } from "@/lib/money";
import { cn } from "@/lib/utils";

/**
 * The same dataset, two approaches. Exists to make the architecture's
 * value falsifiable rather than asserted -- if the naive column ever
 * matched the real one, that would be a real argument this system is
 * over-built, and the panel would show it.
 */
export function ComparisonPanel({ comparison }: { comparison: Comparison }) {
  const { naive, real, difference } = comparison;

  const rows: { label: string; naive: string | boolean; real: string | boolean }[] = [
    { label: "Reconciled", naive: `${naive.match_rate_pct}%`, real: `${real.match_rate_pct}%` },
    { label: "Reported unexplained", naive: `${naive.unresolved}`, real: `${real.unresolved}` },
    { label: "Finds fee leaks", naive: naive.finds_fee_leaks, real: real.finds_fee_leaks },
    { label: "Proves books balance", naive: naive.proves_books_balance, real: real.proves_books_balance },
    {
      label: "Verifies its own answers",
      naive: naive.verifies_independently,
      real: real.verifies_independently,
    },
  ];

  const sameResult =
    naive.match_rate_pct === real.match_rate_pct &&
    difference.false_alarms_avoided === 0 &&
    difference.leak_money_naive_would_miss_paise === 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Versus doing it the simple way</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          The same data, run through exact-reference matching alone — what most reconciliation scripts
          actually do — and through this system.
        </p>

        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[420px]">
            <thead>
              <tr className="border-b">
                <th scope="col" className="text-left font-medium py-2 pr-3">
                  &nbsp;
                </th>
                <th scope="col" className="text-left font-medium py-2 px-3 text-muted-foreground">
                  Simple matching
                </th>
                <th scope="col" className="text-left font-medium py-2 pl-3">
                  This system
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.label} className="border-b last:border-0">
                  <th scope="row" className="text-left font-normal text-muted-foreground py-2 pr-3">
                    {row.label}
                  </th>
                  <Cell value={row.naive} />
                  <Cell value={row.real} strong />
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {sameResult ? (
          <p className="text-sm text-muted-foreground border-t pt-3">
            On this run both approaches reached the same answer. Stated plainly rather than hidden — the
            extra machinery earns its place on the runs below, not on every run.
          </p>
        ) : (
          <div className="space-y-1.5 border-t pt-3">
            {difference.false_alarms_avoided > 0 && (
              <Delta>
                <strong>{difference.false_alarms_avoided}</strong> false alarm
                {difference.false_alarms_avoided === 1 ? "" : "s"} avoided — money that actually
                arrived, which simple matching would have reported missing
                {difference.money_falsely_flagged_paise > 0 && (
                  <>
                    {" "}
                    (₹{formatPaise(difference.money_falsely_flagged_paise)} sent someone hunting for
                    nothing)
                  </>
                )}
                .
              </Delta>
            )}
            {difference.leak_money_naive_would_miss_paise > 0 && (
              <Delta>
                <strong>₹{formatPaise(difference.leak_money_naive_would_miss_paise)}</strong> of real
                losses simple matching cannot see at all — it was taken before the settlement was
                calculated, so the settlement and the bank agree perfectly. Both numbers consistent,
                both wrong.
              </Delta>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Cell({ value, strong }: { value: string | boolean; strong?: boolean }) {
  if (typeof value === "boolean") {
    return (
      <td className="py-2 px-3">
        {value ? (
          <span className="inline-flex items-center gap-1.5 text-success">
            <Check className="h-4 w-4" aria-hidden="true" />
            <span className="sr-only">Yes</span>
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-muted-foreground">
            <X className="h-4 w-4" aria-hidden="true" />
            <span className="sr-only">No</span>
          </span>
        )}
      </td>
    );
  }
  return (
    <td className={cn("py-2 px-3 tabular-nums", strong && "font-medium")}>{value}</td>
  );
}

function Delta({ children }: { children: React.ReactNode }) {
  return <p className="text-sm">{children}</p>;
}
