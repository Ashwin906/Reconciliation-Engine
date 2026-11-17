import { useCallback, useEffect, useState } from "react";
import { LayoutGrid, AlertTriangle, ScrollText, FileDown, PlayCircle } from "lucide-react";
import { OverviewPage } from "@/components/OverviewPage";
import { SimulatePage } from "@/components/SimulatePage";
import { ExceptionsPage } from "@/components/ExceptionsPage";
import { LedgerPage } from "@/components/LedgerPage";
import { ExportPage } from "@/components/ExportPage";
import { EmptyState } from "@/components/EmptyState";
import { API_ORIGIN, client } from "@/lib/api";
import { cn } from "@/lib/utils";

// Order is the user's actual journey, not an alphabet: you run something
// first, THEN read what it means. "Watch it happen" leads because on a
// fresh install every other screen is legitimately empty until it is used.
const NAV_ITEMS = [
  { value: "simulate", label: "Watch it happen", icon: PlayCircle },
  { value: "overview", label: "Overview", icon: LayoutGrid },
  { value: "exceptions", label: "Needs attention", icon: AlertTriangle },
  { value: "ledger", label: "Activity log", icon: ScrollText },
  { value: "export", label: "Reports", icon: FileDown },
] as const;
type TabValue = (typeof NAV_ITEMS)[number]["value"];
const TAB_VALUES = NAV_ITEMS.map((i) => i.value);

function tabFromUrl(): TabValue {
  const value = new URLSearchParams(window.location.search).get("tab");
  return (TAB_VALUES as readonly string[]).includes(value ?? "") ? (value as TabValue) : "simulate";
}

