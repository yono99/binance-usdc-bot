#!/usr/bin/env python3
"""Multi-family edge search — competitive but disciplined (NOT one thin path).

Owner feedback: one family alone is under-competitive. This harness runs a
PORTFOLIO of pre-registered structural ideas in parallel, each with a small
arm count, shared cost/OOS rules, and a global scoreboard.

Philosophy:
  - Many *ideas*, few *parameters per idea* (anti data-dredging)
  - Families are structurally different (not EMA length remixes)
  - Majors/large vs mid split reported separately where relevant
  - PROMOTE_PAPER only per-family bar: train>0, OOS CANDIDATE, lock>0, cost2x OOS>0
  - Family Bonferroni = arms inside that family only
  - Global honesty: report how many families tested (multiple-testing note)

Families (pre-registered):
  F1  short_pb_bear_majors   — short pullback when BTC bear, majors+large only
  F2  long_pb_bull_majors    — long pullback when BTC bull, majors+large only
  F3  short_pb_bear_broad    — same as F1 on broad liquid 25 (replication check)
  F4  resid_fade_basket      — fade residual-z vs BTC (XS), hold 3/5, z=1.5/2.0
  F5  breakout_bull_majors   — long BB break only in bull majors
  F6  risk_skip_breadth      — META: long EW alts skip when breadth low (filter, not entry alpha)

  PYTHONPATH=. python research/edge_hunt_multifamily.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "research")]

from bot.indicators import adx, atr, bollinger, ema, rsi  # noqa: E402
from edge_hunt import pack, verdict_arm  # noqa: E402
from edge_hunt_param_regimes import (  # noqa: E402
    COST_RT,
    Params,
    btc_regime,
    load_1d,
    prep_arrays,
)

MAJORS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "ADA", "DOT",
    "LTC", "ATOM", "NEAR", "UNI", "AAVE", "APT", "ARB", "OP", "SUI", "TRX",
    "FIL", "INJ", "TIA", "SEI", "WLD", "PEPE", "SHIB", "BONK",
}
# bases that are clearly micro/meme style for "broad" vs majors split
# (heuristic; majors list is allowlist for F1/F2/F5)


def base_of(sym: str) -> str:
    return sym.split("/")[0].upper().replace("1000", "").replace("1M", "")


def is_majors_large(sym: str) -> bool:
    b = base_of(sym)
    # strip numeric prefixes already partially
    for m in MAJORS:
        if b == m or b.endswith(m) or m in b:
            return True
    # common large without 1000 prefix handled
    if b in ("SHIB", "BONK", "PEPE", "FLOKI"):
        return True
    return b in MAJORS


def split_three(xs: list[float], f_tr=0.50, f_oos=0.30):
    n = len(xs)
    if n == 0:
        return [], [], []
    i1 = int(n * f_tr)
    i2 = int(n * (f_tr + f_oos))
    return xs[:i1], xs[i1:i2], xs[i2:]


def sim_short_or_long_pb(
    arr: dict,
    *,
    side: str,
    regime_need: int,
    adx_min: float,
    sl_atr: float,
    tp_rr: float,
    touch: float,
    max_hold: int,
    cost: float,
) -> list[float]:
    want_long = side == "long"
    n = arr["n"]
    warm = arr["warm"]
    close, high, low = arr["close"], arr["high"], arr["low"]
    atr_a, adx_a = arr["atr"], arr["adx"]
    ema_m, ema_s = arr["ema_m"], arr["ema_s"]
    rsi_a, pdi, mdi = arr["rsi"], arr["pdi"], arr["mdi"]
    vol_r, s_lo, s_hi = arr["vol_r"], arr["swing_lo"], arr["swing_hi"]
    reg = arr["reg"]
    rs: list[float] = []
    i = warm
    while i < n - 1:
        if (
            reg[i] == regime_need
            and np.isfinite(atr_a[i])
            and atr_a[i] > 0
            and adx_a[i] >= adx_min
        ):
            c = close[i]
            atrv = atr_a[i]
            entry = sl = tp = None
            dist = abs(c - ema_m[i]) / atrv
            if dist <= touch:
                if want_long and c > ema_s[i] and rsi_a[i] < 55 and pdi[i] > mdi[i]:
                    sl = min(s_lo[i], c - sl_atr * atrv) if np.isfinite(s_lo[i]) else c - sl_atr * atrv
                    risk = c - sl
                    if risk > 0 and risk / c >= 0.001:
                        entry, tp = c, c + tp_rr * risk
                elif (not want_long) and c < ema_s[i] and rsi_a[i] > 45 and mdi[i] > pdi[i]:
                    sl = max(s_hi[i], c + sl_atr * atrv) if np.isfinite(s_hi[i]) else c + sl_atr * atrv
                    risk = sl - c
                    if risk > 0 and risk / c >= 0.001:
                        entry, tp = c, c - tp_rr * risk
            if entry is not None:
                risk = abs(entry - sl)
                for j in range(1, max_hold + 1):
                    if i + j >= n:
                        break
                    hi, lo = high[i + j], low[i + j]
                    exit_px = None
                    if want_long:
                        if lo <= sl:
                            exit_px = sl
                        elif hi >= tp:
                            exit_px = tp
                    else:
                        if hi >= sl:
                            exit_px = sl
                        elif lo <= tp:
                            exit_px = tp
                    if exit_px is None and j == max_hold:
                        exit_px = close[i + j]
                    if exit_px is not None:
                        raw = (exit_px - entry) / risk if want_long else (entry - exit_px) / risk
                        rs.append(raw - cost * entry / risk)
                        i = i + j
                        break
        i += 1
    return rs


def sim_breakout_long_bull(arr: dict, adx_min: float, sl_atr: float, tp_rr: float, max_hold: int, cost: float) -> list[float]:
    n = arr["n"]
    warm = arr["warm"]
    close, high, low = arr["close"], arr["high"], arr["low"]
    atr_a, adx_a = arr["atr"], arr["adx"]
    ema_s = arr["ema_s"]
    bb_up = arr["bb_up"]
    vol_r = arr["vol_r"]
    reg = arr["reg"]
    rs = []
    i = warm
    while i < n - 1:
        if (
            reg[i] == 1
            and np.isfinite(atr_a[i])
            and atr_a[i] > 0
            and adx_a[i] >= adx_min
            and close[i] > bb_up[i]
            and close[i] > ema_s[i]
            and (not np.isfinite(vol_r[i]) or vol_r[i] >= 1.0)
        ):
            c = close[i]
            atrv = atr_a[i]
            sl = c - sl_atr * atrv
            risk = c - sl
            if risk > 0:
                tp = c + tp_rr * risk
                for j in range(1, max_hold + 1):
                    if i + j >= n:
                        break
                    hi, lo = high[i + j], low[i + j]
                    exit_px = None
                    if lo <= sl:
                        exit_px = sl
                    elif hi >= tp:
                        exit_px = tp
                    if exit_px is None and j == max_hold:
                        exit_px = close[i + j]
                    if exit_px is not None:
                        raw = (exit_px - c) / risk
                        rs.append(raw - cost * c / risk)
                        i = i + j
                        break
        i += 1
    return rs


def resid_fade_trades(
    panel: pd.DataFrame,
    btc: pd.Series,
    *,
    z_thr: float,
    hold: int,
    cost: float,
) -> list[float]:
    """Day-level equal-weight residual fade: long weak residual, short strong."""
    ret = panel.pct_change()
    b = btc.reindex(panel.index).ffill().pct_change()
    resid = ret.sub(b, axis=0)
    z = resid / (resid.rolling(20).std() + 1e-12)
    fwd = panel.shift(-hold) / panel - 1.0
    rs = []
    for t in panel.index:
        if t + pd.Timedelta(days=hold) > panel.index[-1]:
            break
        zz = z.loc[t]
        ff = fwd.loc[t]
        ok = zz.notna() & ff.notna()
        if ok.sum() < 8:
            continue
        long_m = ok & (zz < -z_thr)
        short_m = ok & (zz > z_thr)
        parts = []
        if long_m.any():
            parts.extend(ff[long_m].tolist())
        if short_m.any():
            parts.extend((-ff[short_m]).tolist())
        if not parts:
            continue
        rs.append(float(np.mean(parts)) - cost)
    return rs


def long_ew_with_breadth_skip(
    panel: pd.DataFrame,
    btc: pd.Series,
    *,
    hold: int,
    cost: float,
    skip_breadth_lo: bool,
) -> tuple[list[float], list[float]]:
    """Baseline long EW hold vs skip when breadth (frac>SMA50) bottom 30% of 100d."""
    ret = panel.pct_change()
    # breadth: fraction of alts above SMA50
    sma = panel.rolling(50).mean()
    above = (panel > sma).astype(float)
    breadth = above.mean(axis=1)
    q30 = breadth.rolling(100, min_periods=50).quantile(0.30)
    lo = breadth <= q30
    fwd = panel.shift(-hold) / panel - 1.0
    base, filt = [], []
    for t in panel.index:
        if t + pd.Timedelta(days=hold) > panel.index[-1]:
            break
        row = fwd.loc[t].replace([np.inf, -np.inf], np.nan).dropna()
        if len(row) < 5:
            continue
        r = float(row.mean()) - cost
        base.append(r)
        if skip_breadth_lo and bool(lo.loc[t]):
            continue  # skip entry
        filt.append(r)
    return base, filt


def judge_family(arm_results: list[dict], n_trials: int) -> dict:
    promoted = []
    for a in arm_results:
        v = a["verdict_oos"]
        ok = (
            v["verdict"] == "CANDIDATE"
            and (a["lockbox"].get("mean") or 0) > 0
            and (a["oos_cost2x"].get("mean") or 0) > 0
            and (a["train"].get("mean") or 0) > 0
        )
        a["promotion"] = "PROMOTE_PAPER" if ok else "NO"
        if ok:
            promoted.append(a["id"])
    return {
        "n_arms": len(arm_results),
        "n_trials": n_trials,
        "promoted": promoted,
        "arms": arm_results,
    }


def pack_splits(r1: list[float], r2: list[float], n_trials: int) -> dict:
    tr, oos, lock = split_three(r1)
    _, oos2, _ = split_three(r2)
    tr_p, oos_p, lock_p = pack(tr), pack(oos), pack(lock)
    oos2_p = pack(oos2)
    v = verdict_arm(oos_p, n_trials=n_trials, train_mean=tr_p.get("mean"), min_n=30)
    return {
        "n_total": len(r1),
        "train": tr_p,
        "oos": oos_p,
        "lockbox": lock_p,
        "oos_cost2x": oos2_p,
        "verdict_oos": v,
    }


def run_pb_family(
    name: str,
    prepped: dict[str, dict],
    *,
    side: str,
    regime_need: int,
    specs: list[tuple],
) -> dict:
    """specs: list of (adx, sl, rr, touch) """
    n_trials = len(specs)
    arms = []
    for adx_min, sl, rr, touch in specs:
        r1, r2 = [], []
        for arr in prepped.values():
            r1.extend(
                sim_short_or_long_pb(
                    arr,
                    side=side,
                    regime_need=regime_need,
                    adx_min=adx_min,
                    sl_atr=sl,
                    tp_rr=rr,
                    touch=touch,
                    max_hold=15,
                    cost=COST_RT,
                )
            )
            r2.extend(
                sim_short_or_long_pb(
                    arr,
                    side=side,
                    regime_need=regime_need,
                    adx_min=adx_min,
                    sl_atr=sl,
                    tp_rr=rr,
                    touch=touch,
                    max_hold=15,
                    cost=COST_RT * 2,
                )
            )
        row = pack_splits(r1, r2, n_trials)
        row["id"] = f"{name}_{side}_adx{adx_min}_sl{sl}_rr{rr}_t{touch}"
        row["params"] = {
            "side": side,
            "regime_need": regime_need,
            "adx_min": adx_min,
            "sl_atr": sl,
            "tp_rr": rr,
            "touch": touch,
        }
        arms.append(row)
        print(
            f"  {row['id']}: n={row['n_total']} tr={row['train'].get('mean')} "
            f"oos={row['oos'].get('mean')} n_oos={row['oos'].get('n')} "
            f"lock={row['lockbox'].get('mean')} {row['verdict_oos']['verdict']}"
        )
    return judge_family(arms, n_trials)


def main() -> int:
    snap = Path("data/snap")
    btc_path = snap / "BTC_USDT_USDT__1d.pkl"
    if not btc_path.exists():
        btc_path = list(snap.glob("BTC_*__1d.pkl"))[0]
    btc_df = pd.read_pickle(btc_path)
    reg = btc_regime(btc_df)
    btc_close = btc_df["close"].astype(float)

    # universes
    all_dfs = load_1d(snap, max_alts=40)
    majors = {s: df for s, df in all_dfs.items() if is_majors_large(s)}
    # ensure we have enough majors; if rank missed them, load explicitly by name
    if len(majors) < 8:
        for p in snap.glob("*__1d.pkl"):
            if "BTCDOM" in p.name:
                continue
            stem = p.stem.replace("__1d", "")
            parts = stem.split("_")
            sym = (
                f"{'_'.join(parts[:-2])}/{parts[-2]}:{parts[-2]}"
                if len(parts) >= 3 and parts[-1] == parts[-2]
                else stem
            )
            if is_majors_large(sym) and sym not in majors:
                try:
                    df = pd.read_pickle(p)
                    if len(df) >= 400:
                        majors[sym] = df.sort_index()
                except Exception:
                    pass
    broad = dict(list(all_dfs.items())[:25])

    def prep_map(dfs: dict) -> dict:
        out = {}
        for s, df in dfs.items():
            out[s] = prep_arrays(df, reg.reindex(df.index).ffill())
        return out

    prep_maj = prep_map(majors)
    prep_br = prep_map(broad)
    print("universe majors", len(prep_maj), list(prep_maj.keys())[:15])
    print("universe broad", len(prep_br))

    # panel for F4/F6
    # align closes for majors
    closes = {}
    for s, df in majors.items():
        closes[s] = df["close"].astype(float)
    if len(closes) >= 5:
        panel = pd.DataFrame(closes).sort_index().ffill(limit=3)
    else:
        panel = pd.DataFrame({s: df["close"].astype(float) for s, df in broad.items()}).sort_index().ffill(limit=3)

    families = {}

    # F1 short pb bear majors — 4 arms
    print("=== F1 short_pb_bear_majors ===")
    specs = [(22, 1.5, 2.5, 1.0), (22, 1.5, 1.5, 1.0), (22, 2.0, 2.5, 1.8), (30, 1.5, 2.5, 1.0)]
    families["F1_short_pb_bear_majors"] = run_pb_family(
        "F1", prep_maj, side="short", regime_need=-1, specs=specs
    )

    # F2 long pb bull majors — 4 arms (mirror)
    print("=== F2 long_pb_bull_majors ===")
    families["F2_long_pb_bull_majors"] = run_pb_family(
        "F2", prep_maj, side="long", regime_need=1, specs=specs
    )

    # F3 short pb bear broad — 2 arms only (replication of best specs)
    print("=== F3 short_pb_bear_broad ===")
    families["F3_short_pb_bear_broad"] = run_pb_family(
        "F3",
        prep_br,
        side="short",
        regime_need=-1,
        specs=[(22, 1.5, 1.5, 1.0), (22, 1.5, 2.5, 1.0)],
    )

    # F4 residual fade basket — 4 arms
    print("=== F4 resid_fade_basket ===")
    f4_arms = []
    f4_specs = [(1.5, 3), (1.5, 5), (2.0, 3), (2.0, 5)]
    for z_thr, hold in f4_specs:
        r1 = resid_fade_trades(panel, btc_close, z_thr=z_thr, hold=hold, cost=COST_RT)
        r2 = resid_fade_trades(panel, btc_close, z_thr=z_thr, hold=hold, cost=COST_RT * 2)
        row = pack_splits(r1, r2, n_trials=len(f4_specs))
        row["id"] = f"F4_residz{z_thr}_h{hold}"
        row["params"] = {"z_thr": z_thr, "hold": hold}
        f4_arms.append(row)
        print(
            f"  {row['id']}: n={row['n_total']} tr={row['train'].get('mean')} "
            f"oos={row['oos'].get('mean')} {row['verdict_oos']['verdict']}"
        )
    families["F4_resid_fade_basket"] = judge_family(f4_arms, len(f4_specs))

    # F5 breakout long bull majors — 3 arms
    print("=== F5 breakout_bull_majors ===")
    f5_arms = []
    f5_specs = [(20, 1.5, 2.0), (25, 1.5, 2.5), (25, 2.0, 2.0)]
    for adx_min, sl, rr in f5_specs:
        r1, r2 = [], []
        for arr in prep_maj.values():
            r1.extend(sim_breakout_long_bull(arr, adx_min, sl, rr, 15, COST_RT))
            r2.extend(sim_breakout_long_bull(arr, adx_min, sl, rr, 15, COST_RT * 2))
        row = pack_splits(r1, r2, n_trials=len(f5_specs))
        row["id"] = f"F5_brk_bull_adx{adx_min}_sl{sl}_rr{rr}"
        row["params"] = {"adx_min": adx_min, "sl_atr": sl, "tp_rr": rr}
        f5_arms.append(row)
        print(
            f"  {row['id']}: n={row['n_total']} tr={row['train'].get('mean')} "
            f"oos={row['oos'].get('mean')} {row['verdict_oos']['verdict']}"
        )
    families["F5_breakout_bull_majors"] = judge_family(f5_arms, len(f5_specs))

    # F6 risk filter meta — not entry alpha
    print("=== F6 risk_skip_breadth (meta) ===")
    base, filt = long_ew_with_breadth_skip(panel, btc_close, hold=3, cost=COST_RT, skip_breadth_lo=True)
    # compare mean and worst; promote_filter if filt maxDD better conceptually via worst_r and mean not much worse
    pb, pf = pack(base), pack(filt)
    meta = {
        "id": "F6_long_ew_h3_skip_breadth_lo",
        "baseline": pb,
        "filtered": pf,
        "delta_mean": (None if pb.get("mean") is None or pf.get("mean") is None else pf["mean"] - pb["mean"]),
        "delta_worst": (
            None
            if pb.get("worst_r") is None or pf.get("worst_r") is None
            else pf["worst_r"] - pb["worst_r"]
        ),
        "note": "PROMOTE_FILTER if worst improves and mean not collapse; already have similar filter paper",
        "promotion": "META_ONLY",
    }
    # simple rule: filtered worst better (higher) and mean >= baseline - 0.05
    if (
        pb.get("worst_r") is not None
        and pf.get("worst_r") is not None
        and pf["worst_r"] > pb["worst_r"]
        and pf.get("mean") is not None
        and pb.get("mean") is not None
        and pf["mean"] >= pb["mean"] - 0.05
    ):
        meta["promotion"] = "PROMOTE_FILTER_CANDIDATE"
    families["F6_risk_skip_breadth"] = {
        "n_arms": 1,
        "n_trials": 1,
        "promoted": [meta["id"]] if meta["promotion"].startswith("PROMOTE_FILTER") else [],
        "arms": [meta],
        "kind": "filter_meta",
    }
    print("  F6", meta["promotion"], "base_mean", pb.get("mean"), "filt_mean", pf.get("mean"))

    # scoreboard
    entry_promoted = []
    filter_promoted = []
    leans = []
    for fname, fam in families.items():
        if fam.get("kind") == "filter_meta":
            filter_promoted.extend(fam.get("promoted") or [])
            continue
        entry_promoted.extend(fam.get("promoted") or [])
        for a in fam.get("arms") or []:
            o, t, l = a.get("oos") or {}, a.get("train") or {}, a.get("lockbox") or {}
            if (t.get("mean") or 0) > 0 and (o.get("mean") or 0) > 0:
                leans.append(
                    {
                        "family": fname,
                        "id": a["id"],
                        "train": t.get("mean"),
                        "oos": o.get("mean"),
                        "n_oos": o.get("n"),
                        "lock": l.get("mean"),
                        "p_adj": (a.get("verdict_oos") or {}).get("p_adj"),
                        "verdict": (a.get("verdict_oos") or {}).get("verdict"),
                        "promo": a.get("promotion"),
                    }
                )

    leans.sort(key=lambda x: (x.get("oos") or -9), reverse=True)
    out = {
        "meta": {
            "spec": "multi-family competitive search",
            "cost_rt": COST_RT,
            "n_families_entry": 5,
            "n_families_meta": 1,
            "promote_paper": len(entry_promoted) > 0,
            "entry_promoted": entry_promoted,
            "filter_promoted": filter_promoted,
            "note": (
                "Multiple families tested in parallel; each family has own Bonferroni. "
                "Cross-family multiple testing still applies for global claims."
            ),
        },
        "universes": {
            "majors_n": len(prep_maj),
            "majors": list(prep_maj.keys()),
            "broad_n": len(prep_br),
        },
        "families": families,
        "scoreboard_leans": leans[:20],
    }
    Path("logs/edge_hunt_multifamily.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("\n=== SCOREBOARD ===")
    print("ENTRY PROMOTED:", entry_promoted or "NONE")
    print("FILTER PROMOTED:", filter_promoted or "NONE")
    print("Top leans train+OOS:")
    for x in leans[:12]:
        print(
            f"  {x['family']} {x['id']}: oos={x['oos']} n={x['n_oos']} "
            f"tr={x['train']} lock={x['lock']} p_adj={x['p_adj']} {x['verdict']} promo={x['promo']}"
        )
    print("wrote logs/edge_hunt_multifamily.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
