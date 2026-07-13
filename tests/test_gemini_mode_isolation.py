"""Tahap 0 (plan-sess) — verifikasi mode-isolation di Gemini tables (decisions/lessons/
reflections) + RuntimeSettings split per-wallet (USDT/USDC). Migrasi SQLite idempotent.

Tujuan: pastikan track record Gemini tak bercampur antar-mode (live-track tak mencemari
dry-track) dan migrasi saldo lama 'balance_usd' → split 'balance_usdt/balance_usdc'
berjalan idempotent tanpa data loss."""
from __future__ import annotations

import pytest

from bot import store
from bot import settings_store


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    return store


def test_schema_has_mode_columns(db):
    """ALTER TABLE menambah kolom mode + index per-mode untuk decisions/lessons/reflections."""
    store._migrate()  # idempotent
    store._migrate()  # panggil dua kali → tak error
    with store._conn() as c:
        for t in ("gemini_decisions", "gemini_lessons", "gemini_reflections"):
            cols = [r["name"] for r in c.execute(f"PRAGMA table_info({t})").fetchall()]
            assert "mode" in cols, f"kolom mode hilang di {t}"


def test_record_decision_isolated_per_mode(db):
    """record_decision(mode=...) → row tersimpan dengan mode tsb; query per-mode
    filtering 其他 mode tak melihat-nya."""
    did_live = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.7,
                                  "konteks live", {"x": 1}, mode="live")
    did_dry = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.5,
                                 "konteks dry", {"x": 1}, mode="dry")
    # recent tanpa mode = lintas; ada 2 row.
    all_decs = db.recent_decisions()
    assert len(all_decs) == 2
    only_live = db.recent_decisions(mode="live")
    assert len(only_live) == 1 and only_live[0]["id"] == did_live
    only_dry = db.recent_decisions(mode="dry")
    assert len(only_dry) == 1 and only_dry[0]["id"] == did_dry


def test_setup_stats_per_mode(db):
    """Statistik per-setup dipisah per-mode (track record tak kontaminasi)."""
    # 5 trade live (intrinsik +EV), 5 trade dry (-EV)
    for i, r in enumerate([1.0] * 5):
        did = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.7, "", {}, mode="live")
        db.settle_decision(did, r)
    for r in [-1.0] * 5:
        did = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.5, "", {}, mode="dry")
        db.settle_decision(did, r)
    live_st = db.setup_stats("trend_pullback", mode="live")
    dry_st = db.setup_stats("trend_pullback", mode="dry")
    all_st = db.setup_stats("trend_pullback")  # None = lintas
    assert live_st["n"] == 5 and live_st["exp_r"] == pytest.approx(1.0)
    assert dry_st["n"] == 5 and dry_st["exp_r"] == pytest.approx(-1.0)
    # lintas agregat: exp_r = 0
    assert all_st["n"] == 10 and all_st["exp_r"] == pytest.approx(0.0)


def test_active_lessons_per_mode(db):
    """Pelajaran diaktifkan HANYA bila bukti di mode-nya sendiri cukup."""
    lid_live = db.add_lesson("*", "trend_pullback", "live works", mode="live")
    lid_dry = db.add_lesson("*", "trend_pullback", "dry kebetulan", mode="dry")
    # data live: 5 trade +EV → aktif
    for _ in range(5):
        d = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.7, "", {}, mode="live")
        db.settle_decision(d, 0.5)
    # data dry: 0 trade → tak aktif
    db.promote_lessons(min_n=5)
    only_live = db.active_lessons(mode="live")
    only_dry = db.active_lessons(mode="dry")
    assert any(l["id"] == lid_live for l in only_live)
    assert not any(l["id"] == lid_dry for l in only_dry)


def test_add_reflection_carries_mode(db):
    rid = db.add_reflection("last_80", "good", {"settled": 5}, mode="live")
    with db._conn() as c:
        row = c.execute("SELECT mode FROM gemini_reflections WHERE id=?", (rid,)).fetchone()
    assert row["mode"] == "live"


def test_runtime_settings_split(tmp_path, monkeypatch):
    """RuntimeSettings.back-compat: KV lama HANYA dgn 'balance_usd' (tanpa balance_usdt/
    balance_usdc) dipecah ke split 50/50 saat load. Idempoten."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    ONLY_LEGACY = {"enabled": False, "technique": "auto",
                   "balance_usd": 100.0, "symbols": ["BTC/USDC:USDC"]}
    store.set_kv("runtime:dry", ONLY_LEGACY)
    rs = settings_store.load_settings("dry")
    # legacy 'balance_usd' 100 → split 50/50 (default migrasi 2 wallet sama)
    assert rs.balance_usdt == pytest.approx(50.0)
    assert rs.balance_usdc == pytest.approx(50.0)
    # pemuatan kedua tak menggandakan (idempotent)
    rs2 = settings_store.load_settings("dry")
    assert rs2.balance_usdt == pytest.approx(50.0)


def test_runtime_settings_existing_split_unchanged(tmp_path, monkeypatch):
    """KV SUDAH punya balance_usdt+balance_usdc (Tahap 0 ke depan) → loader hormati nilai asli."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    store.set_kv("runtime:dry", {"enabled": False, "technique": "auto",
                                 "balance_usd": 100.0,
                                 "balance_usdt": 80.0, "balance_usdc": 20.0,
                                 "symbols": ["BTC/USDC:USDC"]})
    rs = settings_store.load_settings("dry")
    assert rs.balance_usdt == pytest.approx(80.0)
    assert rs.balance_usdc == pytest.approx(20.0)


def test_migrate_balance_split_script_idempotent(tmp_path, monkeypatch):
    """Jalankan alur migrasi satu mode: legacy → split → idempotent (kedua run = no_op)."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    store.set_kv("runtime:dry", {"enabled": False, "technique": "auto",
                                 "balance_usd": 80.0, "dry_quote_split_usdc": 0.25,
                                 "symbols": ["BTC/USDC:USDC"]})
    from migrate_balance_split import migrate
    msg1 = migrate("dry")
    assert msg1.startswith("migrated")
    msg2 = migrate("dry")
    assert msg2 == "no_op"
    # 25% dari 80 → 20 USDC, 60 USDT
    rs = settings_store.load_settings("dry")
    assert rs.balance_usdc == pytest.approx(20.0)
    assert rs.balance_usdt == pytest.approx(60.0)
