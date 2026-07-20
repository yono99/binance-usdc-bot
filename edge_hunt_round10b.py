#!/usr/bin/env python3
"""R10b — pairs residual on forced majors (BTC/ETH/SOL/BNB/XRP/DOGE/AVAX/LINK)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, pack, verdict_arm


def load_major_closes(snap: Path) -> tuple[pd.DataFrame, pd.Series]:
    want = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "ADA", "DOT"]
    series = {}
    for p in sorted(snap.glob("*__1d.pkl")):
        stem = p.stem.upper()
        if "BTCDOM" in stem:
            continue
        coin = stem.split("_")[0]
        if coin not in want:
            continue
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if "close" not in df.columns or len(df) < 400:
            continue
        s = df["close"].astype(float).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        if coin not in series or len(s) > len(series[coin]):
            series[coin] = s
    if "BTC" not in series:
        raise SystemExit("no BTC")
    btc = series.pop("BTC")
    panel = pd.DataFrame(series).sort_index()
    # common window last 1600d
    end = panel.index.max()
    start = end - pd.Timedelta(days=1600)
    panel = panel.loc[panel.index >= start].dropna(how="all")
    # require coverage
    panel = panel.dropna(axis=1, thresh=int(len(panel) * 0.7)).ffill().dropna()
    btc = btc.reindex(panel.index).ffill()
    return panel, btc


def residual_z(y: pd.Series, x: pd.Series, win: int = 60) -> pd.Series:
    beta = y.rolling(win).cov(x) / (x.rolling(win).var() + 1e-12)
    resid = y - beta * x
    return (resid - resid.rolling(win).mean()) / (resid.rolling(win).std() + 1e-12)


def trade_z(z, price, thr, hold, cut, cost):
    tr, oos = [], []
    idx = z.index
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
        i += hold
    return tr, oos


def main() -> int:
    panel, btc = load_major_closes(Path("data/snap"))
    print("panel", panel.shape, list(panel.columns))
    b_ret = btc.pct_change()
    idx = panel.index
    cut = idx[int(len(idx) * 0.70)]
    results = []
    n_trials = len(panel.columns) * 2 * 3 + 6

    def add(rid, tr, oos, min_n=20):
        v = verdict_arm(
            pack(oos), n_trials=n_trials, train_mean=pack(tr).get("mean"), min_n=min_n
        )
        row = {"id": rid, "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(
            f"{rid}: train={row['train'].get('mean')} oos={row['oos'].get('mean')} "
            f"n={row['oos'].get('n')} {v['verdict']}"
        )

    for col in panel.columns:
        y = panel[col].pct_change()
        z = residual_z(y, b_ret, 60)
        for thr in (1.5, 2.0):
            for hold in (1, 3, 5):
                tr, oos = trade_z(z, panel[col], thr, hold, cut, COST_RT)
                add(f"pair_{col}_btc_z{thr}_h{hold}", tr, oos)

    # basket fade
    zdf = pd.DataFrame(
        {c: residual_z(panel[c].pct_change(), b_ret, 60) for c in panel.columns}
    )
    for thr in (1.5, 2.0):
        for hold in (1, 3, 5):
            tr, oos = [], []
            i = 60
            T = len(idx)
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
                pnl = float(np.mean(pnls)) - COST_RT
                (tr if idx[i] < cut else oos).append(pnl)
                i += hold
            add(f"basket_residz_z{thr}_h{hold}", tr, oos)

    # 1h ETH-BTC
    btc1 = eth1 = None
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
        df = df[~df.index.duplicated(keep="last")].iloc[-8000:]
        z = residual_z(df["ETH"].pct_change(), df["BTC"].pct_change(), 48)
        cut1 = df.index[int(len(df) * 0.70)]
        for thr in (1.5, 2.0):
            for hold in (4, 8, 12):
                tr, oos = trade_z(z, df["ETH"], thr, hold, cut1, COST_RT)
                add(f"1h_ETH_btc_z{thr}_h{hold}", tr, oos, min_n=30)

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    both = [
        r
        for r in results
        if (r["train"].get("mean") or -1) > 0 and (r["oos"].get("mean") or -1) > 0
    ]
    out = {
        "meta": {
            "panel": list(panel.shape),
            "cols": list(panel.columns),
            "cut": str(cut),
            "cost_rt": COST_RT,
        },
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
