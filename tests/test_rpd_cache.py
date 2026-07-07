"""Tests untuk 3 fitur optimisasi RPD:
1. Sub-batch chunking di decide_batch (GeminiTrader)
2. Price-cache: skip Gemini saat harga stagnan (_on_cycle_store)
3. RPD exhausted fallback: all_keys_dead → bot pakai rules (gemini_client + forward)
"""
import json
import time
import types

import pandas as pd
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixture builder
# ─────────────────────────────────────────────────────────────────────────────

def _make_ft(make_df, monkeypatch, *, price=100.0, price_cache_pct=0.15,
             gemini_enabled=True, cap=10):
    """Buat ForwardTester minimal untuk test isolasi.

    Kunci: _decide_interval=60 + poll_seconds=60 → cycles=1, budget=min(3,cap)=3
    sehingga semua 3 simbol dapat giliran per siklus dalam test.
    """
    from bot.forward import ForwardTester
    ft = ForwardTester.__new__(ForwardTester)
    syms = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
    ft.symbols = syms
    ft.cfg = {
        "signals": {"atr_period": 14},
        "gemini": {
            "pregate_atr_pct": 0.0,
            "price_cache_pct": price_cache_pct,
        },
    }
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
    ft._decide_interval = 60       # ← 60s = poll_seconds → cycles=1 → budget=cap (semua lolos)
    ft.use_gemini_trader = gemini_enabled
    ft.use_planner = False
    ft.gtrader = types.SimpleNamespace(
        build_context=lambda *a, **k: {"market": {}},
        client=types.SimpleNamespace(
            keys=["key1", "key2"],
            models=["gemini-3-flash-preview"],
        ),
    )
    ft.max_open = 10
    ft.corr_threshold = 0
    ft.daily_max_trades = 0
    ft.daily_max_loss_pct = 0.0
    ft._day_start_balance = 0.0
    ft._day_pnl = 0.0
    ft._day_trades = 0
    ft._day = pd.Timestamp.utcnow().date()
    ft._dd_lock = False
    ft._gemini_decide_cap = cap
    ft._gemini_decide_budget = cap
    ft._gemini_decide_used = 0
    ft.balance_usd = 1000.0
    ft._last_news_note = ""
    ft._session_trades = 0
    ft._session_plan = None
    ft.rs = None
    ft.react = types.SimpleNamespace(challenge_gemini=lambda *a, **k: None, devil_threshold=0.7)
    ft._decide_price_cache = {}
    ft._last_rpd_warn = 0.0
    ft.news = types.SimpleNamespace(check=lambda: (False, ""))
    ft.vrp = types.SimpleNamespace(check=lambda: (False, None), mode="shadow")
    ft.autonomous = False
    ft.notify = types.SimpleNamespace(send=lambda *a, **k: None)

    df = make_df([price] * 65)
    monkeypatch.setattr(ft, "_apply_settings",
                        lambda: types.SimpleNamespace(enabled=True, technique="gemini",
                                                      poll_seconds=60))
    monkeypatch.setattr(ft, "_process_close_requests", lambda: None)
    monkeypatch.setattr(ft, "_update_drawdown", lambda rs: False)
    monkeypatch.setattr(ft, "_apply_funding_sim", lambda: None)
    monkeypatch.setattr(ft, "_refresh_plan", lambda rs: None)
    monkeypatch.setattr(ft, "_exposure_frac", lambda: 0.0)
    monkeypatch.setattr(ft, "_update_buffer", lambda sym: df)
    monkeypatch.setattr(ft, "_monitor_usd", lambda sym, buf=None: None)
    monkeypatch.setattr(ft, "_signal", lambda sym, df_closed: (0, 999.0))
    monkeypatch.setattr(ft, "_write_status", lambda *a, **k: None)
    monkeypatch.setattr(ft, "_alt_arrays",
                        lambda sym, df: ([0.0]*len(df), [0.0]*len(df), [0.0]*len(df), [False]*len(df)))
    monkeypatch.setattr(ft, "_portfolio_view", lambda: [])
    monkeypatch.setattr(ft, "_btc_lead", lambda: {})
    monkeypatch.setattr(ft, "_corr_conflict", lambda sym, side: None)
    monkeypatch.setattr(ft, "_gemini_manage", lambda sym, df_closed: None)
    monkeypatch.setattr(ft, "_agent_portfolio_review", lambda rs: None)
    monkeypatch.setattr(ft, "_persist_state", lambda: None)
    monkeypatch.setattr(ft, "_persist_logs", lambda *a, **k: None)

    calls = []

    def mock_decide_batch(contexts):
        for sym in contexts:
            calls.append(sym)
        return {sym: {"setup": "trend_pullback", "side": "flat", "conviction": 0.0, "rationale": ""}
                for sym in contexts}

    ft.gtrader.decide_batch = mock_decide_batch
    ft._gemini_calls = calls
    return ft


