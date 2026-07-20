#!/usr/bin/env python3
"""Strict 50/30/20 validate CANDIDATE_FILTER arms from edge_hunt_risk_filter.py.

Rule (filter, not entry):
  train ↓maxDD AND oos ↓maxDD AND lockbox ↓maxDD
  + oos worst improved
  + n_kept oos≥30, denied oos≥10
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily
from edge_hunt_risk_filter import risk_stats


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=200, lookback_days=1600)
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = panel.index
    rets = panel.pct_change()
    b = btc.reindex(idx).ffill()
    b_ret = b.pct_change()
    cut_oos = idx[int(T * 0.50)]
    cut_lb = idx[int(T * 0.80)]
    print(f"cuts oos={cut_oos} lb={cut_lb}")

    # streams
    long_ew = []
    for i in range(1, T - 1):
        fwd = close[i + 1] / close[i] - 1.0
        pnl = float(np.nanmean(fwd)) - COST_RT
        if np.isfinite(pnl):
            long_ew.append((idx[i], pnl, i))

    cum3 = (1 + rets).rolling(3).apply(lambda x: np.prod(x) - 1, raw=True)
    st_rev = []
    hold = 3
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
        st_rev.append((idx[i], pnl, i))

    bvol = b_ret.rolling(20).std()
    bvol_hi = bvol > bvol.rolling(100).quantile(0.75)
    ew = rets.mean(axis=1)
    avg_corr = []
    for i in range(T):
        if i < 20:
            avg_corr.append(np.nan)
            continue
        block = rets.iloc[i - 19 : i + 1]
        ewb = ew.iloc[i - 19 : i + 1]
        cors = []
        for c in block.columns:
            x = block[c]
            m = x.notna() & ewb.notna()
            if m.sum() < 10 or x[m].std() < 1e-12 or ewb[m].std() < 1e-12:
                continue
            cors.append(float(x[m].corr(ewb[m])))
        avg_corr.append(float(np.nanmean(cors)) if cors else np.nan)
    avg_corr = pd.Series(avg_corr, index=idx)
    corr_hi = avg_corr > avg_corr.rolling(100).quantile(0.70)
    sma50 = panel.rolling(50).mean()
    breadth = (panel > sma50).astype(float).mean(axis=1)
    breadth_lo = breadth < breadth.rolling(100).quantile(0.30)

    specs = [
        (
            "long_ew__skip_breadth_lo",
            long_ew,
            lambda i: not bool(breadth_lo.iloc[i]) if np.isfinite(breadth.iloc[i]) else True,
        ),
        (
            "st_rev_ls__skip_corr_or_volhi",
            st_rev,
            lambda i: (
                (not bool(corr_hi.iloc[i]) if np.isfinite(avg_corr.iloc[i]) else True)
                and (not bool(bvol_hi.iloc[i]) if np.isfinite(bvol.iloc[i]) else True)
            ),
        ),
        (
            "long_ew__skip_corr_or_volhi",
            long_ew,
            lambda i: (
                (not bool(corr_hi.iloc[i]) if np.isfinite(avg_corr.iloc[i]) else True)
                and (not bool(bvol_hi.iloc[i]) if np.isfinite(bvol.iloc[i]) else True)
            ),
        ),
        (
            "long_ew__skip_btc_vol_hi",
            long_ew,
            lambda i: not bool(bvol_hi.iloc[i]) if np.isfinite(bvol.iloc[i]) else True,
        ),
    ]

    rows = []
    promoted = []
    for name, stream, keep_fn in specs:
        buckets = {
            "train": {"base": [], "filt": []},
            "oos": {"base": [], "filt": []},
            "lock": {"base": [], "filt": []},
        }
        for t, pnl, i in stream:
            if t < cut_oos:
                k = "train"
            elif t < cut_lb:
                k = "oos"
            else:
                k = "lock"
            buckets[k]["base"].append(pnl)
            if keep_fn(i):
                buckets[k]["filt"].append(pnl)
        stats = {
            f"{seg}_{arm}": risk_stats(buckets[seg][arm])
            for seg in ("train", "oos", "lock")
            for arm in ("base", "filt")
        }
        ok = (
            stats["oos_filt"]["n"] >= 30
            and (stats["oos_base"]["n"] - stats["oos_filt"]["n"]) >= 10
            and stats["oos_filt"]["max_dd"] is not None
            and stats["oos_base"]["max_dd"] is not None
            and stats["oos_filt"]["max_dd"] < stats["oos_base"]["max_dd"]
            and stats["oos_filt"]["worst"] > stats["oos_base"]["worst"]
            and stats["train_filt"]["max_dd"] is not None
            and stats["train_base"]["max_dd"] is not None
            and stats["train_filt"]["max_dd"] < stats["train_base"]["max_dd"]
            and stats["lock_filt"]["max_dd"] is not None
            and stats["lock_base"]["max_dd"] is not None
            and stats["lock_filt"]["max_dd"] < stats["lock_base"]["max_dd"]
        )
        soft = (
            stats["oos_filt"]["max_dd"] is not None
            and stats["oos_base"]["max_dd"] is not None
            and stats["oos_filt"]["max_dd"] < stats["oos_base"]["max_dd"]
            and stats["train_filt"]["max_dd"] is not None
            and stats["train_base"]["max_dd"] is not None
            and stats["train_filt"]["max_dd"] < stats["train_base"]["max_dd"]
        )
        promo = "PROMOTE_FILTER_PAPER" if ok else ("WATCHLIST_FILTER" if soft else "NO")
        if ok:
            promoted.append(name)
        row = {
            "id": name,
            **stats,
            "promotion": promo,
            "oos_dd_reduction": (
                stats["oos_base"]["max_dd"] - stats["oos_filt"]["max_dd"]
                if stats["oos_base"]["max_dd"] is not None and stats["oos_filt"]["max_dd"] is not None
                else None
            ),
            "lock_dd_reduction": (
                stats["lock_base"]["max_dd"] - stats["lock_filt"]["max_dd"]
                if stats["lock_base"]["max_dd"] is not None and stats["lock_filt"]["max_dd"] is not None
                else None
            ),
        }
        rows.append(row)
        print(
            f"{name}: promo={promo} "
            f"oos_dd {stats['oos_base']['max_dd']}→{stats['oos_filt']['max_dd']} "
            f"lock_dd {stats['lock_base']['max_dd']}→{stats['lock_filt']['max_dd']} "
            f"n_oos {stats['oos_base']['n']}→{stats['oos_filt']['n']}"
        )

    out = {
        "meta": {
            "cuts": {"oos": str(cut_oos), "lock": str(cut_lb)},
            "panel": list(panel.shape),
            "rule": "train↓DD + oos↓DD+worst + lock↓DD; n_kept≥30 denied≥10",
            "note": "FILTER only — not entry PROMOTE_PAPER. Synthetic streams.",
        },
        "rows": rows,
        "promoted": promoted,
    }
    Path("logs/edge_hunt_validate_risk_filter.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("PROMOTED FILTERS:", promoted or "NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
