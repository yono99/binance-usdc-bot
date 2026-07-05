"""Stempel regime saat OPEN → dipakai laporan EV per-regime (regime_ev.py).
Helper murni (staticmethod) → diuji tanpa instansiasi ForwardTester / jaringan.

Kontrak: SELALU kembalikan dict berisi key 'regime' (str); input rusak → 'unknown'.
Ini observasi murni — tak pernah melempar, tak pernah memblokir trading."""
import numpy as np
import pandas as pd
from bot.forward import ForwardTester

_stamp = ForwardTester._regime_stamp


def _df(n=120, vol=0.002, seed=0):
    """OHLCV sintetis, cukup bar untuk klasifikasi regime."""
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0, vol, n))
    high = close * (1 + abs(rng.normal(0, vol, n)))
    low = close * (1 - abs(rng.normal(0, vol, n)))
    return pd.DataFrame({"open": close, "high": high, "low": low,
                         "close": close, "volume": rng.uniform(1, 5, n)})


def test_selalu_ada_key_regime_str():
    out = _stamp(_df(), {})
    assert isinstance(out, dict) and isinstance(out.get("regime"), str)


def test_regime_termasuk_label_dikenal():
    out = _stamp(_df(vol=0.02, seed=3), {})   # volatil → trend/range/chaos/mixed
    assert out["regime"] in {"trend", "range", "chaos", "mixed", "unknown"}


def test_input_rusak_tidak_melempar_unknown():
    assert _stamp(None, {}) == {"regime": "unknown"}
    assert _stamp("bukan_df", {}) == {"regime": "unknown"}
    assert _stamp(pd.DataFrame(), {}) == {"regime": "unknown"}  # kosong


def test_dict_aman_di_spread_ke_posisi():
    # dipakai sebagai **stamp → tak boleh menimpa field lain selain 'regime'
    pos = {"side": "long", "vrp_brake": 1}
    pos.update(_stamp(_df(), {}))
    assert pos["side"] == "long" and pos["vrp_brake"] == 1 and "regime" in pos