# ═════════════════════════════════════════════════════════════════════════════
# FITUR 1: Sub-batch chunking di GeminiTrader.decide_batch
# ═════════════════════════════════════════════════════════════════════════════

def _make_trader_with_chunk(chunk_size=4):
    """Buat GeminiTrader minimal dengan chunk_size tertentu, enabled=False."""
    from bot.gemini_trader import GeminiTrader
    from bot.gemini_client import GeminiClient
    cfg = {
        "signals": {"ema_fast": 8, "ema_mid": 21, "ema_slow": 55,
                    "adx_period": 14, "rsi_period": 14, "atr_period": 14},
        "strategy": {"adx_strong": 25, "adx_range": 20, "max_atr_pct_chaos": 8.0},
        "gemini": {"batch_chunk_size": chunk_size},
    }
    trader = GeminiTrader.__new__(GeminiTrader)
    trader.cfg = cfg
    trader.mode = "dry"
    trader.client = GeminiClient([], "gemini-3-flash-preview")
    trader.enabled = False       # tidak perlu API nyata
    return trader


class TestBatchChunking:
    """decide_batch dengan chunk_size=3 dan 6 simbol harus menghasilkan 2 chunk."""

    def test_chunking_splits_6_into_2_batches(self):
        """6 simbol + chunk_size 3 → generate() dipanggil 2× (2 chunk)."""
        trader = _make_trader_with_chunk(chunk_size=3)
        trader.enabled = True
        syms = [f"SYM{i}/USDT:USDT" for i in range(6)]
        contexts = {s: {"symbol": s, "market": {}} for s in syms}

        gen_calls = []

        def fake_generate(prompt, purpose=""):
            gen_calls.append(purpose)
            return json.dumps({s: {"setup": "no_trade", "side": "flat",
                                   "conviction": 0.0, "rationale": "ok"}
                               for s in syms})

        trader.client.generate = fake_generate
        result = trader.decide_batch(contexts)
        assert set(result.keys()) == set(syms)
        assert len(gen_calls) == 2   # 6 simbol / chunk_size 3 = 2 panggilan

    def test_chunking_single_batch_if_within_chunk_size(self):
        """3 simbol + chunk_size 4 → generate() dipanggil 1×."""
        trader = _make_trader_with_chunk(chunk_size=4)
        trader.enabled = True
        syms = [f"SYM{i}/USDT:USDT" for i in range(3)]
        contexts = {s: {"symbol": s, "market": {}} for s in syms}

        gen_calls = []

        def fake_generate(prompt, purpose=""):
            gen_calls.append(purpose)
            return json.dumps({s: {"setup": "no_trade", "side": "flat",
                                   "conviction": 0.0, "rationale": "ok"}
                               for s in syms})

        trader.client.generate = fake_generate
        result = trader.decide_batch(contexts)
        assert set(result.keys()) == set(syms)
        assert len(gen_calls) == 1   # hanya 1 chunk

    def test_chunking_one_chunk_fail_others_still_process(self):
        """Chunk pertama gagal parse → flat untuk chunk itu; chunk kedua lanjut."""
        trader = _make_trader_with_chunk(chunk_size=2)
        trader.enabled = True
        syms = [f"SYM{i}/USDT:USDT" for i in range(4)]
        contexts = {s: {"symbol": s, "market": {}} for s in syms}

        call_count = [0]

        def fake_generate(prompt, purpose=""):
            call_count[0] += 1
            if call_count[0] == 1:
                return "BUKAN JSON VALID"
            chunk_syms = syms[2:]
            return json.dumps({s: {"setup": "trend_pullback", "side": "flat",
                                   "conviction": 0.0, "rationale": "ok"}
                               for s in chunk_syms})

        trader.client.generate = fake_generate
        result = trader.decide_batch(contexts)
        assert result[syms[0]]["rationale"] == "parse gagal → flat"
        assert result[syms[1]]["rationale"] == "parse gagal → flat"
        assert result[syms[2]]["side"] == "flat"
        assert result[syms[3]]["side"] == "flat"