function App() {
  // No auto-triggered run. The app starts genuinely empty and stays that
  // way until a simulation is run -- so every number on every screen is
  // traceable to a scenario the viewer chose and watched execute, with no
  // seeded placeholder data as a second, confusable source of numbers.
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attentionCount, setAttentionCount] = useState<number | null>(null);
  const [tab, setTab] = useState<TabValue>(tabFromUrl);

  // Picks up an existing run on reload (the backend keeps state across
  // page refreshes) so a refresh mid-demo doesn't look like data loss.
  useEffect(() => {
    client
      .getStatus()
      .then((s) => {
        if (s.last_run_id) setRunId(s.last_run_id);
        if (typeof s.residue_count === "number") setAttentionCount(s.residue_count);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  function handleTabChange(next: TabValue) {
    setTab(next);
    const url = new URL(window.location.href);
    url.searchParams.set("tab", next);
    window.history.replaceState({}, "", url);
  }

  const handleSimulated = useCallback((newRunId: string, residueCount: number) => {
    setRunId(newRunId);
    setAttentionCount(residueCount);
  }, []);

  const handleReset = useCallback(() => {
    setRunId(null);
    setAttentionCount(null);
  }, []);

  const goToSimulate = useCallback(() => handleTabChange("simulate"), []);

  return (
    <div className="min-h-screen bg-background md:flex">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:top-2 focus:left-2 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground focus:text-sm"
      >
        Skip to main content
      </a>

      <nav
        aria-label="Main"
        className="hidden md:flex md:w-56 md:shrink-0 md:flex-col md:border-r md:h-screen md:sticky md:top-0"
      >
        <div className="px-5 py-5 flex items-center gap-2.5">
          <span
            aria-hidden="true"
            className="h-6 w-6 rounded-md bg-primary text-primary-foreground grid place-items-center text-xs font-bold font-mono"
          >
            R
          </span>
          <h1 className="text-[15px] font-bold tracking-tight">Reconciliation</h1>
        </div>
        <div className="flex-1 px-3 space-y-1">
          {NAV_ITEMS.map((item) => (
            <NavButton
              key={item.value}
              item={item}
              active={tab === item.value}
              badge={item.value === "exceptions" ? attentionCount : null}
              onClick={() => handleTabChange(item.value)}
            />
          ))}
        </div>
      </nav>

      <div className="flex-1 min-w-0 pb-16 md:pb-0">
        <header className="border-b px-5 py-3.5 md:hidden flex items-center gap-2.5">
          <span
            aria-hidden="true"
            className="h-6 w-6 rounded-md bg-primary text-primary-foreground grid place-items-center text-xs font-bold font-mono"
          >
            R
          </span>
          <h1 className="text-base font-bold tracking-tight">Reconciliation</h1>
        </header>

        {error && (
          <div className="m-6 max-w-2xl rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm space-y-2">
            <p className="font-semibold text-destructive">Can&rsquo;t reach the reconciliation API</p>
            <p className="text-muted-foreground font-mono text-xs break-words">{error}</p>
            <p className="text-muted-foreground">
              {API_ORIGIN ? (
                <>
                  This dashboard is pointed at <code className="font-mono">{API_ORIGIN}</code>. A free-tier
                  host can take ~30s to wake from sleep — reload to retry.
                </>
              ) : (
                <>
                  This build has no API URL configured. Start the backend with{" "}
                  <code className="font-mono">uvicorn api.main:app --reload</code>, or rebuild with{" "}
                  <code className="font-mono">VITE_API_BASE</code> set to the deployed backend, then reload.
                </>
              )}
            </p>
          </div>
        )}

        {!error && (
          <main id="main-content">
            {tab === "simulate" && (
              <SimulatePage onSimulated={handleSimulated} onReset={handleReset} />
            )}

            {tab === "overview" &&
              (runId ? (
                <OverviewPage runId={runId} onNavigate={(t) => handleTabChange(t as TabValue)} />
              ) : (
                <EmptyState onAction={goToSimulate} />
              ))}

            {tab === "exceptions" &&
              (runId ? (
                <ExceptionsPage runId={runId} onCountChange={setAttentionCount} />
              ) : (
                <EmptyState onAction={goToSimulate} />
              ))}

            {tab === "ledger" && (runId ? <LedgerPage /> : <EmptyState onAction={goToSimulate} />)}

            {tab === "export" &&
              (runId ? <ExportPage runId={runId} /> : <EmptyState onAction={goToSimulate} />)}
          </main>
        )}
      </div>

      {!error && (
        <nav
          aria-label="Main"
          className="md:hidden fixed bottom-0 inset-x-0 border-t bg-card flex items-stretch z-40"
        >
          {NAV_ITEMS.map((item) => (
            <MobileNavButton
              key={item.value}
              item={item}
              active={tab === item.value}
              badge={item.value === "exceptions" ? attentionCount : null}
              onClick={() => handleTabChange(item.value)}
            />
          ))}
        </nav>
      )}
    </div>
  );
}

type NavItem = (typeof NAV_ITEMS)[number];

function NavButton({
  item,
  active,
  badge,
  onClick,
}: {
  item: NavItem;
  active: boolean;
  badge: number | null;
  onClick: () => void;
}) {
  const Icon = item.icon;
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "w-full flex items-center gap-2.5 rounded-md px-3 py-2 text-sm text-left cursor-pointer transition-colors",
        active ? "bg-accent text-accent-foreground font-medium" : "text-muted-foreground hover:bg-accent/50"
      )}
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
      <span className="truncate flex-1">{item.label}</span>
      {!!badge && (
        <span className="text-xs font-medium rounded-full bg-warning text-warning-foreground min-w-5 h-5 px-1 flex items-center justify-center tabular-nums">
          {badge}
        </span>
      )}
    </button>
  );
}

function MobileNavButton({
  item,
  active,
  badge,
  onClick,
}: {
  item: NavItem;
  active: boolean;
  badge: number | null;
  onClick: () => void;
}) {
  const Icon = item.icon;
  return (
    <button
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex-1 flex flex-col items-center justify-center gap-0.5 py-2 relative cursor-pointer",
        active ? "text-primary" : "text-muted-foreground"
      )}
    >
      <span className="relative">
        <Icon className="h-5 w-5" aria-hidden="true" />
        {!!badge && (
          <span className="absolute -top-1 -right-2 text-[10px] font-medium rounded-full bg-warning text-warning-foreground min-w-4 h-4 px-1 flex items-center justify-center tabular-nums">
            {badge}
          </span>
        )}
      </span>
      <span className="text-[10px] leading-none text-center px-0.5">{item.label}</span>
    </button>
  );
}

export default App;
