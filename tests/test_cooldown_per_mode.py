"""Tahap 4 (plan-sess) — Cooldown/blacklist per-mode PERSISTENT, EventHub mode labels."""
from __future__ import annotations

import time

import pytest

from bot import store
from bot import cooldown as cd


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    return store


def test_cooldown_isolated_per_mode(db):
    """Cooldown live di mode A tak memblokir di mode B (isolasi per-mode)."""
    cd.cooldown_for("dry", "BTC/USDC:USDC", minutes=10)
    assert not cd.available("dry", "BTC/USDC:USDC")   # dry cooldown aktif
    assert cd.available("live", "BTC/USDC:USDC")     # live tak ter-block


def test_cooldown_persistent_across_calls(db):
    """Snapshot & available pull from persistent state (bukan in-memory)."""
    cd.cooldown_for("test", "ETH/USDC:USDC", minutes=5)
    snap = cd.snapshot("test")
    assert "ETH/USDC:USDC" in snap["cooldown_until"]
    assert cd.available("test", "ETH/USDC:USDC") is False


def test_blacklist_after_sl_streak(db):
    """3 SL berturut-turut → blacklist 6 jam (default)."""
    cd.clear("dry")
    # 3 SL closes
    for _ in range(3):
        cd.record_close("dry", "XRP/USDC:USDC", was_sl=True,
                        cooldown_minutes=0, blacklist_after_sl=3,
                        blacklist_hours=6)
    snap = cd.snapshot("dry")
    assert "XRP/USDC:USDC" in snap["blacklist_until"]
    assert not cd.available("dry", "XRP/USDC:USDC")


def test_clear_resets_cooldown_state(db):
    cd.cooldown_for("dry", "A", minutes=5)
    cd.cooldown_for("dry", "B", minutes=5)
    cd.clear("dry")
    assert cd.available("dry", "A")
    assert cd.available("dry", "B")


def test_eventhub_broadcast_includes_mode_label():
    """EventHub.broadcast menerima mode opsional; hasil broadcast diserialisasi dgn mode."""
    import asyncio
    from bot.eventhub import EventHub
    hub = EventHub()
    q = hub.subscribe()

    async def _test():
        await hub.broadcast("test", {"k": 1}, mode="live")
        await asyncio.sleep(0.01)
        frame = await q.get()
        return frame

    frame = asyncio.run(_test())
    # mode adalah field top-level di frame JSON
    assert '"mode": "live"' in frame
    assert '"type": "test"' in frame
