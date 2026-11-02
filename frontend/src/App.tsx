import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { DataBanner } from "./components/DataBanner";
import { ScreenerTable } from "./components/ScreenerTable";
import { StrategyRules } from "./components/SelectionPanel";
import { TickerView } from "./components/TickerView";
import { WatchlistPanel } from "./components/WatchlistPanel";
import type { AppConfig, ScanResponse, Status, WatchlistItem } from "./types";

type Tab = "screen" | "ticker";
type VerdictFilter = "all" | "pass" | "fail";

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);

  const [tab, setTab] = useState<Tab>("screen");
  const [strategy, setStrategy] = useState("bull_put");
  const [ticker, setTicker] = useState("");

  const [result, setResult] = useState<ScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [verdictFilter, setVerdictFilter] = useState<VerdictFilter>("all");
  const [minConfluence, setMinConfluence] = useState(0);

  const loadWatchlist = useCallback(() => {
    api.watchlist().then(setWatchlist).catch(() => undefined);
  }, []);

  useEffect(() => {
    api.config().then(setConfig).catch((e) => setError(String(e)));
    api.status().then(setStatus).catch(() => undefined);
    loadWatchlist();
  }, [loadWatchlist]);

  async function runScan() {
    setLoading(true);
    setError(null);
    try {
      const response =
        tab === "ticker"
          ? await api.scanTicker(ticker.trim().toUpperCase())
          : await api.scanStrategy(strategy);
      setResult(response);
      setStatus(response.status);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const selectedStrategy = useMemo(
    () => config?.strategies.find((s) => s.value === strategy),
    [config, strategy],
  );

  const columns = useMemo(
    () => config?.strategies.find((s) => s.value === strategy)?.columns ?? [],
    [config, strategy],
  );

  const rows = useMemo(() => {
    if (!result) return [];
    return result.rows.filter((row) => {
      if (verdictFilter === "pass" && row.verdict !== "PASS") return false;
      if (verdictFilter === "fail" && row.verdict !== "FAIL") return false;
      if ((row.confluence?.score ?? 0) < minConfluence) return false;
      return true;
    });
  }, [result, verdictFilter, minConfluence]);

  return (
    <div className="flex min-h-screen flex-col">
      <DataBanner status={status} />

      <header className="flex items-center gap-4 border-b border-slate-200 bg-white px-4 py-2">
        <h1 className="text-sm font-bold tracking-tight">Options Screener</h1>

        <div className="flex rounded border border-slate-300">
          <TabButton active={tab === "screen"} onClick={() => setTab("screen")}>
            Strategy → stocks
          </TabButton>
          <TabButton active={tab === "ticker"} onClick={() => setTab("ticker")}>
            Ticker → strategies
          </TabButton>
        </div>

        {tab === "screen" ? (
          <select
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
            className="rounded border border-slate-300 px-2 py-1 text-xs"
          >
            {config?.strategies.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        ) : (
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && runScan()}
            placeholder="Ticker, e.g. AVGO"
            className="w-40 rounded border border-slate-300 px-2 py-1 text-xs uppercase"
          />
        )}

        <button
          onClick={runScan}
          disabled={loading || (tab === "ticker" && !ticker.trim())}
          className="rounded bg-sky-700 px-3 py-1 text-xs font-semibold text-white hover:bg-sky-800 disabled:opacity-40"
        >
          {loading ? "Scanning…" : "Scan"}
        </button>

        {result && (
          <div className="flex items-center gap-3 text-[11px] text-slate-600">
            <span>
              <b className="text-emerald-700">{result.counts.pass}</b> pass /{" "}
              {result.counts.total} candidates
            </span>
            {result.counts.skipped > 0 && (
              <span title={JSON.stringify(result.skipped, null, 2)}>
                {result.counts.skipped} skipped
              </span>
            )}
            <span className="text-slate-400">as of {result.quote_date}</span>
          </div>
        )}

        {config && (
          <div className="ml-auto flex items-center gap-3 text-[10px] text-slate-500">
            <span title="Which volatility measure gates the IV rule">
              IV gate: <b>{config.flags.iv_gate_uses}</b>
            </span>
            <span title="Chart rules are advisory unless this is on">
              chart rules:{" "}
              <b>{config.flags.chart_rules_are_hard_gates ? "hard gates" : "advisory"}</b>
            </span>
            <span title="CLAUDE.md annualises on dte+1; the trackers use plain dte">
              annualise: <b>dte{config.flags.dte_annualise_plus_one ? "+1" : ""}</b>
            </span>
          </div>
        )}
      </header>

      {tab === "screen" && result && (
        <div className="flex items-center gap-3 border-b border-slate-200 bg-slate-50 px-4 py-1.5 text-[11px]">
          <span className="text-slate-500">Filter</span>
          {(["all", "pass", "fail"] as VerdictFilter[]).map((value) => (
            <button
              key={value}
              onClick={() => setVerdictFilter(value)}
              className={`rounded px-2 py-0.5 uppercase ${
                verdictFilter === value
                  ? "bg-slate-800 text-white"
                  : "bg-white text-slate-600 hover:bg-slate-200"
              }`}
            >
              {value}
            </button>
          ))}
          <span className="ml-3 text-slate-500">Min confluence</span>
          <input
            type="range"
            min={0}
            max={5}
            value={minConfluence}
            onChange={(e) => setMinConfluence(Number(e.target.value))}
            className="w-28"
          />
          <span className="tnum w-4">{minConfluence}</span>
          <span className="ml-auto text-slate-400">
            Showing {rows.length} of {result.rows.length}
          </span>
        </div>
      )}

      {error && (
        <div className="whitespace-pre-wrap border-b border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-900">
          {error}
        </div>
      )}

      <div className="flex flex-1">
        <WatchlistPanel items={watchlist} onChange={loadWatchlist} />

        <main className="min-w-0 flex-1">
          {tab === "screen" && selectedStrategy && (
            <div className="px-3 pt-3">
              <StrategyRules label={selectedStrategy.label} guide={selectedStrategy.guide} />
            </div>
          )}

          {!result && !error && (
            <div className="p-10 text-center text-sm text-slate-500">
              <p className="mb-2">
                {tab === "screen"
                  ? "Pick a strategy and scan the watchlist."
                  : "Type a ticker to see which strategies fit it today."}
              </p>
              <p className="text-xs text-slate-400">
                IB Gateway or TWS must be running and logged in. The session is read-only —
                this tool never places an order.
              </p>
            </div>
          )}

          {result && result.mode === "ticker" && <TickerView result={result} />}

          {result && result.mode === "strategy" && (
            <ScreenerTable rows={rows} columns={columns} bands={config?.bands ?? {}} />
          )}
        </main>
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1 text-xs font-medium first:rounded-l last:rounded-r ${
        active ? "bg-slate-800 text-white" : "bg-white text-slate-600 hover:bg-slate-100"
      }`}
    >
      {children}
    </button>
  );
}
