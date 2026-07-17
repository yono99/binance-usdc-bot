"""'blocked' UI tak boleh klaim '→ posisi dibuka' saat _open_usd sebenarnya GAGAL
diam-diam (margin habis, SL invalid, dll). Insiden nyata (deploy Proxmox):
status API menunjukkan blocked='→ posisi dibuka' TAPI in_position=false/position=null
untuk simbol yang sama di siklus yang sama — _open_usd gagal internal (sudah menulis
alasan akurat ke sig_cache) tapi caller MENIMPANYA tanpa syarat dgn label sukses."""
import types

import pandas as pd

from bot.forward import ForwardTester


def _base_ft(make_df):
    ft = ForwardTester.__new__(ForwardTester)
    ft.symbols = ["BTC/USDC:USDC"]
    ft.cfg = {}
    ft.live = False
    ft.use_store = True
    ft.pin_mode = True
    ft.settings = types.SimpleNamespace(mode="dry")
    ft.sig_cache = {}
    ft.last_closed = {}
    ft._last_manage = {}
    ft._last_decide = {}
    ft._manage_interval = 60
    ft._decide_interval = 180
    ft.use_gemini_trader = False          # jalur RULES — _open_usd tanpa gemini (gem=None)
    ft.use_planner = False
    ft.gtrader = None
    ft.max_open = 10
    ft.corr_threshold = 0
    ft.daily_max_trades = 0
    ft.daily_max_loss_pct = 0.0
    ft._day_start_balance = 0.0
    # Tahap 1: per-wallet saldo + PnL + DD peak.
    ft.balance_usdt = 0.0
    ft.balance_usdc = 0.0
    ft._day_start_balance_usdt = 0.0
    ft._day_start_balance_usdc = 0.0
    ft._day_pnl = 0.0
    ft._day_pnl_usdt = 0.0
    ft._day_pnl_usdc = 0.0
    ft._day_trades = 0
    ft._day = pd.Timestamp.utcnow().date()
    ft._dd_lock = False
    ft._peak_balance_usdt = 0.0
    ft._peak_balance_usdc = 0.0
    ft._gemini_decide_budget = 8
    ft._gemini_decide_cap = 8
    ft._gemini_decide_used = 0
    ft.news = types.SimpleNamespace(check=lambda: (False, ""))
    ft.vrp = types.SimpleNamespace(check=lambda: (False, None), mode="shadow")
    ft.autonomous = False
    ft.corr_threshold = 0

    df = make_df([100.0] * 65)
    ft._update_buffer = lambda sym: df
    ft._monitor_usd = lambda sym, buf=None: None
    ft._signal = lambda sym, df_closed: (1, 1.0)         # sinyal LONG rule-based (side=1)
    ft._apply_settings = lambda: types.SimpleNamespace(enabled=True, technique="rules")
    ft._process_close_requests = lambda: None
    ft._update_drawdown = lambda rs: False
    ft._apply_funding_sim = lambda: None
    ft._refresh_plan = lambda rs: None
    ft._exposure_frac = lambda: 0.0
    ft._write_status = lambda *a, **k: None
    ft._persist_state = lambda: None
    ft._persist_logs = lambda *a, **k: None
    ft._react_gate = lambda *a, **k: (True, "ENTER_LONG", "")   # gerbang ReAct: selalu izinkan
    ft._agent_portfolio_review = lambda rs: None
    ft.open = {}
    return ft


def test_failed_open_keeps_accurate_reason_not_fake_success(make_df):
    """_adaptive_bet mengembalikan 0 (margin habis) -> _open_usd gagal & menulis alasan
    akurat -> caller TIDAK BOLEH menimpanya dgn '→ posisi dibuka'."""
    ft = _base_ft(make_df)
    ft.balance_usdc = 10.0
    ft.balance_usdt = 0.0
    from bot.settings_store import RuntimeSettings
    rs_obj = RuntimeSettings(mode="dry", enabled=True)
    ft._apply_settings = lambda: rs_obj
    # Isi open dgn posisi lain yg mengunci HAMPIR SELURUH saldo -> avail < 0.10 -> bet=0
    ft.open = {"ETH/USDC:USDC": {"bet": 9.95}}

    ft._on_cycle_store()

    assert "BTC/USDC:USDC" not in ft.open                       # posisi memang TAK terbuka
    assert ft.sig_cache["BTC/USDC:USDC"]["blocked"].startswith("margin USDC habis")  # alasan AKURAT per-quote
    assert ft.sig_cache["BTC/USDC:USDC"]["blocked"] != "→ posisi dibuka"       # bukan klaim palsu


