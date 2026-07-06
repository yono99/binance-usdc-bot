"""Fondasi Gemini Trader: skema SQLite, evidence-gate (anti-takhayul), decide fail-safe."""
import json

import numpy as np
import pandas as pd
import pytest

from bot import store
from bot.config import Settings
from bot.gemini_trader import GeminiTrader


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.db")
    store.init_db()
    return store


@pytest.fixture
def trader(cfg):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return GeminiTrader(s, cfg)


def _df(n=120, seed=0):
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0, 1, n).cumsum()
    close = pd.Series(close, index=idx)
    return pd.DataFrame({"open": close, "high": close * 1.002, "low": close * 0.998,
                         "close": close, "volume": rng.uniform(1, 5, n)}, index=idx)


# ---------- skema & keputusan ----------

def test_decision_roundtrip_and_settle(db):
    did = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.7, "kuat", {"x": 1})
    assert did > 0
    assert db.recent_decisions("BTC/USDC:USDC")[0]["status"] == "open"
    db.settle_decision(did, 1.5)
    assert db.recent_decisions("BTC/USDC:USDC")[0]["outcome_r"] == 1.5


def test_setup_stats(db):
    for r in (1.0, -1.0, 2.0, -1.0):
        i = db.record_decision("ETH/USDC:USDC", "range_fade", "short", 0.5, "", {})
        db.settle_decision(i, r)
    st = db.setup_stats("range_fade")
    assert st["n"] == 4
    assert st["exp_r"] == pytest.approx(0.25)
    assert st["win_rate"] == pytest.approx(50.0)


# ---------- EVIDENCE-GATE: anti-takhayul (inti) ----------

def test_lesson_inactive_until_enough_evidence(db):
    lid = db.add_lesson("*", "trend_pullback", "pullback di tren kuat layak diikuti")
    # belum ada trade settled → promote tak boleh mengaktifkan
    db.promote_lessons(min_n=5)
    assert db.active_lessons() == []
    # 4 trade (< min_n) → tetap tak aktif
    for _ in range(4):
        i = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.6, "", {})
        db.settle_decision(i, 0.5)
    db.promote_lessons(min_n=5)
    assert db.active_lessons() == []
    # trade ke-5 (≥ min_n) → baru boleh aktif
    i = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.6, "", {})
    db.settle_decision(i, 0.5)
    db.promote_lessons(min_n=5)
    act = db.active_lessons()
    assert len(act) == 1 and act[0]["id"] == lid
    assert act[0]["n_support"] == 5


def test_lesson_without_setup_evidence_stays_low(db):
    db.add_lesson("*", "exhaustion_reversal", "fade kapitulasi")  # setup tanpa data
    db.promote_lessons(min_n=10)
    assert db.active_lessons() == []                              # tak ada bukti → tak aktif


# ---------- decide: fail-safe FLAT ----------

def test_decide_flat_when_disabled(trader):
    d = trader.decide({"symbol": "BTC/USDC:USDC"})
    assert d["side"] == "flat" and d["setup"] == "no_trade"


def test_sanitize_rejects_invalid(trader):
    assert trader._sanitize({"setup": "bogus", "side": "long"})["side"] == "flat"
    assert trader._sanitize({"setup": "trend_pullback", "side": "up"})["side"] == "flat"


def test_sanitize_clamps_conviction(trader):
    out = trader._sanitize({"setup": "breakout_continuation", "side": "long",
                            "conviction": 9, "sl": 95.0, "tp": 110.0})
    assert out["conviction"] == 1.0 and out["side"] == "long"


def test_sanitize_requires_sl(trader):
    # Gemini trader penuh: side actionable tanpa SL valid → FLAT (fail-safe).
    out = trader._sanitize({"setup": "trend_pullback", "side": "long", "conviction": 0.7})
    assert out["side"] == "flat" and out["setup"] == "no_trade"
    bad = trader._sanitize({"setup": "trend_pullback", "side": "long",
                            "conviction": 0.7, "sl": "x"})
    assert bad["side"] == "flat"


