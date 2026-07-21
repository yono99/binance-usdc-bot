#!/usr/bin/env python3
"""Edge hunt round 5 — lead-lag, liquidity, downside beta, sequences.

  H-EH-50  BTC lead: long alts when BTC+ yesterday (EW / strong)
  H-EH-51  BTC lead residual: long positive residual after BTC up day
  H-EH-52  liquidity premium: long high-vol$ short low-vol$ (20d)
  H-EH-53  downside beta: short high down-beta vs BTC
  H-EH-54  idio vol: short high residual vol (lottery premium)
  H-EH-55  consecutive down days (>=3) fade long vs continue short
  H-EH-56  BTC 5d mom continuation vs reverse on alts EW
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


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=200, lookback_days=1600)
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = panel.index
    rets = panel.pct_change()
    b = btc.reindex(idx).ffill()
    b_ret = b.pct_change()
    resid = rets.sub(b_ret, axis=0)
    cut = idx[int(T * 0.70)]
    results = []
    print(f"panel {T}x{N} cut={cut}")

    def add(rid, tr, oos, n_trials=1, min_n=30):
        v = verdict_arm(pack(oos), n_trials=n_trials, train_mean=pack(tr).get("mean"), min_n=min_n)
        row = {"id": rid, "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(
            f"{rid}: train={row['train'].get('mean')} oos={row['oos'].get('mean')} "
            f"n={row['oos'].get('n')} {v['verdict']}"
        )

    # --- H-EH-50 BTC up day → long alts next day ---
    for hold in (1, 3):
        tr, oos = [], []
        for i in range(2, T - hold):
            if not (np.isfinite(b_ret.iloc[i - 1]) and b_ret.iloc[i - 1] > 0.01):
                continue
            fwd = close[i + hold - 1] / close[i - 1 + 1] - 1.0 if hold > 0 else None
            # enter at close of day after signal: signal at i-1, enter i, hold to i+hold
            # simpler: signal at i (BTC ret day i), trade alts from i to i+hold
            pass
        # rewrite cleanly
        tr, oos = [], []
        for i in range(1, T - hold):
            if not (np.isfinite(b_ret.iloc[i]) and b_ret.iloc[i] > 0.01):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd)) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"btc_up1pct_long_alts_h{hold}", tr, oos, n_trials=2, min_n=25)

        tr, oos = [], []
        for i in range(1, T - hold):
            if not (np.isfinite(b_ret.iloc[i]) and b_ret.iloc[i] < -0.01):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd)) - COST_RT  # short after BTC down
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"btc_dn1pct_short_alts_h{hold}", tr, oos, n_trials=2, min_n=25)

    # --- H-EH-51 residual lead: after BTC up, long positive residual alts ---
    for hold in (1, 3):
        tr, oos = [], []
        for i in range(5, T - hold):
            if not (np.isfinite(b_ret.iloc[i]) and b_ret.iloc[i] > 0.005):
                continue
            row = resid.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 10:
                continue
            thr = np.nanpercentile(row[valid], 70)
            L = valid & (row >= thr)
            if L.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"btc_up_long_hiresid_h{hold}", tr, oos, n_trials=2, min_n=25)

    # --- H-EH-52 liquidity premium: use |ret| * proxy via volume if missing use dollar proxy ---
    # dollar volume proxy: close * volume if we can load OHLCV from snaps
    # fallback: turnover proxy = abs(ret) * rolling mean abs ret (activity)
    activity = rets.abs().rolling(20).mean()
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = activity.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row) & (row > 0)
            if valid.sum() < 15:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row >= hi)  # high activity long?
            S = valid & (row <= lo)
            if L.sum() < 3 or S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"activity_premium_ls_h{hold}", tr, oos, n_trials=2, min_n=20)

        # reverse: low activity premium (illiquidity premium)
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = activity.iloc[i].to_numpy(dtype=float)
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
        add(f"illiquidity_premium_ls_h{hold}", tr, oos, n_trials=2, min_n=20)

    # --- H-EH-53 downside beta (60d) ---
    # beta on negative BTC days only
    down_beta = pd.DataFrame(index=idx, columns=panel.columns, dtype=float)
    b_arr = b_ret.to_numpy(dtype=float)
    r_arr = rets.to_numpy(dtype=float)
    win = 60
    for i in range(win, T):
        bb = b_arr[i - win : i]
        mask = np.isfinite(bb) & (bb < 0)
        if mask.sum() < 10:
            continue
        for j in range(N):
            aa = r_arr[i - win : i, j]
            m = mask & np.isfinite(aa)
            if m.sum() < 8:
                continue
            # beta = cov/var on down days
            x, y = bb[m], aa[m]
            vx = np.var(x)
            if vx < 1e-12:
                continue
            down_beta.iloc[i, j] = float(np.cov(x, y)[0, 1] / vx)

    for hold in (5, 10):
        tr, oos = [], []
        for i in range(win + 5, T - hold, hold):
            row = down_beta.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 15:
                continue
            thr = np.nanpercentile(row[valid], 80)
            S = valid & (row >= thr)  # high down-beta short
            if S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd[S])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"short_high_downbeta_h{hold}", tr, oos, n_trials=2, min_n=15)

        # LS: long low downbeta short high
        tr, oos = [], []
        for i in range(win + 5, T - hold, hold):
            row = down_beta.iloc[i].to_numpy(dtype=float)
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
        add(f"downbeta_ls_h{hold}", tr, oos, n_trials=2, min_n=15)

    # --- H-EH-54 idio vol short (lottery) ---
    idvol = resid.rolling(20).std()
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = idvol.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row) & (row > 0)
            if valid.sum() < 15:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row <= lo)  # low idvol long
            S = valid & (row >= hi)
            if L.sum() < 3 or S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"low_idiovol_ls_h{hold}", tr, oos, n_trials=2, min_n=20)

    # --- H-EH-55 consecutive down days ---
    down = (rets < 0).astype(float)
    # streak: count consecutive downs ending at i
    streak = pd.DataFrame(0.0, index=idx, columns=panel.columns)
    for j, col in enumerate(panel.columns):
        s = 0
        for i in range(T):
            if rets.iloc[i, j] < 0:
                s += 1
            else:
                s = 0
            streak.iloc[i, j] = s

    for hold in (1, 3):
        # fade: long names with streak>=3
        tr, oos = [], []
        for i in range(5, T - hold):
            row = streak.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row) & (row >= 3)
            if valid.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[valid])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"streak3_fade_long_h{hold}", tr, oos, n_trials=2, min_n=30)

        # continue: short streak>=3
        tr, oos = [], []
        for i in range(5, T - hold):
            row = streak.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row) & (row >= 3)
            if valid.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd[valid])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"streak3_continue_short_h{hold}", tr, oos, n_trials=2, min_n=30)

    # --- H-EH-56 BTC 5d mom → alts EW ---
    b5 = b.pct_change(5)
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(10, T - hold):
            if not (np.isfinite(b5.iloc[i]) and b5.iloc[i] > 0.05):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd)) - COST_RT  # long alts after strong BTC 5d
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"btc5up_long_alts_h{hold}", tr, oos, n_trials=3, min_n=15)

        tr, oos = [], []
        for i in range(10, T - hold):
            if not (np.isfinite(b5.iloc[i]) and b5.iloc[i] > 0.05):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd)) - COST_RT  # fade
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"btc5up_fade_alts_h{hold}", tr, oos, n_trials=3, min_n=15)

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    out = {
        "meta": {"panel": list(panel.shape), "cut": str(cut), "cost_rt": COST_RT},
        "results": results,
        "candidates": candidates,
        "n_candidates": len(candidates),
    }
    Path("logs/edge_hunt_round5.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("CANDIDATES", len(candidates))
    for c in candidates:
        print(" ", c["id"], c["oos"].get("mean"), c["reason"])
    pos = sorted(
        [r for r in results if (r["oos"].get("mean") or -9) > 0],
        key=lambda x: -(x["oos"]["mean"] or 0),
    )
    print("TOP OOS+")
    for r in pos[:15]:
        print(
            f"  {r['id']}: {r['oos']['mean']:+.4%} n={r['oos']['n']} "
            f"train={r['train'].get('mean')} {r['verdict']}"
        )
    # train+ and oos+ interest
    both = [
        r
        for r in results
        if (r["train"].get("mean") or -1) > 0 and (r["oos"].get("mean") or -1) > 0
    ]
    print("TRAIN+ & OOS+", len(both))
    for r in both:
        print(f"  {r['id']}: train={r['train']['mean']:+.4%} oos={r['oos']['mean']:+.4%} {r['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
