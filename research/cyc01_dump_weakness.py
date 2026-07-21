#!/usr/bin/env python3
"""P0 — H-CYC-01: BTC dump + alt relative weakness → short alt?

Ukur (bukan deploy). Spek: memory/CRYPTO_CYCLE_KNOWLEDGE.md

Hipotesis pemilik:
  BTC turun ≳2% → alt sering turun lebih dalam. Short alt yang LEBIH lemah
  dari BTC (relative weakness), bukan sembarang alt.

Arms (semua entry di close bar dump, tanpa lookahead):
  A  short_weak     — short kuantil alt paling lemah vs BTC pada hari dump
  B  short_all      — short equal-weight semua alt (null: dump saja, tanpa filter)
  C  short_strong   — short kuantil alt paling kuat (kontrol terbalik)
  D  short_random   — short k alt acak (seed tetap; null ranking)
  E  short_btc      — short BTC saja pada dump
  F  short_preweak  — short alt yang SUDAH lemah (resid vs BTC) di lookback pra-dump
                     DAN relatif lemah di hari dump

Walk-forward: split kronologis train 70% / OOS 30% (lockbox ekor).
Juga full-sample deskriptif + p-value (uji-t satu sisi mean>0 untuk short PnL).

Pakai:
  python cyc01_dump_weakness.py
  python cyc01_dump_weakness.py --snapshot-dir data/snap_smallcap1400 --dump-pct 2.0
  python cyc01_dump_weakness.py --tf 1h --snapshot-dir data/snap --holds 4 8 24
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

from bot.xsectional import align_close_panel


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def _sym_from_pickle_name(name: str, tf: str) -> str:
    """BTC_USDT_USDT__1d.pkl → BTC/USDT:USDT (ccxt-ish key for panel)."""
    stem = name
    suffix = f"__{tf}"
    if stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    # BASE_QUOTE_SETTLE
    parts = stem.split("_")
    if len(parts) >= 3 and parts[-1] == parts[-2]:
        base = "_".join(parts[:-2])
        quote = parts[-2]
        return f"{base}/{quote}:{quote}"
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return stem


def load_panel(snapshot_dir: Path, tf: str, min_bars: int) -> tuple[pd.DataFrame, str | None]:
    dfs: dict[str, pd.DataFrame] = {}
    btc_key = None
    for p in sorted(snapshot_dir.glob(f"*__{tf}.pkl")):
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if df is None or len(df) < min_bars or "close" not in getattr(df, "columns", []):
            continue
        sym = _sym_from_pickle_name(p.stem, tf)
        dfs[sym] = df
        base = sym.split("/")[0].upper()
        if base == "BTC" and "DOM" not in base and "BTCDOM" not in sym.upper():
            btc_key = sym
        # prefer pure BTC over BTCDOM
        if p.stem.upper().startswith("BTC_USDT") or p.stem.upper().startswith("BTC_USDC"):
            if "DOM" not in p.stem.upper():
                btc_key = sym
    if not dfs:
        raise SystemExit(f"no OHLCV in {snapshot_dir} tf={tf}")
    if btc_key is None:
        # fallback: column name containing BTC but not DOM
        panel_probe = align_close_panel(dfs, min_coverage=0.5)
        for c in panel_probe.columns:
            u = c.upper()
            if "BTC" in u and "DOM" not in u:
                btc_key = c
                break
    if btc_key is None:
        raise SystemExit("BTC series not found in snapshot")
    panel = align_close_panel(dfs, min_coverage=0.85)
    if btc_key not in panel.columns:
        raise SystemExit(f"BTC key {btc_key} dropped by align; cols={list(panel.columns)[:8]}…")
    return panel, btc_key


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _t_pvalue_one_sided(x: np.ndarray) -> float:
    """H0: mean <= 0 (short PnL should be >0 if edge)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return 1.0
    sd = x.std(ddof=1)
    if sd <= 0:
        return 0.0 if x.mean() > 0 else 1.0
    t = x.mean() / (sd / math.sqrt(n))
    return 0.5 * math.erfc(t / math.sqrt(2.0))


def _summarize(rets: np.ndarray) -> dict:
    r = np.asarray(rets, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "std": None, "win": None,
                "sum": None, "p": None, "sharpe": None}
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    mean = float(r.mean())
    return {
        "n": n,
        "mean": mean,
        "median": float(np.median(r)),
        "std": sd,
        "win": float((r > 0).mean()),
        "sum": float(r.sum()),
        "p": _t_pvalue_one_sided(r),
        "sharpe": (mean / sd) if sd > 0 else None,
    }


