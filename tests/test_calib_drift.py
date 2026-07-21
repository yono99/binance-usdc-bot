"""Phase 6: alarm drift kalibrasi — alarm saja, anti-spam, TANPA auto-ubah threshold."""
from types import SimpleNamespace

import bot.forward_close as fclose
import bot.store as store
from bot.forward import ForwardTester


def _fake(report, monkeypatch):
    """Objek mirip-ForwardTester secukupnya untuk memanggil _check_calib_drift."""
    sent = []
    logged = []
    monkeypatch.setattr(store, "calibration_report", lambda mode, last_n=50, days=14: report)
    # journal diikat di forward_close (mixin), bukan di shell forward.py
    monkeypatch.setattr(fclose, "journal", lambda ev, data: logged.append((ev, data)))
    self = SimpleNamespace(
        settings=SimpleNamespace(mode="dry"),
        rs=SimpleNamespace(calib_drift_margin=0.05, calib_drift_min_n=20, conf_min=0.55),
        notify=SimpleNamespace(send=lambda m: sent.append(m)),
        _calib_drifting=False,
    )
    return self, sent, logged


def test_drift_alerts_once_and_suggests_no_autochange(monkeypatch):
    # recent Brier 0.34 vs baseline 0.20 → +0.14 > margin, di atas koin (0.25) → DRIFT
    rep = {"last_50_trades": {"n": 40, "brier": 0.34}, "last_14_days": {"brier": 0.20}}
    self, sent, logged = _fake(rep, monkeypatch)
    ForwardTester._check_calib_drift(self)
    assert self._calib_drifting is True
    assert len(sent) == 1 and "DRIFT KALIBRASI" in sent[0]
    assert "0.55" in sent[0] and "0.60" in sent[0]            # saran conf_min naik, bukan diterapkan
    assert self.rs.conf_min == 0.55                            # threshold TIDAK diubah otomatis
    assert logged and logged[0][0] == "calib_drift"
    # Panggilan kedua saat masih drift → TIDAK alarm ulang (anti-spam)
    ForwardTester._check_calib_drift(self)
    assert len(sent) == 1


def test_no_drift_when_within_margin(monkeypatch):
    rep = {"last_50_trades": {"n": 40, "brier": 0.23}, "last_14_days": {"brier": 0.20}}
    self, sent, _ = _fake(rep, monkeypatch)
    ForwardTester._check_calib_drift(self)
    assert self._calib_drifting is False and sent == []


def test_insufficient_sample_stays_silent(monkeypatch):
    rep = {"last_50_trades": {"n": 5, "brier": 0.40}, "last_14_days": {"brier": 0.20}}
    self, sent, _ = _fake(rep, monkeypatch)
    ForwardTester._check_calib_drift(self)
    assert sent == []                                         # n < min_n → diam


def test_recovery_resets_flag_and_can_realert(monkeypatch):
    self, sent, _ = _fake({"last_50_trades": {"n": 40, "brier": 0.34},
                           "last_14_days": {"brier": 0.20}}, monkeypatch)
    ForwardTester._check_calib_drift(self)                    # drift
    assert self._calib_drifting is True
    monkeypatch.setattr(store, "calibration_report", lambda *a, **k: {   # pulih
        "last_50_trades": {"n": 40, "brier": 0.21}, "last_14_days": {"brier": 0.20}})
    ForwardTester._check_calib_drift(self)
    assert self._calib_drifting is False                      # reset → boleh alarm lagi nanti
