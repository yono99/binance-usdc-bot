import pandas as pd

from bot import indicators as ind


def test_ema_follows_trend():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    e = ind.ema(s, 3)
    assert len(e) == 5
    assert e.iloc[-1] > e.iloc[0]


def test_rsi_within_bounds_and_high_on_uptrend(make_df):
    df = make_df(list(range(1, 120)))
    r = ind.rsi(df["close"], 14)
    assert r.between(0, 100).all()
    assert r.iloc[-1] > 60


def test_atr_positive(make_df):
    df = make_df([100 + (i % 5) for i in range(60)])
    a = ind.atr(df, 14)
    assert a.iloc[-1] > 0


def test_adx_returns_three_series(make_df):
    df = make_df([100 + i * 0.5 for i in range(80)])
    adx_val, plus_di, minus_di = ind.adx(df, 14)
    assert len(adx_val) == len(df)
    assert (adx_val >= 0).all()
