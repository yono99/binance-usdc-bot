#!/usr/bin/env python3
"""Edge hunt round 7 — true dollar-volume + meta risk FILTER (not entry alpha).

  H-EH-70  dollar-volume premium LS (20d mean $vol)
  H-EH-71  illiquidity premium (low $vol long)
  H-EH-72  volume surprise fade (vol z high + ret extreme)
  H-EH-73  Amihud illiquidity proxy |ret|/volume sort
  H-EH-74  META risk filter: skip entries on high avg-corr days
           (measure: does filter raise day-EW of baseline ST-rev or reduce left tail?)
  H-EH-75  META: skip high BTC-vol days on long residual loser basket
  H-EH-76  high-low range vol premium (Parkinson) LS

Judge: train+ required for CANDIDATE. Meta filters report risk deltas too.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack, verdict_arm


def load_panels(snap: Path, max_alts: int = 200, lookback_days: int = 1600):
    """Return close panel, volume panel, btc close — same universe as load_daily."""
    close_panel, btc = load_daily(snap, max_alts=max_alts, lookback_days=lookback_days)
    # rebuild volume aligned to close_panel columns
    paths = {p.stem: p for p in snap.glob("*__1d.pkl")}
    vol = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)
    high = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)
    low = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)
    for col in close_panel.columns:
        # reverse sym → stem heuristic
        # col like "ETH/USDT:USDT" or from load_daily encoding
        base = col.replace("/", "_").replace(":", "_")
        # try common patterns
        candidates = [
            f"{base}__1d",
            base.replace("_USDT_USDT", "_USDT_USDT") + "",
        ]
        # brute: match by base coin
        coin = col.split("/")[0] if "/" in col else col.split("_")[0]
        hit = None
        for stem, p in paths.items():
            if stem.upper().startswith(coin.upper() + "_") and "BTCDOM" not in stem.upper():
                # prefer USDT dual
                if hit is None or "USDT_USDT" in stem:
                    hit = p
        if hit is None:
            continue
        try:
            df = pd.read_pickle(hit)
        except Exception:
            continue
        df = df.reindex(close_panel.index)
        if "volume" in df.columns:
            vol[col] = df["volume"].astype(float)
        if "high" in df.columns:
            high[col] = df["high"].astype(float)
        if "low" in df.columns:
            low[col] = df["low"].astype(float)
    return close_panel, vol, high, low, btc


def main() -> int:
    close_panel, vol, high, low, btc = load_panels(Path("data/snap"))
    close = close_panel.to_numpy(dtype=float)
    T, N = close.shape
    idx = close_panel.index
    rets = close_panel.pct_change()
    b = btc.reindex(idx).ffill()
    b_ret = b.pct_change()
    dvol = (vol * close_panel).astype(float)  # dollar volume
    dvol20 = dvol.rolling(20).mean()
    vol_z = (vol - vol.rolling(20).mean()) / (vol.rolling(20).std() + 1e-12)
    # Amihud: |ret| / volume
    amihud = rets.abs() / (vol.replace(0, np.nan))
    amihud20 = amihud.rolling(20).mean()
    # Parkinson vol
    hl = (np.log(high / low.replace(0, np.nan))) ** 2
    park = (hl.rolling(20).mean() / (4 * np.log(2))) ** 0.5

    # avg corr proxy
    ew = rets.mean(axis=1)
    # rolling corr each vs EW mean
    avg_corr = []
    for i in range(T):
        if i < 20:
            avg_corr.append(np.nan)
            continue
        block = rets.iloc[i - 19 : i + 1]
        ewb = ew.iloc[i - 19 : i + 1]
        cors = []
        for c in block.columns:
            x = block[c]
            m = x.notna() & ewb.notna()
            if m.sum() < 10:
                continue
            if x[m].std() < 1e-12 or ewb[m].std() < 1e-12:
                continue
            cors.append(float(x[m].corr(ewb[m])))
        avg_corr.append(float(np.nanmean(cors)) if cors else np.nan)
    avg_corr = pd.Series(avg_corr, index=idx)
    btc_vol20 = b_ret.rolling(20).std()

    cut = idx[int(T * 0.70)]
    results = []
    print(f"panel {T}x{N} cut={cut} dvol_nanfrac={float(dvol.isna().mean().mean()):.2%}")

    def add(rid, tr, oos, n_trials=1, min_n=25, extra=None):
        v = verdict_arm(
            pack(oos), n_trials=n_trials, train_mean=pack(tr).get("mean"), min_n=min_n
        )
        row = {"id": rid, "train": pack(tr), "oos": pack(oos), **v}
        if extra:
            row["extra"] = extra
        results.append(row)
        print(
            f"{rid}: train={row['train'].get('mean')} oos={row['oos'].get('mean')} "
            f"n={row['oos'].get('n')} {v['verdict']}"
        )

    # --- H-EH-70 dollar vol premium ---
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = dvol20.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row) & (row > 0)
            if valid.sum() < 15:
                continue
            lo = np.nanpercentile(row[valid], 30)
            hi = np.nanpercentile(row[valid], 70)
            L = valid & (row >= hi)
            S = valid & (row <= lo)
            if L.sum() < 3 or S.sum() < 3:
                continue
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[L]) - np.nanmean(fwd[S])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"dvol_premium_ls_h{hold}", tr, oos, n_trials=2, min_n=20)

    # --- H-EH-71 illiquidity premium ---
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = dvol20.iloc[i].to_numpy(dtype=float)
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
        add(f"illiq_dvol_premium_ls_h{hold}", tr, oos, n_trials=2, min_n=20)

    # --- H-EH-72 volume surprise fade ---
    for hold in (1, 3):
        tr, oos = [], []
        for i in range(25, T - hold):
            vz = vol_z.iloc[i].to_numpy(dtype=float)
            rr = rets.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(vz) & np.isfinite(rr)
            spike = valid & (vz >= 2.0) & (np.abs(rr) >= 0.05)
            if spike.sum() < 2:
                continue
            signs = -np.sign(rr)
            fwd = close[i + hold] / close[i] - 1.0
            pnl = float(np.nanmean(fwd[spike] * signs[spike])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"volsurprise_fade_h{hold}", tr, oos, n_trials=2, min_n=25)

    # --- H-EH-73 Amihud ---
    for hold in (5, 10):
        # long low amihud (liquid), short high amihud
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = amihud20.iloc[i].to_numpy(dtype=float)
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
        add(f"low_amihud_ls_h{hold}", tr, oos, n_trials=2, min_n=20)

    # --- H-EH-76 Parkinson vol LS (low park long) ---
    for hold in (5, 10):
        tr, oos = [], []
        for i in range(25, T - hold, hold):
            row = park.iloc[i].to_numpy(dtype=float)
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
        add(f"low_parkinson_ls_h{hold}", tr, oos, n_trials=2, min_n=20)

    # --- H-EH-74 META: ST rev baseline vs filtered by low corr ---
    cum3 = (1 + rets).rolling(3).apply(lambda x: np.prod(x) - 1, raw=True)
    corr_lo = avg_corr < avg_corr.rolling(100).quantile(0.50)

    def st_rev_series(filter_mask=None, hold=3):
        tr, oos = [], []
        for i in range(100, T - hold, hold):
            if filter_mask is not None and not bool(filter_mask.iloc[i]):
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
            (tr if idx[i] < cut else oos).append(pnl)
        return tr, oos

    tr0, oos0 = st_rev_series(None, 3)
    tr1, oos1 = st_rev_series(corr_lo, 3)
    add("meta_strev_baseline_h3", tr0, oos0, n_trials=2, min_n=20)
    add(
        "meta_strev_lowcorr_h3",
        tr1,
        oos1,
        n_trials=2,
        min_n=20,
        extra={
            "baseline_oos_mean": pack(oos0).get("mean"),
            "baseline_oos_std": float(np.std(oos0, ddof=1)) if len(oos0) > 1 else None,
            "filt_oos_std": float(np.std(oos1, ddof=1)) if len(oos1) > 1 else None,
            "baseline_p05": float(np.percentile(oos0, 5)) if oos0 else None,
            "filt_p05": float(np.percentile(oos1, 5)) if oos1 else None,
        },
    )

    # --- H-EH-75 META: skip high BTC vol on long residual losers ---
    resid = rets.sub(b_ret, axis=0)
    cum_r20 = resid.rolling(20).sum()
    quiet = btc_vol20 < btc_vol20.rolling(100).quantile(0.50)

    def resid_loser(filter_mask=None, hold=5):
        tr, oos = [], []
        for i in range(100, T - hold, hold):
            if filter_mask is not None and not bool(filter_mask.iloc[i]):
                continue
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
            (tr if idx[i] < cut else oos).append(pnl)
        return tr, oos

    tr0, oos0 = resid_loser(None, 5)
    tr1, oos1 = resid_loser(quiet, 5)
    add("meta_residloser_baseline_h5", tr0, oos0, n_trials=2, min_n=20)
    add(
        "meta_residloser_quietbtc_h5",
        tr1,
        oos1,
        n_trials=2,
        min_n=15,
        extra={
            "baseline_oos_mean": pack(oos0).get("mean"),
            "baseline_p05": float(np.percentile(oos0, 5)) if oos0 else None,
            "filt_p05": float(np.percentile(oos1, 5)) if oos1 else None,
        },
    )

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    both = [
        r
        for r in results
        if (r["train"].get("mean") or -1) > 0 and (r["oos"].get("mean") or -1) > 0
    ]
    out = {
        "meta": {"panel": list(close_panel.shape), "cut": str(cut), "cost_rt": COST_RT},
        "results": results,
        "candidates": candidates,
        "n_candidates": len(candidates),
        "train_and_oos_pos": [r["id"] for r in both],
    }
    Path("logs/edge_hunt_round7.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("CANDIDATES", len(candidates))
    print("TRAIN+OOS+", [r["id"] for r in both])
    for r in both:
        print(
            f"  {r['id']}: train={r['train']['mean']:+.4%} oos={r['oos']['mean']:+.4%} "
            f"n={r['oos']['n']} {r['verdict']}"
        )
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
