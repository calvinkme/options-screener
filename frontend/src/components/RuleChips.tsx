import type { RuleResult } from "../types";

/**
 * Failures are red and decide the verdict. Advisory results are amber and never do --
 * that is the user's explicit choice for chart-derived rules. An UNKNOWN is visually
 * distinct from a FAIL because the fix is different: one is a bad trade, the other is
 * missing data.
 */
export function RuleChip({ rule }: { rule: RuleResult }) {
  const tone = rule.advisory
    ? "bg-amber-50 text-amber-900 border-amber-300"
    : rule.passed === null
      ? "bg-slate-100 text-slate-700 border-slate-400 border-dashed"
      : rule.passed
        ? "bg-emerald-50 text-emerald-900 border-emerald-300"
        : "bg-rose-50 text-rose-900 border-rose-300";

  const mark = rule.advisory ? "~" : rule.passed === null ? "?" : rule.passed ? "✓" : "✗";

  return (
    <span
      title={`${rule.label} — required ${rule.required}`}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] leading-tight ${tone}`}
    >
      <span className="font-bold">{mark}</span>
      {rule.message}
    </span>
  );
}

export function RuleChips({
  results,
  only,
}: {
  results: RuleResult[];
  only?: "blocking" | "advisory" | "passing";
}) {
  const filtered = results.filter((r) => {
    if (only === "blocking") return r.blocking;
    if (only === "advisory") return r.advisory;
    if (only === "passing") return r.passed === true && !r.advisory;
    return true;
  });

  if (!filtered.length) return null;

  return (
    <div className="flex flex-wrap gap-1">
      {filtered.map((rule) => (
        <RuleChip key={rule.rule_id} rule={rule} />
      ))}
    </div>
  );
}
