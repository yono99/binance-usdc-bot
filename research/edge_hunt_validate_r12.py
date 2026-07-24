#!/usr/bin/env python3
"""Strict validate R12 top leans — 50/30/20 + cost×2 + family Bonferroni.

Pre-registered families (NOT re-mining full R12):
  F1 breakout: break_up_50 h5/h10, break_dn_short_50 h5/h10  (4 arms)
  F2 range_expand_fade h1/h3/h5                              (3 arms)
  F3 volshock_hi_fade h1/h3/h5                               (3 arms)

Promotion: OOS CANDIDATE (family trials) + lockbox mean>0 + cost2x OOS mean>0
           + train mean>0 + n_oos>=30.

  PYTHONPATH=. python research/edge_hunt_validate_r12.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_RESEARCH = str(Path(__file__).resolve().parent)
_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [_RESEARCH, str(_ROOT)]

from edge_hunt import COST_RT, load_daily, pack, verdict_arm  # noqa: E402
from edge_hunt_round12 import day_ew, hold_fwd, load_ohlc_vol  # noqa: E402


def split_idx(idx, f_oos=0.30, f_lock=0.20):
    n = len(idx)
    i_lock = int(n * (1 - f_lock))
    i_oos = int(n * (1 - f_oos - f_lock))
    return idx[i_oos], idx[i_lock]


def pack_split(series: pd.Series, cut_oos, cut_lock):
    tr = series[series.index < cut_oos]
    oos = series[(series.index >= cut_oos) & (series.index < cut_lock)]
    lock = series[series.index >= cut_lock]
    return pack(tr.to_numpy()), pack(oos.to_numpy()), pack(lock.to_numpy())


def day_series(mask, fwd, cost):
    day_rets, times = [], []
    for t in fwd.index:
        m = mask.loc[t]
        if not hasattr(m, "any") or not bool(m.fillna(False).any()):
            continue
        vals = fwd.loc[t, m.fillna(False)].replace([np.inf, -np.inf], np.nan).dropna()
        if len(vals) < 1:
            continue
        day_rets.append(float(vals.mean()) - cost)
        times.append(t)
    if not day_rets:
        return pd.Series(dtype=float)
    return pd.Series(day_rets, index=pd.DatetimeIndex(times))


def main() -> int:
    snap = Path("data/snap")
    panel, btc = load_daily(snap, max_alts=200, lookback_days=2000, min_bars=200)
    vol, high, low = load_ohlc_vol(snap, panel)
    ret = panel.pct_change()
    vol_z = (vol - vol.rolling(20).mean()) / (vol.rolling(20).std() + 1e-12)
    rng = (high - low) / panel.replace(0, np.nan)
    rng_z = (rng - rng.rolling(20).mean()) / (rng.rolling(20).std() + 1e-12)
    cut_oos, cut_lock = split_idx(panel.index)
    print("panel", panel.shape, "cuts", cut_oos.date(), cut_lock.date())

    rows = []
    promoted = []

    # Family F1 breakout 50
    fam1_n = 4
    for h in (5, 10):
        for side, win in (("up", 50), ("dn", 50)):
            fwd = hold_fwd(panel, h)
            hh = panel.rolling(win).max().shift(1)
            ll = panel.rolling(win).min().shift(1)
            if side == "up":
                mask = (panel > hh) & panel.notna()
                s = day_series(mask, fwd, COST_RT)
                sid = f"break_up_{win}_h{h}"
            else:
                mask = (panel < ll) & panel.notna()
                s = day_series(mask, -fwd, COST_RT)
                sid = f"break_dn_short_{win}_h{h}"
            tr, oos, lock = pack_split(s, cut_oos, cut_lock)
            v = verdict_arm(oos, n_trials=fam1_n, train_mean=tr.get("mean"))
            # cost×2
            s2 = day_series(mask, fwd if side == "up" else -fwd, COST_RT * 2)
            _, oos2, lock2 = pack_split(s2, cut_oos, cut_lock)
            promo = "NO"
            if (
                v["verdict"] == "CANDIDATE"
                and (lock.get("mean") or 0) > 0
                and (oos2.get("mean") or 0) > 0
                and (tr.get("mean") or 0) > 0
            ):
                promo = "PROMOTE_PAPER_CANDIDATE"
                promoted.append(sid)
            rows.append(
                {
                    "id": sid,
                    "family": "F1_breakout50",
                    "train": tr,
                    "oos": oos,
                    "lockbox": lock,
                    "oos_cost2x": oos2,
                    "verdict_oos": v,
                    "promotion": promo,
                }
            )
            print(
                f"{sid}: tr={tr.get('mean')} oos={oos.get('mean')} n={oos.get('n')} "
                f"lock={lock.get('mean')} c2={oos2.get('mean')} {v['verdict']} promo={promo}"
            )

    # F2 range expand fade
    fam2_n = 3
    for h in (1, 3, 5):
        fwd = hold_fwd(panel, h)
        mask = (rng_z > 1.5) & panel.notna()
        signed = -np.sign(ret) * fwd
        s = day_series(mask, signed, COST_RT)
        # day_series expects mask+fwd; signed is already the payoff — use all-true mask on signed panel
        # rebuild: put signed into a 1-col style via day_ew helper
        # simpler: compute series manually
        day_rets, times = [], []
        for t in panel.index:
            m = mask.loc[t]
            if not hasattr(m, "any") or not bool(m.fillna(False).any()):
                continue
            vals = signed.loc[t, m.fillna(False)].replace([np.inf, -np.inf], np.nan).dropna()
            if len(vals) < 1:
                continue
            day_rets.append(float(vals.mean()) - COST_RT)
            times.append(t)
        s = pd.Series(day_rets, index=pd.DatetimeIndex(times)) if day_rets else pd.Series(dtype=float)
        tr, oos, lock = pack_split(s, cut_oos, cut_lock)
        v = verdict_arm(oos, n_trials=fam2_n, train_mean=tr.get("mean"))
        day_rets2 = [x - COST_RT for x in day_rets]  # already paid 1x in series; for 2x recompute
        day_rets2, times2 = [], []
        for t in panel.index:
            m = mask.loc[t]
            if not hasattr(m, "any") or not bool(m.fillna(False).any()):
                continue
            vals = signed.loc[t, m.fillna(False)].replace([np.inf, -np.inf], np.nan).dropna()
            if len(vals) < 1:
                continue
            day_rets2.append(float(vals.mean()) - 2 * COST_RT)
            times2.append(t)
        s2 = pd.Series(day_rets2, index=pd.DatetimeIndex(times2)) if day_rets2 else pd.Series(dtype=float)
        _, oos2, _ = pack_split(s2, cut_oos, cut_lock)
        sid = f"range_expand_fade_h{h}"
        promo = "NO"
        if (
            v["verdict"] == "CANDIDATE"
            and (lock.get("mean") or 0) > 0
            and (oos2.get("mean") or 0) > 0
            and (tr.get("mean") or 0) > 0
        ):
            promo = "PROMOTE_PAPER_CANDIDATE"
            promoted.append(sid)
        rows.append(
            {
                "id": sid,
                "family": "F2_range_fade",
                "train": tr,
                "oos": oos,
                "lockbox": lock,
                "oos_cost2x": oos2,
                "verdict_oos": v,
                "promotion": promo,
            }
        )
        print(
            f"{sid}: tr={tr.get('mean')} oos={oos.get('mean')} n={oos.get('n')} "
            f"lock={lock.get('mean')} c2={oos2.get('mean')} {v['verdict']} promo={promo}"
        )

    # F3 volshock hi fade
    fam3_n = 3
    for h in (1, 3, 5):
        fwd = hold_fwd(panel, h)
        mask = (vol_z > 1.0) & (ret.abs() > 0.03) & panel.notna()
        signed = -np.sign(ret) * fwd
        day_rets, times = [], []
        for t in panel.index:
            m = mask.loc[t]
            if not hasattr(m, "any") or not bool(m.fillna(False).any()):
                continue
            vals = signed.loc[t, m.fillna(False)].replace([np.inf, -np.inf], np.nan).dropna()
            if len(vals) < 1:
                continue
            day_rets.append(float(vals.mean()) - COST_RT)
            times.append(t)
        s = pd.Series(day_rets, index=pd.DatetimeIndex(times)) if day_rets else pd.Series(dtype=float)
        tr, oos, lock = pack_split(s, cut_oos, cut_lock)
        v = verdict_arm(oos, n_trials=fam3_n, train_mean=tr.get("mean"))
        day_rets2, times2 = [], []
        for t in panel.index:
            m = mask.loc[t]
            if not hasattr(m, "any") or not bool(m.fillna(False).any()):
                continue
            vals = signed.loc[t, m.fillna(False)].replace([np.inf, -np.inf], np.nan).dropna()
            if len(vals) < 1:
                continue
            day_rets2.append(float(vals.mean()) - 2 * COST_RT)
            times2.append(t)
        s2 = pd.Series(day_rets2, index=pd.DatetimeIndex(times2)) if day_rets2 else pd.Series(dtype=float)
        _, oos2, _ = pack_split(s2, cut_oos, cut_lock)
        sid = f"volshock_hi_fade_h{h}"
        promo = "NO"
        if (
            v["verdict"] == "CANDIDATE"
            and (lock.get("mean") or 0) > 0
            and (oos2.get("mean") or 0) > 0
            and (tr.get("mean") or 0) > 0
        ):
            promo = "PROMOTE_PAPER_CANDIDATE"
            promoted.append(sid)
        rows.append(
            {
                "id": sid,
                "family": "F3_volshock_fade",
                "train": tr,
                "oos": oos,
                "lockbox": lock,
                "oos_cost2x": oos2,
                "verdict_oos": v,
                "promotion": promo,
            }
        )
        print(
            f"{sid}: tr={tr.get('mean')} oos={oos.get('mean')} n={oos.get('n')} "
            f"lock={lock.get('mean')} c2={oos2.get('mean')} {v['verdict']} promo={promo}"
        )

    out = {
        "meta": {
            "cuts": {"oos": str(cut_oos), "lock": str(cut_lock)},
            "panel": list(panel.shape),
            "cost_rt": COST_RT,
            "rule": "CANDIDATE family-Bonferroni + lock>0 + cost2x OOS>0 + train>0",
        },
        "rows": rows,
        "promoted": promoted,
    }
    Path("logs/edge_hunt_validate_r12.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("PROMOTED:", promoted or "NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
