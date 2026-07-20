#!/usr/bin/env python3
"""Strict validate low_idiovol LS (only train+ & oos+ lean from R5).

Promotion: oos CANDIDATE + lockbox>0 + cost2x oos>0 + day-EW oos>0.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack, verdict_arm


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

    cut_oos = idx[int(T * 0.50)]
    cut_lb = idx[int(T * 0.80)]
    print(f"panel {T}x{N} cuts oos={cut_oos} lb={cut_lb}")

    rows = []
    for hold in (5, 10, 15, 21):
        for cost in (COST_RT, COST_RT * 2):
            tr, oos, lock = [], [], []
            day_oos: dict = {}
            day_lock: dict = {}
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
                pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - cost
                t = idx[i]
                if t < cut_oos:
                    tr.append(pnl)
                elif t < cut_lb:
                    oos.append(pnl)
                    day_oos.setdefault(t, []).append(pnl)
                else:
                    lock.append(pnl)
                    day_lock.setdefault(t, []).append(pnl)
            oos_d = pack([float(np.mean(v)) for v in day_oos.values()])
            lock_d = pack([float(np.mean(v)) for v in day_lock.values()])
            tr_s, oos_s, lock_s = pack(tr), pack(oos), pack(lock)
            v_oos = verdict_arm(oos_s, n_trials=8, train_mean=tr_s.get("mean"), min_n=20)
            v_lock = verdict_arm(lock_s, n_trials=1, train_mean=oos_s.get("mean"), min_n=10)
            v_day = verdict_arm(oos_d, n_trials=8, train_mean=None, min_n=10)
            ok = (
                v_oos["verdict"] == "CANDIDATE"
                and (lock_s.get("mean") or 0) > 0
                and (oos_d.get("mean") or 0) > 0
                and cost == COST_RT  # cost2x checked separately
            )
            # cost2x flag separately in loop
            row = {
                "id": f"low_idiovol_ls_h{hold}_c{1 if cost==COST_RT else 2}",
                "hold": hold,
                "cost": cost,
                "train": tr_s,
                "oos": oos_s,
                "lockbox": lock_s,
                "oos_day_ew": oos_d,
                "lock_day_ew": lock_d,
                "verdict_oos": v_oos,
                "verdict_lock": v_lock,
                "verdict_day": v_day,
            }
            # full promote needs both c1 and will be decided outside
            rows.append(row)
            print(
                f"{row['id']}: train={tr_s.get('mean')} oos={oos_s.get('mean')} n={oos_s.get('n')} "
                f"lock={lock_s.get('mean')} day={oos_d.get('mean')} {v_oos['verdict']}"
            )

    # promote if c1 CANDIDATE + lock>0 + day>0 AND matching c2 oos>0
    promoted = []
    by_hold = {}
    for r in rows:
        by_hold.setdefault(r["hold"], {})[r["cost"]] = r
    for h, m in by_hold.items():
        c1 = m.get(COST_RT)
        c2 = m.get(COST_RT * 2)
        if not c1 or not c2:
            continue
        if (
            c1["verdict_oos"]["verdict"] == "CANDIDATE"
            and (c1["lockbox"].get("mean") or 0) > 0
            and (c1["oos_day_ew"].get("mean") or 0) > 0
            and (c2["oos"].get("mean") or 0) > 0
        ):
            promoted.append(f"low_idiovol_ls_h{h}")
            c1["promotion"] = "PROMOTE_PAPER"
        else:
            if c1:
                c1["promotion"] = "NO"

    out = {
        "meta": {
            "cuts": {"oos": str(cut_oos), "lockbox": str(cut_lb)},
            "panel": list(panel.shape),
            "promotion_rule": "oos CANDIDATE + lock>0 + dayEW oos>0 + cost2x oos>0",
        },
        "rows": rows,
        "promoted": promoted,
    }
    Path("logs/edge_hunt_validate_idiovol.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("PROMOTED:", promoted or "NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
