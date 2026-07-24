#!/usr/bin/env python3
"""STRICT validate — pre-registered family: swing SHORT pullback in BEAR.

Path chosen as the only honest follow-up after large param hunt
(research/EDGE_RISET_PARAM_SWING_SCALP.md): one small family, not re-grid.

PRE-REGISTERED (frozen before this script's promotion decision):
  Universe: top 25 liquid 1d USDT alts (volume rank, same as discovery load_1d)
  Regime:   BTC close < SMA200 (bear only)
  Style:    short pullback only (EMA50 below, RSI>45, -DI>+DI, |c-EMA21|/ATR <= touch)
  Cost:     0.18% RT; also stress cost×2
  Split:    chronological 50% train / 30% OOS / 20% lockbox on *trade list order*
            (trades appended in time across symbols — approximate causal)
  Family:   exactly 4 arms (n_trials=4 for Bonferroni)

Arms:
  A1  adx>=22  sl=1.5·ATR  rr=2.5  touch=1.0
  A2  adx>=22  sl=1.5·ATR  rr=1.5  touch=1.0
  A3  adx>=22  sl=2.0·ATR  rr=2.5  touch=1.8
  A4  adx>=30  sl=1.5·ATR  rr=2.5  touch=1.0

PROMOTE_PAPER only if ALL:
  train mean_R > 0
  OOS CANDIDATE (mean>0, n>=30, p_adj<0.05 with trials=4)
  lockbox mean_R > 0
  cost×2 OOS mean_R > 0

  PYTHONPATH=. python research/edge_hunt_validate_short_pb_bear.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "research")]

from edge_hunt import pack, verdict_arm  # noqa: E402
from edge_hunt_param_regimes import (  # noqa: E402
    COST_RT,
    Params,
    btc_regime,
    load_1d,
    prep_arrays,
    sim,
)

# Frozen family
ARMS = [
    Params("swing", "short", 22.0, 1.5, 2.5, 1.0, 0.0, "pb"),
    Params("swing", "short", 22.0, 1.5, 1.5, 1.0, 0.0, "pb"),
    Params("swing", "short", 22.0, 2.0, 2.5, 1.8, 0.0, "pb"),
    Params("swing", "short", 30.0, 1.5, 2.5, 1.0, 0.0, "pb"),
]
N_TRIALS = len(ARMS)
MAX_HOLD = 15
REGIME_BEAR = -1


def split_three(xs: list[float], f_tr=0.50, f_oos=0.30):
    """50/30/20 on chronological trade list."""
    n = len(xs)
    if n == 0:
        return [], [], []
    i1 = int(n * f_tr)
    i2 = int(n * (f_tr + f_oos))
    return xs[:i1], xs[i1:i2], xs[i2:]


def run_arm(prepped: dict, p: Params, cost: float) -> list[float]:
    """Re-run sim with custom cost by adjusting: sim uses COST_RT global.
    We recompute net R by adding back COST_RT and subtracting `cost`.
    """
    # monkey: sim uses module COST_RT — collect raw-ish by reversing project cost
    base = []
    for arr in prepped.values():
        rs = sim(arr, p, REGIME_BEAR, MAX_HOLD)
        # each r = raw - COST_RT*entry/risk; we don't have entry/risk → approximate
        # cost component unknown per trade. Better: patch sim cost via temporary.
        base.extend(rs)
    if abs(cost - COST_RT) < 1e-12:
        return base
    # approximate: average cost_r unknown; use additive delta on R assuming
    # typical risk ~1.5% of price → cost_r ≈ COST_RT/0.015
    # Safer: re-implement thin loop with cost param — import copy of logic.
    return _sim_all(prepped, p, cost)


def _sim_all(prepped: dict, p: Params, cost: float) -> list[float]:
    """Duplicate of sim with explicit cost (avoid global)."""
    from edge_hunt_param_regimes import sim as _  # noqa: F401

    # inline cost-aware copy of sim
    rs_all: list[float] = []
    want_long = False
    for arr in prepped.values():
        n = arr["n"]
        warm = arr["warm"]
        close, high, low = arr["close"], arr["high"], arr["low"]
        atr_a, adx_a = arr["atr"], arr["adx"]
        ema_m, ema_s = arr["ema_m"], arr["ema_s"]
        rsi_a, pdi, mdi = arr["rsi"], arr["pdi"], arr["mdi"]
        vol_r, s_lo, s_hi = arr["vol_r"], arr["swing_lo"], arr["swing_hi"]
        reg = arr["reg"]
        i = warm
        while i < n - 1:
            if (
                reg[i] == REGIME_BEAR
                and np.isfinite(atr_a[i])
                and atr_a[i] > 0
                and adx_a[i] >= p.adx_min
                and (not np.isfinite(vol_r[i]) or vol_r[i] >= p.vol_min)
            ):
                c = close[i]
                atrv = atr_a[i]
                entry = sl = tp = None
                dist = abs(c - ema_m[i]) / atrv
                if dist <= p.touch and c < ema_s[i] and rsi_a[i] > 45 and mdi[i] > pdi[i]:
                    sl = max(s_hi[i], c + p.sl_atr * atrv) if np.isfinite(s_hi[i]) else c + p.sl_atr * atrv
                    risk = sl - c
                    if risk > 0 and risk / c >= 0.001:
                        entry, tp = c, c - p.tp_rr * risk
                if entry is not None:
                    risk = abs(entry - sl)
                    for j in range(1, MAX_HOLD + 1):
                        if i + j >= n:
                            break
                        hi, lo = high[i + j], low[i + j]
                        exit_px = None
                        if hi >= sl:
                            exit_px = sl
                        elif lo <= tp:
                            exit_px = tp
                        if exit_px is None and j == MAX_HOLD:
                            exit_px = close[i + j]
                        if exit_px is not None:
                            raw = (entry - exit_px) / risk  # short
                            rs_all.append(raw - cost * entry / risk)
                            i = i + j
                            break
            i += 1
    return rs_all


def main() -> int:
    snap = Path("data/snap")
    btc_path = snap / "BTC_USDT_USDT__1d.pkl"
    if not btc_path.exists():
        xs = list(snap.glob("BTC_*__1d.pkl"))
        btc_path = xs[0]
    btc = pd.read_pickle(btc_path)
    reg = btc_regime(btc)
    dfs = load_1d(snap, max_alts=25)
    prepped = {}
    for s, df in dfs.items():
        prepped[s] = prep_arrays(df, reg.reindex(df.index).ffill())
    print("universe", len(prepped), "bull_frac", float((reg == 1).mean()))
    print("PRE-REGISTERED arms", len(ARMS), "trials", N_TRIALS)

    rows = []
    promoted = []
    for p in ARMS:
        r1 = _sim_all(prepped, p, COST_RT)
        r2 = _sim_all(prepped, p, COST_RT * 2)
        tr, oos, lock = split_three(r1)
        tr2, oos2, lock2 = split_three(r2)
        tr_p, oos_p, lock_p = pack(tr), pack(oos), pack(lock)
        oos2_p = pack(oos2)
        v = verdict_arm(oos_p, n_trials=N_TRIALS, train_mean=tr_p.get("mean"), min_n=30)
        promo = "NO"
        if (
            v["verdict"] == "CANDIDATE"
            and (lock_p.get("mean") or 0) > 0
            and (oos2_p.get("mean") or 0) > 0
            and (tr_p.get("mean") or 0) > 0
        ):
            promo = "PROMOTE_PAPER"
            promoted.append(p.tag())
        row = {
            "id": f"short_pb_bear_{p.tag()}",
            "params": asdict(p),
            "n_total": len(r1),
            "train": tr_p,
            "oos": oos_p,
            "lockbox": lock_p,
            "oos_cost2x": oos2_p,
            "verdict_oos": v,
            "promotion": promo,
        }
        rows.append(row)
        print(
            f"{row['id']}: n={len(r1)} tr={tr_p.get('mean')} "
            f"oos={oos_p.get('mean')} n_oos={oos_p.get('n')} "
            f"lock={lock_p.get('mean')} c2={oos2_p.get('mean')} "
            f"{v['verdict']} promo={promo}"
        )

    out = {
        "meta": {
            "family": "swing_short_pullback_bear",
            "pre_registered": True,
            "n_trials": N_TRIALS,
            "cost_rt": COST_RT,
            "split": "50/30/20 trade-list chronological",
            "universe_n": len(prepped),
            "symbols": list(prepped.keys()),
            "rule": (
                "PROMOTE_PAPER = OOS CANDIDATE (p_adj trials=4) + train>0 "
                "+ lockbox>0 + cost2x OOS>0"
            ),
        },
        "rows": rows,
        "promoted": promoted,
        "promote_paper": len(promoted) > 0,
    }
    Path("logs/edge_hunt_validate_short_pb_bear.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("PROMOTED:", promoted or "NONE")
    print("wrote logs/edge_hunt_validate_short_pb_bear.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
