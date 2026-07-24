#!/usr/bin/env python3
"""Edge hunt R11 — listing age / maturity (NOVELTY vs A–F / H24–H32).

Klaim yang diuji (data-first, OOS hakim):
  H-EH-R11-01  Post-list drift: long new listings (age days in [D0,D1]) hold H
  H-EH-R11-02  Fade new listings (short same window)
  H-EH-R11-03  Mature-only ST reverse LS (age ≥ A) vs all-age control
  H-EH-R11-04  Young-only momentum (age < A, ret20 rank long)
  H-EH-R11-05  Age-bucket residual vs BTC (young / mid / mature long EW)

Bukan retread crash-bounce / calendar DoW / short-alts markdown.

  python research/edge_hunt_round11.py
  python research/edge_hunt_round11.py --snap data/snap --out logs/edge_hunt_round11.json
"""
from __future__ import annotations

import argparse
import json
import sys
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


def listing_age_days(panel: pd.DataFrame) -> pd.DataFrame:
    """Days since first non-NaN close per column (listing proxy in snap)."""
    age = pd.DataFrame(index=panel.index, columns=panel.columns, dtype=float)
    for col in panel.columns:
        s = panel[col]
        valid = s.notna()
        if not valid.any():
            continue
        first = valid.idxmax()  # first True
        # integer day index from first
        loc0 = panel.index.get_loc(first)
        if not isinstance(loc0, (int, np.integer)):
            continue
        loc0 = int(loc0)
        # age in calendar days
        ages = (panel.index - first).total_seconds() / 86400.0
        ages = pd.Series(ages, index=panel.index)
        ages = ages.where(valid & (panel.index >= first), np.nan)
        # before first → nan already
        age[col] = ages
    return age


def hold_returns(close: pd.DataFrame, hold: int) -> pd.DataFrame:
    return close.shift(-hold) / close - 1.0


def split_cut(idx: pd.DatetimeIndex, oos_frac: float = 0.30) -> pd.Timestamp:
    return idx[int(len(idx) * (1 - oos_frac))]


def bucket_stats(
    mask: pd.DataFrame,
    fwd: pd.DataFrame,
    cut: pd.Timestamp,
    cost: float,
) -> dict:
    """Equal-weight across symbols on days with ≥1 signal; day-level returns."""
    # per day: mean of fwd where mask, minus cost once (enter/exit day-hold style)
    day_rets = []
    day_times = []
    for t in fwd.index:
        m = mask.loc[t]
        if m is None or not bool(m.any() if hasattr(m, "any") else False):
            continue
        vals = fwd.loc[t, m.fillna(False).astype(bool)]
        vals = vals.replace([np.inf, -np.inf], np.nan).dropna()
        if len(vals) < 1:
            continue
        day_rets.append(float(vals.mean()) - cost)
        day_times.append(t)
    if not day_rets:
        return {"train": pack([]), "oos": pack([])}
    s = pd.Series(day_rets, index=pd.DatetimeIndex(day_times))
    return {
        "train": pack(s[s.index < cut].to_numpy()),
        "oos": pack(s[s.index >= cut].to_numpy()),
    }


