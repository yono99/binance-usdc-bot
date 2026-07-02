"""Simulator maker H30 langkah 3: kontrol +/−, konservatisme fill, unwind paksa."""
import numpy as np
import pandas as pd

from bot import mmsim


def _trades(prices, step_ms=1000, t0=1_700_000_000_000):
    ts = t0 + np.arange(len(prices)) * step_ms
    return pd.DataFrame({"ts": ts, "price": np.asarray(prices, dtype=float),
                         "qty": 1.0, "is_buyer_maker": False})


def test_positive_control_oscillation_captures_spread():
    """Harga berosilasi ±20bps di sekitar 100 (tanpa drift) → dua sisi tertembus
    bergantian → round-trip ≈ 2×offset positif."""
    cyc = [100.0, 99.75, 100.0, 100.25] * 3000
    r = mmsim.simulate(_trades(cyc), offset_bps=10, requote_ms=2000,
                       unwind_cost_bps=5.0)
    assert r["round_trips"] > 100
    assert r["mean_bps"] > 5.0, r                        # ~2×10bps − sedikit unwind


def test_negative_control_gap_down_staircase():
    """Crash bertangga: tiap ~30s harga GAP turun 20bps dalam satu trade —
    menembus bid yang sedang resting (belum sempat re-quote) → beli pisau
    jatuh, ask tak pernah tersentuh, lot menua → unwind rugi."""
    prices = []
    p = 100.0
    for _ in range(300):
        prices += [p] * 30
        p *= (1 - 0.0020)                                # gap −20bps per tangga
    r = mmsim.simulate(_trades(prices), offset_bps=10, requote_ms=5000,
                       max_hold_ms=120_000, unwind_cost_bps=5.0)
    assert r["unwinds"] > 0
    assert r["mean_bps"] < 0, r


def test_touch_does_not_fill_strict_through_only():
    """Trade TEPAT di level bid tidak mengisi (harus menembus)."""
    flat = [100.0] * 200
    df = _trades(flat)
    off = 10 / 1e4
    df.loc[100, "price"] = 100.0 * (1 - off)             # tepat DI bid → bukan fill
    r = mmsim.simulate(df, offset_bps=10, requote_ms=1000, unwind_cost_bps=5.0)
    assert r["round_trips"] == 0


def test_aged_lot_forced_unwind_pays_cost():
    """Satu tembus bid lalu harga diam → lot menua → unwind bayar biaya (rugi)."""
    prices = [100.0] * 5 + [99.8] + [100.0] * 1200       # tembus sekali, lalu diam
    r = mmsim.simulate(_trades(prices, step_ms=1000), offset_bps=10,
                       requote_ms=1000, max_hold_ms=60_000, unwind_cost_bps=5.0)
    assert r["unwinds"] >= 1 and r["round_trips"] >= 1
    # beli di bid 99.9, harga balik ke 100, unwind = 100×(1−5bps) → +10 − 5 ≈ +5 bps:
    # membuktikan biaya unwind benar-benar dipotong dari hasil.
    assert abs(r["mean_bps"] - 5.0) < 0.5
