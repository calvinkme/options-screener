import { useState } from "react";
import type { Row, ScanResponse } from "../types";
import { ConfluenceBadge } from "./ConfluenceBadge";
import { RuleChips } from "./RuleChips";
import { RowDetail } from "./ScreenerTable";

/**
 * View B: one ticker, every strategy. The point is the inversion -- a name with IV at the
 * floor of its year fails the premium sellers and passes the premium buyers, and seeing both
 * answers side by side is what makes that obvious.
 */
export function TickerView({ result }: { result: ScanResponse }) {
  const [open, setOpen] = useState<string | null>(null);
  const u = result.rows[0]?.underlying;

  const missing = Object.entries(result.skipped).filter(([key]) => key.includes(":"));

  return (
    <div className="space-y-3 p-4">
      {u && (
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 rounded border border-slate-200 bg-white px-4 py-3">
          <span className="text-xl font-bold">{u.symbol}</span>
          <span className="tnum text-lg">
            {u.price !== null ? `$${u.price.toFixed(2)}` : "—"}
          </span>
          <Stat
            label="IV"
            value={u.iv !== null ? `${(u.iv * 100).toFixed(1)}%` : "—"}
          />
          <Stat
            label="IV %ile"
            value={u.iv_percentile !== null ? u.iv_percentile.toFixed(0) : "—"}
            emphasis
          />
          <Stat
            label="Hist vol"
            value={
              u.historical_vol !== null ? `${(u.historical_vol * 100).toFixed(1)}%` : "—"
            }
          />
          <Stat label="Earnings" value={u.earnings_date ?? "UNKNOWN"} />
          <Stat label="Sector" value={u.sector ?? "—"} />
          <Stat
            label="Avg vol"
            value={u.avg_volume !== null ? u.avg_volume.toLocaleString() : "—"}
          />
          <Stat label="Shares held" value={u.shares_owned.toLocaleString()} />
        </div>
      )}

      {u && u.iv !== null && u.iv_percentile !== null && (
        <div className="rounded border border-sky-200 bg-sky-50 px-3 py-2 text-[11px] text-sky-900">
          Raw IV reads <b>{(u.iv * 100).toFixed(1)}%</b> while the percentile reads{" "}
          <b>{u.iv_percentile.toFixed(0)}</b>. These are different questions — how volatile,
          versus how volatile <i>for this stock</i>. The gate runs on the percentile, except
          where a strategy says otherwise — the synthetic spread is vega-neutral and does not
          gate on IV at all.
        </div>
      )}

      <div className="space-y-2">
        {result.rows.map((row) => (
          <StrategyCard
            key={row.strategy}
            row={row}
            open={open === row.strategy}
            onToggle={() => setOpen(open === row.strategy ? null : row.strategy)}
          />
        ))}
      </div>

      {missing.length > 0 && (
        <div className="rounded border border-slate-200 bg-white px-3 py-2 text-[11px] text-slate-500">
          <div className="mb-1 font-semibold text-slate-600">No candidates constructed</div>
          {missing.map(([key, reason]) => (
            <div key={key}>
              {key.split(":")[1].replace(/_/g, " ")} — {reason}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StrategyCard({
  row,
  open,
  onToggle,
}: {
  row: Row;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      className={`rounded border bg-white ${
        row.verdict === "PASS" ? "border-emerald-300" : "border-slate-200"
      }`}
    >
      <button
        onClick={onToggle}
        className="flex w-full items-start gap-3 px-3 py-2 text-left hover:bg-slate-50"
      >
        <span
          className={`mt-0.5 rounded px-1.5 py-0.5 text-[11px] font-bold ${
            row.verdict === "PASS"
              ? "bg-emerald-600 text-white"
              : "bg-rose-100 text-rose-800"
          }`}
        >
          {row.verdict}
        </span>

        <span className="w-40 shrink-0 font-semibold">{row.strategy_label}</span>

        <span className="tnum w-40 shrink-0 text-slate-600">
          {String(row.metrics["legs_text"] ?? "")}
        </span>

        <span className="shrink-0">
          <ConfluenceBadge confluence={row.confluence} />
        </span>

        <span className="flex-1">
          <RuleChips
            results={row.results}
            only={row.verdict === "PASS" ? "passing" : "blocking"}
          />
        </span>
      </button>

      {open && (
        <div className="border-t border-slate-200 px-3 py-3">
          <RowDetail row={row} />
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  emphasis,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
}) {
  return (
    <span className="text-[11px] text-slate-500">
      {label}{" "}
      <b className={`tnum text-[12px] ${emphasis ? "text-sky-800" : "text-slate-800"}`}>
        {value}
      </b>
    </span>
  );
}
