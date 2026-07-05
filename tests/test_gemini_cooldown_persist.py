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


def test_rate_day_persists_and_survives_restart(monkeypatch):
    kv = _reset(monkeypatch)
    key = "AQ.testkey123"
    gc._mark_bad(key, "rate_day")                       # RPD habis → cooldown panjang → durable
    assert gc._st(key)["cooldown_until"] > time.time() + 120
    assert kv.get(gc._KV_COOLDOWN)                       # tersimpan ke kv
    # SIMULASI RESTART: buang state RAM, paksa muat ulang dari kv
    gc._states.clear(); gc._persisted.clear(); gc._persist_loaded = False
    assert gc._st(key)["cooldown_until"] > time.time() + 120   # cooldown diwarisi, tak dihajar ulang


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
