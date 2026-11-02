import { useState } from "react";
import { api } from "../api";
import type { WatchlistItem } from "../types";

export function WatchlistPanel({
  items,
  onChange,
}: {
  items: WatchlistItem[];
  onChange: () => void;
}) {
  const [ticker, setTicker] = useState("");
  const [shares, setShares] = useState("0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    if (!ticker.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.addTicker(ticker.trim().toUpperCase(), Number(shares) || 0);
      setTicker("");
      setShares("0");
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(symbol: string) {
    await api.removeTicker(symbol);
    onChange();
  }

  return (
    <div className="w-64 shrink-0 border-r border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        Watchlist ({items.length})
      </div>

      <form onSubmit={add} className="space-y-2 border-b border-slate-200 px-3 py-2">
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value.toUpperCase())}
          placeholder="Add ticker"
          className="w-full rounded border border-slate-300 px-2 py-1 text-xs uppercase"
        />
        <div className="flex gap-2">
          <input
            value={shares}
            onChange={(e) => setShares(e.target.value)}
            type="number"
            step={100}
            title="Shares held — the covered call gate needs at least 100"
            className="w-20 rounded border border-slate-300 px-2 py-1 text-xs tnum"
          />
          <button
            type="submit"
            disabled={busy}
            className="flex-1 rounded bg-slate-800 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
          >
            Add
          </button>
        </div>
        {error && <div className="text-[11px] text-rose-700">{error}</div>}
      </form>

      <ul className="max-h-[calc(100vh-220px)] overflow-y-auto">
        {items.map((item) => (
          <li
            key={item.ticker}
            className="group flex items-center gap-2 border-b border-slate-100 px-3 py-1.5 text-xs"
          >
            <span className="font-semibold">{item.ticker}</span>
            {item.shares_owned > 0 && (
              <span className="tnum text-[10px] text-slate-500">
                {item.shares_owned.toLocaleString()} sh
              </span>
            )}
            {item.earnings_is_stale && (
              <span
                title="Manual earnings date is more than 90 days old"
                className="text-[10px] text-amber-600"
              >
                stale
              </span>
            )}
            <button
              onClick={() => remove(item.ticker)}
              className="ml-auto text-slate-300 opacity-0 transition group-hover:opacity-100 hover:text-rose-600"
              title={`Remove ${item.ticker}`}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
