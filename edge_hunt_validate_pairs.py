#!/usr/bin/env python3
"""Strict validate R10b train+/OOS+ leans: LINK residual fade + basket residz.

Discovery used many trials — report both discovery p_adj and family p_adj (n=6).
Promotion still needs lockbox>0, dayEW, cost2x, train>0.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, pack, verdict_arm
from edge_hunt_round10b import load_major_closes, residual_z


def main() -> int:
    panel, btc = load_major_closes(Path("data/snap"))
    b_ret = btc.pct_change()
    idx = panel.index
    T = len(idx)
    cut_oos = idx[int(T * 0.50)]
    cut_lb = idx[int(T * 0.80)]
    print(f"panel {panel.shape} cuts {cut_oos} {cut_lb}")

    z_link = residual_z(panel["LINK"].pct_change(), b_ret, 60)
    zdf = pd.DataFrame(
        {c: residual_z(panel[c].pct_change(), b_ret, 60) for c in panel.columns}
    )

    rows = []

    def collect_pair(hold, thr, cost):
        tr, oos, lock = [], [], []
        z = z_link.to_numpy(dtype=float)
        px = panel["LINK"].to_numpy(dtype=float)
        i = 0
        while i < T - hold:
            zi = z[i]
            if not np.isfinite(zi) or abs(zi) < thr:
                i += 1
                continue
            if not np.isfinite(px[i]) or px[i] <= 0 or not np.isfinite(px[i + hold]):
                i += 1
                continue
            side = -1.0 if zi > 0 else 1.0
            pnl = side * (px[i + hold] / px[i] - 1.0) - cost
            t = idx[i]
            if t < cut_oos:
                tr.append(float(pnl))
            elif t < cut_lb:
                oos.append(float(pnl))
            else:
                lock.append(float(pnl))
            i += hold
        return tr, oos, lock

    def collect_basket(hold, thr, cost):
        tr, oos, lock = [], [], []
        i = 60
        while i < T - hold:
            row = zdf.iloc[i].dropna()
            if len(row) < 3:
                i += 1
                continue
            L = [n for n, v in row.items() if v <= -thr]
            S = [n for n, v in row.items() if v >= thr]
            if not L and not S:
                i += 1
                continue
            pnls = []
            for n in L:
                pnls.append(float(panel[n].iloc[i + hold] / panel[n].iloc[i] - 1.0))
            for n in S:
                pnls.append(float(-(panel[n].iloc[i + hold] / panel[n].iloc[i] - 1.0)))
            pnl = float(np.mean(pnls)) - cost
            t = idx[i]
            if t < cut_oos:
                tr.append(pnl)
            elif t < cut_lb:
                oos.append(pnl)
            else:
                lock.append(pnl)
            i += hold
        return tr, oos, lock

    specs = [
        ("link_z1.5_h3", lambda c: collect_pair(3, 1.5, c)),
        ("link_z1.5_h5", lambda c: collect_pair(5, 1.5, c)),
        ("basket_z1.5_h5", lambda c: collect_basket(5, 1.5, c)),
        ("basket_z1.5_h3", lambda c: collect_basket(3, 1.5, c)),
    ]

    for name, fn in specs:
        for cost, ctag in ((COST_RT, 1), (COST_RT * 2, 2)):
            tr, oos, lock = fn(cost)
            tr_s, oos_s, lock_s = pack(tr), pack(oos), pack(lock)
            # family trials = 4 specs (pre-registered for this validate)
            v = verdict_arm(oos_s, n_trials=4, train_mean=tr_s.get("mean"), min_n=20)
            row = {
                "id": f"{name}_c{ctag}",
                "train": tr_s,
                "oos": oos_s,
                "lockbox": lock_s,
                "verdict_oos": v,
            }
            rows.append(row)
            print(
                f"{row['id']}: tr={tr_s.get('mean')} oos={oos_s.get('mean')} "
                f"n={oos_s.get('n')} lock={lock_s.get('mean')} {v['verdict']} "
                f"p_adj={v.get('p_adj')}"
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
            and (c2["oos"].get("mean") or 0) > 0
            and (c1["train"].get("mean") or 0) > 0
        )
        c1["promotion"] = "PROMOTE_PAPER" if ok else "NO"
        if ok:
            promoted.append(base)

    out = {
        "meta": {
            "cuts": {"oos": str(cut_oos), "lock": str(cut_lb)},
            "panel": list(panel.shape),
            "note": "family n_trials=4; discovery had ~60 arms so treat as exploratory",
        },
        "rows": rows,
        "promoted": promoted,
    }
    Path("logs/edge_hunt_validate_pairs.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("PROMOTED:", promoted or "NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
