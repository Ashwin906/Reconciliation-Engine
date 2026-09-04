import { Fragment, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Lock, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { DetailFields } from "@/components/DetailFields";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { client, type LedgerRow } from "@/lib/api";

// Every run in this app comes from a simulation, and simulations all run
// as this one merchant -- see api/routes.py's SIM_MERCHANT_ID. There is
// no login and no multi-tenancy, so a single constant is the honest shape
// rather than a merchant picker over accounts that do not exist.
const MERCHANT_ID = "M_SIM";

/** Table-width versions of the same labels DetailFields uses in the
 *  expanded row. Deliberately shorter rather than different: a column
 *  reading "T4" while the detail underneath says "AI investigation" makes
 *  a reader wonder whether they are two separate facts. */
const TIER_SHORT: Record<string, string> = {
  T1: "Exact",
  T2: "Checked",
  T3: "Closest fit",
  T4: "AI",
};

const VERDICT_SHORT: Record<string, string> = {
  ACCEPT: "Verified",
  REJECT: "Failed check",
  UNVERIFIABLE: "Needs a human look",
};

/** DOMAIN_KNOWLEDGE.md / DECISIONS.md D-16: the ledger never records WHO
 * a human is (no user accounts in this system) -- so "decided by" is
 * derived honestly from gate + status, not invented. AUTO always means
 * the system decided; anything else means a human's decision is either
 * still pending or already recorded via approve/reject. */
function decidedBy(row: LedgerRow): string {
  if (row.gate === "AUTO") return "System (auto)";
  // gate=REJECT is set by pipeline/run.py when INDEPENDENT VERIFICATION
  // did not accept the proposal -- nobody reviewed it, the arithmetic
  // simply did not tie. Attributing that to a human (as this did before)
  // credits a person with a decision the system made on its own, in the
  // one screen whose whole job is an accurate record of who decided what.
  if (row.gate === "REJECT") return "System (failed verification)";
  if (row.status === "APPROVED" || row.status === "REJECTED") return "Human";
  return "Pending human review";
}

export function LedgerPage() {
  const [rows, setRows] = useState<LedgerRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showRaw, setShowRaw] = useState<string | null>(null);

  useEffect(() => {
    client.getLedger(MERCHANT_ID).then(setRows).catch((e) => setError(String(e)));
  }, []);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.subject.toLowerCase().includes(q) ||
        r.ts.toLowerCase().includes(q) ||
        (r.hypothesis ?? "").toLowerCase().includes(q)
    );
  }, [rows, query]);

  if (error) return <div className="p-8 text-destructive text-sm">{error}</div>;
  if (rows === null) return <div className="p-8 text-muted-foreground">Loading…</div>;

  return (
    <div className="max-w-5xl mx-auto px-5 py-6 lg:px-8 lg:py-8 space-y-4">
      <p className="text-sm text-muted-foreground">
        Append-only. Every row is permanent — a correction adds a new row, it never edits one.
      </p>

      {rows.length === 0 ? (
        <Card>
          <CardContent className="pt-6 text-sm text-muted-foreground">
            No entries yet. Entries appear here when the AI investigator (T4) forms a hypothesis
            about an unresolved case and it goes through the gate. A run where everything
            reconciled cleanly has nothing to investigate — so this feed is genuinely, not
            artificially, empty.
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="relative">
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"
              aria-hidden="true"
            />
            <label htmlFor="ledger-search" className="sr-only">
              Search by settlement id, date, or hypothesis
            </label>
            <Input
              id="ledger-search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by settlement id, date, or hypothesis…"
              className="pl-9"
            />
          </div>

          <div className="border rounded-md overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8"><span className="sr-only">Expand</span></TableHead>
                  <TableHead>Time</TableHead>
                  <TableHead>Subject</TableHead>
                  <TableHead>Tier</TableHead>
                  <TableHead>Verdict</TableHead>
                  <TableHead>Decided by</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((row) => {
                  const isOpen = expanded === row.id;
                  return (
                    <Fragment key={row.id}>
                      <TableRow
                        onClick={() => setExpanded(isOpen ? null : row.id)}
                        aria-expanded={isOpen}
                        className="cursor-pointer"
                      >
                        <TableCell>
                          {isOpen ? (
                            <ChevronDown className="h-4 w-4" aria-hidden="true" />
                          ) : (
                            <ChevronRight className="h-4 w-4" aria-hidden="true" />
                          )}
                        </TableCell>
                        <TableCell className="font-mono text-xs text-muted-foreground whitespace-nowrap">
                          <Lock className="inline h-3 w-3 mr-1.5 -mt-0.5" aria-hidden="true" />
                          {new Date(row.ts).toLocaleString()}
                        </TableCell>
                        <TableCell className="font-mono text-xs">{row.subject}</TableCell>
                        <TableCell>
                          <Badge variant="outline">{TIER_SHORT[row.tier] ?? row.tier}</Badge>
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              row.verifier_result === "ACCEPT"
                                ? "default"
                                : row.verifier_result === "REJECT"
                                  ? "destructive"
                                  : "secondary"
                            }
                          >
                            {VERDICT_SHORT[row.verifier_result] ?? row.verifier_result}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">{decidedBy(row)}</TableCell>
                      </TableRow>
                      {isOpen && (
                        <TableRow key={`${row.id}-detail`}>
                          <TableCell colSpan={6} className="bg-muted/30 p-4">
                            <div className="space-y-3">
                              {/* `evidence` is a nested object -- rendered
                                  as its own grid below rather than
                                  stringified into a single unreadable cell. */}
                              <DetailFields
                                data={Object.fromEntries(
                                  Object.entries(row).filter(([k]) => k !== "evidence")
                                )}
                              />

                              {row.evidence && Object.keys(row.evidence).length > 0 && (
                                <div>
                                  <p className="text-xs font-medium mb-1.5">Evidence the AI cited</p>
                                  <DetailFields data={row.evidence} />
                                </div>
                              )}

                              <button
                                onClick={() => setShowRaw(showRaw === row.id ? null : row.id)}
                                aria-expanded={showRaw === row.id}
                                className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground cursor-pointer"
                              >
                                {showRaw === row.id ? "Hide raw record" : "Show raw record"}
                              </button>

                              {showRaw === row.id && (
                                <pre
                                  className="text-xs font-mono p-3 border rounded-md bg-background overflow-x-auto tabular-nums"
                                  aria-label={`Raw ledger entry for ${row.subject}`}
                                >
                                  {JSON.stringify(row, null, 2)}
                                </pre>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
            {filtered.length === 0 && (
              <p className="text-sm text-muted-foreground text-center py-8">
                No ledger entries match “{query}”.
              </p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
