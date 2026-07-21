#!/usr/bin/env python3
"""Strict validate smallcap crash-bounce (R9 only train+/OOS+ lean)."""
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


def main() -> int:
    panel, btc = load_daily(
        Path("data/snap_smallcap1800"), max_alts=150, lookback_days=1600, min_bars=300
    )
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = panel.index
    rets = panel.pct_change()
    b = btc.reindex(idx).ffill()
    cut_oos = idx[int(T * 0.50)]
    cut_lb = idx[int(T * 0.80)]
    print(f"panel {T}x{N} cuts {cut_oos} {cut_lb}")

    rows = []
    for thr in (-0.08, -0.10, -0.12, -0.15):
        for hold in (1, 3, 5):
            for cost, ctag in ((COST_RT, 1), (COST_RT * 2, 2)):
                tr, oos, lock = [], [], []
                day_oos: dict = {}
                day_lock: dict = {}
                for i in range(2, T - hold):
                    row = rets.iloc[i].to_numpy(dtype=float)
                    hit = np.isfinite(row) & (row <= thr)
                    if hit.sum() < 2:
                        continue
                    # also cluster size
                    cluster = int(hit.sum())
                    fwd = close[i + hold] / close[i] - 1.0
                    pnl = float(np.nanmean(fwd[hit])) - cost
                    t = idx[i]
                    if t < cut_oos:
                        tr.append(pnl)
                    elif t < cut_lb:
                        oos.append(pnl)
                        day_oos.setdefault(t, []).append(pnl)
                    else:
                        lock.append(pnl)
                        day_lock.setdefault(t, []).append(pnl)
                tr_s, oos_s, lock_s = pack(tr), pack(oos), pack(lock)
                oos_d = pack([float(np.mean(v)) for v in day_oos.values()])
                lock_d = pack([float(np.mean(v)) for v in day_lock.values()])
                # excess vs BTC on OOS events
                excess = []
                for i in range(2, T - hold):
                    t = idx[i]
                    if t < cut_oos or t >= cut_lb:
                        continue
                    row = rets.iloc[i].to_numpy(dtype=float)
                    hit = np.isfinite(row) & (row <= thr)
                    if hit.sum() < 2:
                        continue
                    fwd = close[i + hold] / close[i] - 1.0
                    bfwd = float(b.iloc[i + hold] / b.iloc[i] - 1.0)
                    excess.append(float(np.nanmean(fwd[hit])) - bfwd - cost)
                xs = pack(excess)
                v = verdict_arm(
                    oos_s, n_trials=24, train_mean=tr_s.get("mean"), min_n=25
                )
                row = {
                    "id": f"sc_dd{int(abs(thr)*100)}_h{hold}_c{ctag}",
                    "train": tr_s,
                    "oos": oos_s,
                    "lockbox": lock_s,
                    "oos_day_ew": oos_d,
                    "lock_day_ew": lock_d,
                    "oos_excess_btc": xs,
                    "verdict_oos": v,
                }
                rows.append(row)
                if ctag == 1:
                    print(
                        f"{row['id']}: tr={tr_s.get('mean')} oos={oos_s.get('mean')} "
                        f"n={oos_s.get('n')} lock={lock_s.get('mean')} "
                        f"day={oos_d.get('mean')} xs={xs.get('mean')} {v['verdict']}"
                    )

    promoted = []
    bases = {}
    for r in rows:
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
            and (c1["oos_excess_btc"].get("mean") or 0) > 0
            and (c1["train"].get("mean") or 0) > 0
        )
        c1["promotion"] = "PROMOTE_PAPER" if ok else "NO"
        if ok:
            promoted.append(base)

    out = {
        "meta": {
            "panel": list(panel.shape),
            "cuts": {"oos": str(cut_oos), "lock": str(cut_lb)},
            "src": "snap_smallcap1800",
        },
        "rows": rows,
        "promoted": promoted,
    }
    Path("logs/edge_hunt_validate_sc_crash.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("PROMOTED:", promoted or "NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