def _perm_test(a: np.ndarray, b: np.ndarray, n_perm: int = 2000, seed: int = 42) -> float:
    """One-sided: is mean(a) > mean(b)? p via permutation of labels."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    a = a[np.isfinite(a)]
    b = b[np.isfinite(b)]
    if len(a) < 3 or len(b) < 3:
        return 1.0
    obs = a.mean() - b.mean()
    pool = np.concatenate([a, b])
    na = len(a)
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        if pool[:na].mean() - pool[na:].mean() >= obs:
            hits += 1
    return (hits + 1) / (n_perm + 1)


# ---------------------------------------------------------------------------
# Core experiment
# ---------------------------------------------------------------------------

@dataclass
class ArmResult:
    arm: str
    hold: int
    split: str  # full | train | oos
    n: int
    mean: float | None
    median: float | None
    std: float | None
    win: float | None
    sum: float | None
    p: float | None
    sharpe: float | None


def _cost_roundtrip(fee_pct: float, slip_pct: float) -> float:
    # short open+close: 2 legs, fee+slip each side approx
    return 2.0 * (fee_pct + slip_pct) / 100.0


def event_dump_indices(btc_ret: np.ndarray, dump_thr: float, min_gap: int) -> np.ndarray:
    """Indices t where btc_ret[t] <= -dump_thr. Optional min_gap bars between events."""
    raw = np.where(np.isfinite(btc_ret) & (btc_ret <= -abs(dump_thr)))[0]
    if min_gap <= 1 or len(raw) == 0:
        return raw
    kept = [int(raw[0])]
    for i in raw[1:]:
        if int(i) - kept[-1] >= min_gap:
            kept.append(int(i))
    return np.asarray(kept, dtype=int)


def run_arms(
    close: np.ndarray,
    btc_col: int,
    dump_thr: float,
    holds: list[int],
    quantile: float,
    preweak_lb: int,
    cost: float,
    min_gap: int,
    idx_lo: int,
    idx_hi: int,
    split_name: str,
    rng_seed: int = 7,
) -> list[ArmResult]:
    """Generate arm returns for dump events with t in [idx_lo, idx_hi)."""
    T, N = close.shape
    # daily simple returns (causal: ret[t] uses close[t]/close[t-1])
    ret = np.full_like(close, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    btc_ret = ret[:, btc_col]

    events = event_dump_indices(btc_ret, dump_thr, min_gap)
    events = events[(events >= idx_lo) & (events < idx_hi)]
    # need room for max hold forward
    max_h = max(holds)
    events = events[events + max_h < T]
    # need history for preweak
    events = events[events >= max(2, preweak_lb + 1)]

    alt_cols = [j for j in range(N) if j != btc_col]
    results: list[ArmResult] = []
    rng = np.random.default_rng(rng_seed)

    for hold in holds:
        buckets: dict[str, list[float]] = {
            "short_weak": [],
            "short_all": [],
            "short_strong": [],
            "short_random": [],
            "short_btc": [],
            "short_preweak": [],
        }
        for t in events:
            # relative weakness same bar: more negative (ret_alt - ret_btc) = weaker
            rel = ret[t, :] - btc_ret[t]
            fwd = close[t + hold] / close[t] - 1.0  # raw long return
            # short PnL ≈ -fwd - cost
            valid_alt = [j for j in alt_cols if np.isfinite(rel[j]) and np.isfinite(fwd[j])]
            if len(valid_alt) < 4:
                continue
            rel_v = np.array([rel[j] for j in valid_alt])
            fwd_v = np.array([fwd[j] for j in valid_alt])
            k = max(1, int(len(valid_alt) * quantile))
            order = np.argsort(rel_v)  # ascending: weakest first
            weak_idx = order[:k]
            strong_idx = order[-k:]
            rnd = rng.choice(len(valid_alt), size=k, replace=False)

            def short_mean(ix) -> float:
                return float((-fwd_v[ix]).mean() - cost)

            buckets["short_weak"].append(short_mean(weak_idx))
            buckets["short_strong"].append(short_mean(strong_idx))
            buckets["short_random"].append(short_mean(rnd))
            buckets["short_all"].append(float((-fwd_v).mean() - cost))
            if np.isfinite(fwd[btc_col]):
                buckets["short_btc"].append(float(-fwd[btc_col] - cost))

            # preweak: residual mom vs btc over lookback ending at t-1 (no same-bar peek for rank base)
            t0 = t - preweak_lb
            if t0 < 1:
                continue
            past_alt = close[t - 1, valid_alt] / close[t0, valid_alt] - 1.0
            past_btc = close[t - 1, btc_col] / close[t0, btc_col] - 1.0
            resid = past_alt - past_btc
            # already weak: resid < 0, and same-day still among weakest half of those
            pre = np.where(np.isfinite(resid) & (resid < 0))[0]
            if len(pre) < 2:
                continue
            # among preweak, pick weakest same-day rel
            rel_pre = rel_v[pre]
            order_pre = np.argsort(rel_pre)
            k2 = max(1, int(len(pre) * quantile))
            pick = pre[order_pre[:k2]]
            buckets["short_preweak"].append(short_mean(pick))

        for arm, xs_ in buckets.items():
            s = _summarize(np.asarray(xs_, dtype=float))
            results.append(ArmResult(
                arm=arm, hold=hold, split=split_name,
                n=s["n"], mean=s["mean"], median=s["median"], std=s["std"],
                win=s["win"], sum=s["sum"], p=s["p"], sharpe=s["sharpe"],
            ))
    return results


def collect_arm_series(
    close: np.ndarray,
    btc_col: int,
    dump_thr: float,
    hold: int,
    quantile: float,
    cost: float,
    min_gap: int,
    idx_lo: int,
    idx_hi: int,
    arm: str,
    preweak_lb: int = 5,
    rng_seed: int = 7,
) -> np.ndarray:
    """Return 1d array of per-event PnL for one arm (for perm tests)."""
    T, N = close.shape
    ret = np.full_like(close, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    btc_ret = ret[:, btc_col]
    events = event_dump_indices(btc_ret, dump_thr, min_gap)
    events = events[(events >= idx_lo) & (events < idx_hi) & (events + hold < T)]
    events = events[events >= max(2, preweak_lb + 1)]
    alt_cols = [j for j in range(N) if j != btc_col]
    rng = np.random.default_rng(rng_seed)
    out: list[float] = []
    for t in events:
        rel = ret[t, :] - btc_ret[t]
        fwd = close[t + hold] / close[t] - 1.0
        valid_alt = [j for j in alt_cols if np.isfinite(rel[j]) and np.isfinite(fwd[j])]
        if len(valid_alt) < 4:
            continue
        rel_v = np.array([rel[j] for j in valid_alt])
        fwd_v = np.array([fwd[j] for j in valid_alt])
        k = max(1, int(len(valid_alt) * quantile))
        order = np.argsort(rel_v)

        def short_mean(ix) -> float:
            return float((-fwd_v[ix]).mean() - cost)

        if arm == "short_weak":
            out.append(short_mean(order[:k]))
        elif arm == "short_strong":
            out.append(short_mean(order[-k:]))
        elif arm == "short_random":
            out.append(short_mean(rng.choice(len(valid_alt), size=k, replace=False)))
        elif arm == "short_all":
            out.append(float((-fwd_v).mean() - cost))
        elif arm == "short_btc":
            if np.isfinite(fwd[btc_col]):
                out.append(float(-fwd[btc_col] - cost))
        elif arm == "short_preweak":
            t0 = t - preweak_lb
            past_alt = close[t - 1, valid_alt] / close[t0, valid_alt] - 1.0
            past_btc = close[t - 1, btc_col] / close[t0, btc_col] - 1.0
            resid = past_alt - past_btc
            pre = np.where(np.isfinite(resid) & (resid < 0))[0]
            if len(pre) < 2:
                continue
            rel_pre = rel_v[pre]
            order_pre = np.argsort(rel_pre)
            k2 = max(1, int(len(pre) * quantile))
            out.append(short_mean(pre[order_pre[:k2]]))
    return np.asarray(out, dtype=float)


def descriptive_dump_stats(close: np.ndarray, btc_col: int, dump_thr: float, min_gap: int) -> dict:
    """Is alt beta>1 on dump days? (descriptive, not a trade)."""
    ret = np.full_like(close, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    btc_ret = ret[:, btc_col]
    events = event_dump_indices(btc_ret, dump_thr, min_gap)
    events = events[events >= 1]
    alt_cols = [j for j in range(close.shape[1]) if j != btc_col]
    btc_m, alt_m, deeper = [], [], []
    for t in events:
        br = btc_ret[t]
        if not np.isfinite(br):
            continue
        ar = ret[t, alt_cols]
        ar = ar[np.isfinite(ar)]
        if len(ar) == 0:
            continue
        am = float(ar.mean())
        btc_m.append(float(br))
        alt_m.append(am)
        deeper.append(1.0 if am < br else 0.0)  # alt more negative
    return {
        "n_dump_days": len(btc_m),
        "mean_btc_ret": float(np.mean(btc_m)) if btc_m else None,
        "mean_alt_ret": float(np.mean(alt_m)) if alt_m else None,
        "frac_alt_deeper": float(np.mean(deeper)) if deeper else None,
        "mean_rel_weakness": (
            float(np.mean(np.array(alt_m) - np.array(btc_m))) if btc_m else None
        ),
    }


def verdict_from_oos(oos_rows: list[ArmResult], p_weak_vs_all: float | None) -> dict:
    """Honest verdict for short_weak primary arm across holds — pick best hold by OOS mean
    only for reporting, but require p<0.05 and beat short_all."""
    weak = [r for r in oos_rows if r.arm == "short_weak" and r.n >= 15]
    if not weak:
        return {
            "verdict": "INCONCLUSIVE",
            "reason": "OOS n<15 for short_weak (sampel dump terlalu tipis)",
        }
    # primary: hold with max OOS n among those with mean reported
    best = max(weak, key=lambda r: (r.mean is not None, r.mean or -1e9, r.n))
    all_same = next((r for r in oos_rows if r.arm == "short_all" and r.hold == best.hold), None)
    if best.mean is None:
        return {"verdict": "INCONCLUSIVE", "reason": "no mean"}
    beat_all = all_same is not None and all_same.mean is not None and best.mean > all_same.mean
    sig = best.p is not None and best.p < 0.05
    pos = best.mean > 0
    if pos and sig and beat_all and (p_weak_vs_all is None or p_weak_vs_all < 0.05):
        return {
            "verdict": "CANDIDATE",
            "reason": (
                f"short_weak OOS mean={best.mean:+.4%} hold={best.hold}d n={best.n} "
                f"p={best.p:.4f}; beats short_all; perm p_weak>all={p_weak_vs_all}"
            ),
            "best_hold": best.hold,
        }
    if pos and (sig or beat_all):
        return {
            "verdict": "NOT_PROVEN",
            "reason": (
                f"arah positif di OOS (mean={best.mean:+.4%} hold={best.hold}) "
                f"tapi belum lolos ketat (p={best.p}, beat_all={beat_all}, "
                f"perm={p_weak_vs_all})"
            ),
            "best_hold": best.hold,
        }
    return {
        "verdict": "REJECTED",
        "reason": (
            f"short_weak OOS mean={best.mean:+.4%} hold={best.hold}d n={best.n} p={best.p} "
            f"— tidak ada edge tradeable setelah biaya"
        ),
        "best_hold": best.hold,
    }


def fmt_row(r: ArmResult) -> str:
    if r.n == 0 or r.mean is None:
        return f"  {r.arm:14s} hold={r.hold:<3d} split={r.split:5s} n=0"
    return (
        f"  {r.arm:14s} hold={r.hold:<3d} split={r.split:5s} "
        f"n={r.n:<4d} mean={r.mean:+.4%} med={r.median:+.4%} "
        f"win={r.win:.1%} p={r.p:.4f} sharpe={r.sharpe if r.sharpe is not None else float('nan'):+.3f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="P0 H-CYC-01 BTC dump + alt relative weakness")
    ap.add_argument("--snapshot-dir", default="data/snap_smallcap1800")
    ap.add_argument("--tf", default="1d")
    ap.add_argument("--dump-pct", type=float, default=2.0, help="BTC drop threshold in %")
    ap.add_argument("--holds", nargs="*", type=int, default=[1, 2, 3, 5, 7])
    ap.add_argument("--quantile", type=float, default=0.2, help="weak/strong fraction")
    ap.add_argument("--preweak-lb", type=int, default=5)
    ap.add_argument("--min-gap", type=int, default=1, help="min bars between dump events")
    ap.add_argument("--fee", type=float, default=0.04, help="fee % per leg")
    ap.add_argument("--slippage", type=float, default=0.05, help="slip % per leg")
    ap.add_argument("--oos-frac", type=float, default=0.30)
    ap.add_argument("--min-bars", type=int, default=400)
    ap.add_argument("--out", default="logs/cyc01_dump_weakness.json")
    args = ap.parse_args()

    snap = Path(args.snapshot_dir)
    print(f"Loading panel from {snap} tf={args.tf} …")
    panel, btc_key = load_panel(snap, args.tf, args.min_bars)
    # drop BTCDOM if present as tradable alt noise for short universe — keep as alt ok
    cols = list(panel.columns)
    btc_col = cols.index(btc_key)
    close = panel.to_numpy(dtype=float)
    T, N = close.shape
    dump_thr = abs(args.dump_pct) / 100.0
    cost = _cost_roundtrip(args.fee, args.slippage)
    print(f"Panel: {N} symbols × {T} bars | BTC={btc_key}")
    print(f"Range: {panel.index[0]} → {panel.index[-1]}")
    print(f"dump_thr={dump_thr:.2%}  cost_rt={cost:.4%}  holds={args.holds}  q={args.quantile}")

    desc = descriptive_dump_stats(close, btc_col, dump_thr, args.min_gap)
    print("\n=== Descriptive (dump days) ===")
    for k, v in desc.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}" if abs(v) < 1 else f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")

    # chronological split
    cut = int(T * (1.0 - args.oos_frac))
    cut = max(cut, int(T * 0.5))
    print(f"\nSplit: train bars [0,{cut})  OOS [{cut},{T})  "
          f"({panel.index[0].date()}…{panel.index[cut-1].date()} | "
          f"{panel.index[cut].date()}…{panel.index[-1].date()})")

    all_rows: list[ArmResult] = []
    for lo, hi, name in [(1, T, "full"), (1, cut, "train"), (cut, T, "oos")]:
        rows = run_arms(
            close, btc_col, dump_thr, args.holds, args.quantile,
            args.preweak_lb, cost, args.min_gap, lo, hi, name,
        )
        all_rows.extend(rows)

    print("\n=== Arm results ===")
    # print OOS first (what matters), then train, then full
    for split in ("oos", "train", "full"):
        print(f"\n-- {split.upper()} --")
        subset = [r for r in all_rows if r.split == split]
        subset.sort(key=lambda r: (r.hold, r.arm))
        for r in subset:
            print(fmt_row(r))

    # permutation: weak vs all on OOS, for each hold
    print("\n=== Permutation OOS: short_weak > short_all ? ===")
    perm_by_hold: dict[int, float] = {}
    for hold in args.holds:
        a = collect_arm_series(
            close, btc_col, dump_thr, hold, args.quantile, cost, args.min_gap,
            cut, T, "short_weak", args.preweak_lb,
        )
        b = collect_arm_series(
            close, btc_col, dump_thr, hold, args.quantile, cost, args.min_gap,
            cut, T, "short_all", args.preweak_lb,
        )
        p = _perm_test(a, b)
        perm_by_hold[hold] = p
        print(f"  hold={hold}: n_weak={len(a)} n_all={len(b)} "
              f"mean_w={a.mean() if len(a) else float('nan'):+.4%} "
              f"mean_all={b.mean() if len(b) else float('nan'):+.4%} p={p:.4f}")

    # also weak vs strong
    print("\n=== Permutation OOS: short_weak > short_strong ? ===")
    for hold in args.holds:
        a = collect_arm_series(
            close, btc_col, dump_thr, hold, args.quantile, cost, args.min_gap,
            cut, T, "short_weak", args.preweak_lb,
        )
        b = collect_arm_series(
            close, btc_col, dump_thr, hold, args.quantile, cost, args.min_gap,
            cut, T, "short_strong", args.preweak_lb,
        )
        p = _perm_test(a, b)
        print(f"  hold={hold}: mean_w={a.mean() if len(a) else float('nan'):+.4%} "
              f"mean_s={b.mean() if len(b) else float('nan'):+.4%} p={p:.4f}")

    oos_rows = [r for r in all_rows if r.split == "oos"]
    # use perm at hold with best weak mean
    weak_oos = [r for r in oos_rows if r.arm == "short_weak" and r.mean is not None]
    best_hold = max(weak_oos, key=lambda r: r.mean).hold if weak_oos else args.holds[0]
    p_wa = perm_by_hold.get(best_hold)
    verd = verdict_from_oos(oos_rows, p_wa)
    print(f"\n{'='*60}")
    print(f"VERDICT: {verd['verdict']}")
    print(f"  {verd['reason']}")
    print(f"{'='*60}")
    print(
        "Catatan jujur: CANDIDATE hanya berarti lolos filter riset P0 — "
        "bukan izin live/merge gate. P1 butuh shadow paper terpisah."
    )

    out = {
        "meta": {
            "snapshot": str(snap),
            "tf": args.tf,
            "btc": btc_key,
            "n_symbols": N,
            "n_bars": T,
            "start": str(panel.index[0]),
            "end": str(panel.index[-1]),
            "dump_pct": args.dump_pct,
            "holds": args.holds,
            "quantile": args.quantile,
            "cost_rt": cost,
            "oos_frac": args.oos_frac,
            "cut_index": cut,
            "cut_date": str(panel.index[cut]),
        },
        "descriptive": desc,
        "arms": [asdict(r) for r in all_rows],
        "perm_weak_vs_all_oos": perm_by_hold,
        "verdict": verd,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
