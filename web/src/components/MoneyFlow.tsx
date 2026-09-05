import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { MoneyFlow as MoneyFlowData } from "@/lib/api";
import { formatPaise } from "@/lib/money";
import { cn } from "@/lib/utils";

/**
 * A two-level Sankey of the conservation identity: everything collected
 * flows either to the merchant's bank, into a named deduction, or into an
 * unaccounted residual.
 *
 * Hand-drawn SVG rather than recharts' <Sankey>: this flow has a fixed,
 * known shape (one source, one settled branch, N deductions, one
 * residual), so a generic graph layout engine would add a dependency and
 * a layout it has to guess at, to draw something we can place exactly.
 * Being able to place it exactly is what lets the residual band be
 * unmissable when it is non-zero -- the single most important thing on
 * the diagram.
 */

const BAND_COLORS = [
  "var(--chart-1)",
  "var(--chart-5)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-2)",
];

export function MoneyFlowDiagram({ flow }: { flow: MoneyFlowData }) {
  const collected = flow.collected_paise;
  if (collected <= 0) return null;

  const residual = Math.abs(flow.residual_paise);

  // Every band that leaves the source, in draw order: the money that
  // actually reached the bank first (it is the outcome that matters),
  // then each deduction, then anything unaccounted for.
  const bands = [
    { label: "Reached your bank", paise: flow.reached_bank_paise, tone: "settled" as const },
    ...flow.deductions.map((d) => ({ label: d.label, paise: d.paise, tone: "deduction" as const })),
    ...(residual > 0
      ? [{ label: "Unaccounted for", paise: residual, tone: "residual" as const }]
      : []),
  ];

  const width = 720;
  const height = Math.max(220, bands.length * 46);
  const sourceX = 8;
  const sourceW = 132;
  const targetX = width - 210;
  const gap = 5;

  // Bands are laid out proportionally to real value, but every band gets
  // a visible minimum height. A 0-paise door drawn at literally 0px would
  // vanish, and "no chargebacks this period" is information a merchant
  // should be able to read off the diagram.
  const MIN_BAND = 3;
  const totalGaps = gap * (bands.length - 1);
  const available = height - totalGaps;
  const rawHeights = bands.map((b) => (b.paise / collected) * available);
  const deficit = rawHeights.reduce((sum, h) => sum + Math.max(0, MIN_BAND - h), 0);
  const scalable = rawHeights.reduce((sum, h) => sum + Math.max(0, h - MIN_BAND), 0);
  const heights = rawHeights.map((h) =>
    h <= MIN_BAND ? MIN_BAND : MIN_BAND + (h - MIN_BAND) * (scalable ? (scalable - deficit) / scalable : 1)
  );

  let cursor = 0;
  const laid = bands.map((band, i) => {
    const top = cursor;
    cursor += heights[i] + gap;
    return { ...band, top, height: heights[i], color: colorFor(band.tone, i) };
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">Where the money went</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="overflow-x-auto">
          <svg
            viewBox={`0 0 ${width} ${height}`}
            className="w-full min-w-[520px]"
            role="img"
            aria-label={describe(flow)}
          >
            {/* the source: everything customers paid */}
            <rect x={sourceX} y={0} width={sourceW} height={height} rx={4} fill="var(--chart-1)" opacity={0.9} />
            <text x={sourceX + 10} y={22} className="fill-white text-[13px] font-semibold">
              Collected
            </text>
            <text x={sourceX + 10} y={40} className="fill-white text-[12px] tabular-nums">
              ₹{formatPaise(collected)}
            </text>

            {laid.map((band) => {
              const midSource = (band.top + band.height / 2 + height / 2) / 2;
              const midTarget = band.top + band.height / 2;
              return (
                <g key={band.label}>
                  {/* the ribbon */}
                  <path
                    d={ribbon(sourceX + sourceW, midSource, targetX, midTarget, Math.max(band.height, MIN_BAND))}
                    fill={band.color}
                    opacity={band.tone === "settled" ? 0.5 : band.tone === "residual" ? 0.65 : 0.32}
                  />
                  <rect
                    x={targetX}
                    y={band.top}
                    width={10}
                    height={Math.max(band.height, MIN_BAND)}
                    rx={2}
                    fill={band.color}
                  />
                  <text
                    x={targetX + 18}
                    y={midTarget - 1}
                    className="fill-[var(--foreground)] text-[12px]"
                  >
                    {band.label}
                  </text>
                  <text
                    x={targetX + 18}
                    y={midTarget + 13}
                    className="fill-[var(--muted-foreground)] text-[11px] tabular-nums"
                  >
                    ₹{formatPaise(band.paise)}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>

        <p className={cn("text-sm", flow.closes ? "text-success" : "text-warning")}>
          {flow.closes
            ? "Every rupee collected is accounted for — the flow closes to exactly ₹0.00."
            : `₹${formatPaise(residual)} is unaccounted for. ${
                flow.residual_paise > 0
                  ? "Money was collected that never left through any known door."
                  : "Money left without a matching collection."
              }`}
        </p>
      </CardContent>
    </Card>
  );
}

function colorFor(tone: "settled" | "deduction" | "residual", index: number): string {
  if (tone === "settled") return "var(--chart-2)";
  if (tone === "residual") return "var(--destructive)";
  return BAND_COLORS[index % BAND_COLORS.length];
}

/** A cubic ribbon from the source edge to a target band. */
function ribbon(x1: number, y1: number, x2: number, y2: number, thickness: number): string {
  const half = thickness / 2;
  const cx = (x1 + x2) / 2;
  return [
    `M ${x1} ${y1 - half}`,
    `C ${cx} ${y1 - half}, ${cx} ${y2 - half}, ${x2} ${y2 - half}`,
    `L ${x2} ${y2 + half}`,
    `C ${cx} ${y2 + half}, ${cx} ${y1 + half}, ${x1} ${y1 + half}`,
    "Z",
  ].join(" ");
}

/** Screen-reader description -- the diagram's content as a sentence,
 *  since an SVG of ribbons conveys nothing on its own. */
function describe(flow: MoneyFlowData): string {
  const parts = flow.deductions
    .filter((d) => d.paise > 0)
    .map((d) => `${d.label} ₹${formatPaise(d.paise)}`);
  return (
    `Of ₹${formatPaise(flow.collected_paise)} collected, ` +
    `₹${formatPaise(flow.reached_bank_paise)} reached the bank. ` +
    (parts.length ? `Deductions: ${parts.join(", ")}. ` : "") +
    (flow.closes
      ? "Nothing is unaccounted for."
      : `₹${formatPaise(Math.abs(flow.residual_paise))} is unaccounted for.`)
  );
}
