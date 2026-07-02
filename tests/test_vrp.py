"""Rem-VRP (A/B shadow): mekanik gap, fail-open, stempel, dan analyzer kontrol +/−."""
import numpy as np
import pandas as pd

from bot import vrp


class _FakeEx:
    def __init__(self, closes):
        self._c = closes

    def ohlcv(self, symbol, tf, limit=33):
        idx = pd.date_range("2026-01-01", periods=len(self._c), freq="D", tz="UTC")
        return pd.DataFrame({"close": self._c}, index=idx)


class _BoomEx:
    def ohlcv(self, *a, **k):
        raise RuntimeError("network down")


def _flat_closes(n=40, vol=0.0):
    rng = np.random.default_rng(0)
    return list(100 * np.exp(np.cumsum(rng.normal(0, vol, n))))


def test_compute_gap_known_value():
    closes = _flat_closes(vol=0.0)                 # RV = 0
    gap = vrp.compute_gap(50.0, closes)            # IV 50% − RV 0 = 0.5
    assert abs(gap - 0.5) < 1e-9
    assert vrp.compute_gap(50.0, closes[:10]) is None   # data kurang


def test_brake_threshold_and_stamp():
    b = vrp.VRPBrake(_FakeEx(_flat_closes()), {"vrp": {"mode": "shadow", "gap_threshold": 0.10}},
                     fetch_dvol=lambda: 50.0)      # gap 0.5 > 0.10
    on, gap = b.check()
    assert on and gap > 0.10
    st = b.stamp()
    assert st["vrp_brake"] is True and st["vrp_gap"] == gap


def test_fail_open_on_errors():
    b = vrp.VRPBrake(_BoomEx(), {"vrp": {"mode": "shadow"}}, fetch_dvol=lambda: 50.0)
    assert b.check() == (False, None)              # exchange error → off
    b2 = vrp.VRPBrake(_FakeEx(_flat_closes()), {"vrp": {"mode": "shadow"}},
                      fetch_dvol=lambda: None)     # DVOL gagal → off
    assert b2.check() == (False, None)
    b3 = vrp.VRPBrake(_BoomEx(), {"vrp": {"mode": "off"}})
    assert b3.check() == (False, None) and b3.stamp() == {}


def test_analyze_shadow_controls():
    rng = np.random.default_rng(7)
    # kontrol positif: trade saat brake-on jelas lebih buruk
    rows = ([{"r": float(x), "vrp_brake": True} for x in rng.normal(-0.8, 0.3, 40)]
            + [{"r": float(x), "vrp_brake": False} for x in rng.normal(+0.3, 0.3, 40)])
    v = vrp.analyze_shadow(rows)
    assert v["verdict"] == "VRP_BRAKE_ADDS_VALUE", v
    # kontrol negatif: tak ada beda → NOT_PROVEN
    rows0 = ([{"r": float(x), "vrp_brake": True} for x in rng.normal(0, 0.3, 40)]
             + [{"r": float(x), "vrp_brake": False} for x in rng.normal(0, 0.3, 40)])
    assert vrp.analyze_shadow(rows0)["verdict"] == "NOT_PROVEN"
    # satu sisi kosong → INSUFFICIENT
    assert vrp.analyze_shadow(rows0[:40])["verdict"] == "INSUFFICIENT"


def test_log_close_writes_only_stamped(tmp_path):
    p = tmp_path / "vrp.jsonl"
    vrp.log_close("X", {"vrp_brake": True, "vrp_gap": 0.2}, 1.5, path=p)
    vrp.log_close("Y", {"side": "long"}, -1.0, path=p)      # tanpa stempel → dilewati
    rows = [__import__("json").loads(x) for x in p.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["symbol"] == "X" and rows[0]["r"] == 1.5
