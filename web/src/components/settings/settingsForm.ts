import type { Settings } from "../../types";

/** Form state shared by Personal + Server settings sections. */
export type SettingsForm = {
  enabled: boolean;
  technique: string;
  symbols: string[];
  leverage: number;
  bet_usd: number;
  bet_pct: number;
  balance_usdt: number;
  balance_usdc: number;
  target_profit_pct: number;
  max_open_positions: number;
  max_drawdown_pct: number;
  daily_max_trades: number;
  corr_threshold: number;
  corr_lookback: number;
  poll_seconds: number;
  gemini_decide_seconds: number;
  gemini_manage_seconds: number;
  gemini_min_hold_s: number;
  gemini_portfolio_seconds: number;
  gemini_plan_hours: number;
  gemini_tool_iters: number;
  mode: string;
  order_type: string;
  taker_fee_pct: number;
  maker_fee_pct: number;
  usdc_maker_fee_pct: number;
  usdc_taker_fee_pct: number;
  gemini_model: string;
};

/** Numeric fields validated/clamped by the engine — used for save feedback. */
export const NUM_FIELDS: [keyof SettingsForm, string][] = [
  ["leverage", "Leverage"],
  ["bet_usd", "Bet"],
  ["bet_pct", "Bet % saldo"],
  ["target_profit_pct", "Target profit %"],
  ["max_open_positions", "Max posisi"],
  ["poll_seconds", "Interval screening"],
  ["max_drawdown_pct", "Drawdown lock %"],
  ["daily_max_trades", "Max trade harian"],
  ["taker_fee_pct", "Fee taker USDT %"],
  ["maker_fee_pct", "Fee maker USDT %"],
  ["usdc_taker_fee_pct", "Fee taker USDC %"],
  ["usdc_maker_fee_pct", "Fee maker USDC %"],
  ["gemini_decide_seconds", "Interval keputusan"],
  ["gemini_manage_seconds", "Interval kelola"],
  ["gemini_min_hold_s", "Grace tahan minimal"],
  ["gemini_portfolio_seconds", "Interval portofolio"],
  ["gemini_plan_hours", "Interval planner"],
  ["gemini_tool_iters", "Maks tool-loop"],
];

/**
 * Default teknik gemini — hanya knob teknik/gerbang/throttle/fee.
 * Modal & identitas (saldo, mode, leverage, bet, pair, gemini_model) tak disentuh.
 */
export const REKOMENDASI_GEMINI: Partial<SettingsForm> = {
  technique: "gemini",
  order_type: "limit",
  corr_threshold: 0.85,
  corr_lookback: 50,
  max_open_positions: 5,
  max_drawdown_pct: 20,
  daily_max_trades: 20,
  poll_seconds: 60,
  gemini_decide_seconds: 180,
  gemini_manage_seconds: 60,
  gemini_min_hold_s: 300,
  gemini_portfolio_seconds: 300,
  gemini_plan_hours: 6,
  gemini_tool_iters: 4,
  taker_fee_pct: 0.05,
  maker_fee_pct: 0.02,
  usdc_taker_fee_pct: 0.04,
  usdc_maker_fee_pct: 0.0,
};

export function toSettingsForm(
  d: Settings,
  bal?: { usdt?: number; usdc?: number },
): SettingsForm {
  return {
    enabled: d.enabled,
    technique: d.technique,
    symbols: d.symbols || [],
    leverage: d.leverage,
    bet_usd: d.bet_usd,
    bet_pct: d.bet_pct ?? 0,
    balance_usdt: bal?.usdt ?? d.balance_usdt ?? 0,
    balance_usdc: bal?.usdc ?? d.balance_usdc ?? 0,
    target_profit_pct: d.target_profit_pct,
    max_open_positions: d.max_open_positions,
    max_drawdown_pct: d.max_drawdown_pct ?? 20,
    daily_max_trades: d.daily_max_trades,
    corr_threshold: d.corr_threshold ?? 0.85,
    corr_lookback: d.corr_lookback ?? 50,
    poll_seconds: d.poll_seconds,
    gemini_decide_seconds: d.gemini_decide_seconds ?? 180,
    gemini_manage_seconds: d.gemini_manage_seconds ?? 60,
    gemini_min_hold_s: d.gemini_min_hold_s ?? 300,
    gemini_portfolio_seconds: d.gemini_portfolio_seconds ?? 300,
    gemini_plan_hours: d.gemini_plan_hours ?? 6,
    gemini_tool_iters: d.gemini_tool_iters ?? 4,
    mode: d.mode || "",
    order_type: d.order_type,
    taker_fee_pct: d.taker_fee_pct,
    maker_fee_pct: d.maker_fee_pct,
    usdc_maker_fee_pct: d.usdc_maker_fee_pct,
    usdc_taker_fee_pct: d.usdc_taker_fee_pct,
    gemini_model: d.gemini_model || "",
  };
}

export function riskWarn(lev: number, liq: number): string {
  if (lev >= 50)
    return `⚠ Leverage ${lev}x: gerakan melawan ~${liq}% = LIKUIDASI (modal habis). SL berbasis ATR biasanya lebih lebar, jadi posisi kena likuidasi lebih dulu. Ini judi, bukan trading. Backtest strategi ini masih impas.`;
  if (lev >= 20)
    return `⚠ Leverage ${lev}x berisiko tinggi: likuidasi pada gerakan ~${liq}%.`;
  return "";
}

export const liqPct = (lev: number) =>
  Math.max(1 / lev - 0.005, 0.0005) * 100;

export type FormSetter = <K extends keyof SettingsForm>(
  k: K,
  v: SettingsForm[K],
) => void;
