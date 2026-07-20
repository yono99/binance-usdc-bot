#!/usr/bin/env python3
"""Validate H-EH-12 crash-bounce CANDIDATE — stress, lockbox, clustering, costs.

Entry: close of day when coin ret <= thr (dump day). Hold H days.
Honest risks: same-day cluster, survivorship of snap universe, cost under-stress.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack, verdict_arm


def collect_events(panel: pd.DataFrame, thr: float, hold: int, cost: float):
    """Return list of dicts: t, sym, ret, fwd, cluster_size."""
    events = []
    # precompute daily returns
    rets = panel.pct_change()
    for col in panel.columns:
        s = panel[col].astype(float)
        r = rets[col]
        hits = r <= thr
        for t in s.index[hits.fillna(False)]:
            loc = s.index.get_loc(t)
            if not isinstance(loc, (int, np.integer)):
                continue
            loc = int(loc)
            if loc + hold >= len(s) or loc < 1:
                continue
            fwd = float(s.iloc[loc + hold] / s.iloc[loc] - 1.0) - cost
            events.append({
                "t": t,
                "sym": col,
                "dump_ret": float(r.loc[t]),
                "fwd": fwd,
            })
    # cluster size = how many events same day
    by_day: dict = {}
    for e in events:
        by_day.setdefault(e["t"], 0)
        by_day[e["t"]] += 1
    for e in events:
        e["cluster"] = by_day[e["t"]]
    return events


def split_stats(events, cut_lo, cut_hi=None):
    xs = []
    for e in events:
        if e["t"] < cut_lo:
            continue
        if cut_hi is not None and e["t"] >= cut_hi:
            continue
        xs.append(e["fwd"])
    return pack(xs)


def day_equal_weight(events, cut_lo, cut_hi=None):
    """One return per day = mean of that day's crash-bounce trades (de-cluster)."""
    buckets: dict = {}
    for e in events:
        if e["t"] < cut_lo:
            continue
        if cut_hi is not None and e["t"] >= cut_hi:
            continue
        buckets.setdefault(e["t"], []).append(e["fwd"])
    return pack([float(np.mean(v)) for v in buckets.values()])


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=200, lookback_days=1600)
    idx = panel.index
    # 50/30/20 train / oos / lockbox chronological
    n = len(idx)
    cut_oos = idx[int(n * 0.50)]
    cut_lb = idx[int(n * 0.80)]
    print(f"panel {panel.shape} train< {cut_oos.date()} oos< {cut_lb.date()} lockbox>= {cut_lb.date()}")

    configs = [
        (-0.08, 3, "dd8_h3"),
        (-0.12, 3, "dd12_h3"),
        (-0.08, 1, "dd8_h1"),
        (-0.12, 1, "dd12_h1"),
        (-0.10, 3, "dd10_h3"),
        (-0.15, 3, "dd15_h3"),
    ]

    rows = []
    for thr, hold, name in configs:
        for mult, ctag in ((1.0, "c1"), (2.0, "c2")):
            cost = COST_RT * mult
            ev = collect_events(panel, thr, hold, cost)
            train = split_stats(ev, idx[0], cut_oos)
            oos = split_stats(ev, cut_oos, cut_lb)
            lock = split_stats(ev, cut_lb, None)
            # de-clustered
            oos_d = day_equal_weight(ev, cut_oos, cut_lb)
            lock_d = day_equal_weight(ev, cut_lb, None)
            train_d = day_equal_weight(ev, idx[0], cut_oos)

            # skip if same-day cluster > 15 (market-wide crash days) — optional arm
            ev_small = [e for e in ev if e["cluster"] <= 10]
            oos_sm = split_stats(ev_small, cut_oos, cut_lb)
            lock_sm = split_stats(ev_small, cut_lb, None)

            # vs BTC same windows (beta control): long coin - long btc
            btc_a = btc.reindex(panel.index).ffill()
            excess = []
            for e in ev:
                if not (cut_oos <= e["t"] < cut_lb):
                    continue
                loc = panel.index.get_loc(e["t"])
                if not isinstance(loc, (int, np.integer)):
                    continue
                loc = int(loc)
                if loc + hold >= len(btc_a):
                    continue
                bfwd = float(btc_a.iloc[loc + hold] / btc_a.iloc[loc] - 1.0)
                # e['fwd'] already has cost; add back cost then subtract btc and re-cost once
                raw = e["fwd"] + cost
                excess.append(raw - bfwd - cost)
            oos_xs = pack(excess)

            # trials: we pre-registered 2 from deep + a few neighbors → count 6 configs * 2 costs
            n_trials = len(configs) * 2
            v_oos = verdict_arm(oos, n_trials=n_trials, train_mean=train.get("mean"), min_n=30)
            v_lock = verdict_arm(lock, n_trials=1, train_mean=oos.get("mean"), min_n=20)
            v_day = verdict_arm(oos_d, n_trials=n_trials, train_mean=train_d.get("mean"), min_n=20)

            row = {
                "id": f"{name}_{ctag}",
                "thr": thr,
                "hold": hold,
                "cost": cost,
                "n_events": len(ev),
                "train": train,
                "oos": oos,
                "lockbox": lock,
                "oos_day_ew": oos_d,
                "lock_day_ew": lock_d,
                "train_day_ew": train_d,
                "oos_cluster_le_10": oos_sm,
                "lock_cluster_le_10": lock_sm,
                "oos_excess_vs_btc": oos_xs,
                "verdict_oos": v_oos,
                "verdict_lockbox": v_lock,
                "verdict_day_ew": v_day,
            }
            rows.append(row)
            print(
                f"{row['id']:16s} train={train.get('mean'):+.3%} n={train.get('n')} | "
                f"oos={oos.get('mean'):+.3%} n={oos.get('n')} p_adj={v_oos.get('p_adj'):.3f} {v_oos['verdict']} | "
                f"lock={lock.get('mean')} n={lock.get('n')} {v_lock['verdict']} | "
                f"day_oos={oos_d.get('mean')} n={oos_d.get('n')} | "
                f"xs_btc={oos_xs.get('mean')}"
            )

    # Final promotion rule: CANDIDATE only if
    #  oos CANDIDATE + lockbox mean>0 + day-EW oos mean>0 + cost2x oos mean>0
    promoted = []
    for r in rows:
        if not r["id"].endswith("_c1"):
            continue
        base = r["id"][:-3]
        c2 = next((x for x in rows if x["id"] == base + "_c2"), None)
        ok = (
            r["verdict_oos"]["verdict"] == "CANDIDATE"
            and (r["lockbox"].get("mean") or 0) > 0
            and (r["oos_day_ew"].get("mean") or 0) > 0
            and c2 is not None
            and (c2["oos"].get("mean") or 0) > 0
            and (r["oos_excess_vs_btc"].get("mean") or 0) > 0
        )
        soft = (
            r["verdict_oos"]["verdict"] in ("CANDIDATE", "NOT_PROVEN")
            and (r["oos"].get("mean") or 0) > 0
            and (r["lockbox"].get("mean") or 0) > 0
        )
        r["promotion"] = "PROMOTE_PAPER" if ok else ("WATCHLIST" if soft else "NO")
        if ok:
            promoted.append(r["id"])

    out = {
        "meta": {
            "panel": list(panel.shape),
            "cuts": {"oos": str(cut_oos), "lockbox": str(cut_lb)},
            "cost_rt": COST_RT,
            "promotion_rule": (
                "oos CANDIDATE + lockbox>0 + day-EW oos>0 + cost2x oos>0 + excess_vs_btc>0"
            ),
        },
        "rows": rows,
        "promoted": promoted,
    }
    Path("logs/edge_hunt_validate_crash.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("\nPROMOTED:", promoted or "NONE")
    print("Wrote logs/edge_hunt_validate_crash.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
