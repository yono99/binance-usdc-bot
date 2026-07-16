"""Kalibrasi ambang Entry Confluence Gate dari DATA historis.

Ikuti preseden slcalib.py: cari ambang touch_count_min/strong,
proximity_atr_mult, trend_floor, momentum_floor yang MEMISAHKAN
winner vs loser secara signifikan.

Metode:
1. Tarik SEMUA settled trade dari gemini_decisions.
2. Untuk tiap trade, hitung:
   - touch_count level terdekat searah entry (resistance utk short, support utk long)
   - jarak ke level itu dalam ATR
   - trend_score & momentum_score dari context saat entry (jika tersimpan)
   - btc_lead_score dari context saat entry
3. Cari ambang optimal: yang memaksimalkan pemisahan exp_R(above) vs exp_R(below).

Usage:
    python -m bot.ec_calibrate [--mode dry|test|live]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_settled_trades(mode: str | None = None) -> list[dict]:
    """Load settled trades from gemini_decisions."""
    from bot import store
    decs = store.settled_decisions(mode=mode)
    out = []
    for d in decs:
        if d.get("outcome_r") is None:
            continue
        ctx_raw = None
        try:
            ctx = json.loads(d.get("context") or "{}")
            ctx_raw = ctx.get("market", {})
        except Exception:
            ctx_raw = {}
        out.append({
            "symbol": d["symbol"],
            "side": d["side"],
            "setup": d["setup"],
            "outcome_r": float(d["outcome_r"]),
            "win": float(d["outcome_r"]) > 0,
            "context": ctx_raw,
        })
    return out


def analyze_touch_counts(trades: list[dict]) -> dict:
    """Analyze touch-count distribution for winners vs losers."""
    from bot import levels as lvl_mod

    levels_found = []
    for t in trades:
        sym = t["symbol"]
        side = t["side"]
        ctx = t.get("context", {})
        entry_price = float(ctx.get("price", 0)) or 0.001
        try:
            lvl = lvl_mod.find_nearest_level(
                sym, entry_price, "support" if side == "long" else "resistance",
                max_distance_atr_mult=10.0)
            if lvl:
                levels_found.append({
                    "symbol": sym,
                    "side": side,
                    "setup": t["setup"],
                    "outcome_r": t["outcome_r"],
                    "win": t["win"],
                    "touch_count": lvl.raw_touches,
                    "strength": lvl.strength,
                    "dist_atr": lvl.dist_atr,
                })
        except Exception:
            continue

    if not levels_found:
        return {"error": "No levels found for any trade"}

    df = pd.DataFrame(levels_found)
    out = {"total": len(df), "with_level": len(df)}

    # Try multiple touch_count thresholds
    thresholds = [5, 8, 10, 12, 15, 18, 20, 25, 30]
    tc_results = []
    for thr in thresholds:
        above = df[df["touch_count"] >= thr]
        below = df[df["touch_count"] < thr]
        tc_results.append({
            "threshold": thr,
            "above_n": len(above),
            "above_exp_r": round(float(above["outcome_r"].mean()), 4) if len(above) > 0 else None,
            "above_win_rate": round(float((above["win"].sum() / len(above)) * 100), 1) if len(above) > 0 else None,
            "below_n": len(below),
            "below_exp_r": round(float(below["outcome_r"].mean()), 4) if len(below) > 0 else None,
            "below_win_rate": round(float((below["win"].sum() / len(below)) * 100), 1) if len(below) > 0 else None,
        })
    out["touch_count_analysis"] = tc_results

    # Try multiple proximity_atr_mult thresholds
    prox_thresholds = [0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0]
    prox_results = []
    for thr in prox_thresholds:
        near = df[df["dist_atr"] <= thr]
        far = df[df["dist_atr"] > thr]
        prox_results.append({
            "threshold_atr": thr,
            "near_n": len(near),
            "near_exp_r": round(float(near["outcome_r"].mean()), 4) if len(near) > 0 else None,
            "near_win_rate": round(float((near["win"].sum() / len(near)) * 100), 1) if len(near) > 0 else None,
            "far_n": len(far),
            "far_exp_r": round(float(far["outcome_r"].mean()), 4) if len(far) > 0 else None,
            "far_win_rate": round(float((far["win"].sum() / len(far)) * 100), 1) if len(far) > 0 else None,
        })
    out["proximity_atr_analysis"] = prox_results

    return out


def analyze_structure_scores(trades: list[dict]) -> dict:
    """Analyze trend/momentum scores vs outcome."""
    records = []
    for t in trades:
        ctx = t.get("context", {})
        ema_align = ctx.get("ema_align", 0)
        rsi_val = ctx.get("rsi", 50)
        adx_val = ctx.get("adx", 25)

        trend_score = float(ema_align) * min(abs(ema_align or 0) * 0.3, 1.0)
        mom_score = ((float(rsi_val) - 50) / 25.0) * 0.3
        side_mult = 1 if t["side"] == "long" else -1
        trend_aligned = trend_score * side_mult
        mom_aligned = mom_score * side_mult

        records.append({
            "trend_score": trend_aligned,
            "mom_score": mom_aligned,
            "outcome_r": t["outcome_r"],
            "win": t["win"],
            "setup": t["setup"],
            "side": t["side"],
        })

    if not records:
        return {"error": "No score data"}

    df = pd.DataFrame(records)
    out = {"total": len(df)}

    # Try multiple floor thresholds
    floors = [0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3]
    floor_results = []
    for floor in floors:
        both_pass = df[(df["trend_score"] >= floor) | (df["trend_score"] <= -floor)]
        both_pass = both_pass[(both_pass["mom_score"] >= floor) | (both_pass["mom_score"] <= -floor)]
        both_pass = both_pass[((both_pass["trend_score"] > 0) & (both_pass["mom_score"] > 0)) |
                              ((both_pass["trend_score"] < 0) & (both_pass["mom_score"] < 0))]
        rest = df[~df.index.isin(both_pass.index)]

        floor_results.append({
            "floor": floor,
            "pass_n": len(both_pass),
            "pass_exp_r": round(float(both_pass["outcome_r"].mean()), 4) if len(both_pass) > 0 else None,
            "pass_win_rate": round(float((both_pass["win"].sum() / len(both_pass)) * 100), 1) if len(both_pass) > 0 else None,
            "fail_n": len(rest),
            "fail_exp_r": round(float(rest["outcome_r"].mean()), 4) if len(rest) > 0 else None,
            "fail_win_rate": round(float((rest["win"].sum() / len(rest)) * 100), 1) if len(rest) > 0 else None,
        })
    out["structure_floor_analysis"] = floor_results

    return out


def main():
    parser = argparse.ArgumentParser(description="Entry Confluence Gate Calibration")
    parser.add_argument("--mode", default=None, help="Filter by mode (dry/test/live)")
    args = parser.parse_args()

    print(f"Memuat settled trades (mode={args.mode})...")
    trades = load_settled_trades(mode=args.mode)
    print(f"  Ditemukan {len(trades)} trade settled")

    if not trades:
        print("  Tidak ada data — jalankan forwardtest dulu")
        return

    winners = sum(1 for t in trades if t["win"])
    print(f"  Winner: {winners}/{len(trades)} ({winners/len(trades)*100:.1f}%)")
    exp_r = np.mean([t["outcome_r"] for t in trades])
    print(f"  Avg R: {exp_r:.4f}")

    print("\n=== Analisis Touch Count ===")
    tc = analyze_touch_counts(trades)
    if "error" in tc:
        print(f"  {tc['error']}")
    else:
        print(f"  Trade dengan level terdekat: {tc['with_level']}")
        print(f"\n  Touch Count Threshold Analysis:")
        print(f"  {'Thr':>5} {'N_above':>8} {'exp_R_above':>12} {'WR_above':>9} {'N_below':>8} {'exp_R_below':>12} {'WR_below':>9}")
        for r in tc["touch_count_analysis"]:
            if r["above_exp_r"] is not None:
                print(f"  {r['threshold']:>5} {r['above_n']:>8} {r['above_exp_r']:>+12.4f} {r['above_win_rate']:>8.1f}% "
                      f"{r['below_n']:>8} {r['below_exp_r']:>+12.4f} {r['below_win_rate']:>8.1f}%")
        print(f"\n  Proximity ATR Analysis:")
        print(f"  {'ATR':>5} {'N_near':>8} {'exp_R_near':>12} {'WR_near':>9} {'N_far':>8} {'exp_R_far':>12} {'WR_far':>9}")
        for r in tc["proximity_atr_analysis"]:
            if r["near_exp_r"] is not None:
                print(f"  {r['threshold_atr']:>5.2f} {r['near_n']:>8} {r['near_exp_r']:>+12.4f} {r['near_win_rate']:>8.1f}% "
                      f"{r['far_n']:>8} {r['far_exp_r']:>+12.4f} {r['far_win_rate']:>8.1f}%")

    print("\n=== Analisis Structure Floor ===")
    sc = analyze_structure_scores(trades)
    if "error" in sc:
        print(f"  {sc['error']}")
    else:
        print(f"\n  Floor Analysis:")
        print(f"  {'Floor':>6} {'N_pass':>8} {'exp_R_pass':>12} {'WR_pass':>9} {'N_fail':>8} {'exp_R_fail':>12} {'WR_fail':>9}")
        for r in sc["structure_floor_analysis"]:
            if r["pass_exp_r"] is not None:
                print(f"  {r['floor']:>6.2f} {r['pass_n']:>8} {r['pass_exp_r']:>+12.4f} {r['pass_win_rate']:>8.1f}% "
                      f"{r['fail_n']:>8} {r['fail_exp_r']:>+12.4f} {r['fail_win_rate']:>8.1f}%")

    # Save report
    report = {
        "total_trades": len(trades),
        "winners": winners,
        "exp_r": round(float(exp_r), 4),
        "touch_count": tc,
        "structure": sc,
    }
    report_path = ROOT / "data" / "ec_calibration.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nLaporan tersimpan: {report_path}")


if __name__ == "__main__":
    main()
