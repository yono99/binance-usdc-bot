"""Circuit-breaker Gemini PER-KEY (arsitektur 26-key): gagal beruntun di 1 key
→ cooldown key itu saja; 25 key lain tetap jalan."""
import time

import pytest

from bot import gemini_client as gc


@pytest.fixture(autouse=True)
def _reset():
    gc._states.clear()
    gc._last_call.clear()
    gc._model_health.clear()
    yield
    gc._states.clear()
    gc._last_call.clear()
    gc._model_health.clear()


def test_opens_for_one_key_after_consecutive_fails():
    for _ in range(gc.BREAKER_FAILS_PER_KEY):
        gc._breaker_record_for("k1", False)
    assert gc._st("k1")["cooldown_until"] > time.time()
    assert gc._breaker_open(["k1"]) is True                      # k1 breaker-terbuka


def test_other_key_still_healthy():
    for _ in range(gc.BREAKER_FAILS_PER_KEY):
        gc._breaker_record_for("k1", False)
    assert gc._breaker_open(["k1", "k2"]) is False               # k2 sehat → tidak semua breaker


def test_below_threshold_stays_healthy():
    for _ in range(gc.BREAKER_FAILS_PER_KEY - 1):
        gc._breaker_record_for("k1", False)
    assert gc._st("k1")["cooldown_until"] <= time.time()         # belum cooldown


def test_success_resets_fail_count():
    gc._breaker_record_for("k1", False)
    gc._breaker_record_for("k1", False)
    gc._breaker_record_for("k1", True)
    assert gc._st("k1")["fails"] == 0
    assert gc._st("k1")["cooldown_until"] <= time.time()         # sehat


def test_closes_after_cooldown():
    for _ in range(gc.BREAKER_FAILS_PER_KEY):
        gc._breaker_record_for("k1", False)
    assert gc._breaker_open(["k1"]) is True
    gc._st("k1")["cooldown_until"] = time.time() - 1             # kadaluarsa
    assert gc._breaker_open(["k1"]) is False


def test_generate_shortcircuits_when_all_keys_in_breaker(monkeypatch):
    monkeypatch.setattr(gc, "genai", object())                    # paksa available=True
    c = gc.GeminiClient(["k1"])
    assert c.available is True
    for _ in range(gc.BREAKER_FAILS_PER_KEY):
        gc._breaker_record_for("k1", False)                      # k1 mati krn breaker

    def boom(key):
        raise AssertionError("API TAK BOLEH dipanggil saat breaker terbuka")
    monkeypatch.setattr(gc, "_get_client", boom)
    assert c.generate("hi", purpose="test") is None               # short-circuit → fallback


def test_generate_proceeds_when_other_key_healthy(monkeypatch):
    monkeypatch.setattr(gc, "genai", object())                    # paksa available=True
    c = gc.GeminiClient(["k1", "k2"])
    for _ in range(gc.BREAKER_FAILS_PER_KEY):
        gc._breaker_record_for("k1", False)                      # k1 mati, k2 sehat
    monkeypatch.setattr(gc, "_MIN_INTERVAL", 0.0)                 # tak menunggu throttle

    class _Resp:
        text = "ok"
        usage_metadata = None

    calls = []
    def fake_generate_content(model, contents):
        calls.append(model)
        return _Resp()
    def fake_client(key):
        cm = type("CM", (), {})()
        cm.models = type("M", (), {})()
        cm.models.generate_content = fake_generate_content
        return cm
    monkeypatch.setattr(gc, "_get_client", fake_client)
    monkeypatch.setattr(gc.store, "log_gemini_usage", lambda *a, **kw: None)
    out = c.generate("hi", purpose="test")
    assert out == "ok"                                            # k2 dipakai → sukses
