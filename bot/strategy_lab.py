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


# ----------------------- v3: + funding & open interest -----------------------

@dataclass
class FeaturesV3:
    v2: FeaturesV2
    funding_z: np.ndarray   # z-score funding (ekstrem = crowded)
    oi_delta: np.ndarray    # perubahan open interest (uang baru masuk?)


def precompute_v3(df: pd.DataFrame, cfg: dict, htf_mult: int,
                  funding_z: np.ndarray, oi_delta: np.ndarray) -> FeaturesV3:
    return FeaturesV3(precompute_v2(df, cfg, htf_mult), funding_z, oi_delta)


def decide_v3(f3: FeaturesV3, g: dict, cfg: dict, sessions: set | None) -> np.ndarray:
    """Mulai dari keputusan v2, lalu saring dengan funding & OI bila di-toggle."""
    side = decide_v2(f3.v2, g, cfg, sessions).copy()
    st = cfg["strategy"]

    if g.get("use_funding"):
        fz, blk = f3.funding_z, st["funding_z_block"]
        # hindari masuk searah kerumunan: long saat funding sangat tinggi, short saat sangat rendah
        side = np.where((side == 1) & (fz > blk), 0, side)
        side = np.where((side == -1) & (fz < -blk), 0, side)

    if g.get("use_oi"):
        # konfirmasi: hanya entry bila open interest naik (uang baru), bukan short-covering
        side = np.where((side != 0) & (f3.oi_delta <= 0), 0, side)

    return side.astype(int)


def build_grid_v3(conf_list, sl_list, tp_list, htf_opts, regime_opts,
                  funding_opts, oi_opts) -> list[dict]:
    grid = []
    for conf, sl, tp, htf, reg, fund, oi in product(
            conf_list, sl_list, tp_list, htf_opts, regime_opts, funding_opts, oi_opts):
        if tp <= sl * 0.6:
            continue
        grid.append({"entry_confidence": conf, "sl_atr_mult": sl, "tp_atr_mult": tp,
                     "use_htf": htf, "regime": reg, "use_funding": fund, "use_oi": oi})
    return grid


def walk_forward_v3(df: pd.DataFrame, cfg: dict, grid: list[dict], bt: Backtester,
                    train_len: int, test_len: int, min_trades: int,
                    htf_mult: int, sessions: set | None,
                    funding_z: np.ndarray, oi_delta: np.ndarray):
    """Strategi v3 (v2 + funding + open interest)."""
    f3 = precompute_v3(df, cfg, htf_mult, funding_z, oi_delta)
    return run_walk(df, cfg, grid, bt, f3.v2.base,
                    lambda g: decide_v3(f3, g, cfg, sessions),
                    train_len, test_len, min_trades)


# ----------------------- v4: + order flow / CVD -----------------------

@dataclass
class FeaturesV4:
    v3: FeaturesV3
    cvd_imb: np.ndarray   # imbalance taker buy/sell ∈ [-1,1]
    cvd_div: np.ndarray   # divergensi harga vs CVD (bool)


def precompute_v4(df: pd.DataFrame, cfg: dict, htf_mult: int, funding_z: np.ndarray,
                  oi_delta: np.ndarray, cvd_imb: np.ndarray, cvd_div: np.ndarray) -> FeaturesV4:
    return FeaturesV4(precompute_v3(df, cfg, htf_mult, funding_z, oi_delta), cvd_imb, cvd_div)


def decide_v4(f4: FeaturesV4, g: dict, cfg: dict, sessions: set | None) -> np.ndarray:
    """Keputusan v3, lalu konfirmasi order flow bila di-toggle:
    long butuh net buying, short butuh net selling; veto bila divergensi."""
    side = decide_v3(f4.v3, g, cfg, sessions).copy()
    if g.get("use_of"):
        st = cfg["strategy"]
        imb, mn = f4.cvd_imb, st["cvd_min"]
        side = np.where((side == 1) & (imb < mn), 0, side)     # long perlu imbalance beli
        side = np.where((side == -1) & (imb > -mn), 0, side)   # short perlu imbalance jual
        side = np.where((side != 0) & f4.cvd_div, 0, side)     # veto divergensi
    return side.astype(int)


