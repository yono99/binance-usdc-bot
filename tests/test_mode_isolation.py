"""Isolasi state/riwayat antar mode (dry/test/live) — mencegah kontaminasi
seperti insiden 2026-07-02 (saldo paper $47.89 terbawa ke live)."""
import json

from bot import decision_log, logger, store


def test_journal_writes_separate_files_per_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "LOG_DIR", tmp_path)
    logger.set_journal_mode("dry")
    logger.journal("forward_open", {"symbol": "BTC/USDC:USDC"})
    logger.set_journal_mode("live")
    logger.journal("forward_open", {"symbol": "ETH/USDC:USDC"})

    dry_rows = [json.loads(x) for x in (tmp_path / "trades_dry.jsonl").read_text().splitlines()]
    live_rows = [json.loads(x) for x in (tmp_path / "trades_live.jsonl").read_text().splitlines()]
    assert dry_rows[0]["symbol"] == "BTC/USDC:USDC" and dry_rows[0]["mode"] == "dry"
    assert live_rows[0]["symbol"] == "ETH/USDC:USDC" and live_rows[0]["mode"] == "live"
    logger.set_journal_mode(None)   # reset global agar tak bocor ke test lain


def test_decision_log_path_separated_per_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    decision_log.set_mode("dry")
    decision_log.append({"id": "a", "symbol": "X", "action": "ENTER", "outcome": None})
    decision_log.set_mode("live")
    decision_log.append({"id": "b", "symbol": "Y", "action": "ENTER", "outcome": None})

    assert decision_log.current_path().name == "decision_log_live.jsonl"
    decision_log.set_mode("dry")
    rows = decision_log.read_all()
    assert len(rows) == 1 and rows[0]["id"] == "a"     # tak melihat baris 'live'
    decision_log.set_mode("live")
    rows = decision_log.read_all()
    assert len(rows) == 1 and rows[0]["id"] == "b"     # tak melihat baris 'dry'


def test_botstate_key_separated_per_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")
    store.set_kv("botstate_dry", {"balance": 47.89})
    store.set_kv("botstate_live", {"balance": 0.01})
    assert store.get_kv("botstate_dry")["balance"] == 47.89
    assert store.get_kv("botstate_live")["balance"] == 0.01   # tak tertimpa saldo paper


def test_dashboard_filters_trades_by_active_mode():
    from bot.dashboard import build_trades
    events = [
        {"event": "forward_open", "symbol": "A", "mode": "dry", "side": "long", "ts": "t1"},
        {"event": "forward_close", "symbol": "A", "mode": "dry", "r": 1.0, "ts": "t2"},
        {"event": "forward_open", "symbol": "B", "mode": "live", "side": "short", "ts": "t3"},
        {"event": "forward_close", "symbol": "B", "mode": "live", "r": -1.0, "ts": "t4"},
    ]
    dry_only = build_trades(events, mode="dry")
    live_only = build_trades(events, mode="live")
    assert len(dry_only) == 1 and dry_only[0]["symbol"] == "A"
    assert len(live_only) == 1 and live_only[0]["symbol"] == "B"
    assert len(build_trades(events, mode=None)) == 2          # tanpa filter = semua (back-compat)


def test_switch_mode_moves_isolation_and_does_not_leak_state(cfg, tmp_path, monkeypatch):
    """Regresi: sebelum diperbaiki, _switch_mode() mengganti self.settings.mode
    TAPI meninggalkan _state_key/journal/decision_log di mode LAMA -> saldo &
    riwayat mode baru bisa bocor ke bucket mode lama saat _persist_state()."""
    from bot import forward as fwd
    from bot import store
    from bot.config import Settings
    from bot.forward import ForwardTester, default_params
    from bot.settings_store import load_settings

    class _StubEx:
        def __init__(self, settings):
            self.settings = settings

        def usdc_symbols(self):
            return ["BTC/USDC:USDC"]

    monkeypatch.setattr(fwd, "Exchange", _StubEx)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "bot.db")

    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    ft = ForwardTester(s, ["BTC/USDC:USDC"], default_params())
    # Meniru inisialisasi nyata (use_store=True di __post_init__ men-set ini dari
    # rs.balance_usdt/balance_usdc SEBELUM persist pertama) — tanpa ini, guard
    # cfg_balance di _restore_state() salah baca "konfigurasi berubah" krn dibanding 0.0 palsu.
    cfg_load = load_settings("dry")
    ft._last_cfg_balance_usdt = cfg_load.balance_usdt
    ft._last_cfg_balance_usdc = cfg_load.balance_usdc

    # Tahap 1: split per-wallet — set saldo ke wallet USDC (paper BTC/USDC:USDC).
    ft.balance_usdc = 47.89
    ft.balance_usdt = 0.0
    ft._day_start_balance_usdc = 47.89
    ft._persist_state()
    assert store.get_kv("botstate_dry")["balance_usdc"] == 47.89
    assert store.get_kv("botstate_dry")["balance_usdt"] == 0.0

    ft._switch_mode("test")                      # pindah ke mode paper LAIN
    assert ft.settings.mode == "test"
    assert ft._state_key == "botstate_test"       # <-- kunci state IKUT pindah
    # saldo Test mode default (config), bukan 47.89 warisan dry
    assert ft.balance_usdt + ft.balance_usdc != 47.89 or (
        ft.balance_usdt == 0.0 and ft.balance_usdc == 0.0)

    ft.balance_usdc = 999.0
    ft.balance_usdt = 0.0
    ft._persist_state()
    assert store.get_kv("botstate_test")["balance_usdc"] == 999.0
    assert store.get_kv("botstate_dry")["balance_usdc"] == 47.89   # bucket 'dry' tak tersentuh

    ft._switch_mode("dry")                        # kembali ke 'dry'
    assert ft._state_key == "botstate_dry"
    assert ft.balance_usdc == 47.89               # <-- saldo 'dry' PULIH utuh (USDC wallet)
    assert ft.balance_usdt == 0.0
