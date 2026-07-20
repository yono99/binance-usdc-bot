#!/usr/bin/env python3
"""Strict 50/30/20 + cost2x + day-EW for R6 train+/OOS+ leans."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack, verdict_arm


def load_unlock_win() -> set:
    p = Path("data/unlock_calendar.csv")
    if not p.exists():
        return set()
    df = pd.read_csv(p)
    col = df.columns[0]
    for c in df.columns:
        if "date" in c.lower():
            col = c
            break
    days = set()
    for v in df[col].astype(str):
        try:
            d = pd.Timestamp(v)
            d = d.tz_localize("UTC") if d.tzinfo is None else d.tz_convert("UTC")
            days.add(d.normalize())
        except Exception:
            continue
    win = set()
    for d in days:
        for k in range(-3, 4):
            win.add(d + pd.Timedelta(days=k))
    return win


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=200, lookback_days=1600)
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = panel.index
    rets = panel.pct_change()
    b = btc.reindex(idx).ffill()
    b_ret = b.pct_change()
    med_alt = rets.median(axis=1)
    dom_pressure = b_ret - med_alt
    sma50 = panel.rolling(50).mean()
    breadth = (panel > sma50).astype(float).mean(axis=1)
    b_lo = breadth < breadth.rolling(100).quantile(0.30)
    cum3 = (1 + rets).rolling(3).apply(lambda x: np.prod(x) - 1, raw=True)
    unlock_win = load_unlock_win()

    cut_oos = idx[int(T * 0.50)]
    cut_lb = idx[int(T * 0.80)]
    print(f"cuts oos={cut_oos} lb={cut_lb}")

    def collect(events_fn, holds, n_trials, label):
        rows = []
        for hold in holds:
            for cost, ctag in ((COST_RT, 1), (COST_RT * 2, 2)):
                tr, oos, lock = [], [], []
                day_oos: dict = {}
                for i, pnl in events_fn(hold, cost):
                    t = idx[i]
                    if t < cut_oos:
                        tr.append(pnl)
                    elif t < cut_lb:
                        oos.append(pnl)
                        day_oos.setdefault(t, []).append(pnl)
                    else:
                        lock.append(pnl)
                oos_d = pack([float(np.mean(v)) for v in day_oos.values()])
                tr_s, oos_s, lock_s = pack(tr), pack(oos), pack(lock)
                v = verdict_arm(oos_s, n_trials=n_trials, train_mean=tr_s.get("mean"), min_n=20)
                row = {
                    "id": f"{label}_h{hold}_c{ctag}",
                    "train": tr_s,
                    "oos": oos_s,
                    "lockbox": lock_s,
                    "oos_day_ew": oos_d,
                    "verdict_oos": v,
                }
                rows.append(row)
                print(
                    f"{row['id']}: tr={tr_s.get('mean')} oos={oos_s.get('mean')} "
                    f"n={oos_s.get('n')} lock={lock_s.get('mean')} day={oos_d.get('mean')} {v['verdict']}"
                )
        return rows

    # breadth_lo reverse LS
    def ev_breadth(hold, cost):
        out = []
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
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - cost
            out.append((i, pnl))
        return out

    # dom pressure short alts
    def ev_dom(hold, cost):
        out = []
        for i in range(20, T - hold):
            if not (np.isfinite(dom_pressure.iloc[i]) and dom_pressure.iloc[i] > 0.02):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd)) - cost
            out.append((i, pnl))
        return out

    # nounlock ST rev
    def ev_nounlock(hold, cost):
        out = []
        for i in range(10, T - hold, hold):
            t = idx[i]
            tn = pd.Timestamp(t)
            tn = tn.tz_localize("UTC") if tn.tzinfo is None else tn.tz_convert("UTC")
            tn = tn.normalize()
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
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - cost
            out.append((i, pnl))
        return out

    all_rows = []
    all_rows += collect(ev_breadth, [3, 5], 4, "breadth_lo_rev_ls")
    all_rows += collect(ev_dom, [1, 3], 4, "dom_pressure_short_alts")
    all_rows += collect(ev_nounlock, [3, 5], 4, "nounlock_st_rev_ls")

    promoted = []
    # group by base id without cost tag
    bases = {}
    for r in all_rows:
        base = r["id"].rsplit("_c", 1)[0]
        tag = r["id"].rsplit("_c", 1)[1]
        bases.setdefault(base, {})[tag] = r
    for base, m in bases.items():
        c1, c2 = m.get("1"), m.get("2")
        if not c1 or not c2:
            continue
        ok = (
            c1["verdict_oos"]["verdict"] == "CANDIDATE"
            and (c1["lockbox"].get("mean") or 0) > 0
            and (c1["oos_day_ew"].get("mean") or 0) > 0
            and (c2["oos"].get("mean") or 0) > 0
            and (c1["train"].get("mean") or 0) > 0
        )
        c1["promotion"] = "PROMOTE_PAPER" if ok else "NO"
        if ok:
            promoted.append(base)

    out = {
        "meta": {
            "cuts": {"oos": str(cut_oos), "lockbox": str(cut_lb)},
            "panel": list(panel.shape),
            "rule": "oos CANDIDATE + train>0 + lock>0 + dayEW>0 + cost2x oos>0",
        },
        "rows": all_rows,
        "promoted": promoted,
    }
    Path("logs/edge_hunt_validate_r6.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("PROMOTED:", promoted or "NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
