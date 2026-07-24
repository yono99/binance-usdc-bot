#!/usr/bin/env python3
"""Edge hunt R12 — volume-regime XS + categories from riset_edge.txt (OHLCV 1d).

Novelty vs pure crash-bounce: rank by *volume shock* / dollar-vol regime, not
only price dump. Also screens classic families with hard cost + OOS + multi-trial:

  H-EH-R12-01  Vol-shock fade: high vol_z + extreme ret → reverse hold H
  H-EH-R12-02  Vol-shock follow: high vol_z + ret sign → continue
  H-EH-R12-03  Low $vol long / high $vol short (liquidity premium LS)
  H-EH-R12-04  Trend: N-day high breakout long / low breakdown short
  H-EH-R12-05  Mean-rev: residual z vs BTC extreme fade
  H-EH-R12-06  Momentum XS ret20 LS (control; often NOT_PROVEN)
  H-EH-R12-07  ATR-proxy range breakout (Parkinson/range expand)

Judge: train mean>0 + OOS mean>0 + n≥30 + p_adj (Bonferroni n_trials).
NOT PROMOTE_PAPER until strict 50/30/20 + cost×2 elsewhere.

  PYTHONPATH=. python research/edge_hunt_round12.py
  PYTHONPATH=. python research/edge_hunt_round12.py --snap data/snap --out logs/edge_hunt_round12.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

_RESEARCH = str(Path(__file__).resolve().parent)
_ROOT = Path(__file__).resolve().parent.parent
if _RESEARCH not in sys.path:
    sys.path.insert(0, _RESEARCH)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from edge_hunt import COST_RT, load_daily, pack, verdict_arm  # noqa: E402


def load_ohlc_vol(snap: Path, close_panel: pd.DataFrame):
    """Align volume/high/low to close_panel columns."""
    paths = list(snap.glob("*__1d.pkl"))
    by_coin: dict[str, Path] = {}
    for p in paths:
        stem = p.stem.replace("__1d", "")
        coin = stem.split("_")[0].upper()
        if "BTCDOM" in stem.upper():
            continue
        # prefer USDT dual
        if coin not in by_coin or "USDT_USDT" in stem:
            by_coin[coin] = p
    vol = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)
    high = vol.copy()
    low = vol.copy()
    for col in close_panel.columns:
        coin = col.split("/")[0].upper() if "/" in col else col.split("_")[0].upper()
        p = by_coin.get(coin)
        if p is None:
            continue
        try:
            df = pd.read_pickle(p).reindex(close_panel.index)
        except Exception:
            continue
        if "volume" in df.columns:
            vol[col] = df["volume"].astype(float)
        if "high" in df.columns:
            high[col] = df["high"].astype(float)
        if "low" in df.columns:
            low[col] = df["low"].astype(float)
    return vol, high, low


def day_ew(mask: pd.DataFrame, fwd: pd.DataFrame, cut, cost: float) -> dict:
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
        return {"train": pack([]), "oos": pack([])}
    s = pd.Series(day_rets, index=pd.DatetimeIndex(times))
    return {"train": pack(s[s.index < cut].to_numpy()), "oos": pack(s[s.index >= cut].to_numpy())}


def xs_ls(score: pd.DataFrame, fwd: pd.DataFrame, cut, top_q=0.2, long_high=True, cost=COST_RT) -> dict:
    rets, times = [], []
    for t in score.index:
        sc = score.loc[t]
        fr = fwd.loc[t]
        ok = sc.notna() & fr.notna()
        if ok.sum() < 12:
            continue
        sub = sc[ok].sort_values()
        k = max(1, int(len(sub) * top_q))
        if long_high:
            lng, sht = sub.iloc[-k:].index, sub.iloc[:k].index
        else:
            lng, sht = sub.iloc[:k].index, sub.iloc[-k:].index
        r = float(fr[lng].mean() - fr[sht].mean()) - cost
        if np.isfinite(r):
            rets.append(r)
            times.append(t)
    if not rets:
        return {"train": pack([]), "oos": pack([])}
    s = pd.Series(rets, index=pd.DatetimeIndex(times))
    return {"train": pack(s[s.index < cut].to_numpy()), "oos": pack(s[s.index >= cut].to_numpy())}


def hold_fwd(close: pd.DataFrame, h: int) -> pd.DataFrame:
    return close.shift(-h) / close - 1.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=str(_ROOT / "data" / "snap"))
    ap.add_argument("--out", default=str(_ROOT / "logs" / "edge_hunt_round12.json"))
    ap.add_argument("--max-alts", type=int, default=200)
    ap.add_argument("--lookback-days", type=int, default=2000)
    args = ap.parse_args()

    snap = Path(args.snap)
    panel, btc = load_daily(
        snap, max_alts=args.max_alts, lookback_days=args.lookback_days, min_bars=200
    )
    print(f"panel {panel.shape} {panel.index.min().date()}→{panel.index.max().date()}")
    vol, high, low = load_ohlc_vol(snap, panel)
    cut = panel.index[int(len(panel) * 0.70)]
    ret = panel.pct_change()
    btc_r = btc.reindex(panel.index).ffill().pct_change()
    resid = ret.sub(btc_r, axis=0)
    dvol = (vol * panel).astype(float)
    dvol20 = dvol.rolling(20).mean()
    vol_z = (vol - vol.rolling(20).mean()) / (vol.rolling(20).std() + 1e-12)
    ret20 = ret.rolling(20).mean()
    # residual z 20d
    resid_z = resid / (resid.rolling(20).std() + 1e-12)
    # range expand: (high-low)/close vs 20d mean
    rng = (high - low) / panel.replace(0, np.nan)
    rng_z = (rng - rng.rolling(20).mean()) / (rng.rolling(20).std() + 1e-12)

    arms: list[dict] = []

    # ── 01/02 vol-shock fade / follow ──
    for h in (1, 3, 5):
        fwd = hold_fwd(panel, h)
        # high vol_z top quartile cross-section + |ret| top
        for t_label, thr_v, thr_r in (("hi", 1.0, 0.03), ("vhi", 1.5, 0.05)):
            mask_hi = (vol_z > thr_v) & (ret.abs() > thr_r) & panel.notna()
            # fade: short if ret>0, long if ret<0 → -ret direction = -sign(ret)*fwd ≈ fade
            # day EW of -sign(ret)*fwd on mask
            signed = -np.sign(ret) * fwd
            sp = day_ew(mask_hi, signed, cut, COST_RT)
            arms.append({"id": f"volshock_{t_label}_fade_h{h}", "family": "volshock_fade", **sp})
            signed_f = np.sign(ret) * fwd
            sp_f = day_ew(mask_hi, signed_f, cut, COST_RT)
            arms.append({"id": f"volshock_{t_label}_follow_h{h}", "family": "volshock_follow", **sp_f})

    # ── 03 liquidity premium LS (dvol20) ──
    for h in (5, 10):
        fwd = hold_fwd(panel, h)
        # low dvol long, high dvol short → score = -dvol20 (long high score = low liq)
        sp = xs_ls(-dvol20, fwd, cut, long_high=True)
        arms.append({"id": f"low_dvol_long_ls_h{h}", "family": "liq_premium", **sp})
        sp2 = xs_ls(dvol20, fwd, cut, long_high=True)
        arms.append({"id": f"high_dvol_long_ls_h{h}", "family": "liq_control", **sp2})

    # ── 04 trend breakout N-day ──
    for win in (20, 50):
        for h in (5, 10):
            fwd = hold_fwd(panel, h)
            hh = panel.rolling(win).max().shift(1)
            ll = panel.rolling(win).min().shift(1)
            mask_up = (panel > hh) & panel.notna()
            mask_dn = (panel < ll) & panel.notna()
            sp = day_ew(mask_up, fwd, cut, COST_RT)
            arms.append({"id": f"break_up_{win}_h{h}", "family": "trend_break", **sp})
            sp_s = day_ew(mask_dn, -fwd, cut, COST_RT)
            arms.append({"id": f"break_dn_short_{win}_h{h}", "family": "trend_break", **sp_s})

    # ── 05 mean-rev residual z fade ──
    for zthr in (1.5, 2.0):
        for h in (3, 5):
            fwd = hold_fwd(panel, h)
            # long residual very negative, short residual very positive
            mask_long = (resid_z < -zthr) & panel.notna()
            mask_short = (resid_z > zthr) & panel.notna()
            # combine: long side uses +fwd, short side -fwd on same day EW equal
            day_rets, times = [], []
            for t in panel.index:
                if t + pd.Timedelta(days=h) > panel.index[-1]:
                    break
                lg = mask_long.loc[t]
                sh = mask_short.loc[t]
                parts = []
                if hasattr(lg, "any") and bool(lg.fillna(False).any()):
                    parts.extend(list(fwd.loc[t, lg.fillna(False)].dropna().values))
                if hasattr(sh, "any") and bool(sh.fillna(False).any()):
                    parts.extend(list((-fwd.loc[t, sh.fillna(False)]).dropna().values))
                if not parts:
                    continue
                day_rets.append(float(np.mean(parts)) - COST_RT)
                times.append(t)
            if day_rets:
                s = pd.Series(day_rets, index=pd.DatetimeIndex(times))
                sp = {
                    "train": pack(s[s.index < cut].to_numpy()),
                    "oos": pack(s[s.index >= cut].to_numpy()),
                }
            else:
                sp = {"train": pack([]), "oos": pack([])}
            arms.append({"id": f"residz_fade_z{zthr}_h{h}", "family": "meanrev_resid", **sp})

    # ── 06 mom XS control ──
    for h in (5, 10):
        fwd = hold_fwd(panel, h)
        sp = xs_ls(ret20, fwd, cut, long_high=True)
        arms.append({"id": f"mom20_ls_h{h}", "family": "mom_xs", **sp})
        sp_r = xs_ls(ret20, fwd, cut, long_high=False)
        arms.append({"id": f"rev20_ls_h{h}", "family": "rev_xs", **sp_r})

    # ── 07 range expand fade ──
    for h in (1, 3, 5):
        fwd = hold_fwd(panel, h)
        mask = (rng_z > 1.5) & panel.notna()
        signed = -np.sign(ret) * fwd
        sp = day_ew(mask, signed, cut, COST_RT)
        arms.append({"id": f"range_expand_fade_h{h}", "family": "vol_breakout_fade", **sp})

    n_trials = len(arms)
    results = []
    for a in arms:
        tr, oos = a.get("train") or pack([]), a.get("oos") or pack([])
        v = verdict_arm(oos, n_trials=n_trials, train_mean=tr.get("mean"))
        results.append({"id": a["id"], "family": a.get("family"), "train": tr, "oos": oos, **v})
    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["oos"].get("mean") or -9)))

    out = {
        "meta": {
            "round": "R12_volume_regime_and_riset_categories",
            "source_spec": "riset_edge.txt + EDGE_HUNT_LOOP",
            "snapshot": str(snap.resolve()),
            "panel": list(panel.shape),
            "range": [str(panel.index.min().date()), str(panel.index.max().date())],
            "cut": str(cut.date()) if hasattr(cut, "date") else str(cut),
            "cost_rt": COST_RT,
            "n_trials": n_trials,
            "categories": [
                "volshock_fade/follow",
                "liq_premium",
                "trend_break",
                "meanrev_resid",
                "mom_xs",
                "vol_breakout_fade",
            ],
        },
        "verdicts": dict(Counter(r["verdict"] for r in results)),
        "arms": results,
        "candidates": [r for r in results if r["verdict"] == "CANDIDATE"],
        "train_oos_pos": [
            r
            for r in results
            if (r["train"].get("mean") or 0) > 0 and (r["oos"].get("mean") or 0) > 0
        ],
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("verdicts", out["verdicts"])
    print("CANDIDATES", len(out["candidates"]))
    for r in out["candidates"][:15]:
        print(" CAND", r["id"], r["reason"])
    print("train+OOS+", len(out["train_oos_pos"]))
    for r in sorted(out["train_oos_pos"], key=lambda x: x["oos"].get("mean") or 0, reverse=True)[:15]:
        print(
            f"  {r['id']}: oos={r['oos']['mean']:+.4%} n={r['oos']['n']} "
            f"train={r['train']['mean']:+.4%} v={r['verdict']} p={r.get('p_adj')}"
        )
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
