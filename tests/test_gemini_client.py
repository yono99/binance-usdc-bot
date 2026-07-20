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
    gc._breaker = {"fails": 0, "open_until": 0.0}  # legacy compat (tak dipakai 26-key)
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


def test_parse_retry_seconds():
    assert gc._parse_retry_seconds("Please retry in 42.5s.") == 42.5
    assert gc._parse_retry_seconds("{'retryDelay': '48s'}") == 48.0
    assert gc._parse_retry_seconds("retry in 2m") == 120.0
    assert gc._parse_retry_seconds("retry in 1h") == 3600.0
    assert gc._parse_retry_seconds("no delay here") is None


def test_mark_bad_uses_parsed_retry_for_rate():
    secs = gc._mark_bad("k1", "rate", err="RESOURCE_EXHAUSTED Please retry in 33s.")
    assert secs == 33.0
    left = gc._cooldown_remaining_s("k1")
    assert 30.0 <= left <= 33.0
    # key limited → di-SKIP dari healthy pool
    assert "k1" not in gc._ordered_keys(["k1", "k2"])
    assert gc._ordered_keys(["k1", "k2"])[0] == "k2"


def test_mark_bad_auth_denied_long_cooldown():
    secs = gc._mark_bad(
        "k_denied", "auth",
        err="403 PERMISSION_DENIED. Your project has been denied access. Please contact support.")
    assert secs == gc.COOLDOWN_AUTH_DENIED
    assert gc._cooldown_remaining_s("k_denied") > 5 * 3600
    # durable persist
    assert gc._key_hash("k_denied") in gc._persisted


def test_ordered_keys_spreads_evenly_lru():
    """Pool 26-style: last_used 0 dulu → meratakan ke key belum pernah dipakai."""
    keys = [f"k{i}" for i in range(5)]
    for i, k in enumerate(keys[:3]):
        gc._st(k)["last_used"] = 1000.0 + i   # k0..k2 sudah dipakai
    # k3,k4 last_used=0 → harus di depan
    ordered = gc._ordered_keys(keys)
    assert set(ordered[:2]) == {"k3", "k4"}
    assert ordered[-1] == "k2"                 # paling baru dipakai di belakang


def test_mark_bad_rpd_is_per_key_model_not_whole_key(monkeypatch):
    """RPD habis = per (key,model): tandai model itu mati sampai reset harian, TAPI biarkan
    key tetap hidup untuk model lain (dulu RPD mematikan seluruh key seharian)."""
    monkeypatch.setattr(gc, "_secs_to_rpd_reset", lambda: 7200.0)
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


def test_generate_skips_limited_key_tries_next(monkeypatch):
    """Key kena limit di-SKIP; key sehat berikutnya dipanggil (rotasi merata + fail-soft)."""
    monkeypatch.setattr(gc, "genai", object())
    calls: list[str] = []

    class _FakeModels:
        def generate_content(self, model, contents):
            raise AssertionError("should not call limited key")

    class _FakeClient:
        def __init__(self, api_key, **kw):
            self.api_key = api_key
            self.models = _FakeModels()

    class _OkModels:
        def generate_content(self, model, contents):
            class R:
                text = "ok-from-k2"
                usage_metadata = None
            return R()

    def fake_get(key):
        calls.append(key)
        if key == "k2":
            c = type("C", (), {})()
            c.models = _OkModels()
            return c
        c = type("C", (), {})()
        c.models = _FakeModels()
        return c

    monkeypatch.setattr(gc, "_get_client", fake_get)
    monkeypatch.setattr(gc, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(gc, "store", type("S", (), {
        "log_gemini_usage": staticmethod(lambda *a, **k: None),
    })())
    gc._mark_bad("k1", "rate", err="retry in 60s")
    client = GeminiClient(["k1", "k2"], "gemini-3-flash-preview")
    out = client.generate("p", purpose="test")
    assert out == "ok-from-k2"
    assert "k1" not in calls
    assert calls == ["k2"]