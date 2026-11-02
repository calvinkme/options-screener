import type { Row, SelectionGuide } from "../types";

/**
 * "How this contract was chosen": each course-notes rule for building the trade, the
 * contract the screener actually picked, and whether that pick obeys the rule. Rendered
 * from the same rule checks that decide the verdict, so it cannot drift from them.
 */
export function SelectionPanel({ row }: { row: Row }) {
  const guide = row.selection_guide;
  if (!guide || !row.selection?.length) return null;

  const legs = row.legs
    .map((l) => `${l.qty < 0 ? "Sell" : "Buy"} ${l.strike}${l.right}`)
    .join(" · ");
  const dte = row.metrics["dte"];
  const expiry = row.metrics["expiry"];

  return (
    <div className="rounded border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-3 py-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          How this contract was chosen
        </div>
        <div className="mt-0.5 text-[12px] text-slate-800">
          <b>{legs}</b>
          {expiry ? (
            <span className="text-slate-500">
              {" "}
              · expiry {String(expiry)} ({String(dte)} DTE)
            </span>
          ) : null}
        </div>
      </div>

      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-left text-slate-500">
            <th className="w-6 px-3 py-1.5" />
            <th className="w-28 px-2 py-1.5 font-medium">Step</th>
            <th className="px-2 py-1.5 font-medium">Rule</th>
            <th className="px-2 py-1.5 font-medium">What was picked</th>
          </tr>
        </thead>
        <tbody>
          {row.selection.map((s) => (
            <tr key={s.step} className="border-t border-slate-100 align-top">
              <td className="px-3 py-1.5">
                <Mark passed={s.passed} />
              </td>
              <td className="px-2 py-1.5 font-medium text-slate-700">{s.step}</td>
              <td className="px-2 py-1.5 text-slate-600">{s.rule}</td>
              <td
                className={`px-2 py-1.5 ${
                  s.passed === false
                    ? "text-rose-700"
                    : s.passed === null
                      ? "text-amber-700"
                      : "text-slate-700"
                }`}
              >
                {s.chosen}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="border-t border-slate-200 px-3 py-2 text-[11px] text-slate-500">
        <span className="font-medium text-slate-600">How the screener picks: </span>
        {guide.method}
        <div className="mt-0.5 text-slate-400">Source: {guide.source}</div>
      </div>
    </div>
  );
}

/** The strategy's selection rules on their own, for the strategy picker. */
export function StrategyRules({ label, guide }: { label: string; guide: SelectionGuide }) {
  return (
    <details className="rounded border border-slate-200 bg-white px-3 py-2 text-[11px]">
      <summary className="cursor-pointer font-semibold text-slate-700">
        Rules for {label} — how the contract is chosen
      </summary>
      <ol className="mt-2 list-decimal space-y-1 pl-5 text-slate-600">
        {guide.steps.map((s) => (
          <li key={s.step}>
            <b className="text-slate-700">{s.step}:</b> {s.rule}
          </li>
        ))}
      </ol>
      <p className="mt-2 text-slate-500">
        <span className="font-medium text-slate-600">How the screener picks: </span>
        {guide.method}
      </p>
      <p className="text-slate-400">Source: {guide.source}</p>
    </details>
  );
}

function Mark({ passed }: { passed: boolean | null }) {
  if (passed === true) return <span className="font-bold text-emerald-600">✓</span>;
  if (passed === false) return <span className="font-bold text-rose-600">✗</span>;
  return <span className="font-bold text-amber-600">?</span>;
}
