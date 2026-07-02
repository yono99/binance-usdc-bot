"""Pemicu give-back menuju TP: posisi sempat >50% ke TP lalu balik → paksa review Gemini."""
import types

from bot.forward import ForwardTester

TP = ForwardTester._tp_progress


def _long():
    return {"side": "long", "entry": 100.0, "tp": 110.0, "sl": 95.0, "liq": 90.0}


def test_tp_progress_pure():
    assert TP(_long(), 100.0) == 0.0                    # di entry
    assert TP(_long(), 110.0) == 1.0                    # di TP
    assert TP(_long(), 105.0) == 0.5                    # setengah jalan
    assert TP(_long(), 98.0) == -0.2                    # underwater
    short = {"side": "short", "entry": 100.0, "tp": 90.0}
    assert TP(short, 95.0) == 0.5                       # short: turun = maju ke TP
    assert TP({"side": "long", "entry": 100.0, "tp": None}, 105.0) is None


def _self(pos):
    ticks = {"p": None}
    self = types.SimpleNamespace(
        live=False, open={"BTC": pos}, buffers={},
        ex=types.SimpleNamespace(ticker=lambda s: {"last": ticks["p"]}),
        _tp_progress=ForwardTester._tp_progress,
        _giveback_tp_frac=0.5, _giveback_margin=0.2,
        _last_manage={}, _close_usd=lambda *a: (_ for _ in ()).throw(AssertionError("tak boleh close")),
    )
    def tick(p):
        ticks["p"] = p
        ForwardTester._monitor_usd(self, "BTC")
    return self, tick, pos


def test_giveback_forces_manage_after_peak_then_retrace():
    self, tick, pos = _self(_long())
    tick(106.0)                                         # 60% ke TP — puncak, belum give-back
    assert pos.get("giveback_note") is None
    assert self._last_manage.get("BTC") != 0.0
    tick(103.0)                                         # balik ke 30% → gap 0.3 ≥ 0.2 & puncak ≥ 0.5 → FIRE
    assert pos["giveback_note"] and "menuju TP" in pos["giveback_note"]
    assert self._last_manage["BTC"] == 0.0             # throttle dibuka → manage jalan siklus ini
    assert pos["giveback_fired_at"] == 0.6


def test_giveback_no_respam_without_new_peak():
    self, tick, pos = _self(_long())
    tick(106.0); tick(103.0)                           # fire pertama
    pos["giveback_note"] = None                         # anggap manage sudah konsumsi
    tick(102.0)                                         # masih di bawah puncak lama, tak ada puncak baru
    assert pos["giveback_note"] is None                 # TIDAK fire ulang


def test_no_giveback_below_half_to_tp():
    self, tick, pos = _self(_long())
    tick(104.0)                                         # cuma 40% ke TP (< 0.5)
    tick(100.0)                                         # balik penuh — tapi puncak < 0.5 → tak fire
    assert pos.get("giveback_note") is None
