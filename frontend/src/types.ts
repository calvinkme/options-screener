export type Fmt =
  | "num"
  | "int"
  | "money"
  | "pct"
  | "pct1"
  | "text"
  | "date"
  | "delta";

export interface Column {
  key: string;
  label: string;
  fmt: Fmt;
  band: string | null;
}

export interface SelectionStep {
  step: string;
  rule: string;
  rule_ids: string[];
}

export interface SelectionGuide {
  source: string;
  method: string;
  steps: SelectionStep[];
}

export interface SelectionRow {
  step: string;
  rule: string;
  chosen: string;
  passed: boolean | null;
}

export interface StrategyInfo {
  value: string;
  label: string;
  columns: Column[];
  bullish: boolean;
  kind: string;
  legs: number;
  guide: SelectionGuide;
}

export interface AppConfig {
  strategies: StrategyInfo[];
  bands: Record<string, number[]>;
  flags: {
    iv_gate_uses: string;
    dte_annualise_plus_one: boolean;
    chart_rules_are_hard_gates: boolean;
    csp_cash_basis: string;
    csp_mode: string;
  };
  account: { net_liquidation: number; cash_available: number };
}

export interface Status {
  connected: boolean;
  host: string;
  port: number;
  client_id: number;
  market_data_type: number;
  market_data_label: string;
  is_delayed: boolean;
  greeks_fallback: boolean;
  readonly: boolean;
  recent_errors: { code: number; message: string }[];
}

export interface RuleResult {
  rule_id: string;
  label: string;
  passed: boolean | null;
  actual: unknown;
  required: string;
  message: string;
  advisory: boolean;
  blocking: boolean;
}

export interface ConfluenceLayer {
  name: string;
  scored: boolean;
  detail: string;
}

export interface Confluence {
  score: number;
  raw_score: number;
  capped: boolean;
  guidance: string;
  layers: ConfluenceLayer[];
}

export interface LegData {
  strike: number;
  right: "C" | "P";
  qty: number;
  expiry: string;
  bid: number | null;
  ask: number | null;
  mark: number | null;
  delta: number | null;
  theta: number | null;
  iv: number | null;
  open_interest: number | null;
  volume: number | null;
}

export interface Underlying {
  symbol: string;
  price: number | null;
  iv: number | null;
  iv_percentile: number | null;
  iv_percentile_source: string | null;
  historical_vol: number | null;
  sector: string | null;
  market_cap: number | null;
  avg_volume: number | null;
  dividend_yield: number | null;
  earnings_date: string | null;
  earnings_source: string | null;
  earnings_is_stale: boolean;
  shares_owned: number;
}

export interface Row {
  strategy: string;
  strategy_label: string;
  symbol: string;
  verdict: "PASS" | "FAIL";
  metrics: Record<string, unknown>;
  confluence: Confluence | null;
  results: RuleResult[];
  failures: string[];
  warnings: string[];
  legs: LegData[];
  underlying: Underlying;
  selection?: SelectionRow[];
  selection_guide?: SelectionGuide;
}

export interface ScanResponse {
  mode: "strategy" | "ticker";
  subject: string;
  quote_date: string;
  status: Status;
  counts: { total: number; pass: number; fail: number; skipped: number };
  skipped: Record<string, string>;
  rows: Row[];
}

export interface WatchlistItem {
  ticker: string;
  shares_owned: number;
  notes: string | null;
  sector: string | null;
  earnings_date: string | null;
  earnings_is_stale: boolean;
}
