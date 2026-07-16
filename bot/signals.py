"""Layer 4 — signal engine: gabungkan trend, momentum, struktur -> skor.

Entry Confluence Gate — Faktor 2: Pair Structure Confluence (floor per-komponen).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import indicators as ind


def pair_structure_confluence_ok(trend_score: float, momentum_score: float, side: str,
                                  trend_floor: float, momentum_floor: float) -> bool:
    """Floor per-component check: trend AND momentum must independently meet direction.

    Mencegah kasus di mana skor gabungan lolos threshold walau trend & momentum
    sebenarnya netral, asal struktur (breakout) kuat sendirian.

    trend_score / momentum_score: dari evaluate() — negatif = bearish, positif = bullish.
    side: "long" or "short".
    trend_floor / momentum_floor: ambang minimal (butuh kalibrasi data historis,
        default sementara 0.1 = cukup kecil agar tak blokir SEMUA).

    Returns True jika pair independently aligned = kedua komponen setuju arah.
    """
    if side == "short":
        return trend_score <= -trend_floor and momentum_score <= -momentum_floor
    else:
        return trend_score >= trend_floor and momentum_score >= momentum_floor




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
    long_score: float = 0.0    # skor mentah arah long (untuk OBSERVE ReactAgent)
    short_score: float = 0.0   # skor mentah arah short
    regime: str = "unknown"    # trend | range | chaos (klasifikasi murah dari ADX/ATR)

    @property
    def actionable(self) -> bool:
        return self.side in ("long", "short")


def _score_trend(df: pd.DataFrame, c: dict) -> tuple[float, int]:
    ef = ind.ema(df["close"], c["ema_fast"]).iloc[-1]
    em = ind.ema(df["close"], c["ema_mid"]).iloc[-1]
    es = ind.ema(df["close"], c["ema_slow"]).iloc[-1]
    adx_val = ind.adx(df, c["adx_period"])[0].iloc[-1]
    direction = 0
    if ef > em > es:
        direction = 1
    elif ef < em < es:
        direction = -1
    strength = min(adx_val / 40.0, 1.0)  # ADX 40 -> kuat penuh
    if adx_val < c["adx_trend_min"]:
        strength *= 0.4  # choppy -> diskon besar
    return strength, direction


def _score_momentum(df: pd.DataFrame, c: dict) -> tuple[float, int]:
    r = ind.rsi(df["close"], c["rsi_period"]).iloc[-1]
    _, _, hist = ind.macd(df["close"])
    h_now, h_prev = hist.iloc[-1], hist.iloc[-2]
    direction = 0
    if r > 52 and h_now > 0 and h_now >= h_prev:
        direction = 1
    elif r < 48 and h_now < 0 and h_now <= h_prev:
        direction = -1
    dist = min(abs(r - 50) / 25.0, 1.0)
    return dist, direction


def _score_structure(df: pd.DataFrame) -> tuple[float, int]:
    close = df["close"].iloc[-1]
    hi = df["high"].iloc[-20:-1].max()
    lo = df["low"].iloc[-20:-1].min()
    rng = (hi - lo) or 1e-9
    pos = (close - lo) / rng
    if close > hi:
        return min((close - hi) / rng + 0.5, 1.0), 1   # breakout up
    if close < lo:
        return min((lo - close) / rng + 0.5, 1.0), -1  # breakdown
    if pos > 0.6:
        return pos - 0.5, 1
    if pos < 0.4:
        return 0.5 - pos, -1
    return 0.0, 0


def evaluate(symbol: str, df: pd.DataFrame, cfg: dict,
             btc_ret_pct: float | None = None) -> Signal:
    c = cfg["signals"]
    w = c["weights"]
    price = float(df["close"].iloc[-1])
    atr_val = float(ind.atr(df, c["atr_period"]).iloc[-1])

    ts, td = _score_trend(df, c)
    ms, md = _score_momentum(df, c)
    ss, sd = _score_structure(df)

    long_score = ts * w["trend"] * (td == 1) + ms * w["momentum"] * (md == 1) + ss * w["structure"] * (sd == 1)
    short_score = ts * w["trend"] * (td == -1) + ms * w["momentum"] * (md == -1) + ss * w["structure"] * (sd == -1)

    if long_score >= short_score and long_score >= c["entry_confidence"]:
        side, conf = "long", round(long_score, 3)
    elif short_score > long_score and short_score >= c["entry_confidence"]:
        side, conf = "short", round(short_score, 3)
    else:
        side, conf = "skip", round(max(long_score, short_score), 3)

    # Klasifikasi regime MURAH (tanpa indikator baru — pakai ADX & ATR yang sudah ada).
    adx_val = float(ind.adx(df, c["adx_period"])[0].iloc[-1])
    atr_pct = atr_val / price * 100 if price else 0.0
    chaos_lvl = cfg.get("strategy", {}).get("max_atr_pct_chaos", 8.0)
    if atr_pct >= chaos_lvl:
        regime = "chaos"
    elif adx_val >= c.get("adx_trend_min", 20):
        regime = "trend"
    else:
        regime = "range"

    # Gerbang dominansi BTC (mother coin) — direction-aware, dipakai semua teknik.
    # Entri lawan arah BTC saat BTC bergerak kuat → dibatalkan (side=skip). Skor
    # long/short mentah TETAP dipertahankan untuk OBSERVE ReactAgent.
    if side in ("long", "short"):
        from . import altdata
        gate = altdata.btc_gate(1 if side == "long" else -1, btc_ret_pct, cfg)
        if not gate["allow"]:
            side, conf = "skip", conf
            reason_btc = f" | BTC-gate: {gate['reason']}"
        else:
            reason_btc = ""
    else:
        reason_btc = ""

    reason = f"trend({td},{ts:.2f}) mom({md},{ms:.2f}) struct({sd},{ss:.2f}){reason_btc}"
    return Signal(symbol, side, conf, price, atr_val, reason,
                  long_score=round(float(long_score), 3),
                  short_score=round(float(short_score), 3), regime=regime)
