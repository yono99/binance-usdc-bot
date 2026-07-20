#!/usr/bin/env python3
"""Edge hunt round 4 — dollar-neutral / relative-value / classic factors.

Reject pure directional short-alts that only work in markdown OOS (R3 lesson).
Require train mean > 0 for CANDIDATE (already in verdict_arm).
Also report 50/30/20 lockbox for any arm with OOS>0 and train>0.

  H-EH-40  12-1 monthly XS momentum LS (skip last month)
  H-EH-41  5d residual reverse LS (dollar neutral, non-overlap)
  H-EH-42  alt/BTC ratio z-score mean-reversion LS
  H-EH-43  volume-shock fade (vol z high + ret extreme → fade)
  H-EH-44  range-expansion fade (TR spike → fade 1-3d)
  H-EH-45  1m winner short only dollar? no — long bottom 30% residual 20d
  H-EH-46  skip-Friday / hold weekend residual (crypto 24/7 calendar)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack, verdict_arm


def three_way(idx, t, tr, oos, lock, cut_oos, cut_lb, pnl):
    if t < cut_oos:
        tr.append(pnl)
    elif t < cut_lb:
        oos.append(pnl)
    else:
        lock.append(pnl)


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=200, lookback_days=1600)
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = panel.index
    rets = panel.pct_change()
    b = btc.reindex(idx).ffill()
    b_ret = b.pct_change()
    resid = rets.sub(b_ret, axis=0)

    # 50/30/20 for lockbox reporting
    cut_oos = idx[int(T * 0.50)]
    cut_lb = idx[int(T * 0.80)]
    # also 70/30 for primary verdict (consistent with prior rounds)
    cut70 = idx[int(T * 0.70)]

    results = []
    print(f"panel {T}x{N} cut70={cut70} cut_oos={cut_oos} cut_lb={cut_lb}")

    def finish(rid, tr, oos, lock=None, n_trials=1, min_n=25):
        v = verdict_arm(pack(oos), n_trials=n_trials, train_mean=pack(tr).get("mean"), min_n=min_n)
        row = {
            "id": rid,
            "train": pack(tr),
            "oos": pack(oos),
            "lockbox": pack(lock or []),
            **v,
        }
        # soft promote flag if all three positive
        tm = row["train"].get("mean")
        om = row["oos"].get("mean")
        lm = row["lockbox"].get("mean")
        if (
            v["verdict"] == "CANDIDATE"
            and lm is not None
            and lm > 0
            and (row["lockbox"].get("n") or 0) >= 15
        ):
            row["soft_promote"] = "LOCKBOX_OK"
        else:
            row["soft_promote"] = "NO"
        results.append(row)
        print(
            f"{rid}: train={tm} oos={om} n_oos={row['oos'].get('n')} "
            f"lock={lm} {v['verdict']} promote={row['soft_promote']}"
        )
        return row

    # --- H-EH-40: 12-1 momentum (252d - last 21d) ---
    # use trading-day approx: 252≈12m, 21≈1m
    cum_12 = close / np.roll(close, 252, axis=0) - 1.0
    cum_1 = close / np.roll(close, 21, axis=0) - 1.0
    mom_12_1 = cum_12 - cum_1
    mom_12_1[:252] = np.nan
    for hold in (10, 21):
        tr, oos, lock = [], [], []
        for i in range(260, T - hold, hold):
            row = mom_12_1[i]
            valid = np.isfinite(row)
            if valid.sum() < 20:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row >= hi)  # high mom long
            S = valid & (row <= lo)
            if L.sum() < 3 or S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            three_way(idx, idx[i], tr, oos, lock, cut_oos, cut_lb, pnl)
        # also primary 70/30 for verdict consistency
        tr70, oos70 = [], []
        for i in range(260, T - hold, hold):
            row = mom_12_1[i]
            valid = np.isfinite(row)
            if valid.sum() < 20:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row >= hi)
            S = valid & (row <= lo)
            if L.sum() < 3 or S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            (tr70 if idx[i] < cut70 else oos70).append(pnl)
        finish(f"mom12_1_ls_h{hold}", tr70, oos70, lock, n_trials=2, min_n=15)

    # --- H-EH-41 residual reverse 5d LS ---
    cum_r5 = resid.rolling(5).sum()
    for hold in (1, 3, 5):
        tr70, oos70, lock = [], [], []
        for i in range(30, T - hold, hold):
            row = cum_r5.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 15:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row <= lo)  # reverse: long low residual
            S = valid & (row >= hi)
            if L.sum() < 3 or S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            (tr70 if idx[i] < cut70 else oos70).append(pnl)
            if idx[i] >= cut_oos and idx[i] < cut_lb:
                pass  # already in oos70 if cut70 inside
            if idx[i] >= cut_lb:
                lock.append(pnl)
        finish(f"resid5_rev_ls_h{hold}", tr70, oos70, lock, n_trials=3, min_n=25)

    # --- H-EH-42 alt/BTC ratio z mean-reversion ---
    # ratio = close_alt / btc; z of 20d log-ratio change
    ratio = panel.div(b, axis=0)
    log_r = np.log(ratio.replace(0, np.nan))
    z = (log_r - log_r.rolling(20).mean()) / (log_r.rolling(20).std() + 1e-12)
    for hold in (1, 3, 5):
        tr70, oos70, lock = [], [], []
        for i in range(25, T - hold, hold):
            row = z.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 15:
                continue
            # long low z (cheap vs BTC), short high z
            lo = np.nanpercentile(row[valid], 20)
            hi = np.nanpercentile(row[valid], 80)
            L = valid & (row <= lo)
            S = valid & (row >= hi)
            if L.sum() < 3 or S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            # residual-ish: LS on alts (not pure BTC hedge)
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            (tr70 if idx[i] < cut70 else oos70).append(pnl)
            if idx[i] >= cut_lb:
                lock.append(pnl)
        finish(f"ratio_z_rev_ls_h{hold}", tr70, oos70, lock, n_trials=3, min_n=25)

    # --- H-EH-43 volume-shock fade (need volume panel) ---
    try:
        from bot.xsectional import volume_panel

        vol = volume_panel(Path("data/snap"), list(panel.columns), panel.index)
        vol = vol.reindex(index=panel.index, columns=panel.columns)
        vol_z = (vol - vol.rolling(20).mean()) / (vol.rolling(20).std() + 1e-12)
        r1 = rets
        for hold in (1, 3):
            tr70, oos70, lock = [], [], []
            for i in range(25, T - hold):
                vz = vol_z.iloc[i].to_numpy(dtype=float)
                rr = r1.iloc[i].to_numpy(dtype=float)
                valid = np.isfinite(vz) & np.isfinite(rr)
                # volume spike + extreme return → fade
                spike = valid & (vz >= 2.0) & (np.abs(rr) >= 0.05)
                if spike.sum() < 2:
                    continue
                # long if rr negative, short if rr positive
                signs = -np.sign(rr)
                fwd = close[i + hold] / close[i] - 1.0
                pnl = float(np.nanmean(fwd[spike] * signs[spike])) - COST_RT
                (tr70 if idx[i] < cut70 else oos70).append(pnl)
                if idx[i] >= cut_lb:
                    lock.append(pnl)
            finish(f"volshock_fade_h{hold}", tr70, oos70, lock, n_trials=2, min_n=20)
    except Exception as e:
        print("volshock skip:", e)
        finish("volshock_fade_skip", [], [], [], n_trials=1, min_n=1)

    # --- H-EH-44 range expansion fade (high/low if available else |ret| proxy) ---
    # proxy: |ret| > 2*rolling std → fade
    rstd = rets.rolling(20).std()
    for hold in (1, 3):
        tr70, oos70, lock = [], [], []
        for i in range(25, T - hold):
            rr = rets.iloc[i].to_numpy(dtype=float)
            sd = rstd.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(rr) & np.isfinite(sd) & (sd > 0)
            big = valid & (np.abs(rr) >= 2.0 * sd)
            if big.sum() < 2:
                continue
            signs = -np.sign(rr)
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[big] * signs[big])) - COST_RT
            (tr70 if idx[i] < cut70 else oos70).append(pnl)
            if idx[i] >= cut_lb:
                lock.append(pnl)
        finish(f"range_exp_fade_h{hold}", tr70, oos70, lock, n_trials=2, min_n=30)

    # --- H-EH-45 long bottom residual 20d (asymmetric value) ---
    cum_r20 = resid.rolling(20).sum()
    for hold in (5, 10):
        tr70, oos70, lock = [], [], []
        for i in range(30, T - hold, hold):
            row = cum_r20.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 15:
                continue
            lo = np.nanpercentile(row[valid], 20)
            L = valid & (row <= lo)
            if L.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L])) - COST_RT
            (tr70 if idx[i] < cut70 else oos70).append(pnl)
            if idx[i] >= cut_lb:
                lock.append(pnl)
        finish(f"long_resid_loser20_h{hold}", tr70, oos70, lock, n_trials=2, min_n=20)

    # --- H-EH-46 weekend residual: Fri close → Mon close long EW residual ---
    # crypto 24/7: Fri→Sat, Sat→Sun, Sun→Mon as "weekend" basket
    tr70, oos70, lock = [], [], []
    for i in range(5, T - 1):
        dow = idx[i].dayofweek  # Mon=0
        if dow not in (4, 5, 6):  # Fri Sat Sun
            continue
        # long residual losers over weekend bar
        row = resid.iloc[i].to_numpy(dtype=float) if i > 0 else None
        fwd = close[i + 1] / close[i] - 1.0
        # simple: long EW alts weekend
        pnl = float(np.nanmean(fwd)) - COST_RT
        (tr70 if idx[i] < cut70 else oos70).append(pnl)
        if idx[i] >= cut_lb:
            lock.append(pnl)
    finish("weekend_long_ew_1d", tr70, oos70, lock, n_trials=1, min_n=40)

    # weekend SHORT (opposite)
    tr70, oos70, lock = [], [], []
    for i in range(5, T - 1):
        dow = idx[i].dayofweek
        if dow not in (4, 5, 6):
            continue
        fwd = close[i + 1] / close[i] - 1.0
        pnl = float(-np.nanmean(fwd)) - COST_RT
        (tr70 if idx[i] < cut70 else oos70).append(pnl)
        if idx[i] >= cut_lb:
            lock.append(pnl)
    finish("weekend_short_ew_1d", tr70, oos70, lock, n_trials=1, min_n=40)

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    soft = [r for r in results if r.get("soft_promote") == "LOCKBOX_OK"]
    out = {
        "meta": {
            "panel": list(panel.shape),
            "cut70": str(cut70),
            "cut_oos": str(cut_oos),
            "cut_lb": str(cut_lb),
            "cost_rt": COST_RT,
            "lesson": "R3: directional short-alts OOS+/train- = regime; R4 = neutral/RV",
        },
        "results": results,
        "candidates": candidates,
        "soft_promote": [r["id"] for r in soft],
        "n_candidates": len(candidates),
    }
    Path("logs/edge_hunt_round4.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("CANDIDATES", len(candidates), "SOFT", [r["id"] for r in soft])
    pos = sorted(
        [r for r in results if (r["oos"].get("mean") or -9) > 0],
        key=lambda x: -(x["oos"]["mean"] or 0),
    )
    print("TOP OOS+")
    for r in pos[:15]:
        print(
            f"  {r['id']}: oos={r['oos']['mean']:+.4%} n={r['oos']['n']} "
            f"train={r['train'].get('mean')} lock={r['lockbox'].get('mean')} {r['verdict']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
