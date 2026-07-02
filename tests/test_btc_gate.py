"""Gerbang dominansi BTC (mother coin) — direction-aware, dipakai semua teknik."""
import numpy as np

from bot import altdata


def _cfg(**over):
    b = {"enabled": True, "dump_pct": 0.5, "block_counter": True, "size_floor": 0.4}
    b.update(over)
    return {"btc": b}


# ---------- skalar (jalur live) ----------

def test_gate_lolos_saat_gerak_btc_kecil():
    g = altdata.btc_gate(1, 0.2, _cfg())          # BTC nyaris flat
    assert g["allow"] and g["size_factor"] == 1.0


def test_long_diblok_saat_btc_dump():
    g = altdata.btc_gate(1, -1.5, _cfg())         # BTC dump, long = lawan arah
    assert not g["allow"] and "btc_counter" in g["reason"]


def test_short_lolos_saat_btc_dump():
    g = altdata.btc_gate(-1, -1.5, _cfg())        # BTC dump, short = SEARAH → boleh
    assert g["allow"] and g["size_factor"] == 1.0


def test_short_diblok_saat_btc_pump():
    g = altdata.btc_gate(-1, 1.5, _cfg())         # BTC pump, short = lawan arah
    assert not g["allow"]


def test_mode_diskon_bukan_blok():
    g = altdata.btc_gate(1, -1.5, _cfg(block_counter=False))
    assert g["allow"] and g["size_factor"] < 1.0 and g["size_factor"] >= 0.4


def test_disabled_dan_none_lolos():
    assert altdata.btc_gate(1, -5.0, _cfg(enabled=False))["allow"]
    assert altdata.btc_gate(1, None, _cfg())["allow"]


# ---------- vektor (jalur backtest) ----------

def test_gate_side_vektor():
    side = np.array([1, 1, -1, -1, 1])
    btc = np.array([-1.5, 0.1, -1.5, 1.5, 1.5])   # kuat turun, kecil, turun, naik, naik
    out = altdata.btc_gate_side(side, btc, _cfg())
    #        long+dump=blok, kecil=lolos, short+dump=lolos, short+pump=blok, long+pump=lolos
    assert list(out) == [0, 1, -1, 0, 1]


def test_btc_ret_pct_bar_tertutup():
    import pandas as pd
    df = pd.DataFrame({"close": [100, 101, 102, 103, 100]})  # iloc[-2]=103, base=102
    assert altdata.btc_ret_pct(df, bars=1) == round((103 / 102 - 1) * 100, 3)
    assert altdata.btc_ret_pct(pd.DataFrame({"close": [1, 2]})) is None


# ---------- integrasi run_walk: gerbang berlaku untuk SEMUA teknik (v1–v7) ----------

def _run(df, cfg, side_val, btc_val):
    """Jalankan run_walk dgn make_side konstan + btc_ret konstan → cek efek gerbang."""
    import numpy as np
    from bot.backtest import Backtester
    from bot.optimize import build_grid, precompute, run_walk
    bt = Backtester(cfg)
    f = precompute(df, cfg)
    n = len(df)
    grid = build_grid([0.0], [1.5], [2.6])
    btc_ret = np.full(n, float(btc_val))
    _, trades = run_walk(df, cfg, grid, bt, f, lambda g: np.full(n, side_val, dtype=int),
                         train_len=150, test_len=100, min_trades=1, btc_ret=btc_ret)
    return trades


def test_run_walk_blokir_long_saat_btc_dump(cfg, make_df):
    df = make_df([100 + i * 0.05 for i in range(400)])
    # semua sinyal LONG + BTC dump kuat → gerbang nolkan semua → tak ada trade
    assert _run(df, cfg, side_val=1, btc_val=-5.0) == []


def test_run_walk_izinkan_short_saat_btc_dump(cfg, make_df):
    df = make_df([100 + i * 0.05 for i in range(400)])
    # sinyal SHORT searah BTC dump → lolos → ada trade (gerbang bukan blok-buta)
    assert len(_run(df, cfg, side_val=-1, btc_val=-5.0)) > 0


def test_run_walk_none_backward_compatible(cfg, make_df):
    import numpy as np
    from bot.backtest import Backtester
    from bot.optimize import build_grid, precompute, run_walk
    df = make_df([100 + i * 0.05 for i in range(400)])
    bt = Backtester(cfg)
    f = precompute(df, cfg)
    grid = build_grid([0.0], [1.5], [2.6])
    _, trades = run_walk(df, cfg, grid, bt, f, lambda g: np.ones(len(df), dtype=int),
                         150, 100, 1, btc_ret=None)  # None → perilaku lama (tak diblok)
    assert len(trades) > 0
