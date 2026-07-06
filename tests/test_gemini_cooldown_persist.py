"""Cooldown key Gemini yang PANJANG (rate_day/auth) persist ke SQLite (kv) → tahan restart;
RPM 60s tidak persist; key pulih → catatan durable dibuang."""
import time

import bot.gemini_client as gc


def _reset(monkeypatch):
    kv: dict = {}
    monkeypatch.setattr(gc.store, "get_kv", lambda k: kv.get(k))
    monkeypatch.setattr(gc.store, "set_kv", lambda k, v: kv.__setitem__(k, dict(v)))
    gc._states.clear()
    gc._persisted.clear()
    gc._persist_loaded = False
    return kv


def test_rate_day_per_key_model_persists_and_survives_restart(monkeypatch):
    kv = _reset(monkeypatch)
    key, model = "AQ.testkey123", "gemini-3-flash-preview"
    gc._mark_bad(key, "rate_day", model)                # RPD habis utk (key,model) → durable
    assert gc._model_dead(key, model)                   # model ini mati
    assert not gc._model_dead(key, "gemini-3.1-flash-lite-preview")  # model LAIN di key sama tetap hidup
    assert gc._st(key)["cooldown_until"] <= time.time()  # cooldown PER-KEY tak disetel (key masih boleh)
    assert kv.get(gc._KV_COOLDOWN)                       # tersimpan ke kv
    # SIMULASI RESTART: buang state RAM, paksa muat ulang dari kv
    gc._states.clear(); gc._persisted.clear(); gc._persist_loaded = False
    assert gc._model_dead(key, model)                   # tanda mati diwarisi, tak dihajar ulang


def test_fallback_success_does_not_resurrect_dead_model(monkeypatch):
    """Inti bug: sukses model fallback di key yg sama DULU menghapus tanda RPD-mati primary
    (_mark_ok reset cooldown per-key) → primary di-retry tiap keputusan (429 sia-sia)."""
    _reset(monkeypatch)
    key, dead, alive = "AQ.k9", "gemini-3-flash-preview", "gemini-3.1-flash-lite-preview"
    gc._mark_bad(key, "rate_day", dead)                 # primary kehabisan kuota harian
    gc._mark_ok(key)                                    # model fallback sukses di key yg sama
    assert gc._model_dead(key, dead)                    # primary TETAP mati → tak di-retry lagi
    assert not gc._model_dead(key, alive)


def test_rpm_60s_not_persisted(monkeypatch):
    kv = _reset(monkeypatch)
    gc._mark_bad("AQ.k2", "rate")                       # RPM 60s → TAK durable (restart > 60s)
    assert not kv.get(gc._KV_COOLDOWN)


def test_recovery_clears_persist(monkeypatch):
    kv = _reset(monkeypatch)
    key = "AQ.k3"
    gc._mark_bad(key, "auth")                           # 5 menit → durable
    assert gc._persisted
    gc._mark_ok(key)                                    # pulih → catatan durable dibuang
    assert not gc._persisted
    assert not kv.get(gc._KV_COOLDOWN)
