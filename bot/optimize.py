"""Sweep parameter + walk-forward — cari parameter ber-EDGE yang lolos out-of-sample.

Anti-overfit: parameter dipilih dari data train (in-sample), lalu DIUJI di data
test (out-of-sample) yang belum dilihat, lalu jendela digeser maju. Yang dilaporkan
sebagai verdict = expectancy OUT-OF-SAMPLE (jujur), bukan in-sample.

Performa: komponen sinyal (trend/momentum/struktur) di-precompute sekali secara
vektor; tiap kombinasi hanya mengubah ambang confidence + mult SL/TP (murah).
Akuntansi entry/exit reuse `Backtester` (fee+slippage identik dengan backtest).
"""
from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd

from .backtest import Backtester, Trade
from .indicators import adx, atr, ema, macd, rsi

_Sig = namedtuple("_Sig", ["side", "atr"])


@dataclass
class Features:
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    atr: np.ndarray
    index: np.ndarray  # timestamps
    long_score: np.ndarray
    short_score: np.ndarray


def warmup(cfg: dict) -> int:
    s = cfg["signals"]
    return max(s["ema_slow"], s["atr_period"], s["adx_period"], 20) + 5


def precompute(df: pd.DataFrame, cfg: dict) -> Features:
    """Hitung skor long/short per bar (bobot tetap dari cfg) secara vektor.
    Mirror persis logika `bot.signals.evaluate`."""
    c = cfg["signals"]
    w = c["weights"]
    close = df["close"]

    ef, em, es = ema(close, c["ema_fast"]), ema(close, c["ema_mid"]), ema(close, c["ema_slow"])
    adx_val = adx(df, c["adx_period"])[0]
    td = np.where((ef > em) & (em > es), 1, np.where((ef < em) & (em < es), -1, 0))
    ts = np.minimum(adx_val / 40.0, 1.0)
    ts = np.where(adx_val < c["adx_trend_min"], ts * 0.4, ts)

    r = rsi(close, c["rsi_period"])
    _, _, hist = macd(close)
    hp = hist.shift(1)
    md = np.where((r > 52) & (hist > 0) & (hist >= hp), 1,
                  np.where((r < 48) & (hist < 0) & (hist <= hp), -1, 0))
    ms = np.minimum((r - 50).abs() / 25.0, 1.0).to_numpy()

    hi = df["high"].shift(1).rolling(19).max()
    lo = df["low"].shift(1).rolling(19).min()
    rng = (hi - lo).replace(0, np.nan)
    pos = (close - lo) / rng
    sd = np.where(close > hi, 1,
                  np.where(close < lo, -1,
                           np.where(pos > 0.6, 1, np.where(pos < 0.4, -1, 0))))
    ss = np.where(close > hi, np.minimum((close - hi) / rng + 0.5, 1.0),
                  np.where(close < lo, np.minimum((lo - close) / rng + 0.5, 1.0),
                           np.where(pos > 0.6, pos - 0.5,
                                    np.where(pos < 0.4, 0.5 - pos, 0.0))))
    ss = np.nan_to_num(ss)
    sd = np.nan_to_num(sd).astype(int)

    ts = np.nan_to_num(ts)
    ms = np.nan_to_num(ms)

    long_score = (ts * w["trend"] * (td == 1) + ms * w["momentum"] * (md == 1)
                  + ss * w["structure"] * (sd == 1))
    short_score = (ts * w["trend"] * (td == -1) + ms * w["momentum"] * (md == -1)
                   + ss * w["structure"] * (sd == -1))

    return Features(
        open=df["open"].to_numpy(), high=df["high"].to_numpy(), low=df["low"].to_numpy(),
        close=df["close"].to_numpy(), atr=np.nan_to_num(atr(df, c["atr_period"]).to_numpy()),
        index=df.index.to_numpy(), long_score=long_score, short_score=short_score,
    )


def decide(f: Features, entry_confidence: float) -> np.ndarray:
    """side per bar: 1 long, -1 short, 0 skip — replikasi pemilihan di evaluate()."""
    longs, shorts = f.long_score, f.short_score
    long_sel = (longs >= shorts) & (longs >= entry_confidence)
    short_sel = (shorts > longs) & (shorts >= entry_confidence)
    return np.where(long_sel, 1, np.where(short_sel, -1, 0)).astype(int)