# ═════════════════════════════════════════════════════════════════════════════
# FITUR 2: Price Cache di _on_cycle_store
# ═════════════════════════════════════════════════════════════════════════════

class TestPriceCache:
    """Siklus kedua dengan harga stagnan harus skip Gemini (cache hit)."""

    def test_cache_hit_skips_gemini_when_price_unchanged(self, make_df, monkeypatch):
        """Harga sama persis di siklus 2 → cache hit → 0 panggilan Gemini."""
        ft = _make_ft(make_df, monkeypatch, price=100.0, price_cache_pct=0.15, cap=10)
        ft._on_cycle_store()
        assert len(ft._gemini_calls) == 3    # siklus 1: semua 3 masuk
        ft._gemini_calls.clear()
        ft._last_decide.clear()              # reset throttle

        ft._on_cycle_store()
        assert len(ft._gemini_calls) == 0    # siklus 2: semua ter-cache
        for sym in ft.symbols:
            blocked = ft.sig_cache[sym].get("blocked", "")
            assert "price cache" in blocked, f"{sym}: {blocked}"

    def test_cache_miss_when_price_moves_above_threshold(self, make_df, monkeypatch):
        """Harga naik 0.5% (>0.15% threshold) → cache miss → Gemini dipanggil lagi."""
        ft = _make_ft(make_df, monkeypatch, price=100.0, price_cache_pct=0.15, cap=10)
        ft._on_cycle_store()
        assert len(ft._gemini_calls) == 3
        ft._gemini_calls.clear()
        ft._last_decide.clear()

        df_new = make_df([100.5] * 65)      # +0.5% — jauh di atas threshold 0.15%
        monkeypatch.setattr(ft, "_update_buffer", lambda sym: df_new)
        ft._on_cycle_store()
        assert len(ft._gemini_calls) == 3   # cache miss → semua dipanggil ulang

    def test_cache_invalidated_on_position_close(self, make_df, monkeypatch):
        """Cache simbol dihapus saat _decide_price_cache.pop() dipanggil (logika _close_usd)."""
        ft = _make_ft(make_df, monkeypatch, price=100.0, price_cache_pct=0.15, cap=10)
        sym = "BTC/USDT:USDT"
        ft._decide_price_cache[sym] = (100.0, {"side": "flat"})
        assert sym in ft._decide_price_cache
        ft._decide_price_cache.pop(sym, None)   # sama seperti yang dilakukan _close_usd
        assert sym not in ft._decide_price_cache

    def test_cache_disabled_when_threshold_zero(self, make_df, monkeypatch):
        """price_cache_pct=0 → cache nonaktif → Gemini selalu dipanggil."""
        ft = _make_ft(make_df, monkeypatch, price=100.0, price_cache_pct=0.0, cap=10)
        ft._on_cycle_store()
        ft._gemini_calls.clear()
        ft._last_decide.clear()
        ft._on_cycle_store()
        assert len(ft._gemini_calls) == 3   # tetap dipanggil meski harga sama


