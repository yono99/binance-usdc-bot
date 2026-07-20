#!/usr/bin/env python3
"""Edge hunt round 10 — pairs / cointegration residual (structurally different).

Not single-name ST or crash-bounce. Economic idea: relative value between
correlated majors mean-reverts after residual z-score extremes.

  H-EH-100  BTC-ETH residual z fade (daily)
  H-EH-101  BTC-SOL residual z fade
  H-EH-102  ETH-SOL residual z fade
  H-EH-103  basket: top-3 residual z fade vs BTC (alts)
  H-EH-104  1h BTC-ETH residual z fade (finer)

Promotion still requires train+ OOS+ n p_adj.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, load_daily, pack, verdict_arm


def residual_z(y: pd.Series, x: pd.Series, win: int = 60) -> pd.Series:
    """Rolling residual of y on x, z-scored. Causal (uses only past win)."""
    # beta_t = cov/var on [t-win, t)
    beta = y.rolling(win).cov(x) / (x.rolling(win).var() + 1e-12)
    resid = y - beta * x
    z = (resid - resid.rolling(win).mean()) / (resid.rolling(win).std() + 1e-12)
    return z


def trade_z(
    z: pd.Series,
    price: pd.Series,
    thr: float,
    hold: int,
    cut,
    cost: float,
):
    """When |z|>=thr, fade: short if z>0, long if z<0; hold days; non-overlap."""
    tr, oos = [], []
    idx = z.index
    i = win0 = 0
    # find first valid
    vals = z.to_numpy(dtype=float)
    px = price.to_numpy(dtype=float)
    T = len(z)
    i = 0
    while i < T - hold:
        zi = vals[i]
        if not np.isfinite(zi) or abs(zi) < thr:
            i += 1
            continue
        if not np.isfinite(px[i]) or not np.isfinite(px[i + hold]) or px[i] <= 0:
            i += 1
            continue
        side = -1.0 if zi > 0 else 1.0
        pnl = side * (px[i + hold] / px[i] - 1.0) - cost
        (tr if idx[i] < cut else oos).append(float(pnl))
        i += hold  # non-overlap
    return tr, oos


def main() -> int:
    panel, btc = load_daily(Path("data/snap"), max_alts=50, lookback_days=1600)
    # need ETH SOL if present
    cols = {c.split("/")[0].upper() if "/" in c else c.split("_")[0].upper(): c for c in panel.columns}
    print("coins sample", list(cols.keys())[:20])
    b = btc.reindex(panel.index).ffill()
    b_ret = b.pct_change()
    idx = panel.index
    cut = idx[int(len(idx) * 0.70)]
    results = []

    def add(rid, tr, oos, n_trials=1, min_n=25):
        v = verdict_arm(
            pack(oos), n_trials=n_trials, train_mean=pack(tr).get("mean"), min_n=min_n
        )
        row = {"id": rid, "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(
            f"{rid}: train={row['train'].get('mean')} oos={row['oos'].get('mean')} "
            f"n={row['oos'].get('n')} {v['verdict']}"
        )

    pairs = []
    for name in ("ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK"):
        if name in cols:
            pairs.append((name, cols[name]))

    # H-EH-100.. pair residual vs BTC
    for name, col in pairs:
        y = panel[col].pct_change()
        z = residual_z(y, b_ret, 60)
        for thr in (1.5, 2.0):
            for hold in (1, 3, 5):
                tr, oos = trade_z(z, panel[col], thr, hold, cut, COST_RT)
                add(
                    f"pair_{name}_btc_z{thr}_h{hold}",
                    tr,
                    oos,
                    n_trials=len(pairs) * 2 * 3,
                    min_n=20,
                )

    # H-EH-103 basket: average residual z across alts, fade EW of extreme names
    # build residual z matrix
    zmat = {}
    for name, col in pairs:
        zmat[name] = residual_z(panel[col].pct_change(), b_ret, 60)
    zdf = pd.DataFrame(zmat).reindex(idx)
    for thr in (1.5, 2.0):
        for hold in (1, 3, 5):
            tr, oos = [], []
            i = 60
            T = len(idx)
            while i < T - hold:
                row = zdf.iloc[i]
                valid = row.dropna()
                if len(valid) < 3:
                    i += 1
                    continue
                # long most negative z, short most positive
                lo = valid.nsmallest(2)
                hi = valid.nlargest(2)
                if lo.abs().mean() < thr and hi.abs().mean() < thr:
                    i += 1
                    continue
                # only trade if extremes clear thr
                L_names = [n for n, v in lo.items() if v <= -thr]
                S_names = [n for n, v in hi.items() if v >= thr]
                if not L_names and not S_names:
                    i += 1
                    continue
                pnls = []
                for n in L_names:
                    col = cols[n]
                    px = panel[col]
                    pnls.append(float(px.iloc[i + hold] / px.iloc[i] - 1.0))
                for n in S_names:
                    col = cols[n]
                    px = panel[col]
                    pnls.append(float(-(px.iloc[i + hold] / px.iloc[i] - 1.0)))
                if not pnls:
                    i += 1
                    continue
                pnl = float(np.mean(pnls)) - COST_RT
                (tr if idx[i] < cut else oos).append(pnl)
                i += hold
            add(f"basket_residz_fade_z{thr}_h{hold}", tr, oos, n_trials=6, min_n=20)

    # H-EH-104 1h BTC-ETH residual if available
    try:
        btc1 = None
        eth1 = None
        for p in Path("data/snap").glob("*__1h.pkl"):
            s = p.stem.upper()
            if s.startswith("BTC_") and "DOM" not in s:
                df = pd.read_pickle(p)
                if btc1 is None or len(df) > len(btc1):
                    btc1 = df["close"].astype(float)
            if s.startswith("ETH_"):
                df = pd.read_pickle(p)
                if eth1 is None or len(df) > len(eth1):
                    eth1 = df["close"].astype(float)
        if btc1 is not None and eth1 is not None:
            df = pd.DataFrame({"BTC": btc1, "ETH": eth1}).dropna().sort_index()
            df = df[~df.index.duplicated(keep="last")]
            # last 8000 bars
            df = df.iloc[-8000:]
            br = df["BTC"].pct_change()
            er = df["ETH"].pct_change()
            z = residual_z(er, br, 48)  # 48h window
            cut1 = df.index[int(len(df) * 0.70)]
            for thr in (1.5, 2.0):
                for hold in (4, 8, 12):  # hours
                    tr, oos = trade_z(z, df["ETH"], thr, hold, cut1, COST_RT)
                    add(f"1h_ETH_btc_z{thr}_h{hold}", tr, oos, n_trials=6, min_n=30)
    except Exception as e:
        print("1h skip", e)

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    both = [
        r
        for r in results
        if (r["train"].get("mean") or -1) > 0 and (r["oos"].get("mean") or -1) > 0
    ]
    out = {
        "meta": {"cut": str(cut), "cost_rt": COST_RT, "n_pairs": len(pairs)},
        "results": results,
        "candidates": candidates,
        "n_candidates": len(candidates),
        "train_and_oos_pos": [r["id"] for r in both],
    }
    Path("logs/edge_hunt_round10.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("CANDIDATES", len(candidates))
    for c in candidates:
        print(" ", c["id"], c["oos"].get("mean"), c.get("p_adj"), c["reason"])
    print("TRAIN+OOS+", len(both))
    for r in both:
        print(
            f"  {r['id']}: train={r['train']['mean']:+.4%} oos={r['oos']['mean']:+.4%} "
            f"n={r['oos']['n']} p_adj={r.get('p_adj')} {r['verdict']}"
        )
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