def test_sanitize_passes_sl_tp(trader):
    out = trader._sanitize({"setup": "trend_pullback", "side": "long",
                            "conviction": 0.6, "sl": 98.5, "tp": 105.0})
    assert out["sl"] == 98.5 and out["tp"] == 105.0
    # TP opsional → None bila tak ada/invalid, SL tetap wajib
    no_tp = trader._sanitize({"setup": "trend_pullback", "side": "long",
                              "conviction": 0.6, "sl": 98.5})
    assert no_tp["sl"] == 98.5 and no_tp["tp"] is None


def test_commit_skips_flat(db, trader):
    assert trader.commit("BTC/USDC:USDC", {**{"side": "flat", "setup": "no_trade",
                         "conviction": 0.0, "rationale": ""}}, {}) is None


def test_market_summary_shape(db, trader):
    s = trader.build_context("BTC/USDC:USDC", _df())["market"]
    assert {"price", "adx", "rsi", "atr_pct", "regime", "ema_align"} <= set(s)
    assert s["regime"] in ("trend", "range", "mixed", "chaos")


def test_build_context_includes_portfolio(db, trader):
    pf = {"positions": [{"symbol": "ETH/USDC:USDC", "side": "long"}], "count": 1, "exposure_usd": 10}
    ctx = trader.build_context("BTC/USDC:USDC", _df(), portfolio=pf)
    assert ctx["portfolio"]["count"] == 1


def test_sl_feedback_adapts_after_stopout(db, trader):
    """Adaptasi pasca SL: streak rugi + MFE-sebelum-SL membedakan 'SL mepet' vs 'arah salah'."""
    assert trader._sl_feedback("BTC/USDC:USDC") is None          # belum ada riwayat → tak ada

    # 2 entry beruntun kena SL; MFE besar (sempat searah) = SL terlalu mepet
    for _ in range(2):
        i = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.7, "", {})
        db.settle_decision(i, -1.0, mae_pct=0.4, mfe_pct=1.8, exit_reason="sl")
    fb = trader._sl_feedback("BTC/USDC:USDC")
    assert fb["loss_streak"] == 2 and fb["recent_sl_or_liq"] == 2
    assert fb["avg_mfe_before_sl_pct"] == 1.8                     # besar → sinyal 'SL mepet'
    assert fb["last_reasons"][0] == "sl"

    # trade menang menyusul → streak putus (tak ada lagi yang perlu diadaptasi buta)
    w = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.7, "", {})
    db.settle_decision(w, 2.0, exit_reason="tp")
    assert trader._sl_feedback("BTC/USDC:USDC")["loss_streak"] == 0


def test_sl_feedback_isolated_per_symbol(db, trader):
    i = db.record_decision("ETH/USDC:USDC", "range_fade", "short", 0.6, "", {})
    db.settle_decision(i, -1.0, exit_reason="liq")
    assert trader._sl_feedback("ETH/USDC:USDC")["recent_sl_or_liq"] == 1
    assert trader._sl_feedback("BTC/USDC:USDC") is None           # simbol lain tak terpengaruh


def test_build_context_grounds_gemini_on_sqlite(db, trader):
    """Grounding tambahan: rekam jejak per-setup (dihitung kode) + kalibrasi Brier,
    supaya Gemini PAHAM performa nyatanya — bukan sekadar konteks pasar sesaat."""
    for r in (1.0, -1.0, 2.0):                       # 3 trade settled setup range_fade
        i = db.record_decision("BTC/USDC:USDC", "range_fade", "short", 0.6, "", {})
        db.settle_decision(i, r)
    db.log_calibration(i, "BTC/USDC:USDC", 0.6, 1, "dry")   # satu titik kalibrasi mode dry
    ctx = trader.build_context("BTC/USDC:USDC", _df())
    tr = {row["setup"]: row for row in ctx["setup_track_record"]}
    assert "range_fade" in tr and tr["range_fade"]["n"] == 3      # stats dari SQLite
    assert ctx["calibration"]["n"] == 1 and ctx["calibration"]["brier"] is not None


