#!/usr/bin/env python3
"""Phase 5 — cached-replay harness: uji SEMUA hipotesis alpha di SATU panel identik.

Tujuan (dari rencana kalibrasi): "berhenti generate, mulai test" — scoreboard TERPADU
yang menjalankan tiap builder sinyal di dataset yang SAMA, lalu ranking GABUNGAN
(Brier + PnL/Sharpe), bukan PnL saja. Ganti "top-5 termurah" → "top-5 terkalibrasi".

Bukan menulis ulang walk-forward: `xsectional.walk_forward_scores` DIPAKAI ULANG apa
adanya (jujur, sama seperti CLI hipotesis individual). Yang baru = satu titik masuk
yang menjalankan semua builder di panel identik & menyatukan hasilnya jadi ranking.

Read-only: TIDAK menyentuh store trade per-mode maupun state live/paper. Panel di-cache
ke data/replay_eval/panel.pkl; hasil ditulis ke data/replay_eval/results.json.

Realita repo (jujur): builder cross-sectional TIDAK mengeluarkan confidence → Brier
"skipped" untuk mereka (sesuai instruksi: catat & lewati). Satu-satunya sumber
ber-confidence = jalur GeminiTrader (Phase 1-4) via calibration_log — dimasukkan
sebagai baris ber-Brier bila datanya ada, agar sumbu kalibrasi benar-benar terpakai.

  python -m bot.replay_eval                       # fetch+cache panel, ranking semua
  python -m bot.replay_eval --fresh               # abaikan cache panel
  python -m bot.replay_eval --mode dry            # sumber Brier gemini dari mode ini
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from . import xs_signals as xss
from .xsectional import sharpe, verdict, walk_forward_scores

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "replay_eval"
PANEL_CACHE = OUT_DIR / "panel.pkl"
RESULTS = OUT_DIR / "results.json"


# --- Registri hipotesis: builder 1-fungsi di panel identik (close[T×N], vol, btc_idx) ---
# needs: "cv" = butuh volume; "c" = hanya close+btc; "locked" = butuh data di luar OHLCV.
def _builders(close, vol, bi) -> dict:
    return {
        # id            : (needs, {varian: panel skor})   ← n_trials = varian × holds
        "resid_momentum": ("c", {"l5": xss.score_residual_momentum(close, bi, 5, 60),
                                  "l10": xss.score_residual_momentum(close, bi, 10, 60)}),
        "btc_leadlag":    ("c", {"bw60": xss.score_btc_leadlag(close, bi, 60)}),
        "ivol":           ("c", {"w20": xss.score_ivol(close, bi, 20, 60)}),
        "skew":           ("c", {"w30": xss.score_skew(close, 30),
                                 "w60": xss.score_skew(close, 60)}),
        "bab":            ("c", {"bw60": xss.score_bab(close, bi, 60)}),
        "st_reversal":    ("c", {"l3": xss.score_st_reversal(close, bi, 3, 60),
                                 "l5": xss.score_st_reversal(close, bi, 5, 60)}),
        "coskew":         ("c", {"w60": xss.score_coskew(close, bi, 60)}),
        "amihud":         ("cv", {"w30": xss.score_amihud(close, vol, 30)}),
        "turnover":       ("cv", {"w30": xss.score_turnover(close, vol, 30)}),
        "illiq_shock":    ("cv", {"sw5": xss.score_illiq_shock(close, vol, 5)}),
        "downside_beta":  ("c", {"w60": xss.score_downside_beta(close, bi, 60)}),
    }


# Butuh data di luar panel OHLCV (funding/OI/basis) → tak bisa di dataset identik ini.
DATA_LOCKED = {
    "venue_basis":  "butuh panel basis lintas-venue (Binance/Bybit) — lihat H27/registri",
    "oi_crowding":  "butuh panel OI historis (≥6 bln, oicollect.py) — lihat H19/H29",
    "funding_accel": "butuh panel funding ffilled — lihat H15",
}


def run_hypotheses(close: np.ndarray, vol: np.ndarray, btc_idx: int, *,
                   holds=(3, 7), train_len: int = 250, test_len: int = 60,
                   quantile: float = 0.3, cost_frac: float = 0.002) -> list[dict]:
    """Jalankan tiap hipotesis di panel IDENTIK. Kembalikan record per hipotesis.
    simulated_pnl = OOS mean/rebalance; trade_count = n rebalance OOS; brier = None
    (cross-sectional tak ber-confidence → dilewati, dicatat)."""
    records: list[dict] = []
    for hid, (needs, panels) in _builders(close, vol, btc_idx).items():
        n_trials = len(panels) * len(holds)
        try:
            _, oos = walk_forward_scores(close, panels, list(holds), quantile, cost_frac,
                                         train_len, test_len)
            v = verdict(oos, n_trials)
            records.append({
                "id": hid, "source": "replay", "needs": needs,
                "simulated_pnl": round(v["mean"], 6), "trade_count": v["n"],
                "sharpe": v.get("sharpe"), "p_adj": v.get("p_adj"),
                "brier": None, "brier_note": "no_confidence_output",
                "verdict": "candidate" if v["ok"] else ("insufficient" if v["n"] < 8 else "rejected"),
            })
        except Exception as e:  # boundary — satu hipotesis gagal tak menjatuhkan sisanya
            records.append({"id": hid, "source": "replay", "needs": needs,
                            "simulated_pnl": None, "trade_count": 0, "sharpe": None,
                            "brier": None, "verdict": "error", "error": str(e)[:120]})
    for hid, why in DATA_LOCKED.items():
        records.append({"id": hid, "source": "data_locked", "simulated_pnl": None,
                        "trade_count": 0, "sharpe": None, "brier": None,
                        "verdict": "skipped_data_unavailable", "note": why})
    return records


def gemini_brier_row(mode: str) -> dict | None:
    """Baris ber-Brier dari jalur GeminiTrader (Phase 1-4) — satu-satunya hipotesis
    ber-confidence. Read-only dari calibration_log. None bila belum ada sampel."""
    try:
        from .store import calibration_report
        rep = calibration_report(mode, last_n=500, days=3650)
        agg = rep.get("last_500_trades", {})
        if not agg.get("n"):
            return None
        return {"id": "gemini_classifier", "source": f"live:{mode}",
                "simulated_pnl": None, "trade_count": agg["n"],
                "sharpe": None, "brier": agg["brier"], "brier_note": "from_calibration_log",
                "hit_rate": agg.get("hit_rate"), "verdict": "calibration_tracked"}
    except Exception:  # boundary — sumber Brier opsional
        return None


def _combined_score(rec: dict) -> float:
    """Ranking GABUNGAN (bukan PnL saja): Sharpe (PnL risk-adjusted, skala-bebas)
    DIKURANGI penalti kalibrasi bila ada Brier (>0.25 = lebih buruk dari koin).
    Tanpa Brier → penalti 0 (sumbu kalibrasi hanya menggigit hipotesis ber-confidence)."""
    sh = rec.get("sharpe")
    base = sh if sh is not None else -9.0            # tak ada PnL → paling bawah
    b = rec.get("brier")
    penalty = 2.0 * max(0.0, b - 0.25) if b is not None else 0.0
    # baris murni-Brier (gemini, tanpa sharpe): skor = keunggulan kalibrasi vs koin
    if sh is None and b is not None:
        base = (0.25 - b) * 4.0
    return round(base - penalty, 4)


def rank(records: list[dict]) -> list[dict]:
    """Beri combined_score + rank. Runnable (punya sinyal/kalibrasi) di atas; yang
    error/data-locked di bawah, tak diberi peringkat numerik."""
    rankable = [r for r in records if r.get("sharpe") is not None or r.get("brier") is not None]
    other = [r for r in records if r not in rankable]
    for r in rankable:
        r["combined_score"] = _combined_score(r)
    rankable.sort(key=lambda r: r["combined_score"], reverse=True)
    for i, r in enumerate(rankable, 1):
        r["rank"] = i
    for r in other:
        r["rank"] = None
    return rankable + other


def evaluate(close, vol, btc_idx, *, mode: str = "dry", **kw) -> dict:
    """Pipeline penuh: jalankan hipotesis + baris Brier gemini → ranking."""
    records = run_hypotheses(close, vol, btc_idx, **kw)
    grow = gemini_brier_row(mode)
    if grow:
        records.append(grow)
    ranked = rank(records)
    return {"n_hypotheses": len(ranked), "ranked": ranked,
            "top5": [r["id"] for r in ranked if r.get("rank") and r["rank"] <= 5]}


# --------------------------- CLI (panel identik ter-cache) ---------------------------

def _load_panel(fresh: bool, bars: int, tf: str):
    import pandas as pd
    if PANEL_CACHE.exists() and not fresh:
        d = pd.read_pickle(PANEL_CACHE)
        return d["close"], d["vol"], int(d["btc_idx"]), d["cols"]
    from .backtest import fetch_history
    from .config import load_settings
    from .exchange import Exchange
    from .logger import log
    from .xsectional import align_close_panel, volume_panel
    from combine import DEFAULT_UNIVERSE               # universe riset baku (USDT histori panjang)
    ex = Exchange(load_settings())
    dfs = {}
    for sym in DEFAULT_UNIVERSE:
        try:
            dfs[sym] = fetch_history(ex, sym, tf, bars)
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")
    panel = align_close_panel(dfs)
    btc = [i for i, c in enumerate(panel.columns) if c.startswith("BTC")]
    if not btc or panel.shape[1] < 6:
        raise SystemExit("Butuh BTC + ≥6 simbol untuk panel replay.")
    cols = list(panel.columns)
    close = panel.to_numpy()
    vol = volume_panel(dfs, panel.index, cols)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.to_pickle({"close": close, "vol": vol, "btc_idx": btc[0], "cols": cols}, PANEL_CACHE)
    return close, vol, btc[0], cols


def main() -> None:
    p = argparse.ArgumentParser(description="Replay-eval semua hipotesis di panel identik")
    p.add_argument("--tf", default="1d")
    p.add_argument("--bars", type=int, default=2000)
    p.add_argument("--fresh", action="store_true", help="abaikan cache panel")
    p.add_argument("--mode", default="dry", choices=["dry", "test", "live"],
                   help="sumber Brier baris gemini_classifier")
    p.add_argument("--train", type=int, default=250)
    p.add_argument("--test", type=int, default=60)
    args = p.parse_args()

    close, vol, bi, cols = _load_panel(args.fresh, args.bars, args.tf)
    res = evaluate(close, vol, bi, mode=args.mode, train_len=args.train, test_len=args.test)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")

    try:
        from rich.console import Console
        from rich.table import Table
        t = Table(title=f"Replay-eval — {res['n_hypotheses']} hipotesis, panel {len(cols)}×{close.shape[0]} ({args.tf})")
        for c in ("rank", "id", "source", "sim_pnl", "n", "sharpe", "brier", "combined", "verdict"):
            t.add_column(c, justify="right")
        for r in res["ranked"]:
            t.add_row(str(r.get("rank") or "-"), r["id"], r.get("source", ""),
                      "-" if r.get("simulated_pnl") is None else f"{r['simulated_pnl']:+.4%}",
                      str(r.get("trade_count", 0)),
                      "-" if r.get("sharpe") is None else f"{r['sharpe']:+.2f}",
                      "-" if r.get("brier") is None else f"{r['brier']:.3f}",
                      "-" if r.get("combined_score") is None else f"{r['combined_score']:+.3f}",
                      r.get("verdict", ""))
        Console().print(t)
        Console().print(f"Top-5 terkalibrasi: [cyan]{res['top5']}[/cyan]  → {RESULTS}")
    except Exception:
        print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
