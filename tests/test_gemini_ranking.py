"""Ranking kuota Gemini: kandidat paling volatil/bergerak dapat slot duluan,
anti-starvation memberi boost, dan logika sort top-N deterministik."""
import numpy as np
import pandas as pd

from bot.forward import ForwardTester

score = ForwardTester._gemini_score


def _df(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="15min")
    return pd.DataFrame({"close": np.asarray(closes, dtype=float)}, index=idx)


def test_higher_atr_and_move_scores_higher():
    flat = _df([100.0] * 10)
    moving = _df([100, 100, 100, 100, 100, 101, 102, 103, 104, 105])
    s_flat = score(flat, 100.0, 0.5, 0)
    s_hot = score(moving, 105.0, 2.0, 0)      # ATR% lebih besar + ret_5bar 5%
    assert s_hot > s_flat


def test_starvation_boost_capped():
    df = _df([100.0] * 10)
    base = score(df, 100.0, 1.0, 0)
    fresh = score(df, 100.0, 1.0, 3600)       # 1 jam → +0.1
    starved = score(df, 100.0, 1.0, 100 * 3600)  # cap 2 jam → +0.2
    assert abs(fresh - base - 0.1) < 1e-9
    assert abs(starved - base - 0.2) < 1e-9


def test_short_df_or_zero_price_safe():
    assert score(_df([100.0] * 3), 0.0, 1.0, 0) == 0.0    # price 0 → 0, tak crash
    assert score(_df([100.0] * 3), 100.0, 1.0, 0) > 0     # df pendek → ret5=0, tetap jalan


def test_topn_selection_deterministic():
    # replika logika seleksi di _on_cycle_store: sort (-skor, nama), ambil top-N
    pool = [("B", None, 1.0), ("A", None, 1.0), ("C", None, 5.0), ("D", None, 0.1)]
    pool.sort(key=lambda t: (-t[2], t[0]))
    top2 = [sym for sym, _, _ in pool[:2]]
    rest = [sym for sym, _, _ in pool[2:]]
    assert top2 == ["C", "A"]                 # skor tertinggi dulu; seri → alfabetis
    assert rest == ["B", "D"]
