"""Strategi enhanced (research): filter HTF + regime trend/mean-reversion + sesi.

Semua fitur diturunkan dari OHLCV saja → sepenuhnya bisa di-backtest.
Vektor & faithful: skor tren memakai precompute() yang sama dengan signal engine,
lalu ditambah gate HTF, mode mean-reversion untuk pasar sideways, dan mask sesi.

Belum dipasang ke engine live — diuji walk-forward dulu. Hanya diport ke live
bila OOS terbukti positif.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd

from .backtest import Backtester
from .indicators import adx, ema, rsi
from .optimize import Features, precompute, run_walk


def _tf_minutes(tf: str) -> int:
    unit, n = tf[-1].lower(), int(tf[:-1])
    return n * {"m": 1, "h": 60, "d": 1440}[unit]


@dataclass
class FeaturesV2:
    base: Features
    adx: np.ndarray
    rsi: np.ndarray
    htf_dir: np.ndarray   # +1 uptrend HTF, -1 downtrend, 0 unknown
    hour: np.ndarray


def _htf_dir(df: pd.DataFrame, cfg: dict, mult: int) -> np.ndarray:
    """Arah tren di timeframe lebih tinggi (tf×mult), tanpa lookahead."""
    minutes = _tf_minutes(cfg["market"]["timeframe"]) * mult
    htf_close = df["close"].resample(f"{minutes}min", label="right", closed="right").last().dropna()
    c = cfg["signals"]
    ef = ema(htf_close, c["ema_fast"])
    es = ema(htf_close, c["ema_slow"])
    direction = np.sign((ef - es).to_numpy())
    dser = pd.Series(direction, index=htf_close.index)
    # map ke bar dasar: nilai HTF terakhir yang SUDAH tertutup (shift 1) lalu ffill
    aligned = dser.shift(1).reindex(df.index, method="ffill")
    return np.nan_to_num(aligned.to_numpy())


def precompute_v2(df: pd.DataFrame, cfg: dict, htf_mult: int) -> FeaturesV2:
    base = precompute(df, cfg)
    s = cfg["signals"]
    return FeaturesV2(
        base=base,
        adx=np.nan_to_num(adx(df, s["adx_period"])[0].to_numpy()),
        rsi=np.nan_to_num(rsi(df["close"], s["rsi_period"]).to_numpy(), nan=50.0),
        htf_dir=_htf_dir(df, cfg, htf_mult),
        hour=df.index.hour.to_numpy(),
    )


def decide_v2(f2: FeaturesV2, g: dict, cfg: dict, sessions: set | None) -> np.ndarray:
    """side per bar dengan enhancement. g berisi toggle yang disweep:
    entry_confidence, use_htf (bool), regime (bool)."""
    st = cfg["strategy"]
    longs, shorts = f2.base.long_score, f2.base.short_score
    conf = g["entry_confidence"]
    use_htf, regime = g["use_htf"], g["regime"]

    # mode trend (mengikuti tren) — sama dengan v1, opsional dibatasi regime kuat
    trend_mask = (f2.adx >= st["adx_strong"]) if regime else np.ones_like(f2.adx, dtype=bool)
    long_t = (longs >= shorts) & (longs >= conf) & trend_mask
    short_t = (shorts > longs) & (shorts >= conf) & trend_mask
    if use_htf:
        long_t &= f2.htf_dir >= 0
        short_t &= f2.htf_dir <= 0
    side = np.where(long_t, 1, np.where(short_t, -1, 0)).astype(int)

    # mode mean-reversion untuk pasar sideways (adx rendah): fade ekstrem RSI
    if regime:
        mr_mask = f2.adx <= st["adx_range"]
        mr_long = mr_mask & (f2.rsi < st["mr_rsi_low"])
        mr_short = mr_mask & (f2.rsi > st["mr_rsi_high"])
        if use_htf:  # tetap searah bias HTF
            mr_long &= f2.htf_dir >= 0
            mr_short &= f2.htf_dir <= 0
        side = np.where((side == 0) & mr_long, 1,
                        np.where((side == 0) & mr_short, -1, side))

    # filter sesi jam (UTC)
    if sessions:
        hour_ok = np.isin(f2.hour, list(sessions))
        side = np.where(hour_ok, side, 0)

    return side.astype(int)


def build_grid_v2(conf_list, sl_list, tp_list, htf_opts, regime_opts) -> list[dict]:
    grid = []
    for conf, sl, tp, use_htf, regime in product(conf_list, sl_list, tp_list, htf_opts, regime_opts):
        if tp <= sl * 0.6:
            continue
        grid.append({"entry_confidence": conf, "sl_atr_mult": sl, "tp_atr_mult": tp,
                     "use_htf": use_htf, "regime": regime})
    return grid


def walk_forward_v2(df: pd.DataFrame, cfg: dict, grid: list[dict], bt: Backtester,
                    train_len: int, test_len: int, min_trades: int,
                    htf_mult: int, sessions: set | None):
    """Strategi v2 (HTF + regime + sesi)."""
    f2 = precompute_v2(df, cfg, htf_mult)
    return run_walk(df, cfg, grid, bt, f2.base,
                    lambda g: decide_v2(f2, g, cfg, sessions),
                    train_len, test_len, min_trades)
