#!/usr/bin/env python3
"""Edge hunt round 2 — more structural OHLCV angles (still not H24-H32 clones)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack, verdict_arm
from bot.xsectional import (
    _rebalance_times,
    build_grid,
    walk_forward_xs,
    xs_returns,
    walk_forward_scores,
)


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=200, lookback_days=1600)
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = panel.index
    print(f"panel {T}x{N}")
    cut = idx[int(T * 0.7)]
    results = []

    # H-EH-24: 3-day loser basket long (not 1-day crash)
    rets = panel.pct_change()
    cum3 = (1 + rets).rolling(3).apply(lambda x: np.prod(x) - 1, raw=True)
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(3, T - hold):
            row = cum3.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 10:
                continue
            thr = np.nanpercentile(row[valid], 20)
            losers = valid & (row <= thr)
            if losers.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[losers])) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        for name, arr in (("train", tr), ("oos", oos)):
            pass
        v = verdict_arm(pack(oos), n_trials=3, train_mean=pack(tr).get("mean"), min_n=40)
        row = {"id": f"loser3d_long_h{hold}", "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(row["id"], row["oos"].get("mean"), row["oos"].get("n"), v["verdict"])

    # H-EH-25: winner fade (top 20% 3d short)
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(3, T - hold):
            row = cum3.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 10:
                continue
            thr = np.nanpercentile(row[valid], 80)
            win = valid & (row >= thr)
            if win.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd[win])) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=3, train_mean=pack(tr).get("mean"), min_n=40)
        row = {"id": f"winner3d_short_h{hold}", "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(row["id"], row["oos"].get("mean"), row["oos"].get("n"), v["verdict"])

    # H-EH-26: dollar-neutral loser long + winner short (classic ST reverse LS)
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(3, T - hold, hold):  # non-overlap
            row = cum3.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 15:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row <= lo)
            S = valid & (row >= hi)
            if L.sum() < 3 or S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=3, train_mean=pack(tr).get("mean"), min_n=30)
        row = {"id": f"st_rev_ls_h{hold}", "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(row["id"], row["oos"].get("mean"), row["oos"].get("n"), v["verdict"])

    # H-EH-27: low realized-vol premium (long low-vol, short high-vol) 20d
    vol20 = rets.rolling(20).std()
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = vol20.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row) & (row > 0)
            if valid.sum() < 15:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row <= lo)
            S = valid & (row >= hi)
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=2, train_mean=pack(tr).get("mean"), min_n=20)
        row = {"id": f"lowvol_premium_h{hold}", "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(row["id"], row["oos"].get("mean"), row["oos"].get("n"), v["verdict"])

    # H-EH-28: high-vol short only (asymmetric)
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = vol20.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row) & (row > 0)
            if valid.sum() < 15:
                continue
            hi = np.nanpercentile(row[valid], 80)
            S = valid & (row >= hi)
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd[S])) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=2, train_mean=pack(tr).get("mean"), min_n=20)
        row = {"id": f"highvol_short_h{hold}", "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(row["id"], row["oos"].get("mean"), row["oos"].get("n"), v["verdict"])

    # H-EH-29: trend following BTC only (simple MA) as benchmark
    b = btc.reindex(idx).ffill()
    ma50 = b.rolling(50).mean()
    ma200 = b.rolling(200).mean()
    for name, sig in (
        ("btc_ma50", b > ma50),
        ("btc_ma200", b > ma200),
    ):
        tr, oos = [], []
        for i in range(200, T - 1):
            if not bool(sig.iloc[i]):
                continue
            # long BTC 1d
            pnl = float(b.iloc[i + 1] / b.iloc[i] - 1.0) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=2, train_mean=pack(tr).get("mean"), min_n=40)
        row = {"id": f"{name}_long_1d", "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(row["id"], row["oos"].get("mean"), row["oos"].get("n"), v["verdict"])

    # H-EH-30: alt residual momentum 20d (long positive residual)
    btc_r = b.pct_change()
    resid = rets.sub(btc_r, axis=0)
    cum_r = resid.rolling(20).sum()
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = cum_r.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 15:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row >= hi)  # high residual mom
            S = valid & (row <= lo)
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=2, train_mean=pack(tr).get("mean"), min_n=20)
        row = {"id": f"resid_mom20_ls_h{hold}", "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(row["id"], row["oos"].get("mean"), row["oos"].get("n"), v["verdict"])

    # H-EH-31: skip high-BTC-vol days filter on ST rev LS
    btc_vol = b.pct_change().rolling(20).std()
    quiet = btc_vol < btc_vol.rolling(100).quantile(0.5)
    for hold in (1, 3):
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            if not bool(quiet.iloc[i]):
                continue
            row = cum3.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 15:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row <= lo)
            S = valid & (row >= hi)
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=2, train_mean=pack(tr).get("mean"), min_n=20)
        row = {"id": f"st_rev_quiet_btc_h{hold}", "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(row["id"], row["oos"].get("mean"), row["oos"].get("n"), v["verdict"])

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    out = {
        "meta": {"panel": list(panel.shape), "cut": str(cut), "cost_rt": COST_RT},
        "results": results,
        "candidates": candidates,
        "n_candidates": len(candidates),
    }
    Path("logs/edge_hunt_round2.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("CANDIDATES", len(candidates))
    for c in candidates:
        print(" ", c["id"], c["oos"].get("mean"), c["reason"])
    # top oos
    pos = sorted(
        [r for r in results if (r["oos"].get("mean") or -9) > 0],
        key=lambda x: -x["oos"]["mean"],
    )
    print("TOP OOS+")
    for r in pos[:10]:
        print(f"  {r['id']}: {r['oos']['mean']:+.4%} n={r['oos']['n']} {r['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