def xs_ls_by_age(
    close: pd.DataFrame,
    age: pd.DataFrame,
    *,
    min_age: float | None,
    max_age: float | None,
    score: pd.DataFrame,
    hold: int,
    cut: pd.Timestamp,
    top_q: float = 0.2,
    long_high: bool = True,
) -> dict:
    """Cross-sectional long-short among symbols in age band; score = signal."""
    fwd = hold_returns(close, hold)
    rets = []
    times = []
    for t in close.index:
        if t + pd.Timedelta(days=hold) > close.index[-1]:
            break
        a = age.loc[t]
        sc = score.loc[t]
        ok = sc.notna() & a.notna() & close.loc[t].notna()
        if min_age is not None:
            ok &= a >= min_age
        if max_age is not None:
            ok &= a < max_age
        if ok.sum() < 10:
            continue
        sub_sc = sc[ok]
        sub_fwd = fwd.loc[t, ok]
        n = len(sub_sc)
        k = max(1, int(n * top_q))
        order = sub_sc.sort_values()
        if long_high:
            long_s = order.iloc[-k:].index
            short_s = order.iloc[:k].index
        else:
            long_s = order.iloc[:k].index
            short_s = order.iloc[-k:].index
        r = float(sub_fwd[long_s].mean() - sub_fwd[short_s].mean()) - COST_RT
        if np.isfinite(r):
            rets.append(r)
            times.append(t)
    if not rets:
        return {"train": pack([]), "oos": pack([])}
    s = pd.Series(rets, index=pd.DatetimeIndex(times))
    return {
        "train": pack(s[s.index < cut].to_numpy()),
        "oos": pack(s[s.index >= cut].to_numpy()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=str(_ROOT / "data" / "snap"))
    ap.add_argument("--out", default=str(_ROOT / "logs" / "edge_hunt_round11.json"))
    ap.add_argument("--max-alts", type=int, default=200)
    ap.add_argument("--lookback-days", type=int, default=2000)
    args = ap.parse_args()

    snap = Path(args.snap)
    panel, btc = load_daily(
        snap, max_alts=args.max_alts, lookback_days=args.lookback_days, min_bars=200
    )
    print(f"panel {panel.shape} {panel.index.min().date()}→{panel.index.max().date()}")
    age = listing_age_days(panel)
    cut = split_cut(panel.index, 0.30)
    btc_r = btc.reindex(panel.index).ffill().pct_change()
    ret = panel.pct_change()
    resid = ret.sub(btc_r, axis=0)
    ret20 = ret.rolling(20).mean()

    arms: list[dict] = []
    # ── post-list windows (calendar days since first bar in snap)
    windows = [
        ("postlist_0_7", 0, 7),
        ("postlist_7_30", 7, 30),
        ("postlist_30_90", 30, 90),
        ("postlist_0_30", 0, 30),
    ]
    holds = [1, 3, 5, 10]
    for wname, a0, a1 in windows:
        for h in holds:
            fwd = hold_returns(panel, h)
            mask = (age >= a0) & (age < a1) & panel.notna()
            # long
            sp = bucket_stats(mask, fwd, cut, COST_RT)
            arms.append(
                {
                    "id": f"{wname}_long_h{h}",
                    "family": "postlist_long",
                    **sp,
                }
            )
            # short = -fwd - cost already in pack via -mean; use -fwd for short
            sp_s = bucket_stats(mask, -fwd, cut, COST_RT)
            arms.append(
                {
                    "id": f"{wname}_short_h{h}",
                    "family": "postlist_short",
                    **sp_s,
                }
            )

    # mature residual long EW (age>=365)
    for h in (1, 3, 5):
        fwd = hold_returns(resid, h)
        mask = (age >= 365) & resid.notna()
        sp = bucket_stats(mask, fwd, cut, COST_RT)
        arms.append({"id": f"mature365_resid_long_h{h}", "family": "mature_resid", **sp})
        mask_y = (age < 90) & resid.notna()
        sp_y = bucket_stats(mask_y, fwd, cut, COST_RT)
        arms.append({"id": f"young90_resid_long_h{h}", "family": "young_resid", **sp_y})

    # XS mom among young vs mature
    for min_a, max_a, tag in (
        (0, 90, "young90"),
        (365, None, "mature365"),
        (None, None, "allage"),
    ):
        for h in (5, 10):
            sp = xs_ls_by_age(
                panel,
                age,
                min_age=min_a,
                max_age=max_a,
                score=ret20,
                hold=h,
                cut=cut,
                long_high=True,
            )
            arms.append({"id": f"mom20_ls_{tag}_h{h}", "family": "mom_age_ls", **sp})
            # reverse
            sp_r = xs_ls_by_age(
                panel,
                age,
                min_age=min_a,
                max_age=max_a,
                score=ret20,
                hold=h,
                cut=cut,
                long_high=False,
            )
            arms.append({"id": f"rev20_ls_{tag}_h{h}", "family": "rev_age_ls", **sp_r})

    n_trials = len(arms)
    results = []
    for a in arms:
        tr = a.get("train") or pack([])
        oos = a.get("oos") or pack([])
        v = verdict_arm(oos, n_trials=n_trials, train_mean=tr.get("mean"))
        results.append(
            {
                "id": a["id"],
                "family": a.get("family"),
                "train": tr,
                "oos": oos,
                **v,
            }
        )
    results.sort(
        key=lambda r: (r["verdict"] != "CANDIDATE", -(r["oos"].get("mean") or -9))
    )

    from collections import Counter

    out = {
        "meta": {
            "round": "R11_listing_age",
            "snapshot": str(snap.resolve()),
            "panel": list(panel.shape),
            "range": [str(panel.index.min().date()), str(panel.index.max().date())],
            "cut": str(cut.date()) if hasattr(cut, "date") else str(cut),
            "cost_rt": COST_RT,
            "n_trials": n_trials,
            "novelty": "listing-age / maturity buckets — not A-F retread",
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
    for r in out["candidates"][:10]:
        print(" CAND", r["id"], r["reason"])
    print("train+OOS+", len(out["train_oos_pos"]))
    for r in sorted(
        out["train_oos_pos"],
        key=lambda x: x["oos"].get("mean") or 0,
        reverse=True,
    )[:12]:
        print(
            f"  {r['id']}: oos={r['oos']['mean']:+.4%} n={r['oos']['n']} "
            f"train={r['train']['mean']:+.4%} v={r['verdict']}"
        )
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
