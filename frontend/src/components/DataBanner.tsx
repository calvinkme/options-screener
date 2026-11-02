import type { Status } from "../types";

/**
 * CLAUDE.md: "a stale quote that looks live is worse than no quote". The feed state is
 * therefore always on screen, and delayed data gets a banner that cannot be missed.
 */
export function DataBanner({ status }: { status: Status | null }) {
  if (!status) return null;

  if (!status.connected) {
    return (
      <div className="bg-slate-800 px-4 py-2 text-xs font-semibold tracking-wide text-slate-100">
        NOT CONNECTED — start IB Gateway or TWS on {status.host}:{status.port} and enable API
        access
      </div>
    );
  }

  if (status.is_delayed) {
    return (
      <div className="bg-amber-500 px-4 py-2 text-xs font-bold tracking-wide text-amber-950">
        {status.market_data_label} —{" "}
        {status.greeks_fallback
          ? "IBKR sends no Greeks on this feed; delta and IV are computed locally (Black-Scholes from the delayed mid) and marked 'computed'"
          : "Greeks are unreliable on a delayed feed; delta gates will fail rather than pass on missing data"}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 bg-emerald-700 px-4 py-1.5 text-xs font-medium text-emerald-50">
      <span className="font-bold tracking-wide">{status.market_data_label} DATA</span>
      <span className="opacity-75">
        {status.host}:{status.port} · client {status.client_id}
      </span>
      {status.readonly && (
        <span className="ml-auto rounded bg-emerald-900/40 px-2 py-0.5">
          read-only session · no order placement
        </span>
      )}
    </div>
  );
}
