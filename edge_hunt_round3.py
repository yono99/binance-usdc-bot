#!/usr/bin/env python3
"""Edge hunt round 3 — structurally different from pure OHLCV retreads.

Uses Deribit DVOL (non-OHLCV) + regime constructions:
  H-EH-32  high DVOL → short EW alts (risk-off)
  H-EH-33  DVOL rising (5d) → short high-beta alts
  H-EH-34  IV-RV gap (DVOL − ann. RV30 BTC) high → short alts / long BTC
  H-EH-35  euphoria: BTC +5% day → short alts next 1–3d
  H-EH-36  corr-breakdown: low avg pairwise corr → XS reverse; high corr → short alts
  H-EH-37  highvol short only on high-DVOL days (combine R2 lean + DVOL gate)
  H-EH-38  BTC dump + high DVOL → short alts hold3 (stress short, not bounce)

Judge: train/OOS chronological 70/30, cost RT 0.18%, verdict_arm + multi-trial.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack, verdict_arm


def load_dvol() -> pd.Series:
    p = Path("data/snap_dvol_btc_1d.pkl")
    s = pd.read_pickle(p)
    if not isinstance(s, pd.Series):
        s = pd.Series(s)
    s.index = pd.to_datetime(s.index, utc=True)
    return s.sort_index().astype(float)


def rolling_avg_corr(rets: pd.DataFrame, win: int = 20) -> pd.Series:
    """Approximate average pairwise correlation via var of cross-section.

    avg pairwise corr ≈ (var(mean_i r) * N^2 - sum var_i) / (sum var_i * (N-1))
    for equal-weight; use simpler: corr of each col vs EW, mean abs.
    """
    ew = rets.mean(axis=1)
    out = []
    idx = rets.index
    arr = rets.to_numpy(dtype=float)
    ew_a = ew.to_numpy(dtype=float)
    T, N = arr.shape
    for i in range(T):
        if i < win:
            out.append(np.nan)
            continue
        block = arr[i - win + 1 : i + 1]
        ewb = ew_a[i - win + 1 : i + 1]
        cors = []
        for j in range(N):
            x = block[:, j]
            m = np.isfinite(x) & np.isfinite(ewb)
            if m.sum() < win // 2:
                continue
            if np.std(x[m]) < 1e-12 or np.std(ewb[m]) < 1e-12:
                continue
            cors.append(float(np.corrcoef(x[m], ewb[m])[0, 1]))
        out.append(float(np.nanmean(cors)) if cors else np.nan)
    return pd.Series(out, index=idx)


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=200, lookback_days=1600)
    dvol = load_dvol()
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = panel.index
    rets = panel.pct_change()
    b = btc.reindex(idx).ffill()
    b_ret = b.pct_change()
    d = dvol.reindex(idx).ffill()
    # annualized RV30 BTC from daily
    rv30 = b_ret.rolling(30).std() * np.sqrt(365.0) * 100.0  # DVOL is in %
    gap = d - rv30  # IV - RV in vol points
    vol20 = rets.rolling(20).std()
    # beta proxy: 60d corr * std ratio vs BTC
    beta = rets.rolling(60).cov(b_ret) / (b_ret.rolling(60).var() + 1e-12)
    avg_corr = rolling_avg_corr(rets, 20)

    cut = idx[int(T * 0.70)]
    results = []
    print(f"panel {T}x{N} cut={cut} dvol_cov={d.notna().sum()}")

    def add(row: dict) -> None:
        results.append(row)
        print(
            f"{row['id']}: oos={row['oos'].get('mean')} n={row['oos'].get('n')} "
            f"{row['verdict']}"
        )

    # --- H-EH-32 high DVOL short alts ---
    d_hi = d > d.rolling(100).quantile(0.75)
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(100, T - hold):
            if not bool(d_hi.iloc[i]):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd)) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=3, train_mean=pack(tr).get("mean"), min_n=30)
        add({"id": f"dvol_hi_short_alts_h{hold}", "train": pack(tr), "oos": pack(oos), **v})

    # --- H-EH-33 DVOL rising short high-beta ---
    d_up = d.diff(5) > 0
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(100, T - hold):
            if not bool(d_up.iloc[i]):
                continue
            rowb = beta.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(rowb)
            if valid.sum() < 10:
                continue
            thr = np.nanpercentile(rowb[valid], 70)
            hi = valid & (rowb >= thr)
            if hi.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd[hi])) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=3, train_mean=pack(tr).get("mean"), min_n=30)
        add({"id": f"dvol_up_short_hibeta_h{hold}", "train": pack(tr), "oos": pack(oos), **v})

    # --- H-EH-34 IV-RV gap high ---
    gap_hi = gap > gap.rolling(100).quantile(0.75)
    for hold in (1, 3, 5):
        tr_a, oos_a, tr_b, oos_b = [], [], [], []
        for i in range(120, T - hold):
            if not bool(gap_hi.iloc[i]):
                continue
            # short alts EW
            fwd = close[i + hold] / close[i] - 1.0
            pnl_a = float(-np.nanmean(fwd)) - COST_RT
            # long BTC
            pnl_b = float(b.iloc[i + hold] / b.iloc[i] - 1.0) - COST_RT
            t = idx[i]
            (tr_a if t < cut else oos_a).append(pnl_a)
            (tr_b if t < cut else oos_b).append(pnl_b)
        for name, tr, oos, nt in (
            (f"ivrv_hi_short_alts_h{hold}", tr_a, oos_a, 3),
            (f"ivrv_hi_long_btc_h{hold}", tr_b, oos_b, 3),
        ):
            v = verdict_arm(pack(oos), n_trials=nt, train_mean=pack(tr).get("mean"), min_n=25)
            add({"id": name, "train": pack(tr), "oos": pack(oos), **v})

    # --- H-EH-35 euphoria fade ---
    for thr, hold in ((0.05, 1), (0.05, 3), (0.07, 1), (0.07, 3)):
        tr, oos = [], []
        for i in range(5, T - hold):
            if not (np.isfinite(b_ret.iloc[i]) and b_ret.iloc[i] >= thr):
                continue
            # enter next day open ≈ close i (daily) → hold from i
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd)) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=4, train_mean=pack(tr).get("mean"), min_n=15)
        add({"id": f"euphoria_btc{int(thr*100)}_short_alts_h{hold}", "train": pack(tr), "oos": pack(oos), **v})

    # --- H-EH-36 corr regime ---
    c = avg_corr
    c_lo = c < c.rolling(100).quantile(0.30)
    c_hi = c > c.rolling(100).quantile(0.70)
    # low corr → XS reverse 3d
    cum3 = (1 + rets).rolling(3).apply(lambda x: np.prod(x) - 1, raw=True)
    for hold in (1, 3, 5):
        tr, oos = [], []
        for i in range(100, T - hold, hold):
            if not bool(c_lo.iloc[i]):
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
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=3, train_mean=pack(tr).get("mean"), min_n=20)
        add({"id": f"lowcorr_st_rev_ls_h{hold}", "train": pack(tr), "oos": pack(oos), **v})

    for hold in (1, 3):
        tr, oos = [], []
        for i in range(100, T - hold):
            if not bool(c_hi.iloc[i]):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd)) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=2, train_mean=pack(tr).get("mean"), min_n=30)
        add({"id": f"hicorr_short_alts_h{hold}", "train": pack(tr), "oos": pack(oos), **v})

    # --- H-EH-37 highvol short gated by high DVOL ---
    d_hi2 = d > d.rolling(100).quantile(0.60)
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(100, T - hold, hold):
            if not bool(d_hi2.iloc[i]):
                continue
            row = vol20.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row) & (row > 0)
            if valid.sum() < 15:
                continue
            thr = np.nanpercentile(row[valid], 80)
            S = valid & (row >= thr)
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd[S])) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=2, train_mean=pack(tr).get("mean"), min_n=15)
        add({"id": f"dvolgate_highvol_short_h{hold}", "train": pack(tr), "oos": pack(oos), **v})

    # --- H-EH-38 BTC dump + high DVOL → short alts (no bounce bet) ---
    for dump_thr, hold in ((-0.02, 1), (-0.02, 3), (-0.03, 3)):
        tr, oos = [], []
        for i in range(100, T - hold):
            if not (np.isfinite(b_ret.iloc[i]) and b_ret.iloc[i] <= dump_thr):
                continue
            if not bool(d_hi.iloc[i]):
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(-np.nanmean(fwd)) - COST_RT
            t = idx[i]
            (tr if t < cut else oos).append(pnl)
        v = verdict_arm(pack(oos), n_trials=3, train_mean=pack(tr).get("mean"), min_n=15)
        add({
            "id": f"dump{int(abs(dump_thr)*100)}_dvolhi_short_alts_h{hold}",
            "train": pack(tr),
            "oos": pack(oos),
            **v,
        })

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    out = {
        "meta": {
            "panel": list(panel.shape),
            "cut": str(cut),
            "cost_rt": COST_RT,
            "dvol_range": [str(d.dropna().index.min()), str(d.dropna().index.max())],
        },
        "results": results,
        "candidates": candidates,
        "n_candidates": len(candidates),
    }
    Path("logs/edge_hunt_round3.json").write_text(
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
    for r in pos[:12]:
        print(
            f"  {r['id']}: {r['oos']['mean']:+.4%} n={r['oos']['n']} "
            f"train={r['train'].get('mean')} {r['verdict']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
