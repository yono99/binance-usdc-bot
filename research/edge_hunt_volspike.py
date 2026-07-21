#!/usr/bin/env python3
"""Validate H-EH-19 volume-spike fade (candidate from exploratory)."""
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
    panel, btc = load_daily(Path("data/snap"), max_alts=150, lookback_days=1400)
    snap = Path("data/snap")
    vol_dfs = {}
    for col in panel.columns:
        base = col.split("/")[0]
        cands = list(snap.glob(f"{base}_*__1d.pkl"))
        if not cands:
            continue
        try:
            d = pd.read_pickle(cands[0])
            vol_dfs[col] = d["volume"].reindex(panel.index).ffill()
        except Exception:
            pass
    vol = pd.DataFrame(vol_dfs)
    cols = list(vol.columns)
    rets = panel[cols].pct_change()
    vma = vol.rolling(20).mean()
    idx = panel.index
    n = len(idx)
    cut_oos = idx[int(n * 0.5)]
    cut_lb = idx[int(n * 0.8)]
    print("cuts", cut_oos.date(), cut_lb.date(), "syms", len(cols))

    def run(hold, cost_mult=1.0, min_day_ret=0.01, vol_mult=3.0, max_cluster=99, follow=False):
        cost = COST_RT * cost_mult
        sp = vol > vol_mult * vma
        cl = sp.sum(axis=1)
        buckets = {"train": [], "oos": [], "lock": []}
        day_oos: dict = {}
        for col in cols:
            s = panel[col]
            r = rets[col]
            spi = sp[col]
            for t in s.index[spi.fillna(False)]:
                if cl.loc[t] > max_cluster:
                    continue
                loc = s.index.get_loc(t)
                if not isinstance(loc, (int, np.integer)):
                    continue
                loc = int(loc)
                if loc + hold >= len(s) or loc < 1:
                    continue
                day = float(r.loc[t]) if np.isfinite(r.loc[t]) else 0.0
                if abs(day) < min_day_ret:
                    continue
                fwd = float(s.iloc[loc + hold] / s.iloc[loc] - 1.0)
                if follow:
                    pnl = (fwd if day > 0 else -fwd) - cost
                else:
                    pnl = (-fwd if day > 0 else fwd) - cost
                if t < cut_oos:
                    buckets["train"].append(pnl)
                elif t < cut_lb:
                    buckets["oos"].append(pnl)
                    day_oos.setdefault(t, []).append(pnl)
                else:
                    buckets["lock"].append(pnl)
        day_p = pack([float(np.mean(v)) for v in day_oos.values()])
        return {k: pack(v) for k, v in buckets.items()}, day_p

    rows = []
    for hold in (1, 2, 3, 5):
        for cm, tag in ((1.0, "c1"), (2.0, "c2")):
            b, day = run(hold, cm)
            v = verdict_arm(b["oos"], n_trials=8, train_mean=b["train"].get("mean"), min_n=30)
            vl = verdict_arm(b["lock"], n_trials=1, train_mean=b["oos"].get("mean"), min_n=20)
            print(
                f"h{hold}_{tag} train_mean={b['train'].get('mean')} n={b['train'].get('n')} | "
                f"oos_mean={b['oos'].get('mean')} n={b['oos'].get('n')} {v['verdict']} | "
                f"lock_mean={b['lock'].get('mean')} n={b['lock'].get('n')} {vl['verdict']} | "
                f"day_oos={day.get('mean')} n={day.get('n')}"
            )
            rows.append({
                "id": f"volspike_fade_h{hold}_{tag}",
                "train": b["train"],
                "oos": b["oos"],
                "lock": b["lock"],
                "day_oos": day,
                "v_oos": v,
                "v_lock": vl,
            })

    print("--- tight cluster<=5 vol*4 min_ret2% ---")
    for hold in (2, 3):
        b, day = run(hold, 1.0, min_day_ret=0.02, vol_mult=4.0, max_cluster=5)
        v = verdict_arm(b["oos"], n_trials=4, train_mean=b["train"].get("mean"), min_n=25)
        print("tight h", hold, "train", b["train"], "oos", b["oos"], "lock", b["lock"], v, "day", day)
        rows.append({
            "id": f"volspike_tight_h{hold}",
            "train": b["train"], "oos": b["oos"], "lock": b["lock"], "day_oos": day, "v_oos": v,
        })

    print("--- follow control h3 ---")
    b, day = run(3, 1.0, follow=True)
    v = verdict_arm(b["oos"], n_trials=1, train_mean=b["train"].get("mean"), min_n=30)
    print("follow", b, v)
    rows.append({"id": "volspike_follow_h3_control", "train": b["train"], "oos": b["oos"], "lock": b["lock"], "v_oos": v})

    # promotion
    promoted = []
    for r in rows:
        if not r["id"].startswith("volspike_fade_h") or not r["id"].endswith("_c1"):
            continue
        c2 = next((x for x in rows if x["id"] == r["id"][:-2] + "c2"), None)
        ok = (
            r["v_oos"]["verdict"] == "CANDIDATE"
            and (r["lock"].get("mean") or 0) > 0
            and (r["day_oos"].get("mean") or 0) > 0
            and c2 is not None
            and (c2["oos"].get("mean") or 0) > 0
        )
        r["promotion"] = "PROMOTE_PAPER" if ok else "NO"
        if ok:
            promoted.append(r["id"])

    out = {
        "meta": {
            "cuts": {"oos": str(cut_oos), "lockbox": str(cut_lb)},
            "panel": list(panel.shape),
            "n_syms_vol": len(cols),
            "promoted": promoted,
        },
        "rows": rows,
    }
    Path("logs/edge_hunt_volspike.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("PROMOTED:", promoted or "NONE")
    print("Wrote logs/edge_hunt_volspike.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
