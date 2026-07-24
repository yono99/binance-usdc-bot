#!/usr/bin/env python3
"""Fast parameter hunt: SWING + SCALP × BULL/BEAR (vectorized, cost-aware OOS).

Owner request: search params for scalping/swing in bull & bear until edge or honest fail.

Defaults:
  - Regime: BTC close > SMA200 → bull (1), else bear (-1); ffilled to 15m
  - Swing: 1d, top liquid alts
  - Scalp: 15m majors
  - Cost RT 0.18%
  - Compact grid; Bonferroni p_adj
  - CANDIDATE: train mean_R>0, OOS mean_R>0, n_oos>=40, p_adj<0.05

  PYTHONPATH=. python research/edge_hunt_param_regimes.py
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "research")]

from bot.indicators import adx, atr, bollinger, ema, rsi  # noqa: E402
from edge_hunt import pack, verdict_arm  # noqa: E402

COST_RT = 0.0018


def load_1d(snap: Path, max_alts: int = 25, min_bars: int = 400) -> dict[str, pd.DataFrame]:
    scored = []
    for p in sorted(snap.glob("*__1d.pkl")):
        if "BTCDOM" in p.name.upper():
            continue
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if len(df) < min_bars or "close" not in df.columns:
            continue
        vol = float(df["volume"].tail(90).mean()) if "volume" in df.columns else 0
        stem = p.stem.replace("__1d", "")
        parts = stem.split("_")
        sym = (
            f"{'_'.join(parts[:-2])}/{parts[-2]}:{parts[-2]}"
            if len(parts) >= 3 and parts[-1] == parts[-2]
            else stem
        )
        scored.append((vol, sym, df.sort_index()))
    scored.sort(reverse=True, key=lambda x: x[0])
    out = {}
    for _, s, df in scored[:max_alts]:
        out[s] = df[~df.index.duplicated(keep="last")]
    return out


def load_15m(snap: Path) -> dict[str, pd.DataFrame]:
    out = {}
    for p in sorted(snap.glob("*__15m.pkl")):
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if len(df) < 1500:
            continue
        stem = p.stem.replace("__15m", "")
        parts = stem.split("_")
        sym = (
            f"{'_'.join(parts[:-2])}/{parts[-2]}:{parts[-2]}"
            if len(parts) >= 3 and parts[-1] == parts[-2]
            else stem
        )
        # prefer USDT over USDC for same base
        base = sym.split("/")[0]
        if any(base == k.split("/")[0] and "USDT" in k for k in out) and "USDC" in sym:
            continue
        df = df.sort_index()
        out[sym] = df[~df.index.duplicated(keep="last")]
    return out


def btc_regime(btc: pd.DataFrame) -> pd.Series:
    c = btc["close"].astype(float)
    sma = c.rolling(200, min_periods=100).mean()
    return pd.Series(np.where(c > sma, 1, -1), index=btc.index, dtype=float)


@dataclass(frozen=True)
class Params:
    style: str
    side: str  # long|short
    adx_min: float
    sl_atr: float
    tp_rr: float
    touch: float
    vol_min: float
    mode: str  # pb|brk

    def tag(self) -> str:
        return f"{self.side}_{self.mode}_adx{self.adx_min}_sl{self.sl_atr}_rr{self.tp_rr}_t{self.touch}_v{self.vol_min}"


def grids(style: str) -> list[Params]:
    g = []
    if style == "swing":
        # 3*2*2*2*2*2 * 2 sides = 192 → cut to 3*2*2*2*1*2*2 = 96
        for adx_min, sl, rr, touch, vol_min, mode in itertools.product(
            (22.0, 30.0),
            (1.5, 2.0),
            (1.5, 2.5),
            (1.0, 1.8),
            (0.0,),
            ("pb", "brk"),
        ):
            for side in ("long", "short"):
                g.append(Params("swing", side, adx_min, sl, rr, touch, vol_min, mode))
    else:
        # 2*2*2*2*1*2*2 = 64
        for adx_min, sl, rr, touch, vol_min, mode in itertools.product(
            (18.0, 25.0),
            (0.8, 1.2),
            (1.0, 1.5),
            (0.8, 1.2),
            (1.0,),
            ("pb", "brk"),
        ):
            for side in ("long", "short"):
                g.append(Params("scalp", side, adx_min, sl, rr, touch, vol_min, mode))
    return g


def prep_arrays(df: pd.DataFrame, reg: pd.Series) -> dict:
    d = df.copy()
    c = d["close"].astype(float)
    d["ema_m"] = ema(c, 21)
    d["ema_s"] = ema(c, 50)
    d["rsi"] = rsi(c, 14)
    d["atr"] = atr(d, 14)
    adx_v, pdi, mdi = adx(d, 14)
    d["adx"] = adx_v
    d["pdi"] = pdi
    d["mdi"] = mdi
    _, up, lo, _ = bollinger(c, 20, 2.0)
    d["bb_up"], d["bb_lo"] = up, lo
    vol_ma = d["volume"].astype(float).rolling(20).mean()
    d["vol_r"] = d["volume"].astype(float) / vol_ma.replace(0, np.nan)
    d["swing_lo"] = d["low"].rolling(10).min().shift(1)
    d["swing_hi"] = d["high"].rolling(10).max().shift(1)
    r = reg.reindex(d.index).ffill()
    warm = 60
    return {
        "close": d["close"].to_numpy(float),
        "high": d["high"].to_numpy(float),
        "low": d["low"].to_numpy(float),
        "atr": d["atr"].to_numpy(float),
        "adx": d["adx"].to_numpy(float),
        "ema_m": d["ema_m"].to_numpy(float),
        "ema_s": d["ema_s"].to_numpy(float),
        "rsi": d["rsi"].to_numpy(float),
        "pdi": d["pdi"].to_numpy(float),
        "mdi": d["mdi"].to_numpy(float),
        "bb_up": d["bb_up"].to_numpy(float),
        "bb_lo": d["bb_lo"].to_numpy(float),
        "vol_r": d["vol_r"].to_numpy(float),
        "swing_lo": d["swing_lo"].to_numpy(float),
        "swing_hi": d["swing_hi"].to_numpy(float),
        "reg": r.to_numpy(float),
        "warm": warm,
        "n": len(d),
    }


def sim(arr: dict, p: Params, regime_need: int, max_hold: int) -> list[float]:
    n = arr["n"]
    warm = arr["warm"]
    close, high, low = arr["close"], arr["high"], arr["low"]
    atr_a, adx_a = arr["atr"], arr["adx"]
    ema_m, ema_s = arr["ema_m"], arr["ema_s"]
    rsi_a, pdi, mdi = arr["rsi"], arr["pdi"], arr["mdi"]
    bb_up, bb_lo = arr["bb_up"], arr["bb_lo"]
    vol_r, s_lo, s_hi = arr["vol_r"], arr["swing_lo"], arr["swing_hi"]
    reg = arr["reg"]
    want_long = p.side == "long"
    rs: list[float] = []
    i = warm
    while i < n - 1:
        # signal only if flat
        if (
            reg[i] == regime_need
            and np.isfinite(atr_a[i])
            and atr_a[i] > 0
            and adx_a[i] >= p.adx_min
            and (not np.isfinite(vol_r[i]) or vol_r[i] >= p.vol_min)
        ):
            c = close[i]
            atrv = atr_a[i]
            entry = sl = tp = None
            if p.mode == "brk":
                if want_long and c > bb_up[i] and c > ema_s[i]:
                    sl = c - p.sl_atr * atrv
                    risk = c - sl
                    if risk > 0:
                        entry, tp = c, c + p.tp_rr * risk
                elif (not want_long) and c < bb_lo[i] and c < ema_s[i]:
                    sl = c + p.sl_atr * atrv
                    risk = sl - c
                    if risk > 0:
                        entry, tp = c, c - p.tp_rr * risk
            else:
                dist = abs(c - ema_m[i]) / atrv
                if dist <= p.touch:
                    if want_long and c > ema_s[i] and rsi_a[i] < 55 and pdi[i] > mdi[i]:
                        sl = min(s_lo[i], c - p.sl_atr * atrv) if np.isfinite(s_lo[i]) else c - p.sl_atr * atrv
                        risk = c - sl
                        if risk > 0 and risk / c >= 0.001:
                            entry, tp = c, c + p.tp_rr * risk
                    elif (not want_long) and c < ema_s[i] and rsi_a[i] > 45 and mdi[i] > pdi[i]:
                        sl = max(s_hi[i], c + p.sl_atr * atrv) if np.isfinite(s_hi[i]) else c + p.sl_atr * atrv
                        risk = sl - c
                        if risk > 0 and risk / c >= 0.001:
                            entry, tp = c, c - p.tp_rr * risk
            if entry is not None:
                risk = abs(entry - sl)
                # walk forward for exit
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
                        cost_r = COST_RT * entry / risk
                        rs.append(raw - cost_r)
                        i = i + j  # skip to after exit
                        break
                else:
                    pass
        i += 1
    return rs


def split70(xs: list[float]) -> tuple[list[float], list[float]]:
    if not xs:
        return [], []
    k = int(len(xs) * 0.70)
    return xs[:k], xs[k:]


def run_style(style: str, dfs: dict[str, pd.DataFrame], btc_reg: pd.Series, max_hold: int) -> dict:
    g = grids(style)
    prepped = {}
    for s, df in dfs.items():
        reg = btc_reg.reindex(df.index, method="ffill") if style == "scalp" else btc_reg.reindex(df.index).ffill()
        prepped[s] = prep_arrays(df, reg)
    n_trials = len(g) * 2
    print(f"{style}: params={len(g)} ×2 = {n_trials} trials, syms={len(prepped)}", flush=True)
    results = []
    for regime_need, rname in ((1, "bull"), (-1, "bear")):
        for pi, p in enumerate(g):
            all_r: list[float] = []
            for arr in prepped.values():
                all_r.extend(sim(arr, p, regime_need, max_hold))
            tr, oos = split70(all_r)
            tr_p, oos_p = pack(tr), pack(oos)
            v = verdict_arm(oos_p, n_trials=n_trials, train_mean=tr_p.get("mean"), min_n=40)
            results.append(
                {
                    "id": f"{style}_{rname}_{p.tag()}",
                    "style": style,
                    "regime": rname,
                    "params": asdict(p),
                    "train": tr_p,
                    "oos": oos_p,
                    **v,
                }
            )
        print(f"  done {style} {rname}", flush=True)
    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["oos"].get("mean") or -9)))
    cands = [r for r in results if r["verdict"] == "CANDIDATE"]
    both = [
        r
        for r in results
        if (r["train"].get("mean") or 0) > 0 and (r["oos"].get("mean") or 0) > 0
    ]
    return {
        "n_trials": n_trials,
        "n_grid": len(g),
        "verdicts": dict(Counter(r["verdict"] for r in results)),
        "candidates": cands,
        "train_oos_pos_top": sorted(both, key=lambda x: x["oos"].get("mean") or 0, reverse=True)[:25],
        "best_oos": results[:20],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=str(ROOT / "data" / "snap"))
    ap.add_argument("--mode", choices=("all", "swing", "scalp"), default="all")
    ap.add_argument("--max-alts", type=int, default=25)
    ap.add_argument("--out", default=str(ROOT / "logs" / "edge_hunt_param_regimes.json"))
    args = ap.parse_args()
    snap = Path(args.snap)

    btc_path = next(
        (p for p in [snap / "BTC_USDT_USDT__1d.pkl", snap / "BTC_USDC_USDC__1d.pkl"] if p.exists()),
        None,
    )
    if not btc_path:
        xs = list(snap.glob("BTC_*__1d.pkl"))
        btc_path = xs[0] if xs else None
    if not btc_path:
        print("no BTC")
        return 2
    btc = pd.read_pickle(btc_path)
    reg = btc_regime(btc)
    print("BTC bull_frac", round(float((reg == 1).mean()), 3))

    out: dict = {
        "meta": {
            "cost_rt": COST_RT,
            "regime": "BTC>SMA200 bull",
            "promote_paper": False,
            "note": "discovery only; strict lockbox not auto-promote",
        }
    }
    if args.mode in ("all", "swing"):
        dfs = load_1d(snap, max_alts=args.max_alts)
        print("swing n", len(dfs))
        out["swing"] = run_style("swing", dfs, reg, max_hold=15)
        print("SWING", out["swing"]["verdicts"], "CAND", len(out["swing"]["candidates"]))
    if args.mode in ("all", "scalp"):
        dfs15 = load_15m(snap)
        if not dfs15:
            out["scalp"] = {"error": "no_15m", "candidates": [], "verdicts": {}}
        else:
            print("scalp", list(dfs15.keys()))
            out["scalp"] = run_style("scalp", dfs15, reg, max_hold=12)
            print("SCALP", out["scalp"]["verdicts"], "CAND", len(out["scalp"]["candidates"]))

    all_c = []
    for k in ("swing", "scalp"):
        if k in out and "candidates" in out[k]:
            all_c.extend(out[k]["candidates"])
    out["meta"]["n_candidates_total"] = len(all_c)
    out["candidates_all"] = all_c
    Path(args.out).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("TOTAL CAND", len(all_c))
    for c in all_c[:12]:
        print(" ", c["id"], c.get("reason"))
    if not all_c:
        for k in ("swing", "scalp"):
            if k not in out or "train_oos_pos_top" not in out[k]:
                continue
            print("TOP", k)
            for r in out[k]["train_oos_pos_top"][:8]:
                print(
                    f"  {r['id']}: oos={r['oos'].get('mean')} n={r['oos'].get('n')} "
                    f"tr={r['train'].get('mean')} v={r['verdict']}"
                )
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
