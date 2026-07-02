"""Regresi insiden 2026-07-02: tab dashboard basi menimpa mode aktif + leverage.

Fix: POST /api/settings tak lagi bisa mengubah 'mode' (jalur resmi: POST /api/mode),
dan bersifat PATCH (field yang tak dikirim payload dipertahankan dari nilai
tersimpan, bukan direset ke default RuntimeSettings())."""
import json

from bot import dashboard, store
from bot.settings_store import RuntimeSettings, get_active_mode, load_settings, save_settings


def test_settings_save_cannot_change_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")
    save_settings(RuntimeSettings(mode="live", leverage=3, bet_usd=2.0))
    assert get_active_mode() == "live"

    # payload BASI membawa mode="test" (simulasi tab lama) -> HARUS diabaikan
    resp = dashboard.api_set_settings({"mode": "test", "leverage": 3, "bet_usd": 2.0})
    body = json.loads(resp.body)
    assert body["mode"] == "live"                  # tak berubah walau diminta
    assert get_active_mode() == "live"              # active_mode juga tak tersentuh


def test_settings_save_is_non_destructive_for_omitted_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")
    save_settings(RuntimeSettings(mode="live", leverage=3, bet_usd=2.0,
                                  max_open_positions=3, news_veto=True))

    # payload PARSIAL (spt form basi yg tak tahu field leverage) -> leverage TAK
    # boleh reset ke default (100), field lain yg disebut tetap ter-update.
    resp = dashboard.api_set_settings({"bet_usd": 5.0})
    body = json.loads(resp.body)
    assert body["bet_usd"] == 5.0                   # yang diminta berubah
    assert body["leverage"] == 3                    # yang tak disebut TETAP (bukan reset ke 100)
    assert body["max_open_positions"] == 3
    assert body["news_veto"] is True


def test_mode_endpoint_is_the_only_way_to_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")
    save_settings(RuntimeSettings(mode="dry"))
    assert json.loads(dashboard.api_get_mode().body)["mode"] == "dry"

    resp = dashboard.api_set_mode({"mode": "live"})
    assert json.loads(resp.body) == {"ok": True, "mode": "live"}
    assert get_active_mode() == "live"


def test_mode_endpoint_rejects_invalid_value(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")
    resp = dashboard.api_set_mode({"mode": "banana"})
    assert resp.status_code == 400
    body = json.loads(resp.body)
    assert body["ok"] is False
