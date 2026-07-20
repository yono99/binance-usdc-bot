#!/usr/bin/env python3
"""Edge hunt round 8 — 1h majors microstructure-ish + overnight / weekend.

Uses 1h snaps (not daily panel retreads). Cost RT 0.18%.

  H-EH-80  overnight (last hour Asia → first hour US) long/short majors
  H-EH-81  hour-of-day residual: best/worst UTC hours net of cost
  H-EH-82  4h momentum fade on majors (hold 4-12h)
  H-EH-83  range breakout fail (false break of 24h high/low) fade
  H-EH-84  BTC 1h lead ETH/SOL 1-4h
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from edge_hunt import COST_RT, pack, verdict_arm


def load_majors_1h(snap: Path) -> dict[str, pd.DataFrame]:
    out = {}
    for p in sorted(snap.glob("*__1h.pkl")):
        stem = p.stem.upper()
        if not any(x in stem for x in ("BTC_", "ETH_", "SOL_", "BNB_", "XRP_", "DOGE_")):
            continue
        if "BTCDOM" in stem:
            continue
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if len(df) < 2000 or "close" not in df.columns:
            continue
        coin = stem.split("_")[0]
        # keep longest series per coin
        if coin not in out or len(df) > len(out[coin]):
            out[coin] = df.sort_index()
            out[coin] = out[coin][~out[coin].index.duplicated(keep="last")]
    return out


def main() -> int:
    maj = load_majors_1h(Path("data/snap"))
    if len(maj) < 2:
        print("not enough 1h majors", list(maj.keys()))
        return 1
    print("majors", {k: len(v) for k, v in maj.items()})

    # align closes
    closes = {k: v["close"].astype(float) for k, v in maj.items()}
    panel = pd.DataFrame(closes).sort_index().ffill()
    # drop early sparse
    panel = panel.dropna(how="any")
    T = len(panel)
    idx = panel.index
    cut = idx[int(T * 0.70)]
    rets = panel.pct_change()
    btc = panel["BTC"] if "BTC" in panel.columns else panel.iloc[:, 0]
    b_ret = btc.pct_change()
    results = []
    print(f"panel1h {panel.shape} cut={cut}")

    def add(rid, tr, oos, n_trials=1, min_n=40):
        v = verdict_arm(
            pack(oos), n_trials=n_trials, train_mean=pack(tr).get("mean"), min_n=min_n
        )
        row = {"id": rid, "train": pack(tr), "oos": pack(oos), **v}
        results.append(row)
        print(
            f"{rid}: train={row['train'].get('mean')} oos={row['oos'].get('mean')} "
            f"n={row['oos'].get('n')} {v['verdict']}"
        )

    hours = idx.hour
    # --- H-EH-80: "overnight" proxy: 00-08 UTC return → fade next 8h ---
    for hold_h in (4, 8, 12):
        tr, oos = [], []
        # sample once per day at 08:00
        for i in range(24, T - hold_h):
            if hours[i] != 8:
                continue
            # prior 8h return (00-08)
            if i < 8:
                continue
            prev = float(btc.iloc[i] / btc.iloc[i - 8] - 1.0)
            if not np.isfinite(prev) or abs(prev) < 0.005:
                continue
            # fade: short if up, long if down — on EW majors
            fwd = panel.iloc[i + hold_h] / panel.iloc[i] - 1.0
            pnl = float(-np.sign(prev) * np.nanmean(fwd.to_numpy(dtype=float))) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"overnight_fade_h{hold_h}", tr, oos, n_trials=3, min_n=30)

        # continuation opposite
        tr, oos = [], []
        for i in range(24, T - hold_h):
            if hours[i] != 8:
                continue
            if i < 8:
                continue
            prev = float(btc.iloc[i] / btc.iloc[i - 8] - 1.0)
            if not np.isfinite(prev) or abs(prev) < 0.005:
                continue
            fwd = panel.iloc[i + hold_h] / panel.iloc[i] - 1.0
            pnl = float(np.sign(prev) * np.nanmean(fwd.to_numpy(dtype=float))) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"overnight_cont_h{hold_h}", tr, oos, n_trials=3, min_n=30)

    # --- H-EH-81 hour-of-day long majors net ---
    # pick top/bottom 3 hours by TRAIN only, test OOS (honest)
    train_mask = idx < cut
    hour_means = {}
    for h in range(24):
        m = (hours == h) & train_mask
        r = rets.loc[m].mean(axis=1) - COST_RT  # long EW 1h
        hour_means[h] = float(r.mean()) if len(r) else np.nan
    ranked = sorted(
        [(v, h) for h, v in hour_means.items() if np.isfinite(v)], reverse=True
    )
    best3 = {h for _, h in ranked[:3]}
    worst3 = {h for _, h in ranked[-3:]}
    print("best3 hours train", best3, "worst3", worst3)

    for name, seth, sign in (
        ("hour_best3_long", best3, 1),
        ("hour_worst3_short", worst3, -1),
    ):
        tr, oos = [], []
        for i in range(1, T - 1):
            if hours[i] not in seth:
                continue
            fwd = float(np.nanmean((panel.iloc[i + 1] / panel.iloc[i] - 1.0).to_numpy()))
            pnl = sign * fwd - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(name, tr, oos, n_trials=2, min_n=100)

    # --- H-EH-82 4h mom fade ---
    r4 = panel.pct_change(4)
    for hold in (4, 8, 12):
        tr, oos = [], []
        for i in range(10, T - hold, hold):
            row = r4.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 2:
                continue
            # fade extremes
            signs = -np.sign(row)
            fwd = (panel.iloc[i + hold] / panel.iloc[i] - 1.0).to_numpy(dtype=float)
            pnl = float(np.nanmean(fwd[valid] * signs[valid])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"mom4h_fade_h{hold}", tr, oos, n_trials=3, min_n=40)

        tr, oos = [], []
        for i in range(10, T - hold, hold):
            row = r4.iloc[i].to_numpy(dtype=float)
            valid = np.isfinite(row)
            if valid.sum() < 2:
                continue
            signs = np.sign(row)
            fwd = (panel.iloc[i + hold] / panel.iloc[i] - 1.0).to_numpy(dtype=float)
            pnl = float(np.nanmean(fwd[valid] * signs[valid])) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"mom4h_cont_h{hold}", tr, oos, n_trials=3, min_n=40)

    # --- H-EH-83 false break 24h high/low fade ---
    # use BTC only for signal clarity
    if "high" in maj["BTC"].columns:
        hi = maj["BTC"]["high"].reindex(idx).ffill()
        lo = maj["BTC"]["low"].reindex(idx).ffill()
        hh24 = hi.rolling(24).max()
        ll24 = lo.rolling(24).min()
        for hold in (4, 8):
            tr, oos = [], []
            for i in range(30, T - hold):
                # break high then close back below = false break short
                c0, c1 = float(btc.iloc[i - 1]), float(btc.iloc[i])
                hlev = float(hh24.iloc[i - 1])
                llev = float(ll24.iloc[i - 1])
                if not all(np.isfinite([c0, c1, hlev, llev])):
                    continue
                side = 0
                if c0 > hlev and c1 < hlev:
                    side = -1  # short fade
                elif c0 < llev and c1 > llev:
                    side = 1  # long fade
                if side == 0:
                    continue
                fwd = float(btc.iloc[i + hold] / btc.iloc[i] - 1.0)
                pnl = side * fwd - COST_RT
                (tr if idx[i] < cut else oos).append(pnl)
            add(f"falsebreak_fade_h{hold}", tr, oos, n_trials=2, min_n=30)

    # --- H-EH-84 BTC lead ETH/SOL ---
    alts = [c for c in panel.columns if c != "BTC"]
    for lag_hold in (1, 2, 4):
        tr, oos = [], []
        for i in range(5, T - lag_hold):
            br = float(b_ret.iloc[i])
            if not np.isfinite(br) or abs(br) < 0.003:
                continue
            # long alts if BTC up
            fwd = panel[alts].iloc[i + lag_hold] / panel[alts].iloc[i] - 1.0
            pnl = float(np.sign(br) * np.nanmean(fwd.to_numpy(dtype=float))) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"btc_lead_alts_h{lag_hold}", tr, oos, n_trials=3, min_n=100)

        # fade lead
        tr, oos = [], []
        for i in range(5, T - lag_hold):
            br = float(b_ret.iloc[i])
            if not np.isfinite(br) or abs(br) < 0.003:
                continue
            fwd = panel[alts].iloc[i + lag_hold] / panel[alts].iloc[i] - 1.0
            pnl = float(-np.sign(br) * np.nanmean(fwd.to_numpy(dtype=float))) - COST_RT
            (tr if idx[i] < cut else oos).append(pnl)
        add(f"btc_lead_fade_alts_h{lag_hold}", tr, oos, n_trials=3, min_n=100)

    candidates = [r for r in results if r["verdict"] == "CANDIDATE"]
    both = [
        r
        for r in results
        if (r["train"].get("mean") or -1) > 0 and (r["oos"].get("mean") or -1) > 0
    ]
    out = {
        "meta": {
            "panel": list(panel.shape),
            "majors": list(panel.columns),
            "cut": str(cut),
            "cost_rt": COST_RT,
        },
        "results": results,
        "candidates": candidates,
        "n_candidates": len(candidates),
        "train_and_oos_pos": [r["id"] for r in both],
    }
    Path("logs/edge_hunt_round8.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("CANDIDATES", len(candidates))
    print("TRAIN+OOS+", [r["id"] for r in both])
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
    for r in pos[:12]:
        print(
            f"  {r['id']}: {r['oos']['mean']:+.4%} n={r['oos']['n']} "
            f"train={r['train'].get('mean')} {r['verdict']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
