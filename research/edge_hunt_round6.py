#!/usr/bin/env python3
"""Edge hunt round 6 — breadth / BTC.D-proxy / unlock-filter novelty.

  H-EH-60  market breadth (% alts > SMA50) high → XS mom; low → XS reverse
  H-EH-61  breadth thrust: breadth jump 20pp in 5d → long/short alts
  H-EH-62  BTC share proxy: BTC ret - median alt ret (dominance pressure)
  H-EH-63  unlock-week FILTER on ST reverse (only trade when NOT unlock week)
  H-EH-64  low_idiovol only when breadth high (combine R5 lean + regime)
  H-EH-65  winner-short only when breadth falling (R2 lean + regime)

Strict: train must be + for CANDIDATE.
"""
from __future__ import annotations

# Ensure research/ is importable when run from repo root
import sys as _sys
from pathlib import Path as _Path
_RESEARCH = str(_Path(__file__).resolve().parent)
if _RESEARCH not in _sys.path:
    _sys.path.insert(0, _RESEARCH)


import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack, verdict_arm


def load_unlock_days() -> set:
    p = Path("data/unlock_calendar.csv")
    if not p.exists():
        return set()
    df = pd.read_csv(p)
    # expect date column
    col = None
    for c in df.columns:
        if "date" in c.lower() or "unlock" in c.lower() or c.lower() == "day":
            col = c
            break
    if col is None:
        col = df.columns[0]
    days = set()
    for v in df[col].astype(str):
        try:
            d = pd.Timestamp(v)
            if d.tzinfo is None:
                d = d.tz_localize("UTC")
            else:
                d = d.tz_convert("UTC")
            days.add(d.normalize())
        except Exception:
            continue
    return days


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=200, lookback_days=1600)
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = panel.index
    rets = panel.pct_change()
    b = btc.reindex(idx).ffill()
    b_ret = b.pct_change()
    resid = rets.sub(b_ret, axis=0)
    idvol = resid.rolling(20).std()
    sma50 = panel.rolling(50).mean()
    above = (panel > sma50).astype(float)
    breadth = above.mean(axis=1)  # fraction above SMA50
    breadth_chg5 = breadth.diff(5)
    # dominance pressure proxy: BTC ret - median alt ret
    med_alt = rets.median(axis=1)
    dom_pressure = b_ret - med_alt

    cum3 = (1 + rets).rolling(3).apply(lambda x: np.prod(x) - 1, raw=True)
    unlock_days = load_unlock_days()
    # expand unlock to ±3 days window
    unlock_win = set()
    for d in unlock_days:
        for k in range(-3, 4):
            unlock_win.add(d + pd.Timedelta(days=k))

    cut = idx[int(T * 0.70)]
    results = []
    print(f"panel {T}x{N} cut={cut} unlock_days={len(unlock_days)} win={len(unlock_win)}")

    def add(rid, tr, oos, n_trials=1, min_n=25):
        v = verdict_arm(
            pack(oos), n_trials=n_trials, train_mean=pack(tr).get("mean"), min_n=min_n
        )
        row = {"id": rid, "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(
            f"{rid}: train={row['train'].get('mean')} oos={row['oos'].get('mean')} "
            f"n={row['oos'].get('n')} {v['verdict']}"
        )

    # H-EH-60 breadth regime for XS
    b_hi = breadth > breadth.rolling(100).quantile(0.70)
    b_lo = breadth < breadth.rolling(100).quantile(0.30)
    for hold in (3, 5):
        # high breadth → momentum LS
        tr, oos = [], []
        for i in range(100, T - hold, hold):
            if not bool(b_hi.iloc[i]):
                continue
            row = cum3.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 15:
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
        add(f"breadth_hi_mom_ls_h{hold}", tr, oos, n_trials=2, min_n=20)

        # low breadth → reverse LS
        tr, oos = [], []
        for i in range(100, T - hold, hold):
            if not bool(b_lo.iloc[i]):
                continue
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
        add(f"breadth_lo_rev_ls_h{hold}", tr, oos, n_trials=2, min_n=20)

    # H-EH-61 breadth thrust
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(60, T - hold):
            if not (np.isfinite(breadth_chg5.iloc[i]) and breadth_chg5.iloc[i] >= 0.15):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd)) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"breadth_thrust_long_h{hold}", tr, oos, n_trials=3, min_n=15)

        tr, oos = [], []
        for i in range(60, T - hold):
            if not (np.isfinite(breadth_chg5.iloc[i]) and breadth_chg5.iloc[i] <= -0.15):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd)) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"breadth_collapse_short_h{hold}", tr, oos, n_trials=3, min_n=15)

    # H-EH-62 dominance pressure
    for hold in (1, 3, 5):
        # high dom pressure (BTC>>alts) → short alts? or long BTC
        tr, oos = [], []
        for i in range(20, T - hold):
            if not (np.isfinite(dom_pressure.iloc[i]) and dom_pressure.iloc[i] > 0.02):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd)) - COST_RT  # short alts under BTC dominance
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"dom_pressure_short_alts_h{hold}", tr, oos, n_trials=3, min_n=20)

        tr, oos = [], []
        for i in range(20, T - hold):
            if not (np.isfinite(dom_pressure.iloc[i]) and dom_pressure.iloc[i] < -0.02):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd)) - COST_RT  # alts outperforming → long
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"alt_pressure_long_alts_h{hold}", tr, oos, n_trials=3, min_n=20)

    # H-EH-63 unlock filter on ST rev: ONLY non-unlock weeks
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(10, T - hold, hold):
            t = idx[i]
            tn = pd.Timestamp(t).tz_convert("UTC").normalize() if t.tzinfo else pd.Timestamp(t, tz="UTC").normalize()
            if tn in unlock_win:
                continue
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
            (tr if t < cut else oos).append(pnl)
        add(f"nounlock_st_rev_ls_h{hold}", tr, oos, n_trials=3, min_n=25)

    # H-EH-64 low idiovol when breadth high
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(100, T - hold, hold):
            if not bool(b_hi.iloc[i]):
                continue
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
        add(f"breadth_hi_low_idiovol_ls_h{hold}", tr, oos, n_trials=2, min_n=15)

    # H-EH-65 winner short when breadth falling
    b_fall = breadth_chg5 < -0.05
    for hold in (3, 5):
        tr, oos = [], []
        for i in range(60, T - hold):
            if not bool(b_fall.iloc[i]):
                continue
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
        add(f"breadth_fall_winner_short_h{hold}", tr, oos, n_trials=2, min_n=20)

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    both = [
        r
        for r in results
        if (r["train"].get("mean") or -1) > 0 and (r["oos"].get("mean") or -1) > 0
    ]
    out = {
        "meta": {
            "panel": list(panel.shape),
            "cut": str(cut),
            "cost_rt": COST_RT,
            "n_unlock_days": len(unlock_days),
        },
        "results": results,
        "candidates": candidates,
        "n_candidates": len(candidates),
        "train_and_oos_pos": [r["id"] for r in both],
    }
    Path("logs/edge_hunt_round6.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("CANDIDATES", len(candidates))
    print("TRAIN+OOS+", [r["id"] for r in both])
    for r in both:
        print(
            f"  {r['id']}: train={r['train']['mean']:+.4%} oos={r['oos']['mean']:+.4%} "
            f"n={r['oos']['n']} {r['verdict']}"
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
