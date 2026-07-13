"""Layer 4 — Signal Engine v8: PURE TREND FOLLOWING.

Only setup: trend_continuation (pullback complete + momentum resumes)
Killed: trend_pullback (-1.25R), range_fade, breakout_continuation, scalp_range
BTC gate as PRIMARY FILTER (not just blocker): only trade WITH BTC direction
Halving phase as MACRO BIAS: bull→LONG bias, bear→SHORT bias, accumulation→flat
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import indicators as ind


@dataclass
class Signal:
    symbol: str
    side: str          # "long" | "short" | "skip"
    confidence: float  # 0..1
    price: float
    atr: float
    reason: str
    long_score: float = 0.0
    short_score: float = 0.0
    regime: str = "unknown"
    setup: str = "trend_continuation"
    sl: float = 0.0
    tp: float = 0.0
    rr: float = 0.0
    dynamic_sl_mult: float = 0.0
    dynamic_tp_mult: float = 0.0

    @property
    def actionable(self) -> bool:
        return self.side in ("long", "short")


def _score_trend_v8(df: pd.DataFrame, c: dict) -> tuple[float, int]:
    """Trend score: EMA alignment + ADX strength. Only trend-following direction."""
    ef = ind.ema(df["close"], c["ema_fast"]).iloc[-1]
    em = ind.ema(df["close"], c["ema_mid"]).iloc[-1]
    es = ind.ema(df["close"], c["ema_slow"]).iloc[-1]
    adx_val = ind.adx(df, c["adx_period"])[0].iloc[-1]

    direction = 0
    if ef > em > es:
        direction = 1
    elif ef < em < es:
        direction = -1

    if direction == 0:
        return 0.0, 0

    strength = min(adx_val / 40.0, 1.0)
    if adx_val < c.get("adx_trend_min", 20):
        strength *= 0.4
    return strength, direction


def _score_pullback(df: pd.DataFrame, c: dict) -> tuple[float, int]:
    """Pullback completion score: RSI normalization + MACD histogram turn.

    For LONG: RSI was oversold (<40) and now rising, MACD hist turning up
    For SHORT: RSI was overbought (>60) and now falling, MACD hist turning down
    """
    r = ind.rsi(df["close"], c["rsi_period"]).iloc[-1]
    _, _, hist = ind.macd(df["close"])
    h_now, h_prev = hist.iloc[-1], hist.iloc[-2]

    direction = 0
    if r < 40 and h_now > h_prev:          # was oversold, momentum turning up
        direction = 1
    elif r > 60 and h_now < h_prev:         # was overbought, momentum turning down
        direction = -1

    if direction == 0:
        return 0.0, 0

    # Distance from midpoint = pullback depth
    dist = min(abs(r - 50) / 25.0, 1.0)
    return dist, direction


def _score_structure_v8(df: pd.DataFrame, direction: int) -> float:
    """Structure: breakout confirmation in trend direction."""
    close = df["close"].iloc[-1]
    hi = df["high"].iloc[-20:-1].max()
    lo = df["low"].iloc[-20:-1].min()
    rng = (hi - lo) or 1e-9

    if direction == 1 and close > hi:
        return min((close - hi) / rng + 0.5, 1.0)
    if direction == -1 and close < lo:
        return min((lo - close) / rng + 0.5, 1.0)
    return 0.0


def _btc_bias(cfg: dict) -> int:
    """Return BTC macro bias from halving phase or BTC trend.
    1 = bullish (favor LONG), -1 = bearish (favor SHORT), 0 = neutral.
    """
    # Check halving phase from config
    halving = cfg.get("halving_phase", "")
    if halving in ("bull", "post-halving"):
        return 1
    if halving in ("bear", "blow-off"):
        return -1

    # Fallback: check BTC trend from existing BTC gate data
    btc_cfg = cfg.get("btc", {})
    if btc_cfg.get("enabled", True):
        # If we have dump_flag, bias is SHORT; else neutral
        return 0
    return 0


def evaluate_v8(symbol: str, df: pd.DataFrame, cfg: dict,
                btc_ret_pct: float | None = None) -> Signal:
    """Pure trend-following evaluation (v8)."""
    c = cfg["signals"]
    w = c["weights"]
    price = float(df["close"].iloc[-1])
    atr_val = float(ind.atr(df, c["atr_period"]).iloc[-1])

    # 1. Trend score (EMA alignment + ADX)
    ts, td = _score_trend_v8(df, c)

    # 2. Pullback score (RSI + MACD turn)
    ps, pd_dir = _score_pullback(df, c)

    # 3. Structure (breakout in trend direction)
    ss = _score_structure_v8(df, td)

    # Combined scores — ONLY in trend direction
    if td == 1:
        long_score = ts * w["trend"] + ps * w["momentum"] + ss * w["structure"]
        short_score = 0.0
    elif td == -1:
        long_score = 0.0
        short_score = ts * w["trend"] + ps * w["momentum"] + ss * w["structure"]
    else:
        return Signal(symbol, "skip", 0.0, price, atr_val, "no_trend",
                      long_score=0.0, short_score=0.0, regime="chop")

    # Regime classification
    adx_val = float(ind.adx(df, c["adx_period"])[0].iloc[-1])
    atr_pct = atr_val / price * 100 if price else 0.0
    chaos_lvl = cfg.get("strategy", {}).get("max_atr_pct_chaos", 8.0)
    if atr_pct >= chaos_lvl:
        regime = "chaos"
    elif adx_val >= c.get("adx_trend_min", 20):
        regime = "trend"
    else:
        regime = "range"

    # Chaos = no entry
    if regime == "chaos":
        return Signal(symbol, "skip", 0.0, price, atr_val, "regime=chaos",
                      long_score=round(float(long_score), 3),
                      short_score=round(float(short_score), 3), regime="chaos")

    # Entry threshold
    entry_conf = c["entry_confidence"]
    if long_score >= short_score and long_score >= entry_conf:
        side, conf = "long", round(long_score, 3)
    elif short_score > long_score and short_score >= entry_conf:
        side, conf = "short", round(short_score, 3)
    else:
        return Signal(symbol, "skip", round(max(long_score, short_score), 3), price, atr_val,
                      "below_threshold", long_score=round(float(long_score), 3),
                      short_score=round(float(short_score), 3), regime=regime)

    # Dynamic SL/TP by regime
    if regime == "trend":
        sl_mult, tp_mult = 1.75, 2.6
    elif regime == "range":
        sl_mult, tp_mult = 1.0, 1.2
    else:
        sl_mult, tp_mult = 0.0, 0.0

    is_long = side == "long"
    sl = price - atr_val * sl_mult if is_long else price + atr_val * sl_mult
    tp = price + atr_val * tp_mult if is_long else price - atr_val * tp_mult
    rr = tp_mult / sl_mult if sl_mult > 0 else 0.0

    # BTC gate (direction-aware, primary filter)
    reason_btc = ""
    if side in ("long", "short"):
        from . import altdata
        gate = altdata.btc_gate(1 if side == "long" else -1, btc_ret_pct, cfg)
        if not gate["allow"]:
            side, conf = "skip", conf
            reason_btc = f" | BTC-gate: {gate['reason']}"

    reason = f"trend({td},{ts:.2f}) pullback({pd_dir},{ps:.2f}) struct({ss:.2f}){reason_btc} rr={rr:.2f}"
    return Signal(symbol, side, conf, price, atr_val, reason,
                  long_score=round(float(long_score), 3),
                  short_score=round(float(short_score), 3),
                  regime=regime, setup="trend_continuation",
                  sl=sl, tp=tp, rr=rr,
                  dynamic_sl_mult=sl_mult, dynamic_tp_mult=tp_mult)


# Backward compatibility alias
def evaluate(symbol: str, df: pd.DataFrame, cfg: dict,
             btc_ret_pct: float | None = None) -> Signal:
    """Wrapper to maintain backward compatibility."""
    return evaluate_v8(symbol, df, cfg, btc_ret_pct)