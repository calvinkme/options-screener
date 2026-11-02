import type { Fmt } from "./types";

/** A missing value is shown as an em dash, never as 0 -- they mean different things. */
export const EMPTY = "—";

export function formatValue(value: unknown, fmt: Fmt): string {
  if (value === null || value === undefined || value === "") return EMPTY;

  switch (fmt) {
    case "money": {
      const n = Number(value);
      return Number.isFinite(n)
        ? n.toLocaleString(undefined, {
            style: "currency",
            currency: "USD",
            maximumFractionDigits: 2,
          })
        : EMPTY;
    }
    case "pct": {
      const n = Number(value);
      return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : EMPTY;
    }
    case "pct1": {
      const n = Number(value);
      return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : EMPTY;
    }
    case "delta": {
      const n = Number(value);
      return Number.isFinite(n) ? n.toFixed(3) : EMPTY;
    }
    case "int": {
      const n = Number(value);
      return Number.isFinite(n) ? Math.round(n).toLocaleString() : EMPTY;
    }
    case "num": {
      const n = Number(value);
      return Number.isFinite(n) ? n.toFixed(2) : EMPTY;
    }
    case "date":
      return String(value);
    default:
      return String(value);
  }
}

/**
 * Colour bands from CLAUDE.md, matching the original spreadsheet tool:
 * max ROI annualised <15 / 15-25 / 25-40 / >40; CSP ROI on cash <10 / 10-20 / 20-30 / >30.
 */
export function bandClass(
  value: unknown,
  band: string | null,
  bands: Record<string, number[]>,
): string {
  if (!band || value === null || value === undefined) return "";
  const thresholds = bands[band];
  if (!thresholds) return "";

  const n = Number(value);
  if (!Number.isFinite(n)) return "";

  if (n < thresholds[0]) return "bg-slate-100 text-slate-600";
  if (n < thresholds[1]) return "bg-sky-100 text-sky-900";
  if (n < thresholds[2]) return "bg-emerald-100 text-emerald-900";
  return "bg-emerald-300 text-emerald-950 font-semibold";
}

export function isNumericFmt(fmt: Fmt): boolean {
  return ["money", "pct", "pct1", "num", "int", "delta"].includes(fmt);
}
