"""Setting server shared (one-to-many): satu runtime:server untuk dry/test/live."""
from __future__ import annotations

import bot.settings_store as ss
from bot.settings_store import RuntimeSettings, SERVER_SETTING_KEYS, SERVER_KV_KEY


def test_server_keys_are_process_fields_not_risk():
    assert "poll_seconds" in SERVER_SETTING_KEYS
    assert "gemini_model" in SERVER_SETTING_KEYS
    assert "gemini_decide_seconds" in SERVER_SETTING_KEYS
    # personal / risk harus di luar shared
    for k in ("leverage", "bet_usd", "enabled", "max_open_positions", "order_type"):
        assert k not in SERVER_SETTING_KEYS


def test_save_server_from_one_mode_loads_on_all(tmp_path, monkeypatch):
    import bot.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")

    # Personal berbeda per mode
    ss.save_settings(
        RuntimeSettings(
            mode="dry",
            leverage=5,
            bet_usd=4,
            poll_seconds=60,
            gemini_decide_seconds=180,
            gemini_manage_seconds=120,
            gemini_model="models/gemini-test",
            gemini_tool_iters=3,
        ),
        set_active=False,
    )
    ss.save_settings(
        RuntimeSettings(
            mode="live",
            leverage=3,
            bet_usd=2,
            # sengaja beda di payload mode — harus tertimpa shared dari dry save terakhir?
            # save live juga menulis server shared
            poll_seconds=90,
            gemini_decide_seconds=300,
            gemini_manage_seconds=200,
            gemini_model="models/gemini-live",
            gemini_tool_iters=2,
        ),
        set_active=False,
    )

    dry = ss.load_settings("dry")
    live = ss.load_settings("live")
    test = ss.load_settings("test")

    # Personal tetap terpisah
    assert dry.leverage == 5 and dry.bet_usd == 4
    assert live.leverage == 3 and live.bet_usd == 2

    # Server = nilai save live terakhir (shared)
    for s in (dry, live, test):
        assert s.poll_seconds == 90
        assert s.gemini_decide_seconds == 300
        assert s.gemini_manage_seconds == 200
        assert s.gemini_model == "models/gemini-live"
        assert s.gemini_tool_iters == 2

    # KV shared ada
    assert store.get_kv(SERVER_KV_KEY) is not None


def test_seed_server_from_dry_when_missing(tmp_path, monkeypatch):
    import bot.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")

    # Tulis dry langsung ke KV (tanpa save_settings — simulasi bucket lama)
    store.set_kv(
        "runtime:dry",
        {
            "mode": "dry",
            "leverage": 5,
            "poll_seconds": 77,
            "gemini_decide_seconds": 333,
            "gemini_manage_seconds": 111,
            "gemini_min_hold_s": 222,
            "gemini_portfolio_seconds": 444,
            "gemini_plan_hours": 6,
            "gemini_tool_iters": 4,
            "gemini_model": "models/from-dry",
        },
    )
    # live bucket tanpa server fields / nilai default beda
    store.set_kv(
        "runtime:live",
        {
            "mode": "live",
            "leverage": 100,
            "poll_seconds": 5,
            "gemini_decide_seconds": 30,
        },
    )

    # Belum ada runtime:server → seed dari dry
    live = ss.load_settings("live")
    assert live.leverage == 100  # personal live
    assert live.poll_seconds == 77  # dari dry seed
    assert live.gemini_decide_seconds == 333
    assert live.gemini_model == "models/from-dry"

    dry = ss.load_settings("dry")
    assert dry.poll_seconds == 77
    assert dry.gemini_model == "models/from-dry"


def test_extract_and_save_server_settings_roundtrip(tmp_path, monkeypatch):
    import bot.store as store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")
    s = RuntimeSettings(poll_seconds=45, gemini_plan_hours=8, gemini_model="m1")
    out = ss.save_server_settings(s)
    assert out["poll_seconds"] == 45
    assert out["gemini_plan_hours"] == 8
    loaded = ss.load_server_settings()
    assert loaded["poll_seconds"] == 45
    assert loaded["gemini_model"] == "m1"
