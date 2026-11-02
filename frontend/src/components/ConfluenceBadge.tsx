import type { Confluence } from "../types";

const TONES = [
  "bg-slate-200 text-slate-700",
  "bg-slate-200 text-slate-700",
  "bg-amber-200 text-amber-900",
  "bg-sky-200 text-sky-900",
  "bg-emerald-200 text-emerald-900",
  "bg-emerald-400 text-emerald-950",
];

export function ConfluenceBadge({ confluence }: { confluence: Confluence | null }) {
  if (!confluence) return <span className="text-slate-400">—</span>;

  const capNote = confluence.capped
    ? ` (capped from ${confluence.raw_score} — a mandatory layer failed)`
    : "";

  return (
    <span
      title={`${confluence.guidance}${capNote}\n\n${confluence.layers
        .map((l) => `${l.scored ? "✓" : "✗"} ${l.name}: ${l.detail}`)
        .join("\n")}`}
      className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-bold tnum ${
        TONES[confluence.score] ?? TONES[0]
      }`}
    >
      {confluence.score}/5{confluence.capped ? "*" : ""}
    </span>
  );
}

/** Expanded form: which layers actually contributed, per CLAUDE.md. */
export function ConfluenceDetail({ confluence }: { confluence: Confluence | null }) {
  if (!confluence) return null;

  return (
    <div className="text-[11px]">
      <div className="mb-1 font-semibold text-slate-700">
        Confluence {confluence.score}/5 — {confluence.guidance}
        {confluence.capped && (
          <span className="ml-2 rounded bg-amber-100 px-1 text-amber-900">
            capped from {confluence.raw_score}: a mandatory layer failed
          </span>
        )}
      </div>
      <ul className="space-y-0.5">
        {confluence.layers.map((layer) => (
          <li key={layer.name} className="flex gap-2">
            <span
              className={`w-3 font-bold ${
                layer.scored ? "text-emerald-600" : "text-slate-400"
              }`}
            >
              {layer.scored ? "✓" : "✗"}
            </span>
            <span className="w-20 shrink-0 capitalize text-slate-600">{layer.name}</span>
            <span className="text-slate-700">{layer.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
