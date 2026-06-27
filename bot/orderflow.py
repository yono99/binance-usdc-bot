"""Order flow / CVD dari taker buy/sell volume (per-bar, bisa di-backtest).

Binance klines menyertakan *taker buy base volume* (volume yang menghantam ASK =
pembeli agresif). Maka:
    delta = takerBuy - takerSell = 2*takerBuy - totalVolume
    CVD   = kumulatif delta
Ini order flow NYATA pada resolusi bar — tersedia sepanjang histori OHLCV,
berbeda dari data tick yang tak praktis di-backtest. Semua causal (tanpa lookahead).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .exchange import Exchange
from .logger import log


def fetch_taker(ex: Exchange, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    """Ambil klines mentah (berisi taker buy volume) — endpoint publik."""
    method = getattr(ex.client, "fapiPublicGetKlines", None)
    if method is None:
        log.warning("fapiPublicGetKlines tak tersedia; order flow non-aktif")
        return pd.DataFrame()
    market_id = ex.markets[symbol]["id"]
    tf_ms = ex.client.parse_timeframe(timeframe) * 1000
    since = ex.client.milliseconds() - bars * tf_ms
    rows: list = []
    try:
        while len(rows) < bars:
            chunk = method({"symbol": market_id, "interval": timeframe,
                            "startTime": since, "limit": 1500})
            if not chunk:
                break
            rows += chunk
            since = int(chunk[-1][0]) + tf_ms
            if len(chunk) < 1500:
                break
    except Exception as e:  # boundary
        log.warning(f"order flow {symbol} gagal: {e}")
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    idx = pd.to_datetime([int(r[0]) for r in rows], unit="ms", utc=True)
    df = pd.DataFrame({
        "volume": [float(r[5]) for r in rows],
        "taker_buy": [float(r[9]) for r in rows],
    }, index=idx)
    return df[~df.index.duplicated(keep="last")]


def cvd_from_series(close: pd.Series, volume: pd.Series, taker_buy: pd.Series,
                    lookback: int) -> tuple[np.ndarray, np.ndarray]:
    """Hitung (imbalance, divergence) per bar.
    - imbalance = Σdelta / Σvolume pada `lookback` bar terakhir ∈ [-1,1]
    - divergence = arah harga vs arah CVD berlawanan (sinyal lemah)."""
    delta = 2 * taker_buy - volume
    roll_delta = delta.rolling(lookback).sum()
    roll_vol = volume.rolling(lookback).sum().replace(0, np.nan)
    imbalance = (roll_delta / roll_vol).fillna(0.0)

    cvd_chg = delta.cumsum().diff(lookback)
    price_chg = close.diff(lookback)
    divergence = ((np.sign(price_chg) != np.sign(cvd_chg)) & (price_chg.abs() > 0)).fillna(False)
    return imbalance.to_numpy(), divergence.to_numpy().astype(bool)


def cvd_features(ex: Exchange, symbol: str, timeframe: str, df: pd.DataFrame,
                 lookback: int) -> tuple[np.ndarray, np.ndarray]:
    """Selaraskan taker volume ke index bar df, lalu hitung fitur CVD."""
    taker = fetch_taker(ex, symbol, timeframe, len(df) + lookback + 5)
    if taker.empty:
        return np.zeros(len(df)), np.zeros(len(df), dtype=bool)
    vol = taker["volume"].reindex(df.index).fillna(0.0)
    tb = taker["taker_buy"].reindex(df.index).fillna(0.0)
    return cvd_from_series(df["close"], vol, tb, lookback)
