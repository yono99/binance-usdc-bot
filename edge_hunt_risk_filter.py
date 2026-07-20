#!/usr/bin/env python3
"""Risk-filter harness (Jalan A) — META filters on synthetic entry stream.

Bukan entry alpha. Ukur apakah filter MENURUNKAN risiko (maxDD / std / worst R)
pada stream trade OOS, sambil melaporkan exp_R (boleh NOT_PROVEN).

Baseline stream: equal-weight daily long alts (naive) + cost RT — worst case
"always long" book. Filters SKIP days; kept days form Arm B.

Also: ST reverse LS stream as second baseline (neutral-ish).

Promotion for FILTER (bukan entry):
  - OOS reduces maxDD vs baseline AND worst_r better (less negative)
  - n_kept >= 30, n_denied >= 10
  - train also reduces maxDD (consistent)
  - exp_R kept does not collapse below baseline by > 0.5R mean (optional soft)

  python edge_hunt_risk_filter.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack


def risk_stats(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0, "mean": None, "max_dd": None, "std": None, "worst": None}
    arr = np.asarray(rs, dtype=float)
    cum = np.cumsum(arr)
    peak = np.maximum.accumulate(cum)
    dd = float(np.max(peak - cum))
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "max_dd": dd,
        "std": float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        "worst": float(arr.min()),
    }


def verdict_filter(base: dict, filt: dict, train_base: dict, train_filt: dict) -> dict:
    if filt["n"] < 30 or (base["n"] - filt["n"]) < 10:
        return {
            "verdict": "INCONCLUSIVE",
            "reason": f"n_kept={filt['n']} denied={base['n']-filt['n']} need ≥30/10",
        }
    if filt["max_dd"] is None or base["max_dd"] is None:
        return {"verdict": "INCONCLUSIVE", "reason": "no dd"}
    reduces = filt["max_dd"] < base["max_dd"] and filt["worst"] > base["worst"]
    train_ok = (
        train_filt["max_dd"] is not None
        and train_base["max_dd"] is not None
        and train_filt["max_dd"] < train_base["max_dd"]
    )
    if reduces and train_ok:
        return {
            "verdict": "CANDIDATE_FILTER",
            "reason": (
                f"OOS maxDD {filt['max_dd']:.3f}<{base['max_dd']:.3f} "
                f"worst {filt['worst']:.3f}>{base['worst']:.3f}; train also ↓DD"
            ),
        }
    if reduces and not train_ok:
        return {
            "verdict": "NOT_PROVEN",
            "reason": "OOS reduces risk but train does not (regime)",
        }
    return {
        "verdict": "REJECTED",
        "reason": (
            f"OOS maxDD {filt.get('max_dd')} vs base {base.get('max_dd')}; "
            f"worst {filt.get('worst')} vs {base.get('worst')}"
        ),
    }


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=200, lookback_days=1600)
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = panel.index
    rets = panel.pct_change()
    b = btc.reindex(idx).ffill()
    b_ret = b.pct_change()
    cut = idx[int(T * 0.70)]

    # streams: list of (t, pnl) chronological non-overlap daily for long EW
    long_ew = []
    for i in range(1, T - 1):
        fwd = close[i + 1] / close[i] - 1.0
        pnl = float(np.nanmean(fwd)) - COST_RT
        if np.isfinite(pnl):
            long_ew.append((idx[i], pnl, i))

    # ST rev LS hold 3 non-overlap
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

    # precompute filter series
    # high BTC vol
    bvol = b_ret.rolling(20).std()
    bvol_hi = bvol > bvol.rolling(100).quantile(0.75)
    # high corr (avg corr vs EW)
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
    # BTC dump day
    btc_dump = b_ret <= -0.02
    # high dispersion
    disp = rets.std(axis=1)
    disp_hi = disp > disp.rolling(100).quantile(0.75)
    # DVOL if available
    dvol_hi = None
    dpath = Path("data/snap_dvol_btc_1d.pkl")
    if dpath.exists():
        dvol = pd.read_pickle(dpath)
        dvol = dvol.reindex(idx).ffill()
        dvol_hi = dvol > dvol.rolling(100).quantile(0.75)

    # breadth
    sma50 = panel.rolling(50).mean()
    breadth = (panel > sma50).astype(float).mean(axis=1)
    breadth_lo = breadth < breadth.rolling(100).quantile(0.30)

    filters = {
        "skip_btc_vol_hi": lambda i: not bool(bvol_hi.iloc[i]) if np.isfinite(bvol.iloc[i]) else True,
        "skip_corr_hi": lambda i: not bool(corr_hi.iloc[i]) if np.isfinite(avg_corr.iloc[i]) else True,
        "skip_btc_dump": lambda i: not bool(btc_dump.iloc[i]) if np.isfinite(b_ret.iloc[i]) else True,
        "skip_disp_hi": lambda i: not bool(disp_hi.iloc[i]) if np.isfinite(disp.iloc[i]) else True,
        "skip_breadth_lo": lambda i: not bool(breadth_lo.iloc[i]) if np.isfinite(breadth.iloc[i]) else True,
    }
    if dvol_hi is not None:
        filters["skip_dvol_hi"] = (
            lambda i: not bool(dvol_hi.iloc[i]) if np.isfinite(dvol_hi.iloc[i]) else True
        )
    # combine: skip if ANY of dump or high vol
    filters["skip_dump_or_volhi"] = lambda i: (
        filters["skip_btc_dump"](i) and filters["skip_btc_vol_hi"](i)
    )
    filters["skip_corr_or_volhi"] = lambda i: (
        filters["skip_corr_hi"](i) and filters["skip_btc_vol_hi"](i)
    )

    results = []

    def eval_stream(stream, stream_name, filt_name, keep_fn):
        tr_b, oos_b, tr_f, oos_f = [], [], [], []
        for t, pnl, i in stream:
            if t < cut:
                tr_b.append(pnl)
                if keep_fn(i):
                    tr_f.append(pnl)
            else:
                oos_b.append(pnl)
                if keep_fn(i):
                    oos_f.append(pnl)
        tb, to = risk_stats(tr_b), risk_stats(oos_b)
        tf, fo = risk_stats(tr_f), risk_stats(oos_f)
        v = verdict_filter(to, fo, tb, tf)
        row = {
            "id": f"{stream_name}__{filt_name}",
            "stream": stream_name,
            "filter": filt_name,
            "train_base": tb,
            "train_filt": tf,
            "oos_base": to,
            "oos_filt": fo,
            "oos_n_denied": to["n"] - fo["n"],
            "dd_reduction_oos": (
                (to["max_dd"] - fo["max_dd"]) if to["max_dd"] is not None and fo["max_dd"] is not None else None
            ),
            **v,
        }
        results.append(row)
        print(
            f"{row['id']}: oos_dd {to.get('max_dd')}→{fo.get('max_dd')} "
            f"mean {to.get('mean')}→{fo.get('mean')} n {to.get('n')}→{fo.get('n')} "
            f"{v['verdict']}"
        )
        return row

    for sname, stream in (("long_ew", long_ew), ("st_rev_ls", st_rev)):
        for fname, fn in filters.items():
            eval_stream(stream, sname, fname, fn)

    cands = [r for r in results if r["verdict"] == "CANDIDATE_FILTER"]
    out = {
        "meta": {
            "panel": list(panel.shape),
            "cut": str(cut),
            "cost_rt": COST_RT,
            "rule": "CANDIDATE_FILTER = OOS↓maxDD & better worst + train↓maxDD; n_kept≥30 denied≥10",
            "note": "Risk filter only — not entry edge. Wire as skip-gate if candidate.",
        },
        "results": results,
        "candidates": cands,
        "n_candidates": len(cands),
    }
    Path("logs/edge_hunt_risk_filter.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("CANDIDATE_FILTER", len(cands))
    for c in cands:
        print(" ", c["id"], c["reason"])
    # top by dd reduction among those that at least reduce oos dd
    reducers = [
        r
        for r in results
        if (r.get("dd_reduction_oos") or 0) > 0 and r["oos_filt"]["n"] >= 20
    ]
    reducers.sort(key=lambda x: -x["dd_reduction_oos"])
    print("TOP DD REDUCERS OOS")
    for r in reducers[:12]:
        print(
            f"  {r['id']}: Δdd={r['dd_reduction_oos']:+.4f} "
            f"mean {r['oos_base']['mean']}→{r['oos_filt']['mean']} {r['verdict']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
