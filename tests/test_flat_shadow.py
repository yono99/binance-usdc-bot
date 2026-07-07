"""Flat shadow: record→settle roundtrip, metrik miss, verdict pra-registrasi, isolasi mode."""
import numpy as np
import pandas as pd

from bot import flat_shadow, store

CFG = {"flat_shadow": {"mode": "shadow", "horizon_bars": 4, "k_atr": 1.0,
                       "sample": 5, "miss_threshold": 0.35, "retention_days": 90}}
DEC = {"conviction": 0.4, "rationale": "noise"}


def _buf(closes, start="2026-01-01", freq="15min"):
    idx = pd.date_range(start, periods=len(closes), freq=freq)
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c * 1.001, "low": c * 0.999, "close": c,
                         "volume": 1.0}, index=idx)


def _iso(db, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")


def test_record_settle_miss_up(tmp_path, monkeypatch):
    _iso(None, tmp_path, monkeypatch)
    buf = _buf([100, 100, 100, 105, 106, 107, 108])       # naik jauh setelah bar ke-2
    flat_shadow.record_flat("dry", "X", 100.0, 2.0, DEC, "trend", buf.index[2], CFG)
    n = flat_shadow.settle_pending("dry", {"X": buf}, CFG)
    assert n == 1
    rep = flat_shadow.report("dry", CFG)
    assert rep["n"] == 1 and rep["miss_rate"] == 1.0
    assert rep["miss_dirs"]["up"] == 1 and rep["miss_dirs"]["down"] == 0


def test_flat_tape_no_miss(tmp_path, monkeypatch):
    _iso(None, tmp_path, monkeypatch)
    buf = _buf([100.0] * 7)                                # datar total
    flat_shadow.record_flat("dry", "X", 100.0, 2.0, DEC, "range", buf.index[2], CFG)
    assert flat_shadow.settle_pending("dry", {"X": buf}, CFG) == 1
    assert flat_shadow.report("dry", CFG)["miss_rate"] == 0.0


def test_short_buffer_stays_pending(tmp_path, monkeypatch):
    _iso(None, tmp_path, monkeypatch)
    buf = _buf([100, 100, 100])                            # belum ada bar setelah bar_ts
    flat_shadow.record_flat("dry", "X", 100.0, 2.0, DEC, "trend", buf.index[-1], CFG)
    assert flat_shadow.settle_pending("dry", {"X": buf}, CFG) == 0
    assert flat_shadow.report("dry", CFG)["verdict"] == "NO_DATA"
    assert flat_shadow.report("dry", CFG)["pending"] == 1


def test_mode_isolation(tmp_path, monkeypatch):
    _iso(None, tmp_path, monkeypatch)
    buf = _buf([100.0] * 7)
    flat_shadow.record_flat("dry", "X", 100.0, 2.0, DEC, "trend", buf.index[2], CFG)
    flat_shadow.record_flat("live", "X", 100.0, 2.0, DEC, "trend", buf.index[2], CFG)
    flat_shadow.settle_pending("dry", {"X": buf}, CFG)
    assert flat_shadow.report("dry", CFG)["n"] == 1
    assert flat_shadow.report("live", CFG)["verdict"] == "NO_DATA"   # live masih pending


def test_verdict_insufficient_then_too_expensive(tmp_path, monkeypatch):
    _iso(None, tmp_path, monkeypatch)
    miss_buf = _buf([100, 100, 100, 110, 111, 112, 113])
    for i in range(4):                                     # 4 < sample 5
        flat_shadow.record_flat("dry", f"S{i}", 100.0, 2.0, DEC, "trend", miss_buf.index[2], CFG)
    flat_shadow.settle_pending("dry", {f"S{i}": miss_buf for i in range(4)}, CFG)
    assert flat_shadow.report("dry", CFG)["verdict"] == "INSUFFICIENT"

    # tambah sampai lolos sample + regime bucket n>=50 → pakai cfg sample kecil & regime sama
    cfg2 = {"flat_shadow": {**CFG["flat_shadow"], "sample": 5}}
    for i in range(4, 60):                                 # regime 'trend' n>=50, semua miss
        flat_shadow.record_flat("dry", f"S{i}", 100.0, 2.0, DEC, "trend", miss_buf.index[2], cfg2)
    flat_shadow.settle_pending("dry", {f"S{i}": miss_buf for i in range(60)}, cfg2)
    rep = flat_shadow.report("dry", cfg2)
    assert rep["verdict"] == "FLAT_BIAS_TOO_EXPENSIVE"
    assert rep["per_regime"]["trend"]["n"] >= 50


def test_off_mode_records_nothing(tmp_path, monkeypatch):
    _iso(None, tmp_path, monkeypatch)
    off = {"flat_shadow": {**CFG["flat_shadow"], "mode": "off"}}
    buf = _buf([100.0] * 7)
    flat_shadow.record_flat("dry", "X", 100.0, 2.0, DEC, "trend", buf.index[2], off)
    assert flat_shadow.report("dry", off)["verdict"] == "NO_DATA"
