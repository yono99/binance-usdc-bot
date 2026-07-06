"""GeminiClient — helper pure: klasifikasi error, rotasi key, fallback model."""
import time

import pytest

from bot import gemini_client as gc
from bot.gemini_client import FALLBACK_MODELS, GeminiClient


@pytest.fixture(autouse=True)
def _reset_states(monkeypatch, tmp_path):
    # isolasi cooldown DURABLE: tanpa ini, _persisted + store nyata bocor antar-test/antar-run
    # (key "auth"/"rate_day" ter-persist ke SQLite → cooldown basi mencemari test berikutnya).
    from bot import store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "gc.db")
    store.init_db()
    gc._states.clear()
    gc._last_call.clear()
    gc._persisted.clear()
    gc._persist_loaded = False
    gc._breaker.update({"fails": 0, "open_until": 0.0})
    yield
    gc._states.clear()
    gc._last_call.clear()
    gc._persisted.clear()
    gc._persist_loaded = False


class _Err(Exception):
    def __init__(self, msg="", status_code=None):
        super().__init__(msg)
        if status_code is not None:
            self.status_code = status_code


# ---------- klasifikasi error ----------

def test_classify_auth():
    assert gc._classify(_Err("API key not valid")) == "auth"
    assert gc._classify(_Err(status_code=403)) == "auth"


def test_classify_rate():
    assert gc._classify(_Err(status_code=429)) == "rate"
    assert gc._classify(_Err("resource_exhausted: quota")) == "rate"


def test_classify_model_and_request_and_other():
    assert gc._classify(_Err(status_code=503)) == "model"
    assert gc._classify(_Err("model is overloaded")) == "model"
    assert gc._classify(_Err(status_code=400)) == "request"
    assert gc._classify(_Err("something weird")) == "other"


# ---------- rotasi key (LRU + cooldown) ----------

def test_ordered_keys_lru():
    keys = ["k1", "k2", "k3"]
    gc._mark_ok("k1")                       # k1 baru dipakai → harus paling belakang
    ordered = gc._ordered_keys(keys)
    assert ordered[-1] == "k1" and set(ordered) == set(keys)


def test_ordered_keys_prefers_healthy_over_cooldown():
    keys = ["k1", "k2"]
    gc._mark_bad("k1", "rate")              # k1 cooldown
    ordered = gc._ordered_keys(keys)
    assert ordered[0] == "k2"               # yang sehat didahulukan


def test_next_available_zero_when_any_healthy():
    keys = ["k1", "k2"]
    assert gc._next_available_s(keys) == 0.0
    gc._mark_bad("k1", "rate")
    assert gc._next_available_s(keys) == 0.0   # k2 masih sehat


# ---------- model & ketersediaan ----------

def test_set_model_primary_first_and_dedup():
    c = GeminiClient([], "gemini-2.5-flash")
    assert c.models[0] == "gemini-2.5-flash"
    assert len(c.models) == len(set(c.models))         # tak ada duplikat
    assert set(FALLBACK_MODELS).issubset(set(c.models))


def test_set_model_default_uses_fallback_list():
    c = GeminiClient([], "")
    assert c.models == FALLBACK_MODELS


def test_available_false_without_keys():
    assert GeminiClient([]).available is False          # tanpa key → tak tersedia


# ---------- throttle RPM (jeda wajib antar-request) ----------

def test_throttle_enforces_min_interval_per_key(monkeypatch):
    monkeypatch.setattr(gc, "_MIN_INTERVAL", 0.05)
    gc._throttle("k1")                      # panggilan pertama k1 → lewat tanpa tunggu
    t0 = time.time()
    gc._throttle("k1")                      # k1 lagi langsung → tertahan ~0.05s
    assert time.time() - t0 >= 0.045


def test_throttle_independent_across_keys(monkeypatch):
    monkeypatch.setattr(gc, "_MIN_INTERVAL", 0.05)
    gc._throttle("k1")
    t0 = time.time()
    gc._throttle("k2")                      # key BEDA → tak menunggu k1 (kuota per-project terpisah)
    assert time.time() - t0 < 0.02


def test_classify_rpd_vs_rpm():
    assert gc._classify(_Err("Quota exceeded: GenerateRequestsPerDayPerProject")) == "rate_day"
    assert gc._classify(_Err(status_code=429)) == "rate"      # per-menit (tanpa 'per day')


def test_mark_bad_rpd_is_per_key_model_not_whole_key():
    """RPD habis = per (key,model): tandai model itu mati sampai reset harian, TAPI biarkan
    key tetap hidup untuk model lain (dulu RPD mematikan seluruh key seharian)."""
    import time
    gc._mark_bad("k2", "rate_day", "gemini-3-flash-preview")
    assert gc._model_dead("k2", "gemini-3-flash-preview")            # model ini mati (jam, sampai reset)
    assert gc._persisted[gc._model_key("k2", "gemini-3-flash-preview")] > time.time() + 3600
    assert gc._st("k2")["cooldown_until"] <= time.time()            # key TIDAK dimatikan seharian


def test_generate_skips_when_all_keys_cooling(monkeypatch):
    monkeypatch.setattr(gc, "genai", object())   # available=True (butuh genai truthy + keys)
    c = GeminiClient(["k1"], "gemini-2.5-flash")
    gc._mark_bad("k1", "rate")                    # satu-satunya key masuk cooldown 60s
    assert c.generate("halo") is None             # → tak menembak request; fallback deterministik
