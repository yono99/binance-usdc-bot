"""GeminiClient — helper pure: klasifikasi error, rotasi key, fallback model."""
import pytest

from bot import gemini_client as gc
from bot.gemini_client import FALLBACK_MODELS, GeminiClient


@pytest.fixture(autouse=True)
def _reset_states():
    gc._states.clear()
    yield
    gc._states.clear()


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
