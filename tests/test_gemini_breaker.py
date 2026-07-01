"""Circuit-breaker Gemini: gagal beruntun → berhenti panggil (anti-spiral 429)."""
import time

import pytest

from bot import gemini_client as gc


@pytest.fixture(autouse=True)
def _reset():
    gc._breaker.update(fails=0, open_until=0.0)
    gc._states.clear()
    yield
    gc._breaker.update(fails=0, open_until=0.0)


def test_opens_after_consecutive_fails():
    for _ in range(gc.BREAKER_FAILS):
        gc._breaker_record(False)
    assert gc._breaker_open() is True


def test_below_threshold_stays_closed():
    for _ in range(gc.BREAKER_FAILS - 1):
        gc._breaker_record(False)
    assert gc._breaker_open() is False


def test_success_resets_fail_count():
    gc._breaker_record(False)
    gc._breaker_record(False)
    gc._breaker_record(True)
    assert gc._breaker["fails"] == 0 and gc._breaker_open() is False


def test_closes_after_cooldown():
    for _ in range(gc.BREAKER_FAILS):
        gc._breaker_record(False)
    assert gc._breaker_open() is True
    gc._breaker["open_until"] = time.time() - 1        # kadaluarsa
    assert gc._breaker_open() is False


def test_generate_shortcircuits_without_api_call(monkeypatch):
    monkeypatch.setattr(gc, "genai", object())         # paksa available=True
    c = gc.GeminiClient(["k1"])
    assert c.available is True
    gc._breaker["open_until"] = time.time() + 60        # breaker terbuka

    def boom(key):
        raise AssertionError("API TAK BOLEH dipanggil saat breaker terbuka")
    monkeypatch.setattr(gc, "_get_client", boom)
    assert c.generate("hi", purpose="test") is None     # short-circuit → fallback
