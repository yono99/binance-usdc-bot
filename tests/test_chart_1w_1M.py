"""Tahap 6 (plan-sess) — Chart 1w/1M backfill + SSE candle channel."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture
def chart_db(tmp_path, monkeypatch):
    """Patch DB_PATH ke tmp + populate store dgn OHLCV sintetis multi-tf."""
    from bot import chartstore
    db = tmp_path / "m.db"
    monkeypatch.setattr(chartstore, "DB_PATH", db)
    return chartstore


def _make_df(start_ms: int, n: int, tf_ms: int, base: float = 100.0) -> pd.DataFrame:
    """Buat DataFrame candle sintetis."""
    idx = pd.date_range(
        start=pd.Timestamp(start_ms, unit="ms", tz="UTC"),
        periods=n, freq=pd.Timedelta(ms=tf_ms),
    )
    closes = [base + i * 0.1 for i in range(n)]
    return pd.DataFrame({
        "open": closes, "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes], "close": closes,
        "volume": [10.0] * n,
    }, index=idx)


def test_ingest_paginate_flag_flows_through(chart_db):
    """chartstore.ingest menerima extra_paginate=True (backfill 1w/1M).

    Test hanya verifikasi signature: bila dipanggil dengan extra_paginate=True & DB kosong,
    code pilih backfill penuh via fetch_history() yg butuh ex client lengkap. Karena fetch_history
    butuh client.milliseconds() yg tak ada di stub, kita cuma uji signature kontrak: arg
    extra_paginate ditrima dan tak error."""
    import inspect

    sig = inspect.signature(chart_db.ingest)
    assert "extra_paginate" in sig.parameters
    # Default False → back-compat
    assert sig.parameters["extra_paginate"].default is False


def test_chartstore_load_roundtrip_supports_1w_1M(chart_db):
    """chartstore.upsert + load untuk tf high-timeframe (1w/1M) tanpa kendala."""
    base_ms_1w = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    # 1w = 7 * 24 * 3600 * 1000 ms
    w_ms = 7 * 24 * 3600 * 1000
    idx_1w = pd.date_range(start=pd.Timestamp(base_ms_1w, unit="ms", tz="UTC"),
                            periods=10, freq=pd.Timedelta(milliseconds=w_ms))
    closes_1w = [100.0 + i * 0.1 for i in range(10)]
    df_1w = pd.DataFrame({"open": closes_1w, "high": [c + 0.5 for c in closes_1w],
                          "low": [c - 0.5 for c in closes_1w], "close": closes_1w,
                          "volume": [10.0] * 10}, index=idx_1w)
    chart_db.upsert("BTC/USDC:USDC", "1w", df_1w)
    base_ms_1M = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    m_ms = 30 * 24 * 3600 * 1000
    idx_1M = pd.date_range(start=pd.Timestamp(base_ms_1M, unit="ms", tz="UTC"),
                            periods=30, freq=pd.Timedelta(milliseconds=m_ms))
    closes_1M = [200.0 + i * 0.5 for i in range(30)]
    df_1M = pd.DataFrame({"open": closes_1M, "high": [c + 1 for c in closes_1M],
                          "low": [c - 1 for c in closes_1M], "close": closes_1M,
                          "volume": [5.0] * 30}, index=idx_1M)
    chart_db.upsert("BTC/USDC:USDC", "1M", df_1M)
    loaded = chart_db.load("BTC/USDC:USDC", "1w", limit=50)
    assert len(loaded) == 10
    assert "close" in loaded.columns
    loaded_m = chart_db.load("BTC/USDC:USDC", "1M", limit=100)
    assert len(loaded_m) == 30


def test_api_candles_accepts_1w_1M(monkeypatch, chart_db):
    """/api/candles menerima tf=1w dan 1M (whitelist)."""
    from bot import dashboard as dbmod
    base_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    w_ms = 7 * 24 * 3600 * 1000
    idx = pd.date_range(start=pd.Timestamp(base_ms, unit="ms", tz="UTC"),
                        periods=50, freq=pd.Timedelta(milliseconds=w_ms))
    closes = [100.0 + i * 0.1 for i in range(50)]
    df = pd.DataFrame({"open": closes, "high": [c + 0.5 for c in closes],
                       "low": [c - 0.5 for c in closes], "close": closes,
                       "volume": [10.0] * 50}, index=idx)
    chart_db.upsert("BTC/USDC:USDC", "1w", df)
    res = dbmod.api_candles("BTC/USDC:USDC", tf="1w", limit=100)
    body = json.loads(res.body)
    assert body.get("tf") == "1w"
    assert body.get("n", 0) >= 30


def test_api_candles_rejects_unknown_tf(chart_db):
    """/api/candles menolak tf tak dikenal (whitelist)."""
    from bot import dashboard as dbmod
    res = dbmod.api_candles("BTC/USDC:USDC", tf="Xyz", limit=100)
    body = json.loads(res.body)
    assert "error" in body
    assert "tf tak dikenal" in body["error"]
