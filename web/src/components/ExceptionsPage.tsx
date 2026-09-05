import { useEffect, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { DetailFields } from "@/components/DetailFields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { client, type ExceptionItem } from "@/lib/api";
import { formatPaise } from "@/lib/money";
import { reasonLabel } from "@/lib/labels";
import { cn } from "@/lib/utils";

// Tap-to-fill options for the reject reason -- a free-text box with
// nothing in it is intimidating for a merchant who doesn't know the
// "right" words to use. These chips fill the (still required) reason;
// the textarea stays editable for anyone who wants to add detail.
const REJECT_REASONS = [
  "This looks wrong to me",
  "I already got this money another way",
  "Not sure — flag this for someone to check",
];

export function ExceptionsPage({
  runId,
  onCountChange,
}: {
  runId: string;
  onCountChange?: (count: number) => void;
}) {
  const [exceptions, setExceptions] = useState<ExceptionItem[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showDetail, setShowDetail] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [rejectTarget, setRejectTarget] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  useEffect(() => {
    client.listExceptions(runId).then(setExceptions).catch((e) => setError(String(e)));
  }, [runId]);

  // Keeps the sidebar/bottom-nav badge in sync as items leave the queue,
  // instead of only reflecting the count from the initial run trigger.
  useEffect(() => {
    onCountChange?.(exceptions.length);
  }, [exceptions.length, onCountChange]);

  // sorted by rupee amount descending -- UI_GENERATION_PROMPT.md's own spec
  const sorted = [...exceptions].sort((a, b) => (impact(b) ?? 0) - (impact(a) ?? 0));

  async function handleApprove(id: string) {
    setBusy(id);
    try {
      await client.approveException(runId, id);
      setExceptions((prev) => prev.filter((e) => e.settlement_id !== id));
      setAnnouncement(`${id} approved and removed from the queue.`);
    } finally {
      setBusy(null);
    }
  }

  function openRejectDialog(id: string) {
    setRejectReason("");
    setRejectTarget(id);
  }

  async function confirmReject() {
    if (!rejectTarget || !rejectReason.trim()) return;
    setBusy(rejectTarget);
    try {
      await client.rejectException(runId, rejectTarget, rejectReason.trim());
      setExceptions((prev) => prev.filter((e) => e.settlement_id !== rejectTarget));
      setAnnouncement(`${rejectTarget} rejected and removed from the queue.`);
      setRejectTarget(null);
    } finally {
      setBusy(null);
    }
  }

  if (error) return <div className="p-8 text-destructive text-sm">{error}</div>;

  if (exceptions.length === 0) {
    return (
      <div className="max-w-5xl mx-auto px-5 py-6 lg:px-8 lg:py-8">
        <div className="flex items-center gap-2 text-success text-sm rounded-md border border-success/30 bg-success/10 px-4 py-3">
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
          All checked. Nothing needs you.
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-5 py-6 lg:px-8 lg:py-8 space-y-2">
      {/* Web Interface Guidelines: async list updates need aria-live so a
          screen-reader user is told an item left the queue after
          approve/reject, not just left staring at a list that silently
          shrank. Visually hidden, announced only. */}
      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>

      <h2 className="text-sm font-medium text-muted-foreground mb-3">
        {sorted.length} item{sorted.length === 1 ? " needs" : "s need"} your attention
      </h2>
      {sorted.map((exc) => {
        const isOpen = expanded === exc.settlement_id;
        const amt = impact(exc);
        return (
          <Card key={exc.settlement_id}>
            <CardContent className="p-0">
              <button
                onClick={() => setExpanded(isOpen ? null : exc.settlement_id)}
                aria-expanded={isOpen}
                className="w-full flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left cursor-pointer transition-colors hover:bg-accent/50 focus-visible:outline-2 focus-visible:outline-ring focus-visible:outline-offset-[-2px]"
              >
                {isOpen ? (
                  <ChevronDown className="h-4 w-4 shrink-0" aria-hidden="true" />
                ) : (
                  <ChevronRight className="h-4 w-4 shrink-0" aria-hidden="true" />
                )}
                <span className="tabular-nums font-semibold shrink-0">
                  {amt != null ? `₹${formatPaise(amt)}` : "—"}
                </span>
                <span className="font-mono text-xs text-muted-foreground truncate min-w-0">{exc.settlement_id}</span>
                <Badge variant="secondary" className="shrink-0">{reasonLabel(exc.reason)}</Badge>
              </button>

              {isOpen && (
                <div className="border-t px-4 py-4 bg-muted/30 space-y-3">
                  <p className="text-sm">{reasonLabel(exc.reason)}.</p>

                  {/* The readable view is the DEFAULT, not the fallback.
                      Raw JSON stays available one more click away for a
                      technical reviewer who wants the unformatted record. */}
                  <DetailFields data={exc.detail} />

                  <button
                    onClick={() =>
                      setShowDetail((prev) => ({ ...prev, [exc.settlement_id]: !prev[exc.settlement_id] }))
                    }
                    aria-expanded={!!showDetail[exc.settlement_id]}
                    className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground cursor-pointer"
                  >
                    {showDetail[exc.settlement_id] ? "Hide raw record" : "Show raw record"}
                  </button>

                  {showDetail[exc.settlement_id] && (
                    <pre
                      className="text-xs font-mono bg-background border rounded-md p-3 overflow-x-auto tabular-nums"
                      aria-label={`Raw record for ${exc.settlement_id}`}
                    >
                      {JSON.stringify(exc.detail, null, 2)}
                    </pre>
                  )}

                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      disabled={busy === exc.settlement_id}
                      onClick={() => handleApprove(exc.settlement_id)}
                    >
                      Approve {amt != null ? `₹${formatPaise(amt)}` : ""}
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy === exc.settlement_id}
                      onClick={() => openRejectDialog(exc.settlement_id)}
                    >
                      Reject
                    </Button>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}

      {/* Web Interface Guidelines anti-pattern: window.prompt() for a
          required-reason field -- not stylable, not keyboard/focus
          managed by us, invisible to screen-reader users the same way
          the rest of this app is. A real Dialog does all three
          correctly and matches the rest of the UI. */}
      <Dialog open={rejectTarget !== null} onOpenChange={(open) => !open && setRejectTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject exception</DialogTitle>
            <DialogDescription>
              {rejectTarget} — a reason is required before this can be rejected.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {REJECT_REASONS.map((r) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => setRejectReason(r)}
                  className={cn(
                    "text-xs rounded-full border px-3 py-1.5 cursor-pointer transition-colors",
                    rejectReason === r
                      ? "bg-primary text-primary-foreground border-primary"
                      : "hover:bg-accent/50"
                  )}
                >
                  {r}
                </button>
              ))}
            </div>
            <div className="space-y-2">
              <label htmlFor="reject-reason" className="text-sm font-medium">
                Anything else to add? (optional)
              </label>
              <Textarea
                id="reject-reason"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                placeholder="Tap an option above, or type your own reason…"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={!rejectReason.trim() || busy === rejectTarget}
              onClick={confirmReject}
            >
              Confirm reject
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function impact(exc: ExceptionItem): number | null {
  const d = exc.detail as Record<string, unknown>;
  const candidates = ["expected_amount", "gap", "expected"];
  for (const key of candidates) {
    const v = d[key];
    if (typeof v === "number") return Math.abs(v);
  }
  return null;
}
