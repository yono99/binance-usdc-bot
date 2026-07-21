#!/usr/bin/env python3
"""Edge-hunt loop — hipotesis struktural BARU di luar antrian H24–H32 / H-CYC yang sudah mati.

Disiplin (METHODOLOGY):
  - OOS walk-forward / chronological split = hakim
  - Fee+slippage RT default 0.18% (0.04+0.05 per leg ×2)
  - Multiple trials → Bonferroni p_adj
  - CANDIDATE hanya bila mean OOS>0, n≥30, p_adj<0.05, train/OOS konsisten tanda
  - "Tidak ketemu" = hasil valid; dokumentasikan

  python edge_hunt.py
  python edge_hunt.py --round all --out logs/edge_hunt.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from bot.xsectional import (
    align_close_panel,
    build_grid,
    sharpe,
    t_pvalue,
    volume_panel,
    walk_forward_scores,
    walk_forward_xs,
    xs_returns,
    _rebalance_times,
)


ROOT = Path(__file__).resolve().parent.parent
SNAP = ROOT / "data" / "snap"
COST_RT = 0.0018  # 0.04% fee + 0.05% slip per leg × 2


def pack(x: list | np.ndarray) -> dict:
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    n = int(len(a))
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "win": None, "p_pos": 1.0, "sharpe": None}
    sd = float(a.std(ddof=1)) if n > 1 else 0.0
    mean = float(a.mean())
    p = t_pvalue(a)
    return {
        "n": n,
        "mean": mean,
        "median": float(np.median(a)),
        "win": float((a > 0).mean()),
        "p_pos": p,
        "sharpe": float(mean / sd) if sd > 0 else None,
    }


def verdict_arm(stats: dict, n_trials: int, *, min_n: int = 30, train_mean: float | None = None) -> dict:
    n = stats.get("n") or 0
    mean = stats.get("mean")
    if mean is None or n < min_n:
        return {
            "verdict": "INCONCLUSIVE",
            "reason": f"n={n} < {min_n} or no mean",
            "p_adj": 1.0,
        }
    p = stats.get("p_pos", 1.0) or 1.0
    p_adj = min(p * max(1, n_trials), 1.0)
    if mean <= 0:
        return {"verdict": "REJECTED", "reason": f"OOS mean {mean:+.4%} ≤ 0 n={n}", "p_adj": p_adj}
    if p_adj >= 0.05:
        return {
            "verdict": "NOT_PROVEN",
            "reason": f"OOS mean {mean:+.4%} n={n} but p_adj={p_adj:.3f} (trials={n_trials})",
            "p_adj": p_adj,
        }
    if train_mean is not None and train_mean <= 0:
        return {
            "verdict": "NOT_PROVEN",
            "reason": f"OOS ok but train mean {train_mean:+.4%} ≤ 0 (inconsistent)",
            "p_adj": p_adj,
        }
    return {
        "verdict": "CANDIDATE",
        "reason": f"OOS mean {mean:+.4%} n={n} p_adj={p_adj:.4f} — still needs cost×2 + lockbox + paper",
        "p_adj": p_adj,
    }


def load_daily(
    snap: Path,
    min_bars: int = 400,
    max_alts: int | None = None,
    lookback_days: int = 1600,
) -> tuple[pd.DataFrame, pd.Series]:
    """Panel close daily alts + BTC. Top liquid, recent window (avoid empty intersection)."""
    dfs: dict[str, pd.DataFrame] = {}
    btc = None
    paths = sorted(snap.glob("*__1d.pkl"))
    for p in paths:
        stem = p.stem
        if "BTCDOM" in stem.upper():
            continue
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if "close" not in df.columns or len(df) < min_bars:
            continue
        base = stem.replace("__1d", "")
        parts = base.split("_")
        if len(parts) >= 3 and parts[-1] == parts[-2]:
            sym = f"{'_'.join(parts[:-2])}/{parts[-2]}:{parts[-2]}"
        else:
            sym = base
        if stem.upper().startswith("BTC_") and "DOM" not in stem.upper():
            if btc is None or len(df) > len(btc):
                btc = df["close"].astype(float).sort_index()
                btc = btc[~btc.index.duplicated(keep="last")]
            continue
        dfs[sym] = df
    if not dfs:
        raise SystemExit("no alt series")
    # rank by recent volume
    scored = []
    for s, df in dfs.items():
        vol = float(df["volume"].tail(90).mean()) if "volume" in df.columns else 0.0
        scored.append((vol, s))
    scored.sort(reverse=True)
    if max_alts:
        scored = scored[:max_alts]
    dfs = {s: dfs[s] for _, s in scored}

    # recent window only → more symbols share coverage
    end = max(df.index.max() for df in dfs.values())
    start = end - pd.Timedelta(days=lookback_days)
    trimmed = {}
    for s, df in dfs.items():
        d = df.loc[df.index >= start].copy()
        if len(d) >= min(min_bars, 250):
            trimmed[s] = d
    # coverage vs union of dates in window (not full history)
    panel = align_close_panel(trimmed, min_coverage=0.70)
    if panel.shape[1] < 20:
        # fallback: take top 80 by volume that exist on last common 900 days
        end2 = max(df.index.max() for df in trimmed.values())
        start2 = end2 - pd.Timedelta(days=900)
        slim = {}
        for _, s in scored[:120]:
            if s not in trimmed:
                continue
            d = trimmed[s].loc[trimmed[s].index >= start2]
            if len(d) >= 400:
                slim[s] = d
        panel = align_close_panel(slim, min_coverage=0.60)
    if btc is None:
        raise SystemExit("BTC not found in snap")
    btc = btc.reindex(panel.index).ffill()
    return panel, btc


def load_1h(snap: Path) -> dict[str, pd.DataFrame]:
    out = {}
    for p in sorted(snap.glob("*__1h.pkl")):
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if len(df) < 500 or "close" not in df.columns:
            continue
        stem = p.stem.replace("__1h", "")
        parts = stem.split("_")
        if len(parts) >= 3 and parts[-1] == parts[-2]:
            sym = f"{'_'.join(parts[:-2])}/{parts[-2]}:{parts[-2]}"
        else:
            sym = stem
        out[sym] = df
    return out


# ─── Round A: calendar / seasonality ─────────────────────────────────────────

def round_a_calendar(panel: pd.DataFrame, btc: pd.Series, oos_frac: float = 0.30) -> dict:
    """H-EH-01 DoW · H-EH-02 turn-of-month · H-EH-03 month-end vs mid."""
    close = panel.to_numpy(dtype=float)
    idx = panel.index
    # equal-weight alt daily ret
    ret = pd.DataFrame(close, index=idx).pct_change()
    ew = ret.mean(axis=1).dropna()
    btc_r = btc.pct_change().reindex(ew.index).fillna(0.0)
    # residual alt vs btc
    resid = ew - btc_r

    cut = ew.index[int(len(ew) * (1 - oos_frac))]
    trials = 0
    arms = {}

    # DoW: long EW Mon..Sun (cost once per day as if enter/exit — harsh but honest for day-hold)
    for dow in range(7):
        trials += 1
        mask = ew.index.dayofweek == dow
        for split, m in (
            ("train", mask & (ew.index < cut)),
            ("oos", mask & (ew.index >= cut)),
        ):
            r = ew[m].to_numpy() - COST_RT  # pay RT if day-trade
            arms.setdefault(f"dow_{dow}_long_ew", {})[split] = pack(r)
            arms.setdefault(f"dow_{dow}_long_resid", {})[split] = pack(resid[m].to_numpy() - COST_RT)

    # Turn of month: last 3 / first 3 calendar days
    def tom_mask(side: str) -> pd.Series:
        d = ew.index.day
        if side == "first3":
            return d <= 3
        if side == "last3":
            # last 3 days of month
            next_m = (ew.index + pd.Timedelta(days=1)).month
            return (next_m != ew.index.month) | ((ew.index + pd.Timedelta(days=2)).month != ew.index.month) | (
                (ew.index + pd.Timedelta(days=3)).month != ew.index.month
            )
        return pd.Series(False, index=ew.index)

    # cleaner last-3: day >= 28 often wrong; use month-end
    last3 = ew.index.to_series().groupby([ew.index.year, ew.index.month]).transform(
        lambda s: s.isin(s.nlargest(3).index) if hasattr(s, "nlargest") else False
    )
    # fix: for DatetimeIndex
    last3_mask = pd.Series(False, index=ew.index)
    first3_mask = ew.index.day <= 3
    for (y, m), g in ew.groupby([ew.index.year, ew.index.month]):
        last3_mask.loc[g.index[-3:]] = True

    for name, mask in (("tom_first3", first3_mask), ("tom_last3", last3_mask)):
        trials += 1
        for split, m in (("train", mask & (ew.index < cut)), ("oos", mask & (ew.index >= cut))):
            arms.setdefault(f"{name}_long_ew", {})[split] = pack(ew[m].to_numpy() - COST_RT)

    # Weekend effect crypto: Fri vs Mon (UTC)
    for name, dows in (("fri", 4), ("mon", 0), ("sat", 5), ("sun", 6)):
        trials += 1
        mask = ew.index.dayofweek == dows
        for split, m in (("train", mask & (ew.index < cut)), ("oos", mask & (ew.index >= cut))):
            arms.setdefault(f"cal_{name}_long_ew", {})[split] = pack(ew[m].to_numpy() - COST_RT)

    # Score each arm on OOS
    results = []
    for name, sp in arms.items():
        oos = sp.get("oos") or {"n": 0}
        tr = sp.get("train") or {}
        v = verdict_arm(oos, n_trials=max(trials, 1), train_mean=tr.get("mean"))
        results.append({"id": name, "train": tr, "oos": oos, **v})

    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["oos"].get("mean") or -9)))
    return {
        "round": "A_calendar",
        "hypotheses": ["H-EH-01 DoW", "H-EH-02 turn-of-month", "H-EH-03 weekend/session-day"],
        "n_days": len(ew),
        "cut": str(cut),
        "n_trials": trials,
        "arms": results,
        "best": results[0] if results else None,
    }


# ─── Round B: cross-sectional short-term reverse + residual ──────────────────

def round_b_xs_reversal(panel: pd.DataFrame, btc: pd.Series) -> dict:
    """H-EH-04 XS mean-reversion short lookback (reverse momentum).
    H-EH-05 residual ST reverse vs BTC.
    """
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    # find btc column if in panel else use series
    cost = COST_RT
    # grid short LB × holds
    grid = build_grid([1, 2, 3, 5], [1, 2, 3, 5])
    train, test = 300, 120
    windows, oos = walk_forward_xs(
        close, grid, quantile=0.3, cost_frac=cost, train_len=train, test_len=test,
        min_rebalances=6, reverse=True,
    )
    oos_p = pack(oos)
    v = verdict_arm(oos_p, n_trials=len(grid), train_mean=None)
    # train aggregate from windows IS not stored as returns — use window oos only
    # Also residual scores: score = -(ret_alt - beta*ret_btc) short-term
    rets = np.vstack([np.full(N, np.nan), close[1:] / close[:-1] - 1.0])
    btc_a = btc.reindex(panel.index).ffill().to_numpy()
    btc_r = np.r_[np.nan, btc_a[1:] / btc_a[:-1] - 1.0]
    # residual 3d sum
    score = np.full_like(close, np.nan)
    lb = 3
    for t in range(lb, T):
        # simple residual = ret_sum - btc_sum (beta=1 approx)
        r_sum = close[t] / close[t - lb] - 1.0
        b_sum = btc_a[t] / btc_a[t - lb] - 1.0 if btc_a[t - lb] > 0 else 0.0
        score[t] = -(r_sum - b_sum)  # reverse residual: long underperformers
    from bot.xsectional import xs_returns_score
    holds = [1, 2, 5]
    panels = {"resid_rev3": score}
    w2, oos2 = walk_forward_scores(
        close, panels, holds, quantile=0.3, cost_frac=cost,
        train_len=train, test_len=test, min_rebalances=6, reverse=False,
    )
    oos2_p = pack(oos2)
    v2 = verdict_arm(oos2_p, n_trials=len(holds), train_mean=None)

    return {
        "round": "B_xs_reversal",
        "hypotheses": [
            "H-EH-04 XS short-term reverse (lookback 1-5d)",
            "H-EH-05 residual reverse vs BTC (3d)",
        ],
        "n_symbols": N,
        "n_bars": T,
        "xs_reverse": {
            "grid": len(grid),
            "n_windows": len(windows),
            "oos": oos_p,
            **v,
            "window_params": [w.params for w in windows[-5:]],
        },
        "resid_reverse": {
            "n_windows": len(w2),
            "oos": oos2_p,
            **v2,
            "window_params": [w.params for w in w2[-5:]],
        },
    }


# ─── Round C: post-dump bounce long ──────────────────────────────────────────

def round_c_dump_bounce(panel: pd.DataFrame, btc: pd.Series, oos_frac: float = 0.30) -> dict:
    """H-EH-06: long EW alts on BTC dump day (opposite of rejected short) — bounce harvest.
    H-EH-07: long strongest alts on dump (relative strength survivors).
    """
    btc_r = btc.pct_change()
    dump = btc_r <= -0.02
    idx = panel.index
    dump = dump.reindex(idx).fillna(False)
    close = panel
    ret1 = close.pct_change().shift(-1)  # next-day forward — careful: for study at t use ret from t to t+h
    # better: for each dump day t, hold h from close t
    cut = idx[int(len(idx) * (1 - oos_frac))]
    arms = {}
    trials = 0
    for hold in (1, 3, 5, 7):
        trials += 2
        long_all_tr, long_all_oos = [], []
        long_strong_tr, long_strong_oos = [], []
        for t in idx[dump.reindex(idx).fillna(False).to_numpy()]:
            if t not in close.index:
                continue
            loc = close.index.get_loc(t)
            if not isinstance(loc, (int, np.integer)):
                continue
            loc = int(loc)
            if loc + hold >= len(close):
                continue
            row0 = close.iloc[loc].to_numpy(dtype=float)
            row1 = close.iloc[loc + hold].to_numpy(dtype=float)
            fwd = row1 / row0 - 1.0
            valid = np.isfinite(fwd)
            if valid.sum() < 5:
                continue
            fv = fwd[valid]
            ew = float(fv.mean()) - COST_RT
            # strong = top 30% same-day ret (relative strength into dump)
            if loc >= 1:
                day_ret = close.iloc[loc].to_numpy() / close.iloc[loc - 1].to_numpy() - 1.0
                day_ret = day_ret[valid]
                k = max(1, int(len(day_ret) * 0.3))
                order = np.argsort(day_ret)
                strong = float(fv[order[-k:]].mean()) - COST_RT
            else:
                strong = ew
            if t < cut:
                long_all_tr.append(ew)
                long_strong_tr.append(strong)
            else:
                long_all_oos.append(ew)
                long_strong_oos.append(strong)
        arms[f"long_ew_after_dump_h{hold}"] = {
            "train": pack(long_all_tr),
            "oos": pack(long_all_oos),
        }
        arms[f"long_strong_after_dump_h{hold}"] = {
            "train": pack(long_strong_tr),
            "oos": pack(long_strong_oos),
        }

    results = []
    for name, sp in arms.items():
        oos = sp["oos"]
        tr = sp["train"]
        v = verdict_arm(oos, n_trials=trials, train_mean=tr.get("mean"))
        results.append({"id": name, "train": tr, "oos": oos, **v})
    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["oos"].get("mean") or -9)))
    return {
        "round": "C_dump_bounce",
        "hypotheses": [
            "H-EH-06 long EW alts after BTC dump (bounce)",
            "H-EH-07 long relative-strong alts after dump",
        ],
        "n_dump_days": int(dump.sum()),
        "cut": str(cut),
        "n_trials": trials,
        "arms": results,
        "best": results[0] if results else None,
    }


# ─── Round D: vol compression → breakout ─────────────────────────────────────

def round_d_vol_breakout(panel: pd.DataFrame, oos_frac: float = 0.30) -> dict:
    """H-EH-08: after n-day range compression, long breakout direction (close > high range).
    Per-symbol then EW across triggers.
    """
    idx = panel.index
    cut = idx[int(len(idx) * (1 - oos_frac))]
    # use first 80 symbols for speed
    cols = list(panel.columns)[:80]
    holds = [1, 3, 5]
    trials = len(holds) * 2  # two thr
    arms: dict[str, dict] = {}
    for win in (10, 20):
        for hold in holds:
            key = f"compress{win}_break_h{hold}"
            tr, oos = [], []
            for col in cols:
                s = panel[col].astype(float)
                # rolling high-low range / price
                hi = s.rolling(win).max()
                lo = s.rolling(win).min()
                rng = (hi - lo) / s
                # compression: rng in bottom 20% of its 60d history
                q = rng.rolling(60).quantile(0.2)
                compress = rng <= q
                # breakout up: close > prior win high
                brk_up = s > hi.shift(1)
                brk_dn = s < lo.shift(1)
                sig_up = compress.shift(1) & brk_up  # causal: compress known prev, break today
                # entry close today, exit hold later
                for t in s.index[sig_up.fillna(False)]:
                    loc = s.index.get_loc(t)
                    if not isinstance(loc, (int, np.integer)):
                        continue
                    loc = int(loc)
                    if loc + hold >= len(s) or loc < win + 60:
                        continue
                    fwd = float(s.iloc[loc + hold] / s.iloc[loc] - 1.0) - COST_RT
                    if t < cut:
                        tr.append(fwd)
                    else:
                        oos.append(fwd)
            arms[key] = {"train": pack(tr), "oos": pack(oos)}

    results = []
    for name, sp in arms.items():
        v = verdict_arm(sp["oos"], n_trials=len(arms), train_mean=sp["train"].get("mean"))
        results.append({"id": name, "train": sp["train"], "oos": sp["oos"], **v})
    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["oos"].get("mean") or -9)))
    return {
        "round": "D_vol_breakout",
        "hypotheses": ["H-EH-08 range compression breakout (long)"],
        "n_symbols_used": len(cols),
        "cut": str(cut),
        "n_trials": len(arms),
        "arms": results,
        "best": results[0] if results else None,
    }


# ─── Round E: time-of-day 1h ─────────────────────────────────────────────────

def round_e_session_1h(dfs: dict[str, pd.DataFrame], oos_frac: float = 0.30) -> dict:
    """H-EH-09: session buckets UTC (Asia 0-8, EU 8-16, US 16-24) long/short EW.
    H-EH-10: hour-of-day effects on BTC/ETH/SOL only.
    """
    if not dfs:
        return {"round": "E_session_1h", "error": "no 1h data", "arms": []}
    # align closes
    panel = align_close_panel(dfs, min_coverage=0.7)
    if panel.empty or len(panel) < 500:
        return {"round": "E_session_1h", "error": "thin 1h panel", "arms": []}
    ret = panel.pct_change()
    ew = ret.mean(axis=1).dropna()
    cut = ew.index[int(len(ew) * (1 - oos_frac))]
    hours = ew.index.hour
    arms = {}
    sessions = {
        "asia": (hours >= 0) & (hours < 8),
        "eu": (hours >= 8) & (hours < 16),
        "us": (hours >= 16) & (hours < 24),
    }
    # hold = 1 bar (1h), cost RT each hour is harsh → also report GROSS for structure
    for name, mask in sessions.items():
        for split, m in (
            ("train", mask & (ew.index < cut)),
            ("oos", mask & (ew.index >= cut)),
        ):
            r = ew[m].to_numpy()
            arms.setdefault(f"sess_{name}_long_gross", {})[split] = pack(r)
            arms.setdefault(f"sess_{name}_long_net", {})[split] = pack(r - COST_RT)

    # best hours top/bottom by train, evaluate OOS (data-mined → high n_trials)
    train_mask = ew.index < cut
    hour_means = {}
    for h in range(24):
        m = train_mask & (ew.index.hour == h)
        if m.sum() >= 20:
            hour_means[h] = float(ew[m].mean())
    ranked = sorted(hour_means, key=hour_means.get)
    trials = 24 + 6
    results = []
    for name, sp in arms.items():
        oos = sp.get("oos") or {"n": 0}
        # net arms use cost; for gross structure use lower bar
        nt = trials if "net" in name else 1
        v = verdict_arm(oos, n_trials=nt, train_mean=(sp.get("train") or {}).get("mean"), min_n=50)
        results.append({"id": name, "train": sp.get("train"), "oos": oos, **v})

    # long best-3 train hours / short worst-3 — OOS only
    if len(ranked) >= 6:
        best_h, worst_h = ranked[-3:], ranked[:3]
        for label, hs, sign in (("best3", best_h, 1), ("worst3_short", worst_h, -1)):
            oos_m = (ew.index >= cut) & ew.index.hour.isin(hs)
            tr_m = (ew.index < cut) & ew.index.hour.isin(hs)
            oos_r = sign * ew[oos_m].to_numpy() - COST_RT
            tr_r = sign * ew[tr_m].to_numpy() - COST_RT
            oos_p, tr_p = pack(oos_r), pack(tr_r)
            v = verdict_arm(oos_p, n_trials=trials, train_mean=tr_p.get("mean"), min_n=50)
            results.append({
                "id": f"hour_{label}_net",
                "hours": list(hs),
                "train": tr_p,
                "oos": oos_p,
                **v,
            })

    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["oos"].get("mean") or -9)))
    return {
        "round": "E_session_1h",
        "hypotheses": ["H-EH-09 session buckets 1h", "H-EH-10 hour-of-day mined"],
        "n_bars": len(ew),
        "n_symbols": panel.shape[1],
        "cut": str(cut),
        "n_trials": trials,
        "arms": results,
        "best": results[0] if results else None,
    }


# ─── Round F: momentum crash / dispersion ────────────────────────────────────

def round_f_dispersion(panel: pd.DataFrame) -> dict:
    """H-EH-11: when cross-sectional dispersion high, reverse (mean-reversion regime).
    When low, momentum. Regime switch XS.
    """
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    # dispersion of 5d returns
    mom5 = np.full((T, N), np.nan)
    for t in range(5, T):
        mom5[t] = close[t] / close[t - 5] - 1.0
    disp = np.nanstd(mom5, axis=1)
    # high disp = top 30% rolling 120d
    disp_s = pd.Series(disp)
    thr = disp_s.rolling(120, min_periods=60).quantile(0.7).to_numpy()
    high = disp >= thr
    cost = COST_RT
    grid_rev = build_grid([3, 5], [1, 3])
    grid_mom = build_grid([10, 20], [5, 10])
    train, test = 300, 120

    # custom walk: only rebalance on high-disp days for reverse
    def walk_masked(grid, reverse, mask):
        windows = []
        oos_all = []
        max_lb = max(g["lookback"] for g in grid)
        start = max_lb + 120
        while start + train + test <= T:
            tr0, tr1 = start, start + train
            te0, te1 = tr1, tr1 + test
            best, best_s = None, float("-inf")
            for g in grid:
                times = [t for t in _rebalance_times(tr0, tr1, g["lookback"], g["hold"]) if mask[t]]
                if len(times) < 5:
                    continue
                r = xs_returns(close, times, g["lookback"], g["hold"], 0.3, cost, reverse)
                if len(r) < 5:
                    continue
                s = sharpe(r)
                if s > best_s:
                    best, best_s, best_n = g, s, len(r)
            if best is not None:
                te_times = [t for t in _rebalance_times(te0, te1, best["lookback"], best["hold"]) if mask[t]]
                te_r = xs_returns(close, te_times, best["lookback"], best["hold"], 0.3, cost, reverse)
                oos_all.extend(te_r.tolist())
                windows.append({"params": best, "oos_n": len(te_r), "oos_mean": float(np.mean(te_r)) if len(te_r) else None})
            start += test
        return windows, np.asarray(oos_all, dtype=float)

    w_hi, oos_hi = walk_masked(grid_rev, True, high)
    w_lo, oos_lo = walk_masked(grid_mom, False, ~high & np.isfinite(disp))
    p_hi, p_lo = pack(oos_hi), pack(oos_lo)
    v_hi = verdict_arm(p_hi, n_trials=len(grid_rev), min_n=20)
    v_lo = verdict_arm(p_lo, n_trials=len(grid_mom), min_n=20)
    return {
        "round": "F_dispersion_regime",
        "hypotheses": [
            "H-EH-11a high dispersion → XS reverse",
            "H-EH-11b low dispersion → XS momentum",
        ],
        "high_disp_reverse": {"oos": p_hi, "n_windows": len(w_hi), **v_hi},
        "low_disp_momentum": {"oos": p_lo, "n_windows": len(w_lo), **v_lo},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir", default=str(SNAP))
    ap.add_argument("--max-alts", type=int, default=200, help="cap alts for speed")
    ap.add_argument("--round", default="all",
                    choices=["all", "A", "B", "C", "D", "E", "F"])
    ap.add_argument("--out", default="logs/edge_hunt.json")
    args = ap.parse_args()
    snap = Path(args.snapshot_dir)

    print(f"Loading daily from {snap} (max_alts={args.max_alts})…")
    panel, btc = load_daily(snap, min_bars=400, max_alts=args.max_alts)
    print(f"Panel {panel.shape[0]} days × {panel.shape[1]} alts; BTC len={len(btc)}")

    out: dict = {
        "meta": {
            "snapshot": str(snap),
            "panel": list(panel.shape),
            "cost_rt": COST_RT,
            "rule": "CANDIDATE = OOS mean>0, n≥30, p_adj<0.05, train mean>0 when available",
        },
        "rounds": {},
    }
    want = set("ABCDEF") if args.round == "all" else {args.round}

    if "A" in want:
        print("\n=== Round A: calendar ===")
        ra = round_a_calendar(panel, btc)
        out["rounds"]["A"] = ra
        print("best", ra.get("best", {}).get("id"), ra.get("best", {}).get("verdict"),
              ra.get("best", {}).get("oos", {}).get("mean"))

    if "B" in want:
        print("\n=== Round B: XS reversal ===")
        rb = round_b_xs_reversal(panel, btc)
        out["rounds"]["B"] = rb
        print("xs_reverse", rb["xs_reverse"]["verdict"], rb["xs_reverse"]["oos"].get("mean"))
        print("resid_reverse", rb["resid_reverse"]["verdict"], rb["resid_reverse"]["oos"].get("mean"))

    if "C" in want:
        print("\n=== Round C: dump bounce ===")
        rc = round_c_dump_bounce(panel, btc)
        out["rounds"]["C"] = rc
        print("best", rc.get("best", {}).get("id"), rc.get("best", {}).get("verdict"),
              rc.get("best", {}).get("oos", {}).get("mean"))

    if "D" in want:
        print("\n=== Round D: vol breakout ===")
        rd = round_d_vol_breakout(panel)
        out["rounds"]["D"] = rd
        print("best", rd.get("best", {}).get("id"), rd.get("best", {}).get("verdict"),
              rd.get("best", {}).get("oos", {}).get("mean"))

    if "E" in want:
        print("\n=== Round E: session 1h ===")
        h1 = load_1h(snap)
        print(f"1h symbols={len(h1)}")
        re = round_e_session_1h(h1)
        out["rounds"]["E"] = re
        if re.get("best"):
            print("best", re["best"].get("id"), re["best"].get("verdict"),
                  re["best"].get("oos", {}).get("mean"))
        else:
            print(re.get("error") or re)

    if "F" in want:
        print("\n=== Round F: dispersion regime ===")
        rf = round_f_dispersion(panel)
        out["rounds"]["F"] = rf
        print("high_disp", rf["high_disp_reverse"]["verdict"], rf["high_disp_reverse"]["oos"].get("mean"))
        print("low_disp", rf["low_disp_momentum"]["verdict"], rf["low_disp_momentum"]["oos"].get("mean"))

    # summary
    candidates = []
    rejected = []
    for rk, rv in out["rounds"].items():
        if "arms" in rv:
            for a in rv["arms"]:
                row = {"round": rk, "id": a.get("id"), "verdict": a.get("verdict"),
                       "oos_mean": (a.get("oos") or {}).get("mean"),
                       "oos_n": (a.get("oos") or {}).get("n"),
                       "reason": a.get("reason")}
                if a.get("verdict") == "CANDIDATE":
                    candidates.append(row)
                else:
                    rejected.append(row)
        for k in ("xs_reverse", "resid_reverse", "high_disp_reverse", "low_disp_momentum"):
            if k in rv:
                row = {"round": rk, "id": k, "verdict": rv[k].get("verdict"),
                       "oos_mean": (rv[k].get("oos") or {}).get("mean"),
                       "oos_n": (rv[k].get("oos") or {}).get("n"),
                       "reason": rv[k].get("reason")}
                if rv[k].get("verdict") == "CANDIDATE":
                    candidates.append(row)
                else:
                    rejected.append(row)

    out["summary"] = {
        "n_candidates": len(candidates),
        "candidates": candidates,
        "n_arms_logged": len(rejected) + len(candidates),
        "top_rejected_by_oos_mean": sorted(
            [r for r in rejected if r.get("oos_mean") is not None],
            key=lambda x: -x["oos_mean"],
        )[:15],
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n=== SUMMARY candidates={len(candidates)} ===")
    for c in candidates:
        print(" CANDIDATE", c)
    if not candidates:
        print(" No CANDIDATE this pass. Top OOS means (still rejected/not proven):")
        for r in out["summary"]["top_rejected_by_oos_mean"][:8]:
            print(f"  {r['round']} {r['id']}: mean={r['oos_mean']:+.4%} n={r['oos_n']} {r['verdict']}")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
