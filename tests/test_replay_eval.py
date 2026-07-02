"""Phase 5 replay-eval: run semua hipotesis di panel identik + ranking gabungan."""
import numpy as np

from bot import replay_eval as re
from bot.replay_eval import _combined_score, gemini_brier_row, rank, run_hypotheses


def _panel(T=600, N=8, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0, 1, (T, N)).cumsum(axis=0)
    vol = rng.uniform(1e5, 1e6, (T, N))
    return close, vol, 0                       # kolom 0 = BTC


def test_run_hypotheses_covers_all_and_identical_panel():
    close, vol, bi = _panel()
    recs = run_hypotheses(close, vol, bi, holds=(3,), train_len=120, test_len=40)
    ids = {r["id"] for r in recs}
    # 11 builder OHLCV/vol + 3 data-locked
    assert {"skew", "bab", "amihud", "downside_beta", "resid_momentum"} <= ids
    assert {"venue_basis", "oi_crowding", "funding_accel"} <= ids
    for r in recs:
        if r["source"] == "replay" and r.get("simulated_pnl") is not None:
            assert r["brier"] is None and r["brier_note"] == "no_confidence_output"
    locked = [r for r in recs if r["source"] == "data_locked"]
    assert all(r["verdict"] == "skipped_data_unavailable" for r in locked)


def test_rank_orders_rankable_and_parks_locked():
    close, vol, bi = _panel()
    recs = run_hypotheses(close, vol, bi, holds=(3,), train_len=120, test_len=40)
    ranked = rank(recs)
    ranks = [r["rank"] for r in ranked if r.get("rank")]
    assert ranks == sorted(ranks)                        # 1,2,3,... berurutan
    # combined_score menurun sepanjang rank
    scored = [r for r in ranked if r.get("rank")]
    scores = [r["combined_score"] for r in scored]
    assert scores == sorted(scores, reverse=True)
    # data-locked & error tak diberi rank numerik
    assert all(r["rank"] is None for r in ranked if r["source"] == "data_locked")


def test_combined_score_calibration_axis():
    # PnL sama → Brier lebih baik (rendah) menang
    good = {"sharpe": 1.0, "brier": 0.10}
    bad = {"sharpe": 1.0, "brier": 0.40}
    assert _combined_score(good) > _combined_score(bad)
    # tanpa Brier → hanya Sharpe (penalti 0)
    assert _combined_score({"sharpe": 1.0, "brier": None}) == 1.0
    # baris murni-Brier (gemini, tanpa sharpe): lebih baik dari koin → skor positif
    assert _combined_score({"sharpe": None, "brier": 0.20}) > 0
    assert _combined_score({"sharpe": None, "brier": 0.30}) < 0


def test_gemini_brier_row_from_store(monkeypatch):
    monkeypatch.setattr(re, "gemini_brier_row", gemini_brier_row)  # pakai asli
    import bot.store as store
    monkeypatch.setattr(store, "calibration_report",
                        lambda mode, last_n=500, days=3650: {
                            "last_500_trades": {"n": 12, "brier": 0.18, "hit_rate": 66.7}})
    row = gemini_brier_row("dry")
    assert row["id"] == "gemini_classifier" and row["brier"] == 0.18
    assert row["trade_count"] == 12 and row["source"] == "live:dry"


def test_gemini_brier_row_none_when_empty(monkeypatch):
    import bot.store as store
    monkeypatch.setattr(store, "calibration_report",
                        lambda mode, last_n=500, days=3650: {"last_500_trades": {"n": 0}})
    assert gemini_brier_row("dry") is None