def simulate(bt: Backtester, f: Features, side: np.ndarray, d0: int, d1: int,
             sl_mult: float, tp_mult: float) -> list[Trade]:
    """Entry untuk keputusan di [d0, d1); exit WAJIB di dalam jendela (tutup di d1-1
    bila belum kena) → jendela mandiri, tanpa kebocoran antar-window."""
    bt.sl_mult, bt.tp_mult = sl_mult, tp_mult
    trades: list[Trade] = []
    pos: dict | None = None
    end = d1  # bar terakhir yang boleh dipakai

    for b in range(d0 + 1, end):
        if pos is not None:
            long = pos["side"] == "long"
            hit_sl = f.low[b] <= pos["sl"] if long else f.high[b] >= pos["sl"]
            hit_tp = f.high[b] >= pos["tp"] if long else f.low[b] <= pos["tp"]
            if hit_sl:
                trades.append(bt._close(pos, pos["sl"], f.index[b], b, "sl"))
                pos = None
            elif hit_tp:
                trades.append(bt._close(pos, pos["tp"], f.index[b], b, "tp"))
                pos = None

        dec = b - 1
        if pos is None and d0 <= dec < d1 and side[dec] != 0 and f.atr[dec] > 0:
            sig = _Sig("long" if side[dec] == 1 else "short", float(f.atr[dec]))
            row = {"open": float(f.open[b])}
            pos = bt._open(pos_symbol(), sig, row, f.index[b], b)

    if pos is not None:
        trades.append(bt._close(pos, float(f.close[end - 1]), f.index[end - 1], end - 1, "eod"))
    return trades


def pos_symbol() -> str:
    return "OPT"


def expectancy(trades: list[Trade]) -> float:
    return sum(t.r for t in trades) / len(trades) if trades else float("-inf")


@dataclass
class WindowResult:
    train_range: tuple[int, int]
    test_range: tuple[int, int]
    params: dict
    is_exp: float
    is_n: int
    oos_exp: float
    oos_n: int


def build_grid(conf_list, sl_list, tp_list) -> list[dict]:
    grid = []
    for conf, sl, tp in product(conf_list, sl_list, tp_list):
        if tp <= sl * 0.6:  # buang RR yang tak masuk akal
            continue
        grid.append({"entry_confidence": conf, "sl_atr_mult": sl, "tp_atr_mult": tp})
    return grid


def walk_forward(df: pd.DataFrame, cfg: dict, grid: list[dict], bt: Backtester,
                 train_len: int, test_len: int, min_trades: int = 15):
    f = precompute(df, cfg)
    warm = warmup(cfg)
    n = len(df)

    # cache side per entry_confidence unik (hemat hitung ulang)
    confs = sorted({g["entry_confidence"] for g in grid})
    side_cache = {c: decide(f, c) for c in confs}

    results: list[WindowResult] = []
    oos_trades: list[Trade] = []

    start = warm
    while start + train_len + test_len <= n:
        tr0, tr1 = start, start + train_len
        te0, te1 = tr1, tr1 + test_len

        best, best_exp = None, float("-inf")
        for g in grid:
            tr = simulate(bt, f, side_cache[g["entry_confidence"]], tr0, tr1,
                          g["sl_atr_mult"], g["tp_atr_mult"])
            if len(tr) < min_trades:
                continue
            e = expectancy(tr)
            if e > best_exp:
                best, best_exp = g, e

        if best is not None:
            is_tr = simulate(bt, f, side_cache[best["entry_confidence"]], tr0, tr1,
                             best["sl_atr_mult"], best["tp_atr_mult"])
            te = simulate(bt, f, side_cache[best["entry_confidence"]], te0, te1,
                          best["sl_atr_mult"], best["tp_atr_mult"])
            oos_trades += te
            results.append(WindowResult(
                (tr0, tr1), (te0, te1), best, expectancy(is_tr), len(is_tr),
                expectancy(te), len(te),
            ))

        start += test_len

    return results, oos_trades
