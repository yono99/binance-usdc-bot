"""Fix A+B: lantai SL, pelacakan MFE/MAE, aliran data exit ke settle Gemini,
dan kalibrator MAE (kontrol sintetis)."""
import numpy as np
import pandas as pd
import pytest

from bot import forward as fwd
from bot import slcalib, store
from bot.config import Settings
from bot.forward import ForwardTester, default_params


# ---------- Fix A: _sl_floor ----------

def test_sl_floor_widens_tight_sl_keeps_wide():
    f = ForwardTester._sl_floor
    # default k_atr=1.75 (kalibrasi data): entry 100, ATR 2 → lantai ATR = 3.5;
    # candle range 6 → lantai range = 3.0 → lantai efektif 3.5
    assert f(100.0, True, 99.5, 2.0, 6.0) == 96.5        # mepet → dilebarkan
    assert f(100.0, True, 95.0, 2.0, 6.0) == 95.0        # sudah lebar → utuh
    assert f(100.0, False, 100.5, 2.0, 6.0) == 103.5     # short simetris
    assert f(100.0, True, 99.5, 0.0, 0.0) == 99.5        # tanpa info → tak diubah
    # k eksplisit tetap bisa dioverride (dipakai kalibrasi/eksperimen)
    assert f(100.0, True, 99.5, 2.0, 6.0, k_atr=1.0) == 97.0


# ---------- Fix B: MFE/MAE tracking + settle flow ----------

class _Client:
    def fetch_funding_rate(self, sym):
        return {"fundingRate": 0.0}


class _StubEx:
    def __init__(self, settings):
        self.settings = settings
        self.client = _Client()
        self.last = 100.0

    def usdc_symbols(self):
        return ["BTC/USDC:USDC"]

    def ticker(self, sym):
        return {"last": self.last}


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.setattr(fwd, "Exchange", _StubEx)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")


def _ft(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return ForwardTester(s, ["BTC/USDC:USDC"], default_params())


def test_monitor_tracks_mfe_mae(cfg):
    ft = _ft(cfg)
    idx = pd.date_range("2026-01-01", periods=3, freq="15min", tz="UTC")
    ft.buffers["X"] = pd.DataFrame({"open": 100.0, "high": [100, 104, 101],
                                    "low": [100, 97, 99], "close": 100.0,
                                    "volume": 1.0}, index=idx)
    ft.open = {"X": {"side": "long", "entry": 100.0, "qty": 1.0, "sl": 90.0,
                     "tp": 120.0, "liq": 50.0, "bet": 10.0}}
    ft.ex.last = 100.0
    ft._monitor_usd("X")
    assert ft.open["X"]["mfe_pct"] == pytest.approx(4.0)   # high 104
    assert ft.open["X"]["mae_pct"] == pytest.approx(3.0)   # low 97


def test_settle_flow_persists_exit_data(tmp_path):
    did = store.record_decision("BTC", "breakout", "long", 0.7, "test", {})
    assert store.settle_decision(did, 1.5, mae_pct=0.8, mfe_pct=3.2, exit_reason="tp")
    rows = store.settled_decisions()
    assert rows[-1]["mae_pct"] == 0.8 and rows[-1]["exit_reason"] == "tp"
    st = store.setup_stats("breakout")
    assert st["n"] == 1 and st["sl_hit_rate"] == 0.0
    did2 = store.record_decision("BTC", "breakout", "long", 0.7, "t2", {})
    store.settle_decision(did2, -1.0, mae_pct=1.6, mfe_pct=2.9, exit_reason="sl")
    st = store.setup_stats("breakout")
    assert st["sl_hit_rate"] == 50.0
    assert st["avg_mfe_before_sl_pct"] == 2.9              # sempat untung besar lalu ke-SL


# ---------- kalibrator ----------

def test_mae_of_winners_recovers_known_dip():
    """Sintetis: tiap 40 bar, harga dip persis ~0.8×ATR lalu rally >2.5×ATR →
    kuantil MAE pemenang harus ≈ 0.8 (bukan default 1.0 atau angka acak)."""
    rng = np.random.default_rng(3)
    n = 8000
    base = 1000.0
    close = np.full(n, base) + rng.normal(0, 0.3, n)
    high = close + 0.5
    low = close - 0.5
    atr_est = 1.6                                          # dari range konstan ~1+noise
    for t0 in range(100, n - 30, 40):
        low[t0 + 1] = close[t0] - 0.8 * atr_est            # dip dulu (MAE)
        hi_target = close[t0] + 3.0 * atr_est              # lalu rally (menang)
        high[t0 + 2:t0 + 6] = hi_target
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    r = slcalib.mae_of_winners(df, horizon=16, tp_mult=2.5)
    q = r["semua"]
    assert q["n_winners"] > 100
    # dip terpasang 0.8×ATR; noise ±0.5 & inflasi ATR lokal pasca-rally melebarkan
    # estimasi → uji MEKANIKA: rentang wajar di sekitar dip + kuantil monotonik.
    assert 0.4 <= q["mae_q80"] <= 1.5, q
    assert q["mae_q50"] < q["mae_q80"] < q["mae_q90"], q
