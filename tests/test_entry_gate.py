"""Gerbang anti-'entry di pucuk / sell minus' di decide_v2 (RSI + jarak-ke-EMA)."""
import numpy as np

from bot.optimize import Features
from bot.strategy_lab import FeaturesV2, decide_v2

CFG = {"strategy": {"adx_strong": 25, "adx_range": 18, "mr_rsi_low": 30, "mr_rsi_high": 70,
                    "entry_rsi_max": 75, "entry_rsi_min": 25, "entry_ext_atr": 2.5}}
G = {"entry_confidence": 0.1, "use_htf": False, "regime": False}


def _f2(long_score, short_score, rsi, close, ema_ref, atr=1.0):
    n = len(rsi)
    a = lambda v: np.array(v, dtype=float)
    base = Features(open=a(close), high=a(close), low=a(close), close=a(close),
                    atr=np.full(n, atr), index=np.arange(n),
                    long_score=a(long_score), short_score=a(short_score))
    return FeaturesV2(base=base, adx=np.full(n, 30.0), rsi=a(rsi),
                      htf_dir=np.zeros(n), hour=np.zeros(n), ema_ref=a(ema_ref))


def test_long_vetoed_when_overextended():
    # 3 bar long: bersih / RSI jenuh-beli / harga jauh di atas EMA
    f2 = _f2(long_score=[0.5, 0.5, 0.5], short_score=[0, 0, 0],
             rsi=[55, 80, 55], close=[100, 100, 103.5], ema_ref=[100, 100, 100], atr=1.0)
    side = decide_v2(f2, G, CFG, sessions=None)
    assert side[0] == 1        # entry bersih lolos
    assert side[1] == 0        # RSI 80 >= 75 → tolak (kejar pucuk)
    assert side[2] == 0        # (103.5-100)/1 = 3.5 >= 2.5 ATR → tolak (terlalu jauh dari mean)


def test_short_vetoed_when_oversold_or_extended_down():
    f2 = _f2(long_score=[0, 0, 0], short_score=[0.5, 0.5, 0.5],
             rsi=[45, 20, 45], close=[100, 100, 96.5], ema_ref=[100, 100, 100], atr=1.0)
    side = decide_v2(f2, G, CFG, sessions=None)
    assert side[0] == -1       # short bersih lolos
    assert side[1] == 0        # RSI 20 <= 25 → tolak (jual di dasar)
    assert side[2] == 0        # (96.5-100)/1 = -3.5 <= -2.5 ATR → tolak (jauh di bawah mean)