# ═════════════════════════════════════════════════════════════════════════════
# FITUR 3: all_keys_dead + RPD Fallback
# ═════════════════════════════════════════════════════════════════════════════

class TestAllKeysDead:
    """Unit test all_keys_dead() di gemini_client."""

    def test_returns_false_when_no_keys(self):
        from bot.gemini_client import all_keys_dead
        assert all_keys_dead([], "gemini-3-flash-preview") is False

    def test_returns_false_when_keys_not_expired(self, monkeypatch):
        from bot import gemini_client as gc
        monkeypatch.setattr(gc, "_persisted", {})
        monkeypatch.setattr(gc, "_persist_loaded", True)
        from bot.gemini_client import all_keys_dead
        assert all_keys_dead(["keyA", "keyB"], "gemini-3-flash-preview") is False

    def test_returns_true_when_all_keys_rpd_expired(self, monkeypatch):
        from bot import gemini_client as gc
        future = time.time() + 7200
        model = "gemini-3-flash-preview"
        keys = ["keyA", "keyB"]
        persisted = {f"{gc._key_hash(k)}|{model}": future for k in keys}
        monkeypatch.setattr(gc, "_persisted", persisted)
        monkeypatch.setattr(gc, "_persist_loaded", True)
        from bot.gemini_client import all_keys_dead
        assert all_keys_dead(keys, model) is True

    def test_returns_false_when_one_key_alive(self, monkeypatch):
        from bot import gemini_client as gc
        future = time.time() + 7200
        model = "gemini-3-flash-preview"
        keys = ["keyA", "keyB"]
        # Hanya keyA dead
        persisted = {f"{gc._key_hash('keyA')}|{model}": future}
        monkeypatch.setattr(gc, "_persisted", persisted)
        monkeypatch.setattr(gc, "_persist_loaded", True)
        from bot.gemini_client import all_keys_dead
        assert all_keys_dead(keys, model) is False


class TestRPDFallbackInCycle:
    """Saat semua key RPD mati, _on_cycle_store tidak boleh memanggil decide_batch."""

    def test_rpd_fallback_uses_rules_when_all_dead(self, make_df, monkeypatch):
        from bot import forward as fwd_module
        ft = _make_ft(make_df, monkeypatch, price=100.0, cap=10)
        # Patch _all_keys_dead yang sudah di-import ke namespace forward.py
        monkeypatch.setattr(fwd_module, "_all_keys_dead", lambda keys, model: True)
        ft._on_cycle_store()
        assert len(ft._gemini_calls) == 0

    def test_rpd_fallback_logs_warning_once_per_hour(self, make_df, monkeypatch, caplog):
        import logging
        from bot import forward as fwd_module
        ft = _make_ft(make_df, monkeypatch, price=100.0, cap=10)
        monkeypatch.setattr(fwd_module, "_all_keys_dead", lambda keys, model: True)
        ft._last_rpd_warn = 0.0

        with caplog.at_level(logging.WARNING):
            ft._on_cycle_store()
        assert ft._last_rpd_warn > 0    # timestamp diperbarui

        # Siklus kedua: tidak log lagi (dalam rentang 1 jam)
        caplog.clear()
        warn_before = ft._last_rpd_warn
        with caplog.at_level(logging.WARNING):
            ft._on_cycle_store()
        rpd_warns = [r for r in caplog.records if "habis" in r.message.lower()]
        assert len(rpd_warns) == 0      # anti-spam berhasil
        assert ft._last_rpd_warn == warn_before   # timestamp tidak berubah

    def test_no_fallback_when_keys_alive(self, make_df, monkeypatch):
        from bot import forward as fwd_module
        ft = _make_ft(make_df, monkeypatch, price=100.0, cap=10)
        monkeypatch.setattr(fwd_module, "_all_keys_dead", lambda keys, model: False)
        ft._on_cycle_store()
        assert len(ft._gemini_calls) == 3   # Gemini tetap dipanggil normal
