import { PlayCircle, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Shown on every data screen before any simulation has run.
 *
 *  This app deliberately ships with NO seeded data, so an empty screen
 *  here is the honest truth rather than a loading failure -- and it says
 *  so, plus exactly what to do about it. An empty state that explains
 *  itself and offers the next action beats a spinner that never resolves. */
export function EmptyState({ onAction }: { onAction: () => void }) {
  return (
    <div className="max-w-md mx-auto p-6 py-16 text-center space-y-4">
      <div className="inline-flex rounded-full bg-accent p-3">
        <PlayCircle className="h-6 w-6 text-primary" aria-hidden="true" />
      </div>
      <div className="space-y-1.5">
        <h2 className="text-base font-semibold">Nothing here yet</h2>
        <p className="text-sm text-muted-foreground">
          This screen shows real reconciliation results — and there aren't any yet, because nothing has
          been run. No sample data, no placeholder numbers.
        </p>
        <p className="text-sm text-muted-foreground">
          Run a scenario and every screen fills with that run's real figures.
        </p>
      </div>
      <Button onClick={onAction}>
        Run a scenario
        <ArrowRight className="h-4 w-4" aria-hidden="true" />
      </Button>
    </div>
  );
}
