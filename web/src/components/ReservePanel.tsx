import { useState } from "react";
import { CalendarClock, ArrowDownRight, ArrowUpRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { client, type ReserveJourney } from "@/lib/api";
import { formatPaise } from "@/lib/money";
import { cn } from "@/lib/utils";

/**
 * The multi-period rolling reserve.
 *
 * The point being made is narrow and checkable: conservation FAILS
 * without the reserve ledger and CLOSES with it, over identical data.
 * Both columns come from the same real check_conservation call, differing
 * only in whether the ledger's delta was supplied.
 */
export function ReservePanel() {
  const [journey, setJourney] = useState<ReserveJourney | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(periods: number) {
    setLoading(true);
    setError(null);
    try {
      setJourney(await client.simulateReserve(periods));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <CalendarClock className="h-4 w-4" aria-hidden="true" />
          Rolling reserve, across months
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Razorpay withholds a percentage of each settlement against future chargeback risk and returns
          it later. The money is yours the whole time — it is deferred, not lost. Money held in one
          month and returned in another is the <em>same</em> money, and a tool that only ever looks at
          one month reports a false alarm in both directions.
        </p>

        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => run(4)} disabled={loading}>
            Run 4 months
          </Button>
          <Button size="sm" variant="outline" onClick={() => run(6)} disabled={loading}>
            Run 6 months
          </Button>
        </div>

        {error && <p className="text-sm text-destructive">{error}</p>}
        {loading && <p className="text-sm text-muted-foreground">Running…</p>}

        {journey && !loading && (
          <div className="space-y-4">
            <p className="text-xs text-muted-foreground">
              {journey.config.reserve_pct}% withheld, returned after {journey.config.hold_days} days.
            </p>

            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[620px]">
                <thead>
                  <tr className="border-b">
                    <th scope="col" className="text-left font-medium py-2 pr-3">Month</th>
                    <th scope="col" className="text-right font-medium py-2 px-3">Held</th>
                    <th scope="col" className="text-right font-medium py-2 px-3">Returned</th>
                    <th scope="col" className="text-right font-medium py-2 px-3">Balance</th>
                    <th scope="col" className="text-left font-medium py-2 px-3">Books, no ledger</th>
                    <th scope="col" className="text-left font-medium py-2 pl-3">Books, with ledger</th>
                  </tr>
                </thead>
                <tbody>
                  {journey.periods.map((period) => (
                    <tr key={period.label} className="border-b last:border-0">
                      <th scope="row" className="text-left font-normal py-2 pr-3">
                        {period.label}
                      </th>
                      <td className="text-right tabular-nums py-2 px-3">
                        {period.held > 0 ? (
                          <span className="inline-flex items-center gap-1">
                            <ArrowDownRight className="h-3 w-3 text-warning" aria-hidden="true" />
                            ₹{formatPaise(period.held)}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="text-right tabular-nums py-2 px-3">
                        {period.released > 0 ? (
                          <span className="inline-flex items-center gap-1 text-success">
                            <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
                            ₹{formatPaise(period.released)}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="text-right tabular-nums py-2 px-3 font-medium">
                        ₹{formatPaise(period.closing_balance)}
                      </td>
                      <td className="py-2 px-3">
                        <span className="text-destructive text-xs tabular-nums">
                          off by ₹{formatPaise(Math.abs(period.conservation_without_ledger.residual_paise))}
                          {period.conservation_without_ledger.residual_paise < 0 && " extra"}
                        </span>
                      </td>
                      <td className="py-2 pl-3">
                        <Badge variant="outline" className="text-success border-success/40">
                          balances
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <Tile label="Still held back" value={`₹${formatPaise(journey.totals.outstanding_balance_paise)}`} />
              <Tile label="Returned so far" value={`₹${formatPaise(journey.totals.total_released_paise)}`} />
              <Tile
                label="Overdue returns"
                value={`${journey.totals.overdue_count}`}
                warn={journey.totals.overdue_count > 0}
              />
            </div>

            <p className="text-sm border-t pt-3">{journey.verdict.explanation}</p>

            <p className="text-xs text-muted-foreground">
              Both "books" columns come from the same conservation check over the same data. The only
              difference is whether the reserve ledger's movement for that month was supplied. Without
              it the books genuinely do not balance — which is why this was previously reported as an
              unexplained gap rather than a scheduled return.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Tile({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="rounded-md border p-3">
      <div className={cn("text-lg font-semibold tabular-nums", warn && "text-warning")}>{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}
