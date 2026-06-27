export interface Stats {
  trades: number;
  liquidations: number;
  win_rate: number;
  expectancy_r: number;
  profit_factor: number | null;
  total_r: number;
  equity: number;
  return_pct: number;
  equity_curve: number[];
  liq_points: number[];
  open_positions: { symbol: string; side: string; entry: number; sl: number; tp: number }[];
  per_symbol: { symbol: string; trades: number; win_rate: number; sum_r: number }[];
  recent: { ts: string; symbol: string; reason: string; r: number; equity: number }[];
}

export interface Settings {
  enabled: boolean;
  technique: string;
  symbols: string[];
  leverage: number;
  bet_usd: number;
  balance_usd: number;
  target_profit_pct: number;
  max_open_positions: number;
  poll_seconds: number;
  order_type: string;
  taker_fee_pct: number;
  maker_fee_pct: number;
  gemini_model: string;
  techniques: string[];
  timeframe: string;
  liq_pct: number;
}

export interface Position {
  side: string;
  entry: number;
  sl: number;
  tp: number;
  liq: number;
  pnl_usd: number;
}

export interface PairStatus {
  symbol: string;
  price: number | null;
  atr_pct: number | null;
  signal: string;
  in_position: boolean;
  blocked: string | null;
  position: Position | null;
}

export interface Status {
  ts?: string;
  mode?: string;
  enabled?: boolean;
  technique?: string;
  timeframe?: string;
  leverage?: number;
  bet_usd?: number;
  balance_usd?: number;
  open_count?: number;
  max_open?: number;
  poll_seconds?: number;
  order_type?: string;
  fee_pct?: number;
  news_veto?: { active: boolean; note: string };
  symbols?: PairStatus[];
}

export interface Account {
  mode: string;
  api_valid: boolean | null;
  balance_usdc?: number;
  paper?: boolean;
  gemini_enabled: boolean;
  gemini_keys: number;
  error?: string;
}

export interface Ohlcv {
  symbol: string;
  tf: string;
  bars: { x: number; o: number; h: number; l: number; c: number }[];
  ema_fast?: number[];
  ema_mid?: number[];
  ema_slow?: number[];
  rsi?: number[];
  periods?: { fast: number; mid: number; slow: number; rsi: number };
  error?: string;
}

export interface Trade {
  id: number | null;
  symbol: string;
  side: string | null;
  entry: number | null;
  exit: number | null;
  sl: number | null;
  tp: number | null;
  liq: number | null;
  lev: number | null;
  bet: number | null;
  r: number | null;
  pnl_usd: number | null;
  reason: string | null;
  equity: number | null;
  open_ts: string | null;
  close_ts: string | null;
}

export interface TradesResp {
  count: number;
  trades: Trade[];
}

export interface NewsLogEntry {
  id: number;
  ts: string;
  active: boolean;
  note: string | null;
}

export interface ScreenLogEntry {
  id: number;
  ts: string;
  symbol: string;
  signal: string | null;
  price: number | null;
  atr_pct: number | null;
  blocked: string | null;
}

export interface GeminiUsage {
  total: { calls: number; tokens: number; errors: number };
  today: { calls: number; tokens: number };
  per_model: { model: string; calls: number; tok: number }[];
  per_key: { key_idx: number; calls: number; tok: number; errs: number }[];
  per_purpose: { purpose: string; calls: number; tok: number }[];
  recent: {
    id: number;
    ts: string;
    model: string;
    purpose: string;
    key_idx: number;
    prompt_tokens: number;
    output_tokens: number;
    total_tokens: number;
    ok: number;
    error: string;
  }[];
}