def build_grid_v4(conf_list, sl_list, tp_list, htf_opts, regime_opts,
                  funding_opts, oi_opts, of_opts) -> list[dict]:
    grid = []
    for conf, sl, tp, htf, reg, fund, oi, of in product(
            conf_list, sl_list, tp_list, htf_opts, regime_opts, funding_opts, oi_opts, of_opts):
        if tp <= sl * 0.6:
            continue
        grid.append({"entry_confidence": conf, "sl_atr_mult": sl, "tp_atr_mult": tp,
                     "use_htf": htf, "regime": reg, "use_funding": fund,
                     "use_oi": oi, "use_of": of})
    return grid


def walk_forward_v4(df: pd.DataFrame, cfg: dict, grid: list[dict], bt: Backtester,
                    train_len: int, test_len: int, min_trades: int,
                    htf_mult: int, sessions: set | None, funding_z: np.ndarray,
                    oi_delta: np.ndarray, cvd_imb: np.ndarray, cvd_div: np.ndarray):
    """Strategi v4 (v3 + order flow/CVD)."""
    f4 = precompute_v4(df, cfg, htf_mult, funding_z, oi_delta, cvd_imb, cvd_div)
    return run_walk(df, cfg, grid, bt, f4.v3.v2.base,
                    lambda g: decide_v4(f4, g, cfg, sessions),
                    train_len, test_len, min_trades)


# ----------------------- v5: + event/volatility guard (reflek pasar) -----------------------

@dataclass
class FeaturesV5:
    v4: FeaturesV4
    event_recent: np.ndarray   # True bila ada lonjakan volume abnormal baru-baru ini


def event_recent_flags(df: pd.DataFrame, cfg: dict) -> np.ndarray:
    """Jejak berita di data: volume z-score > ambang = lonjakan; blokir N bar setelahnya."""
    st = cfg["strategy"]
    vol = df["volume"]
    mean = vol.rolling(st["event_vol_window"]).mean()
    std = vol.rolling(st["event_vol_window"]).std().replace(0, np.nan)
    spike = ((vol - mean) / std > st["event_vol_z"]).fillna(False)
    recent = spike.rolling(st["event_lookback"], min_periods=1).max().fillna(0)
    return recent.to_numpy().astype(bool)


def precompute_v5(df: pd.DataFrame, cfg: dict, htf_mult: int, funding_z: np.ndarray,
                  oi_delta: np.ndarray, cvd_imb: np.ndarray, cvd_div: np.ndarray) -> FeaturesV5:
    f4 = precompute_v4(df, cfg, htf_mult, funding_z, oi_delta, cvd_imb, cvd_div)
    return FeaturesV5(f4, event_recent_flags(df, cfg))


def decide_v5(f5: FeaturesV5, g: dict, cfg: dict, sessions: set | None) -> np.ndarray:
    """Keputusan v4, lalu blokir entry saat pasar sedang bereaksi (lonjakan volume)."""
    side = decide_v4(f5.v4, g, cfg, sessions).copy()
    if g.get("use_event"):
        side = np.where((side != 0) & f5.event_recent, 0, side)
    return side.astype(int)


def build_grid_v5(conf_list, sl_list, tp_list, htf_opts, regime_opts,
                  funding_opts, oi_opts, of_opts, event_opts) -> list[dict]:
    grid = []
    for conf, sl, tp, htf, reg, fund, oi, of, ev in product(
            conf_list, sl_list, tp_list, htf_opts, regime_opts,
            funding_opts, oi_opts, of_opts, event_opts):
        if tp <= sl * 0.6:
            continue
        grid.append({"entry_confidence": conf, "sl_atr_mult": sl, "tp_atr_mult": tp,
                     "use_htf": htf, "regime": reg, "use_funding": fund,
                     "use_oi": oi, "use_of": of, "use_event": ev})
    return grid


def walk_forward_v5(df: pd.DataFrame, cfg: dict, grid: list[dict], bt: Backtester,
                    train_len: int, test_len: int, min_trades: int,
                    htf_mult: int, sessions: set | None, funding_z: np.ndarray,
                    oi_delta: np.ndarray, cvd_imb: np.ndarray, cvd_div: np.ndarray):
    """Strategi v5 (v4 + event/volatility guard)."""
    f5 = precompute_v5(df, cfg, htf_mult, funding_z, oi_delta, cvd_imb, cvd_div)
    return run_walk(df, cfg, grid, bt, f5.v4.v3.v2.base,
                    lambda g: decide_v5(f5, g, cfg, sessions),
                    train_len, test_len, min_trades)
