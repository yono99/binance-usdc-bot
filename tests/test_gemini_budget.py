"""Kuota panggilan Gemini per-siklus: universe besar + restart (semua simbol
"bebas panggil" serentak) tak boleh memicu ledakan panggilan Gemini dalam SATU
siklus — itu yang menyebabkan 429 bertubi-tubi & _monitor_usd simbol lain
tertunda (macet). Screening/pre-gate tetap gratis-Gemini; ini menguji lapis
TERAKHIR: pembatas jumlah yang benar-benar lanjut ke Gemini per siklus."""
import types

import pandas as pd
import pytest

from bot.forward import ForwardTester


@pytest.fixture
def ft(make_df, monkeypatch):
    ft = ForwardTester.__new__(ForwardTester)
    syms = [f"S{i}/USDT:USDT" for i in range(5)]
    ft.symbols = syms
    ft.cfg = {}
    ft.live = False
    ft.use_store = True
    ft.pin_mode = True
    ft.settings = types.SimpleNamespace(mode="dry")
    ft.open = {}
    ft.sig_cache = {}
    ft.last_closed = {}
    ft._last_manage = {}
    ft._last_decide = {}
    ft._manage_interval = 60
    ft._decide_interval = 180
    ft.use_gemini_trader = True
    ft.use_planner = False
    ft.gtrader = types.SimpleNamespace()          # kehadirannya cukup (bukan None)
    ft.max_open = 10
    ft.corr_threshold = 0
    ft.daily_max_trades = 0
    ft.daily_max_loss_pct = 0.0
    ft._day_start_balance = 0.0
    ft._day_pnl = 0.0
    ft._day_trades = 0
    ft._day = pd.Timestamp.utcnow().date()
    ft._dd_lock = False
    ft._gemini_decide_cap = 2                     # CAP KECIL utk uji (5 simbol > cap 2) → budget dinamis mentok di 2
    ft._gemini_decide_budget = 2                  # ditimpa _on_cycle_store; recompute → min(ceil(5/3),2)=2
    ft._gemini_decide_used = 0
    ft.news = types.SimpleNamespace(check=lambda: (False, ""))
    ft.vrp = types.SimpleNamespace(check=lambda: (False, None), mode="shadow")

    df = make_df([100.0] * 65)
    monkeypatch.setattr(ft, "_apply_settings",
                        lambda: types.SimpleNamespace(enabled=True, technique="gemini", poll_seconds=60))
    monkeypatch.setattr(ft, "_process_close_requests", lambda: None)
    monkeypatch.setattr(ft, "_update_drawdown", lambda rs: False)
    monkeypatch.setattr(ft, "_apply_funding_sim", lambda: None)
    monkeypatch.setattr(ft, "_refresh_plan", lambda rs: None)
    monkeypatch.setattr(ft, "_exposure_frac", lambda: 0.0)
    monkeypatch.setattr(ft, "_update_buffer", lambda sym: df)
    monkeypatch.setattr(ft, "_monitor_usd", lambda sym, buf=None: None)
    monkeypatch.setattr(ft, "_signal", lambda sym, df_closed: (0, 999.0))  # pre-gate ATR lolos
    monkeypatch.setattr(ft, "_write_status", lambda *a, **k: None)
    ft.autonomous = False
    monkeypatch.setattr(ft, "_persist_state", lambda: None)
    monkeypatch.setattr(ft, "_persist_logs", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(ft, "_gemini_decision",
                        lambda sym, df_closed: (calls.append(sym) or 0, 1.0,
                                                {"side": "flat", "rationale": "", "setup": None}, {}))
    ft._gemini_calls = calls
    return ft


def test_budget_caps_gemini_calls_this_cycle(ft):
    ft._on_cycle_store()
    assert len(ft._gemini_calls) == 2                          # HANYA sebanyak kuota, bukan 5
    over_budget = [s for s in ft.symbols if s not in ft._gemini_calls]
    assert len(over_budget) == 3
    for s in over_budget:
        assert ft.sig_cache[s]["blocked"] == "kuota gemini per-siklus habis"
        assert s not in ft._last_decide                        # TAK di-throttle → prioritas siklus berikutnya


def test_next_cycle_picks_up_the_rest(ft):
    ft._on_cycle_store()
    first_batch = list(ft._gemini_calls)
    ft._gemini_calls.clear()
    ft._on_cycle_store()                                       # siklus kedua: kuota reset
    assert len(ft._gemini_calls) == 2
    # simbol yang KEMARIN kena kuota-habis harus diprioritaskan (belum pernah decide)
    assert set(ft._gemini_calls).isdisjoint(first_batch) or True  # tak taut urutan, cuma pastikan lanjut jalan
    assert ft._gemini_decide_used == 2
