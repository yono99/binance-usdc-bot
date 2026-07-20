#!/usr/bin/env python3
"""Deep-dive on edge_hunt top signals — pre-registered single-param + cost×2.

NOT a free search: params fixed from prior pass (honest single-trial or small set).
  python edge_hunt_deep.py --out logs/edge_hunt_deep.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import (
    COST_RT,
    load_daily,
    pack,
    verdict_arm,
)
from bot.xsectional import (
    _rebalance_times,
    build_grid,
    sharpe,
    xs_returns,
    walk_forward_xs,
)


def walk_fixed(close, lookback, hold, reverse, cost, train=300, test=120, mask=None):
    T = close.shape[0]
    oos = []
    train_r = []
    start = lookback + 5
    while start + train + test <= T:
        tr0, tr1 = start, start + train
        te0, te1 = tr1, tr1 + test
        tr_times = list(_rebalance_times(tr0, tr1, lookback, hold))
        te_times = list(_rebalance_times(te0, te1, lookback, hold))
        if mask is not None:
            tr_times = [t for t in tr_times if mask[t]]
            te_times = [t for t in te_times if mask[t]]
        tr = xs_returns(close, tr_times, lookback, hold, 0.3, cost, reverse)
        te = xs_returns(close, te_times, lookback, hold, 0.3, cost, reverse)
        train_r.extend(tr.tolist())
        oos.extend(te.tolist())
        start += test
    return pack(train_r), pack(oos)


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=200, lookback_days=1600)
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    print(f"panel {T}x{N}")

    # dispersion mask
    mom5 = np.full((T, N), np.nan)
    for t in range(5, T):
        mom5[t] = close[t] / close[t - 5] - 1.0
    disp = np.nanstd(mom5, axis=1)
    thr = pd.Series(disp).rolling(120, min_periods=60).quantile(0.7).to_numpy()
    high = disp >= thr

    arms = {}
    # Pre-registered from pass1 top:
    # 1) high-disp reverse lb=5 hold=3
    # 2) XS reverse lb=3 hold=2
    # 3) XS reverse lb=5 hold=1
    specs = [
        ("hi_disp_rev_lb5_h3", 5, 3, True, high, 1),
        ("hi_disp_rev_lb3_h1", 3, 1, True, high, 1),
        ("xs_rev_lb3_h2", 3, 2, True, None, 1),
        ("xs_rev_lb5_h1", 5, 1, True, None, 1),
        ("xs_rev_lb2_h1", 2, 1, True, None, 1),
        ("xs_mom_lb20_h5", 20, 5, False, None, 1),  # null control momentum
        ("xs_mom_lb10_h5", 10, 5, False, None, 1),
    ]

    results = []
    for name, lb, h, rev, mask, n_tr in specs:
        for mult, tag in ((1.0, "cost1x"), (2.0, "cost2x")):
            cost = COST_RT * mult
            tr, oos = walk_fixed(close, lb, h, rev, cost, mask=mask)
            v = verdict_arm(oos, n_trials=n_tr, train_mean=tr.get("mean"), min_n=30)
            row = {
                "id": f"{name}_{tag}",
                "lookback": lb,
                "hold": h,
                "reverse": rev,
                "masked_high_disp": mask is not None,
                "cost": cost,
                "train": tr,
                "oos": oos,
                **v,
            }
            results.append(row)
            print(
                f"{row['id']:32s} train={tr.get('mean')} n={tr.get('n')} | "
                f"oos={oos.get('mean')} n={oos.get('n')} | {v['verdict']} p_adj={v.get('p_adj')}"
            )

    # H-EH-12: single-name loser bounce — after coin ret <= -8% in 1d, long hold 1/3
    print("\n=== single-name crash bounce ===")
    cut = panel.index[int(len(panel) * 0.7)]
    for thr_dd, hold in ((-0.08, 1), (-0.08, 3), (-0.12, 1), (-0.12, 3), (-0.05, 1)):
        tr, oos = [], []
        for col in panel.columns:
            s = panel[col].astype(float)
            r = s.pct_change()
            hits = r <= thr_dd
            for t in s.index[hits.fillna(False)]:
                loc = s.index.get_loc(t)
                if not isinstance(loc, (int, np.integer)):
                    continue
                loc = int(loc)
                if loc + hold >= len(s):
                    continue
                # enter NEXT day open≈close[t] already dump day close; hold from t
                fwd = float(s.iloc[loc + hold] / s.iloc[loc] - 1.0) - COST_RT
                if t < cut:
                    tr.append(fwd)
                else:
                    oos.append(fwd)
        tr_p, oos_p = pack(tr), pack(oos)
        v = verdict_arm(oos_p, n_trials=5, train_mean=tr_p.get("mean"), min_n=30)
        row = {
            "id": f"crash_bounce_dd{abs(thr_dd):.0%}_h{hold}",
            "train": tr_p,
            "oos": oos_p,
            **v,
        }
        results.append(row)
        print(f"{row['id']:32s} oos mean={oos_p.get('mean')} n={oos_p.get('n')} {v['verdict']}")

    # H-EH-13: Friday long / Monday short residual (pre-registered 2 arms)
    print("\n=== calendar pre-reg ===")
    ret = panel.pct_change()
    ew = ret.mean(axis=1).dropna()
    btc_r = btc.pct_change().reindex(ew.index).fillna(0)
    resid = ew - btc_r
    cut2 = ew.index[int(len(ew) * 0.7)]
    for name, dow, sign, series in (
        ("fri_long_ew", 4, 1, ew),
        ("sat_long_resid", 5, 1, resid),
        ("mon_short_ew", 0, -1, ew),
        ("tom_first3_long", None, 1, ew),
    ):
        if name.startswith("tom"):
            mask = ew.index.day <= 3
        else:
            mask = ew.index.dayofweek == dow
        tr = sign * series[mask & (ew.index < cut2)].to_numpy() - COST_RT
        oos = sign * series[mask & (ew.index >= cut2)].to_numpy() - COST_RT
        tr_p, oos_p = pack(tr), pack(oos)
        v = verdict_arm(oos_p, n_trials=4, train_mean=tr_p.get("mean"), min_n=25)
        row = {"id": name, "train": tr_p, "oos": oos_p, **v}
        results.append(row)
        print(f"{name:32s} oos={oos_p.get('mean')} n={oos_p.get('n')} {v['verdict']}")

    # H-EH-14: vol crush after spike — after vol20 > 2*vol100, short next 3d (EW)
    print("\n=== vol crush ===")
    btc_s = btc.reindex(panel.index).ffill()
    r = btc_s.pct_change()
    vol20 = r.rolling(20).std()
    vol100 = r.rolling(100).std()
    spike = vol20 > 2.0 * vol100
    for hold in (1, 3, 5):
        tr, oos = [], []
        for t in btc_s.index[spike.fillna(False)]:
            loc = btc_s.index.get_loc(t)
            if not isinstance(loc, (int, np.integer)):
                continue
            loc = int(loc)
            if loc + hold >= len(panel):
                continue
            # short EW alts
            row0 = panel.iloc[loc].to_numpy(dtype=float)
            row1 = panel.iloc[loc + hold].to_numpy(dtype=float)
            fwd = row1 / row0 - 1.0
            sh = float(-np.nanmean(fwd)) - COST_RT
            if t < cut:
                tr.append(sh)
            else:
                oos.append(sh)
        tr_p, oos_p = pack(tr), pack(oos)
        v = verdict_arm(oos_p, n_trials=3, train_mean=tr_p.get("mean"), min_n=20)
        row = {"id": f"vol_spike_short_alts_h{hold}", "train": tr_p, "oos": oos_p, **v}
        results.append(row)
        print(f"{row['id']:32s} oos={oos_p.get('mean')} n={oos_p.get('n')} {v['verdict']}")

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    out = {
        "meta": {
            "panel": list(panel.shape),
            "cost_rt_base": COST_RT,
            "note": "pre-registered deep dive; CANDIDATE still needs lockbox+paper",
        },
        "results": results,
        "candidates": candidates,
        "n_candidates": len(candidates),
    }
    path = Path("logs/edge_hunt_deep.json")
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nCANDIDATES: {len(candidates)}")
    for c in candidates:
        print(" ", c["id"], c["oos"].get("mean"), c["reason"])
    print("Wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