def test_split_batch_hoists_global_context(db, trader):
    """Grounding global (track record, kalibrasi, btc_lead, portfolio) dikirim SEKALI;
    market/sl_feedback tetap per-simbol → itu inti penghematan token batch."""
    ctxs = {}
    for s in ("BTC/USDC:USDC", "ETH/USDC:USDC"):
        ctxs[s] = trader.build_context(s, _df(), btc_lead={"dir": 1})
    shared, per = trader._split_batch(ctxs)
    assert set(shared) == set(trader._SHARED_KEYS)          # semua field global terangkat
    assert len(per) == 2
    for p in per:                                           # per-simbol TAK memuat field global
        assert "market" in p and "symbol" in p
        assert "setup_track_record" not in p and "btc_lead" not in p


def test_decide_batch_keyed_by_symbol_and_fail_safe(db, trader, monkeypatch):
    """Satu balasan → dict {symbol: decision}; simbol yang hilang / side aneh → FLAT."""
    reply = {
        "BTC/USDC:USDC": {"setup": "trend_pullback", "side": "long", "conviction": 0.7,
                          "sl": 95.0, "tp": 110.0, "rationale": "kuat"},
        "ETH/USDC:USDC": {"setup": "range_fade", "side": "flat", "conviction": 0.0,
                          "rationale": "ragu"},
        # SOL sengaja TIDAK dibalas → harus jadi FLAT (fail-safe)
    }
    trader.enabled = True
    monkeypatch.setattr(trader.client, "generate", lambda *a, **k: json.dumps(reply))
    ctxs = {s: trader.build_context(s, _df())
            for s in ("BTC/USDC:USDC", "ETH/USDC:USDC", "SOL/USDC:USDC")}
    out = trader.decide_batch(ctxs)
    assert out["BTC/USDC:USDC"]["side"] == "long" and out["BTC/USDC:USDC"]["sl"] == 95.0
    assert out["ETH/USDC:USDC"]["side"] == "flat"
    assert out["SOL/USDC:USDC"]["side"] == "flat"           # hilang dari balasan → FLAT
    # parse gagal → SEMUA flat
    monkeypatch.setattr(trader.client, "generate", lambda *a, **k: "bukan json")
    allflat = trader.decide_batch(ctxs)
    assert all(d["side"] == "flat" for d in allflat.values())


def test_track_record_evidence_gate_small_sample_is_noise(db, trader):
    """Anti-beku: exp_r negatif pada sampel KECIL = 'insufficient' (noise), bukan vonis
    'hindari'. Baru 'adequate' setelah eff_n cukup (>=30)."""
    # 5 trade sedikit-negatif → sampel kecil, tak boleh jadi alasan hindari total
    for _ in range(5):
        i = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.6, "", {})
        db.settle_decision(i, -0.1)
    tr = {r["setup"]: r for r in trader._track_record()}
    assert tr["trend_pullback"]["exp_r"] < 0
    assert tr["trend_pullback"]["evidence"] == "insufficient"     # noise → NETRAL, kumpulkan data

    # 40 trade i.i.d. → eff_n cukup → vonis 'adequate' baru sah
    for k in range(40):
        i = db.record_decision("ETH/USDC:USDC", "range_fade", "short", 0.5, "", {})
        db.settle_decision(i, 1.0 if k % 2 else -1.2)             # exp_r negatif, tak berkorelasi
    tr = {r["setup"]: r for r in trader._track_record()}
    assert tr["range_fade"]["exp_r"] < 0 and tr["range_fade"]["evidence"] == "adequate"


