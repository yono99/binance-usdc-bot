#!/usr/bin/env python3
"""Edge hunt round 9 — smallcap universe (less HFT-arbitraged than top-66).

Uses data/snap_smallcap1800 or snap_smallcap1400 if present, else snap with
max_alts=400 lower volume tail.

  H-EH-90  ST reverse LS 3d (smallcaps)
  H-EH-91  low idiovol LS
  H-EH-92  winner 3d short
  H-EH-93  residual reverse 5d
  H-EH-94  crash bounce dd12 h3 (cluster-aware day-EW reported)
  H-EH-95  12-1 mom LS
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack, verdict_arm


def load_smallcap():
    for d in (
        Path("data/snap_smallcap1800"),
        Path("data/snap_smallcap1400"),
        Path("data/snap"),
    ):
        if d.exists() and any(d.glob("*__1d.pkl")):
            # for smallcap dirs, don't filter max_alts as hard
            max_alts = 150 if "smallcap" in d.name else 400
            # take lower half of volume for true smallcap if using main snap
            panel, btc = load_daily(d, max_alts=max_alts, lookback_days=1600, min_bars=300)
            return panel, btc, str(d)
    raise SystemExit("no snap")


def main() -> int:
    # Prefer smallcap dir; if main snap, drop top 30 by recent vol proxy (first cols from load are high vol)
    panel, btc, src = load_smallcap()
    if "snap_smallcap" not in src:
        # drop most liquid third — keep mid/small
        n = panel.shape[1]
        keep = list(panel.columns[n // 3 :])  # load_daily ranks high vol first
        panel = panel[keep]
        print(f"using mid/small tail of main snap: {panel.shape}")
    else:
        print(f"using {src}: {panel.shape}")

    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = panel.index
    rets = panel.pct_change()
    b = btc.reindex(idx).ffill()
    b_ret = b.pct_change()
    resid = rets.sub(b_ret, axis=0)
    cut = idx[int(T * 0.70)]
    results = []
    print(f"panel {T}x{N} cut={cut}")

    def add(rid, tr, oos, n_trials=1, min_n=30, extra=None):
        v = verdict_arm(
            pack(oos), n_trials=n_trials, train_mean=pack(tr).get("mean"), min_n=min_n
        )
        row = {"id": rid, "train": pack(tr), "oos": pack(oos), **v}
        if extra:
            row["extra"] = extra
        results.append(row)
        print(
            f"{rid}: train={row['train'].get('mean')} oos={row['oos'].get('mean')} "
            f"n={row['oos'].get('n')} {v['verdict']}"
        )

    cum3 = (1 + rets).rolling(3).apply(lambda x: np.prod(x) - 1, raw=True)
    idvol = resid.rolling(20).std()
    cum_r5 = resid.rolling(5).sum()

    # H-EH-90 ST rev LS
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(10, T - hold, hold):
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
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"sc_st_rev_ls_h{hold}", tr, oos, n_trials=3, min_n=30)

    # H-EH-91 low idiovol
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = idvol.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row) & (row > 0)
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
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"sc_low_idiovol_ls_h{hold}", tr, oos, n_trials=2, min_n=20)

    # H-EH-92 winner short
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(5, T - hold):
            row = cum3.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 10:
                continue
            thr = np.nanpercentile(row[valid], 80)
            S = valid & (row >= thr)
            if S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd[S])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"sc_winner_short_h{hold}", tr, oos, n_trials=3, min_n=40)

    # H-EH-93 residual reverse
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(10, T - hold, hold):
            row = cum_r5.iloc[i].to_numpy(dtype=float)
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
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"sc_resid5_rev_ls_h{hold}", tr, oos, n_trials=3, min_n=25)

    # H-EH-94 crash bounce with day-EW
    for thr, hold in ((-0.12, 3), (-0.15, 3), (-0.10, 3)):
        tr, oos = [], []
        day_oos: dict = {}
        for i in range(2, T - hold):
            row = rets.iloc[i].to_numpy(dtype=float)
            hit = np.isfinite(row) & (row <= thr)
            if hit.sum() < 2:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[hit])) - COST_RT
            t = idx[i]
            if t < cut:
                tr.append(pnl)
            else:
                oos.append(pnl)
                day_oos.setdefault(t, []).append(pnl)
        day_s = pack([float(np.mean(v)) for v in day_oos.values()])
        add(
            f"sc_crash_bounce_dd{int(abs(thr)*100)}_h{hold}",
            tr,
            oos,
            n_trials=3,
            min_n=30,
            extra={"oos_day_ew": day_s},
        )

    # H-EH-95 12-1 mom
    cum_12 = close / np.roll(close, 252, axis=0) - 1.0
    cum_1 = close / np.roll(close, 21, axis=0) - 1.0
    mom = cum_12 - cum_1
    mom[:252] = np.nan
    for hold in (10, 21):
        tr, oos = [], []
        for i in range(260, T - hold, hold):
            row = mom[i]
            valid = np.isfinite(row)
            if valid.sum() < 20:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row >= hi)
            S = valid & (row <= lo)
            if L.sum() < 3 or S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"sc_mom12_1_ls_h{hold}", tr, oos, n_trials=2, min_n=15)

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    both = [
        r
        for r in results
        if (r["train"].get("mean") or -1) > 0 and (r["oos"].get("mean") or -1) > 0
    ]
    out = {
        "meta": {
            "src": src,
            "panel": list(panel.shape),
            "cut": str(cut),
            "cost_rt": COST_RT,
        },
        "results": results,
        "candidates": candidates,
        "n_candidates": len(candidates),
        "train_and_oos_pos": [r["id"] for r in both],
    }
    Path("logs/edge_hunt_round9.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("CANDIDATES", len(candidates))
    for c in candidates:
        print(" ", c["id"], c["oos"].get("mean"), c["reason"])
    print("TRAIN+OOS+", [r["id"] for r in both])
    for r in both:
        print(
            f"  {r['id']}: train={r['train']['mean']:+.4%} oos={r['oos']['mean']:+.4%} "
            f"n={r['oos']['n']} p_adj={r.get('p_adj')} {r['verdict']}"
        )
    pos = sorted(
        [r for r in results if (r["oos"].get("mean") or -9) > 0],
        key=lambda x: -(x["oos"]["mean"] or 0),
    )
    print("TOP OOS+")
    for r in pos[:12]:
        print(
            f"  {r['id']}: {r['oos']['mean']:+.4%} n={r['oos']['n']} "
            f"train={r['train'].get('mean')} {r['verdict']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
