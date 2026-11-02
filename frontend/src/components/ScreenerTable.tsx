import { Fragment, useMemo, useState } from "react";
import type { Column, Row } from "../types";
import { bandClass, formatValue, isNumericFmt } from "../format";
import { ConfluenceBadge, ConfluenceDetail } from "./ConfluenceBadge";
import { PayoffDiagram } from "./PayoffDiagram";
import { RuleChips } from "./RuleChips";
import { SelectionPanel } from "./SelectionPanel";

interface Props {
  rows: Row[];
  columns: Column[];
  bands: Record<string, number[]>;
}

type SortDir = "asc" | "desc";

/** Verdict first, then the failed gates, then the numbers -- the order CLAUDE.md asks for. */
export function ScreenerTable({ rows, columns, bands }: Props) {
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [expanded, setExpanded] = useState<number | null>(null);

  const dataColumns = useMemo(
    () => columns.filter((c) => !["verdict", "confluence"].includes(c.key)),
    [columns],
  );

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = cellValue(a, sortKey);
      const bv = cellValue(b, sortKey);
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      const an = Number(av);
      const bn = Number(bv);
      const cmp =
        Number.isFinite(an) && Number.isFinite(bn)
          ? an - bn
          : String(av).localeCompare(String(bv));
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [rows, sortKey, sortDir]);

  function toggleSort(key: string) {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  if (!rows.length) {
    return (
      <div className="p-8 text-center text-slate-500">
        No candidates. Either no contracts sat near the target delta band, or the chain had no
        expiry in the required DTE window.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[12px]">
        <thead className="sticky top-0 z-10 bg-slate-100">
          <tr>
            <th className="border-b border-slate-300 px-2 py-1.5 text-left font-semibold">
              Verdict
            </th>
            <th className="border-b border-slate-300 px-2 py-1.5 text-left font-semibold">
              Confl.
            </th>
            <th className="min-w-[280px] border-b border-slate-300 px-2 py-1.5 text-left font-semibold">
              Failed gates
            </th>
            {dataColumns.map((col) => (
              <th
                key={col.key}
                onClick={() => toggleSort(col.key)}
                className={`cursor-pointer select-none whitespace-nowrap border-b border-slate-300 px-2 py-1.5 font-semibold hover:bg-slate-200 ${
                  isNumericFmt(col.fmt) ? "text-right" : "text-left"
                }`}
              >
                {col.label}
                {sortKey === col.key && (
                  <span className="ml-1 text-slate-500">{sortDir === "asc" ? "▲" : "▼"}</span>
                )}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {sorted.map((row, index) => {
            const isOpen = expanded === index;
            return (
              <Fragment key={`${row.symbol}-${row.strategy}-${index}`}>
                <tr
                  onClick={() => setExpanded(isOpen ? null : index)}
                  className={`cursor-pointer border-b border-slate-200 hover:bg-sky-50 ${
                    isOpen ? "bg-sky-50" : index % 2 ? "bg-white" : "bg-slate-50/60"
                  }`}
                >
                  <td className="px-2 py-1">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${
                        row.verdict === "PASS"
                          ? "bg-emerald-600 text-white"
                          : "bg-rose-100 text-rose-800"
                      }`}
                    >
                      {row.verdict}
                    </span>
                  </td>
                  <td className="px-2 py-1">
                    <ConfluenceBadge confluence={row.confluence} />
                  </td>
                  <td className="px-2 py-1">
                    {row.verdict === "PASS" ? (
                      row.warnings.length ? (
                        <span className="text-[11px] text-amber-700">
                          {row.warnings.length} advisory
                        </span>
                      ) : (
                        <span className="text-[11px] text-slate-400">clean</span>
                      )
                    ) : (
                      <RuleChips results={row.results} only="blocking" />
                    )}
                  </td>

                  {dataColumns.map((col) => {
                    const value = cellValue(row, col.key);
                    return (
                      <td
                        key={col.key}
                        className={`whitespace-nowrap px-2 py-1 ${
                          isNumericFmt(col.fmt) ? "text-right tnum" : "text-left"
                        } ${bandClass(value, col.band, bands)}`}
                      >
                        {formatValue(value, col.fmt)}
                      </td>
                    );
                  })}
                </tr>

                {isOpen && (
                  <tr className="border-b-2 border-slate-300 bg-white">
                    <td colSpan={dataColumns.length + 3} className="px-4 py-3">
                      <RowDetail row={row} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function RowDetail({ row }: { row: Row }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-6">
        <PayoffDiagram row={row} />
        <ConfluenceDetail confluence={row.confluence} />
      </div>

      <SelectionPanel row={row} />

      <div>
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          All rules
        </div>
        <RuleChips results={row.results} />
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-slate-600">
        <span>
          Raw IV{" "}
          <b className="tnum">
            {row.underlying.iv !== null ? `${(row.underlying.iv * 100).toFixed(1)}%` : "—"}
          </b>
        </span>
        <span>
          IV percentile{" "}
          <b className="tnum">{row.underlying.iv_percentile?.toFixed(0) ?? "—"}</b>
          {row.underlying.iv_percentile_source && (
            <span className="ml-1 text-slate-400">
              ({row.underlying.iv_percentile_source})
            </span>
          )}
        </span>
        <span>
          Historical vol{" "}
          <b className="tnum">
            {row.underlying.historical_vol !== null
              ? `${(row.underlying.historical_vol * 100).toFixed(1)}%`
              : "—"}
          </b>
        </span>
        <span>
          Earnings <b>{row.underlying.earnings_date ?? "UNKNOWN"}</b>
          {row.underlying.earnings_is_stale && (
            <span className="ml-1 text-amber-700">stale &gt;90d</span>
          )}
        </span>
        <span>
          Sector <b>{row.underlying.sector ?? "—"}</b>
        </span>
      </div>

      <div className="text-[11px] text-slate-600">
        <span className="font-semibold">Legs: </span>
        {row.legs.map((leg, i) => (
          <span key={i} className="mr-3 tnum">
            {leg.qty < 0 ? "−" : "+"}
            {Math.abs(leg.qty / 100)} {leg.strike}
            {leg.right} @ {leg.mark?.toFixed(2) ?? "—"}
            {leg.delta !== null && (
              <span className="text-slate-400"> Δ{leg.delta.toFixed(3)}</span>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

function cellValue(row: Row, key: string): unknown {
  if (key === "symbol") return row.symbol;
  if (key === "verdict") return row.verdict;
  if (key === "confluence") return row.confluence?.score ?? null;
  if (key in row.metrics) return row.metrics[key];
  if (key in (row.underlying as unknown as Record<string, unknown>)) {
    return (row.underlying as unknown as Record<string, unknown>)[key];
  }
  return null;
}
