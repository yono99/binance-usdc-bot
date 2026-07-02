"""H30 di fill nyata: kontrol +/− adverse selection, spread efektif, parser vision."""
import io
import zipfile

import numpy as np
import pandas as pd

from bot import aggresearch as ar
from bot import vision


def _trades(T=20000, spread_bps=10.0, adverse=0.0, seed=2):
    """Trade sintetis: mid random-walk; print bergantian bid/ask (±spread/2).
    adverse>0 → setelah print bid, mid drift TURUN (maker-buy dirugikan)."""
    rng = np.random.default_rng(seed)
    mid = 100 * np.exp(np.cumsum(rng.normal(0, 1e-4, T)))
    half = spread_bps / 2 / 1e4
    bid_side = (np.arange(T) % 2 == 0)
    if adverse:
        for i in np.where(bid_side)[0]:
            mid[i + 1:i + 40] *= (1 - adverse)
    px = np.where(bid_side, mid * (1 - half), mid * (1 + half))
    ts = 1_700_000_000_000 + np.arange(T) * 3000          # 1 trade / 3 detik
    return pd.DataFrame({"ts": ts, "price": px, "qty": 1.0, "is_buyer_maker": bid_side})


def test_effective_spread_recovers_true_spread():
    sp = ar.effective_spread_bps(_trades())
    assert abs(float(sp.median()) - 10.0) < 1.5


def test_edge_positive_without_adverse_negative_with():
    clean = ar.analyze_trades(_trades())
    assert clean["edge_gross_bps"] is not None and clean["edge_gross_bps"] > 3.0
    hurt = ar.analyze_trades(_trades(adverse=0.0008, seed=4))
    assert hurt["adverse_bps"] > clean["adverse_bps"]
    assert hurt["edge_gross_bps"] < clean["edge_gross_bps"] - 2.0


def _zip_csv(name, text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def test_parse_aggtrades_and_metrics():
    agg = _zip_csv("a.csv",
                   "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n"
                   "1,2.5,10,1,1,1700000001000,true\n"
                   "2,2.6,5,2,2,1700000000000,false\n")
    df = vision.parse_aggtrades(agg)
    assert list(df["ts"]) == [1700000000000, 1700000001000]     # diurutkan
    assert bool(df["is_buyer_maker"].iloc[1]) is True
    met = _zip_csv("m.csv",
                   "create_time,symbol,sum_open_interest,sum_open_interest_value\n"
                   "2025-01-15 00:00:00,FILUSDT,100,555.5\n"
                   "2025-01-15 00:05:00,FILUSDT,101,556.5\n")
    s = vision.parse_metrics(met)
    assert len(s) == 2 and float(s["oi_value"].iloc[-1]) == 556.5


def test_vision_symbol_mapping():
    assert vision._safe_sym("FIL/USDC:USDC") == "FILUSDC"
    assert "monthly/aggTrades/FILUSDC/FILUSDC-aggTrades-2026-05.zip" in vision.aggtrades_url("FIL/USDC:USDC", "2026-05")
    assert "daily/metrics/CRVUSDT/CRVUSDT-metrics-2025-01-15.zip" in vision.metrics_url("CRV/USDT:USDT", "2025-01-15")
