import type { AppConfig, ScanResponse, Status, WatchlistItem } from "./types";

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* keep the status line */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  status: () => json<Status>("/api/status"),
  config: () => json<AppConfig>("/api/config"),
  watchlist: () => json<WatchlistItem[]>("/api/watchlist"),

  addTicker: (ticker: string, shares_owned = 0) =>
    json<{ ok: boolean }>("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({ ticker, shares_owned }),
    }),

  removeTicker: (ticker: string) =>
    json<{ ok: boolean }>(`/api/watchlist/${ticker}`, { method: "DELETE" }),

  scanStrategy: (strategy: string, symbols?: string[]) =>
    json<ScanResponse>("/api/scan", {
      method: "POST",
      body: JSON.stringify({ strategy, symbols }),
    }),

  scanTicker: (ticker: string) =>
    json<ScanResponse>("/api/scan", {
      method: "POST",
      body: JSON.stringify({ ticker }),
    }),
};