# ---------- kelola posisi: GUARDRAIL exit-only / tighten tak boleh longgar ----------

def test_valid_tighten_never_loosens():
    from bot.gemini_trader import valid_tighten
    # LONG: stop hanya boleh NAIK (mendekat harga) & di bawah harga
    assert valid_tighten("long", old_sl=100.0, new_sl=102.0, price=105.0) is True
    assert valid_tighten("long", old_sl=100.0, new_sl=98.0, price=105.0) is False   # turun = longgar
    assert valid_tighten("long", old_sl=100.0, new_sl=106.0, price=105.0) is False  # di atas harga
    # SHORT: stop hanya boleh TURUN (mendekat harga) & di atas harga
    assert valid_tighten("short", old_sl=100.0, new_sl=98.0, price=95.0) is True
    assert valid_tighten("short", old_sl=100.0, new_sl=102.0, price=95.0) is False  # naik = longgar
    assert valid_tighten("short", old_sl=100.0, new_sl=94.0, price=95.0) is False   # di bawah harga
    assert valid_tighten("long", old_sl=100.0, new_sl=None, price=105.0) is False   # invalid


def test_sanitize_manage_fail_safe(trader):
    assert trader._sanitize_manage({"action": "buy_more"})["action"] == "hold"      # aksi terlarang
    assert trader._sanitize_manage({"action": "tighten_stop"})["action"] == "hold"  # tanpa new_sl
    assert trader._sanitize_manage({"action": "exit", "reason": "x"})["action"] == "exit"
    t = trader._sanitize_manage({"action": "tighten_stop", "new_sl": 101.5})
    assert t["action"] == "tighten_stop" and t["new_sl"] == 101.5


def test_manage_flat_when_disabled(trader):
    assert trader.manage({"position": {}})["action"] == "hold"


# ---------- kurikulum ----------

def test_track_record_verdict(db):
    from bot.gemini_trader import track_record
    assert track_record()["verdict"] == "INSUFFICIENT"     # belum ada trade
    # rekam jejak rugi → REJECTED
    for _ in range(30):
        i = db.record_decision("BTC/USDC:USDC", "range_fade", "short", 0.5, "", {})
        db.settle_decision(i, -0.2)
    tr = track_record()
    assert tr["n"] == 30 and tr["verdict"] == "REJECTED" and tr["exp_r"] < 0
    assert any(s["setup"] == "range_fade" for s in tr["per_setup"])


def test_trade_lifecycle_commit_settle_reflect(db, trader):
    """Urutan persis yang dipakai forward.py: commit saat open → settle saat close."""
    dec = {"setup": "trend_pullback", "side": "long", "conviction": 0.8, "rationale": "x"}
    ids = []
    for _ in range(22):
        did = trader.commit("BTC/USDC:USDC", dec, {"market": {"regime": "trend"}})
        assert did is not None
        ids.append(did)
        trader.settle(did, 0.4)
    trader.propose_lesson("*", "trend_pullback", "pullback di tren bekerja")
    out = trader.reflect(min_settled=10, min_n_promote=20)
    assert out["active_lessons"] == 1          # 22 ≥ 20 → pelajaran lolos bukti
    # pelajaran teruji muncul di konteks keputusan berikutnya
    ctx = trader.build_context("BTC/USDC:USDC", _df())
    assert ctx["tested_lessons"] and ctx["tested_lessons"][0]["setup"] == "trend_pullback"


def test_curriculum_has_core_modules():
    from bot.trader_curriculum import KNOWLEDGE, curriculum_prompt
    for m in ("decision_process", "risk", "psychology", "market_structure",
              "chart_patterns", "candlesticks", "indicators", "meta"):
        assert m in KNOWLEDGE
    full = curriculum_prompt()
    assert "EXPECTANCY" in full and "FLAT" in full and "JSON" in full
    for k in ("trend_pullback", "no_trade"):
        assert k in full                              # SETUPS ikut terangkai