def test_successful_open_still_shows_success_label(make_df, monkeypatch):
    """Kontrol: saat _open_usd BENAR-BENAR berhasil, label sukses tetap tampil."""
    ft = _base_ft(make_df)
    ft.balance_usdc = 1000.0
    ft.balance_usdt = 0.0
    from bot.settings_store import RuntimeSettings
    rs_obj = RuntimeSettings(mode="dry", enabled=True)
    ft._apply_settings = lambda: rs_obj
    ft.ex = types.SimpleNamespace(ticker=lambda sym: {"last": 100.0})
    ft.bt = types.SimpleNamespace(sl_mult=1.5, tp_mult=2.5)
    ft.buffers = {"BTC/USDC:USDC": make_df([100.0] * 65)}
    ft.mtf = types.SimpleNamespace(stamp=lambda *a, **k: {})
    ft.vrp = types.SimpleNamespace(check=lambda: (False, None), mode="shadow", stamp=lambda: {})
    ft._sl_floor = lambda entry, is_long, sl, atr, last_range: sl
    ft.settings = types.SimpleNamespace(mode="dry")
    ft.slippage = 0.02
    ft.tf = "15m"
    ft.notify = types.SimpleNamespace(send=lambda msg: None)
    ft._session_trades = 0
    ft.open = {}

    ft._on_cycle_store()

    assert "BTC/USDC:USDC" in ft.open                            # posisi BENAR-BENAR terbuka
    assert ft.sig_cache["BTC/USDC:USDC"]["blocked"] == "→ posisi dibuka"
    assert ft.open["BTC/USDC:USDC"].get("opened_ts")             # utk marker panah entry di chart


def test_status_position_view_includes_opened_ts():
    """pos_view (dikirim ke /api/status, dipakai chart) harus meneruskan opened_ts."""
    ft = ForwardTester.__new__(ForwardTester)
    ft.symbols = ["BTC/USDC:USDC"]
    ft.sig_cache = {"BTC/USDC:USDC": {"price": 101.0}}
    ft.open = {"BTC/USDC:USDC": {"side": "long", "entry": 100.0, "sl": 95.0, "tp": 110.0,
                                 "liq": 90.0, "qty": 1.0, "bet": 10.0,
                                 "opened_ts": "2026-07-03T10:00:00+00:00"}}
    ft.live = False
    from bot.settings_store import RuntimeSettings
    rs_obj = RuntimeSettings(mode="dry")
    ft._circuit_breaker = lambda: None
    ft._dd_lock, ft._dd_reason = False, ""
    ft._dd_check = lambda peak, bal, pct: (False, 0.0)
    ft.balance_usdc = 100.0
    ft.balance_usdt = 0.0
    ft.corr_threshold = 0
    ft._day_pnl_usdt, ft._day_pnl_usdc, ft._day_trades = 0.0, 0.0, 0
    ft._gemini_decide_budget, ft._gemini_decide_cap = 8, 8
    ft._peak_balance_usdt, ft._peak_balance_usdc = 100.0, 0.0
    ft.settings = types.SimpleNamespace(mode="dry")
    ft.pin_mode = True
    ft.tf = "15m"
    ft.max_open = 10
    ft.pending = {}                      # state baru untuk limit resting
    from unittest.mock import patch
    with patch("bot.store.set_kv") as m:
        ft._write_status(rs_obj, False, "")
        status = m.call_args_list[0][0][1]   # tangkap payload yg ditulis ke kv
    pv = next(s["position"] for s in status["symbols"] if s["symbol"] == "BTC/USDC:USDC")
    assert pv["opened_ts"] == "2026-07-03T10:00:00+00:00"
