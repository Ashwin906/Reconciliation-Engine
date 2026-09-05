import { useEffect, useState } from "react";
import { Plus, Trash2, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { client, type DefectKind, type DefectParam } from "@/lib/api";
import { cn } from "@/lib/utils";

type Chosen = { id: number; kind: string; params: Record<string, number | string> };

let nextId = 1;

/**
 * Lets the viewer compose their own defect plan.
 *
 * The controls are generated from the catalogue the backend derives by
 * INTROSPECTING gen/defects.py (see sim/catalog.py) -- so what is on
 * screen is exactly what the injectors accept, and cannot drift into
 * offering a control that does nothing. That is the point: a judge asking
 * "what if I break it differently" gets to actually do it, on the same
 * code path every built-in scenario uses.
 */
export function BreakItYourself({
  onRun,
  running,
}: {
  onRun: (defects: { kind: string; params: Record<string, number | string> }[], seed: number) => void;
  running: boolean;
}) {
  const [catalog, setCatalog] = useState<DefectKind[] | null>(null);
  const [chosen, setChosen] = useState<Chosen[]>([]);
  const [seed, setSeed] = useState(7);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    client.listDefects().then(setCatalog).catch((e) => setError(String(e)));
  }, []);

  function defaultsFor(kind: string): Record<string, number | string> {
    const def = catalog?.find((d) => d.kind === kind);
    const out: Record<string, number | string> = {};
    def?.params.forEach((p) => {
      out[p.name] = p.default;
    });
    return out;
  }

  function add(kind: string) {
    setChosen((prev) => [...prev, { id: nextId++, kind, params: defaultsFor(kind) }]);
  }

  function update(id: number, name: string, value: number | string) {
    setChosen((prev) =>
      prev.map((c) => (c.id === id ? { ...c, params: { ...c.params, [name]: value } } : c))
    );
  }

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!catalog) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const available = catalog.filter((d) => !chosen.some((c) => c.kind === d.kind));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium flex items-center gap-2">
          <Wrench className="h-4 w-4" aria-hidden="true" />
          Break it yourself
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Pick what goes wrong and how badly. Nothing here is pre-arranged — it runs the same generator
          and the same pipeline as every built-in scenario, and the system has no idea what you chose.
        </p>

        {chosen.length > 0 && (
          <div className="space-y-3">
            {chosen.map((c) => {
              const def = catalog.find((d) => d.kind === c.kind)!;
              return (
                <div key={c.id} className="rounded-md border p-3 space-y-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium">{def.label}</span>
                        {def.costs_money ? (
                          <Badge variant="secondary">costs money</Badge>
                        ) : (
                          <Badge variant="outline">data only</Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">{def.description}</p>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setChosen((prev) => prev.filter((x) => x.id !== c.id))}
                      aria-label={`Remove ${def.label}`}
                      className="shrink-0"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2">
                    {def.params.map((p) => (
                      <ParamControl
                        key={p.name}
                        param={p}
                        value={c.params[p.name]}
                        onChange={(v) => update(c.id, p.name, v)}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {available.length > 0 && chosen.length < 6 && (
          <div className="space-y-1.5">
            <label htmlFor="add-defect" className="text-sm font-medium">
              {chosen.length === 0 ? "What should go wrong?" : "Add another problem"}
            </label>
            <Select value="" onValueChange={(v) => v && add(v)}>
              <SelectTrigger id="add-defect" className="w-full">
                <SelectValue placeholder="Choose a problem to inject…" />
              </SelectTrigger>
              <SelectContent>
                {available.map((d) => (
                  <SelectItem key={d.kind} value={d.kind}>
                    {d.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="flex flex-wrap items-end gap-3 pt-1">
          <div className="space-y-1.5">
            <label htmlFor="seed" className="text-xs text-muted-foreground">
              Seed (same seed = same data)
            </label>
            <input
              id="seed"
              type="number"
              value={seed}
              onChange={(e) => setSeed(Number(e.target.value) || 0)}
              className="h-9 w-24 rounded-md border bg-transparent px-3 text-sm tabular-nums"
            />
          </div>
          <Button
            onClick={() => onRun(chosen.map(({ kind, params }) => ({ kind, params })), seed)}
            disabled={running || chosen.length === 0}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            Break it
          </Button>
          {chosen.length === 0 && (
            <span className="text-xs text-muted-foreground">Pick at least one problem first.</span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ParamControl({
  param,
  value,
  onChange,
}: {
  param: DefectParam;
  value: number | string;
  onChange: (value: number | string) => void;
}) {
  const id = `param-${param.name}`;

  if (param.choices) {
    return (
      <div className="space-y-1.5">
        <label htmlFor={id} className="text-xs text-muted-foreground">
          {param.label}
        </label>
        <Select value={String(value)} onValueChange={(v) => v !== null && onChange(v)}>
          <SelectTrigger id={id} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {param.choices.map((choice) => (
              <SelectItem key={choice} value={choice}>
                {choice}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-xs text-muted-foreground flex justify-between gap-2">
        <span>{param.label}</span>
        <span className="tabular-nums font-medium text-foreground">
          {value}
          {param.unit ? ` ${param.unit}` : ""}
        </span>
      </label>
      <input
        id={id}
        type="range"
        min={param.min}
        max={param.max}
        step={param.step}
        value={Number(value)}
        onChange={(e) => onChange(Number(e.target.value))}
        className={cn(
          "w-full h-2 rounded-full appearance-none bg-secondary cursor-pointer",
          "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-4",
          "[&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:rounded-full",
          "[&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:cursor-pointer",
          "[&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full",
          "[&::-moz-range-thumb]:bg-primary [&::-moz-range-thumb]:border-0"
        )}
      />
    </div>
  );
}