def test_reflect_offline_grounds_on_stats_and_gates(db, trader):
    # 25 trade settled untuk satu setup → cukup bukti
    for k in range(25):
        i = db.record_decision("BTC/USDC:USDC", "trend_pullback", "long", 0.6, "", {})
        db.settle_decision(i, 0.3 if k % 2 else -0.1)
    db.add_lesson("*", "trend_pullback", "pullback searah tren layak")
    out = trader.reflect(min_settled=10, min_n_promote=20)
    assert out["settled"] == 25
    assert "trend_pullback" in out["setups"]
    assert out["active_lessons"] == 1            # lolos evidence-gate (n=25 ≥ 20)
    # reflection tersimpan
    import bot.store as s
    with s._conn() as c:
        assert c.execute("SELECT COUNT(*) n FROM gemini_reflections").fetchone()["n"] == 1


def test_reflect_insufficient_data_no_active_lessons(db, trader):
    for _ in range(3):
        i = db.record_decision("ETH/USDC:USDC", "range_fade", "short", 0.4, "", {})
        db.settle_decision(i, 0.1)
    db.add_lesson("*", "range_fade", "fade tepi range")
    out = trader.reflect(min_settled=10, min_n_promote=20)
    assert out["active_lessons"] == 0            # bukti kurang → tak ada pelajaran aktif


def test_curriculum_module_selection_is_subset():
    from bot.trader_curriculum import curriculum_prompt
    only_risk = curriculum_prompt(modules=["risk"])
    assert "average down" in only_risk                # modul risk masuk
    assert "POLA CANDLE" not in only_risk             # modul lain tak ikut


# ─── Fitur: Sub-batch chunking di decide_batch ───────────────────────────────

def test_decide_batch_chunking_splits_correctly(trader, cfg):
    """batch_chunk_size=2 dengan 4 simbol → generate() dipanggil 2× (2 chunk).
    Spy pada generate() karena itu jalur yang benar-benar berbeda per-chunk."""
    trader.cfg = {**cfg, "gemini": {"batch_chunk_size": 2}}
    trader.enabled = True
    syms = [f"S{i}/USDT:USDT" for i in range(4)]
    contexts = {s: {"symbol": s, "market": {}} for s in syms}

    gen_calls = []

    def fake_generate(prompt, purpose=""):
        gen_calls.append(purpose)
        # Balas flat untuk semua simbol dalam chunk (ambil dari prompt tidak diperlukan—langsung flat)
        chunk_flat = {s: {"setup": "no_trade", "side": "flat",
                          "conviction": 0.0, "rationale": "test"} for s in syms}
        return json.dumps(chunk_flat)

    trader.client.generate = fake_generate
    result = trader.decide_batch(contexts)

    # 4 simbol / chunk 2 → 2 panggilan generate()
    assert len(gen_calls) == 2
    assert all(p == "trader_batch" for p in gen_calls)
    # Semua simbol ada di output
    assert set(result.keys()) == set(syms)


def test_decide_batch_chunking_default_is_4(trader, cfg):
    """batch_chunk_size default=4: 3 simbol → 1 panggilan generate()."""
    trader.cfg = {**cfg, "gemini": {}}   # tanpa override → default 4
    trader.enabled = True
    syms = [f"S{i}/USDT:USDT" for i in range(3)]
    contexts = {s: {"symbol": s, "market": {}} for s in syms}

    gen_calls = []

    def fake_generate(prompt, purpose=""):
        gen_calls.append(purpose)
        chunk_flat = {s: {"setup": "no_trade", "side": "flat",
                          "conviction": 0.0, "rationale": "test"} for s in syms}
        return json.dumps(chunk_flat)

    trader.client.generate = fake_generate
    trader.decide_batch(contexts)
    assert len(gen_calls) == 1    # 3 simbol ≤ 4 → satu chunk
