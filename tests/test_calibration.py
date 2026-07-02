"""Kalibrasi confidence (Phase 1+2): skor Brier per mode + tier gerbang SIZE."""
from bot import store
from bot.settings_store import RuntimeSettings, _from_dict


# ---------- Phase 1: calibration_log + laporan rolling ----------

def test_log_and_report_per_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")
    store.log_calibration(1, "BTC/USDC:USDC", 0.8, 1, "dry")   # brier 0.04
    store.log_calibration(2, "ETH/USDC:USDC", 0.8, 0, "dry")   # brier 0.64
    store.log_calibration(3, "BTC/USDC:USDC", 0.9, 1, "live")  # mode lain: terisolasi

    rep = store.calibration_report("dry", last_n=50, days=14)
    agg = rep["last_50_trades"]
    assert agg["n"] == 2
    assert abs(agg["brier"] - 0.34) < 1e-9          # (0.04+0.64)/2
    assert agg["hit_rate"] == 50.0
    assert rep["last_14_days"]["n"] == 2

    assert store.calibration_report("live")["last_50_trades"]["n"] == 1
    assert store.calibration_report("test")["last_50_trades"]["n"] == 0


def test_predicted_prob_clamped_to_unit_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")
    store.log_calibration(None, "X", 1.7, 1, "dry")            # p di-clamp ke 1.0
    assert store.calibration_report("dry")["last_50_trades"]["brier"] == 0.0


# ---------- Phase 2: tier confidence -> ukuran / abstain ----------

def test_conf_size_mult_tiers():
    s = RuntimeSettings()                                       # 0.75 / 0.55 / 0.5x
    assert s.conf_size_mult(None) == 1.0                        # rule-based: tak digerbang
    assert s.conf_size_mult(0.80) == 1.0                        # penuh
    assert s.conf_size_mult(0.75) == 1.0                        # batas inklusif
    assert s.conf_size_mult(0.60) == 0.5                        # reduced
    assert s.conf_size_mult(0.54) is None                       # abstain


def test_conf_tier_clamp_keeps_min_below_full():
    s = _from_dict({"conf_full": 0.6, "conf_min": 0.9, "conf_reduced_mult": 3.0})
    assert s.conf_full == 0.6
    assert s.conf_min == 0.6                                    # dipaksa <= conf_full
    assert s.conf_reduced_mult == 1.0                           # clamp <= 1
