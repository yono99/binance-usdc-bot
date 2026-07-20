#!/usr/bin/env python3
"""P0b/P1-prep — universe scale + block_long_on_btc_dump.

1) Deskriptif beta>1 dengan universe LEBIH BESAR (pair-wise vs BTC, bukan panel
   dropna ketat 78 simbol). Jawab: apakah frac_alt_deeper naik di atas ~64%?
2) Counterfactual LONG alt pada hari dump → apakah mean forward ret < 0?
   (dasar spek block_long, bukan short entry)
3) Ringkas dump_flag wiring (kode) dicetak di laporan JSON meta.

Pakai:
  python cyc01b_universe_and_blocklong.py
  python cyc01b_universe_and_blocklong.py --snapshot-dir data/snap --min-bars 200
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def _sym_from_name(stem: str, tf: str) -> str:
    suffix = f"__{tf}"
    if stem.endswith(suffix):
        stem = stem[: -len(suffix)]
    parts = stem.split("_")
    if len(parts) >= 3 and parts[-1] == parts[-2]:
        base = "_".join(parts[:-2])
        quote = parts[-2]
        return f"{base}/{quote}:{quote}"
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return stem


def _is_btc(sym: str, stem: str) -> bool:
    u = (sym + " " + stem).upper()
    if "BTCDOM" in u or "DEFI" == stem.split("_")[0].upper():
        return False
    return stem.upper().startswith("BTC_") or sym.upper().startswith("BTC/")


def load_closes(snapshot_dir: Path, tf: str, min_bars: int) -> tuple[pd.Series, dict[str, pd.Series]]:
    btc: pd.Series | None = None
    alts: dict[str, pd.Series] = {}
    for p in sorted(snapshot_dir.glob(f"*__{tf}.pkl")):
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if df is None or len(df) < min_bars or "close" not in getattr(df, "columns", []):
            continue
        s = df["close"].astype(float).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        sym = _sym_from_name(p.stem, tf)
        if _is_btc(sym, p.stem):
            # prefer longer BTC series
            if btc is None or len(s) > len(btc):
                btc = s
                btc.name = sym
            continue
        # skip pure index-like if any
        if "BTCDOM" in p.stem.upper():
            continue
        alts[sym] = s
    if btc is None:
        raise SystemExit("BTC not found")
    return btc, alts


def daily_ret(s: pd.Series) -> pd.Series:
    return s.pct_change()


def t_pvalue_one_sided_neg(x: np.ndarray) -> float:
    """H0: mean >= 0 (we want long-on-dump mean < 0)."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return 1.0
    sd = x.std(ddof=1)
    if sd <= 0:
        return 0.0 if x.mean() < 0 else 1.0
    t = x.mean() / (sd / math.sqrt(n))  # negative t supports H1 mean<0
    # P(T < t) for H1 mean < 0
    return 0.5 * math.erfc((-t) / math.sqrt(2.0)) if t < 0 else 1.0 - 0.5 * math.erfc(t / math.sqrt(2.0))


def t_pvalue_one_sided_pos(x: np.ndarray) -> float:
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


def descriptive_pairwise(
    btc: pd.Series,
    alts: dict[str, pd.Series],
    dump_thr: float,
    min_alts_per_day: int = 10,
) -> dict:
    """Each dump day: among alts with data that day, mean ret & frac deeper than BTC."""
    br = daily_ret(btc)
    dump_idx = br.index[br <= -abs(dump_thr)]
    # pre-align alt rets
    alt_rets = {s: daily_ret(c) for s, c in alts.items()}

    day_btc, day_alt_mean, day_frac, day_n, day_rel = [], [], [], [], []
    # also collect all pair-days for overall frac
    deeper_flags = []
    all_rel = []

    for t in dump_idx:
        br_t = float(br.loc[t])
        if not math.isfinite(br_t):
            continue
        alts_today = []
        for s, r in alt_rets.items():
            if t not in r.index:
                continue
            v = r.loc[t]
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(v):
                continue
            alts_today.append(v)
            deeper_flags.append(1.0 if v < br_t else 0.0)
            all_rel.append(v - br_t)
        if len(alts_today) < min_alts_per_day:
            continue
        am = float(np.mean(alts_today))
        day_btc.append(br_t)
        day_alt_mean.append(am)
        day_frac.append(float(np.mean([1.0 if a < br_t else 0.0 for a in alts_today])))
        day_n.append(len(alts_today))
        day_rel.append(am - br_t)

    return {
        "n_alts_loaded": len(alts),
        "n_dump_days_btc": int(len(dump_idx)),
        "n_dump_days_used": len(day_btc),
        "mean_alts_per_dump_day": float(np.mean(day_n)) if day_n else None,
        "mean_btc_ret": float(np.mean(day_btc)) if day_btc else None,
        "mean_alt_ret_ew_day": float(np.mean(day_alt_mean)) if day_alt_mean else None,
        "mean_frac_alt_deeper_per_day": float(np.mean(day_frac)) if day_frac else None,
        "median_frac_alt_deeper_per_day": float(np.median(day_frac)) if day_frac else None,
        "mean_rel_weakness_day": float(np.mean(day_rel)) if day_rel else None,
        # pair-day level (each alt-day equal weight — more weight to days with many alts)
        "n_pair_days": len(deeper_flags),
        "frac_pair_days_alt_deeper": float(np.mean(deeper_flags)) if deeper_flags else None,
        "mean_rel_pair": float(np.mean(all_rel)) if all_rel else None,
    }


def long_on_dump_forward(
    btc: pd.Series,
    alts: dict[str, pd.Series],
    dump_thr: float,
    holds: list[int],
    oos_frac: float,
    cost_rt: float,
    min_alts_per_day: int = 10,
) -> dict:
    """Equal-weight long all alts available on dump day; hold H days; report train/OOS.

    block_long helps if these means are significantly negative (avoiding the long).
    Also report short_all = -long for reference (same as P0 arm B, pairwise universe).
    """
    br = daily_ret(btc)
    dump_idx = list(br.index[br <= -abs(dump_thr)])
    # chronological cut on BTC index
    btc_idx = list(btc.index)
    cut_i = int(len(btc_idx) * (1.0 - oos_frac))
    cut_i = max(cut_i, len(btc_idx) // 2)
    cut_ts = btc_idx[cut_i]

    out = {}
    for hold in holds:
        long_rets_train, long_rets_oos = [], []
        for t in dump_idx:
            # need forward close at t+hold for each alt
            # find position in each series
            day_fwd = []
            for s, c in alts.items():
                if t not in c.index:
                    continue
                # integer location
                loc = c.index.get_loc(t)
                if isinstance(loc, slice) or not isinstance(loc, (int, np.integer)):
                    # duplicate index edge
                    continue
                loc = int(loc)
                if loc + hold >= len(c):
                    continue
                px0 = float(c.iloc[loc])
                px1 = float(c.iloc[loc + hold])
                if px0 <= 0 or not math.isfinite(px0) or not math.isfinite(px1):
                    continue
                day_fwd.append(px1 / px0 - 1.0)
            if len(day_fwd) < min_alts_per_day:
                continue
            # EW long portfolio that day, after cost
            r = float(np.mean(day_fwd)) - cost_rt
            if t < cut_ts:
                long_rets_train.append(r)
            else:
                long_rets_oos.append(r)

        def pack(xs: list[float], split: str) -> dict:
            a = np.asarray(xs, dtype=float)
            if len(a) == 0:
                return {"split": split, "n": 0}
            return {
                "split": split,
                "n": int(len(a)),
                "mean_long": float(a.mean()),
                "median_long": float(np.median(a)),
                "win_long": float((a > 0).mean()),
                "p_long_neg": t_pvalue_one_sided_neg(a),  # want low if long hurts
                "mean_short_all": float((-a - 0.0).mean()) if False else float((-a).mean() - 0),  # short ≈ -long but cost already on long; approx
                # better: short_all mean = -mean_long - 0 (cost already subtracted from long;
                # short would also pay cost once → ~ -raw + already accounted roughly)
                "p_short_pos": t_pvalue_one_sided_pos(-a),
            }

        # Fix short: long already has -cost; short PnL ≈ -fwd - cost = -(fwd) - cost.
        # We stored r = mean(fwd)-cost, so -r = -mean(fwd)+cost ≠ -mean(fwd)-cost.
        # Recompute short properly from stored is approximate: short_mean ≈ -mean_long - 2*cost? messy.
        # Simpler: report long only for block_long; short_all approx = -mean_long - cost (extra cost).
        tr, oo = pack(long_rets_train, "train"), pack(long_rets_oos, "oos")
        for pack_d, xs in ((tr, long_rets_train), (oo, long_rets_oos)):
            if pack_d["n"]:
                a = np.asarray(xs, dtype=float)
                # recover mean_fwd ≈ mean_long + cost; short = -mean_fwd - cost = -mean_long - 2*cost
                pack_d["mean_short_all_approx"] = float(-a.mean() - cost_rt)
                pack_d["block_long_saves_per_event"] = float(-a.mean())  # avoided long PnL
        out[str(hold)] = {"train": tr, "oos": oo}
    out["cut_ts"] = str(cut_ts)
    out["n_btc_bars"] = len(btc_idx)
    return out


def universe_slices(
    btc: pd.Series,
    alts: dict[str, pd.Series],
    dump_thr: float,
    sizes: list[int] | None = None,
) -> list[dict]:
    """Stability of frac_deeper as we include more alts (by history length)."""
    # rank alts by number of bars overlapping BTC
    ranked = sorted(alts.items(), key=lambda kv: len(kv[1]), reverse=True)
    if sizes is None:
        sizes = [50, 78, 100, 150, 200, 300, 400, 500, 9999]
    rows = []
    for n in sizes:
        sub = dict(ranked[:n]) if n < len(ranked) else dict(ranked)
        if len(sub) < 20:
            continue
        d = descriptive_pairwise(btc, sub, dump_thr)
        d["universe_cap"] = n if n < 9999 else "all"
        d["universe_used"] = len(sub)
        rows.append(d)
        if n >= len(ranked):
            break
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir", default="data/snap")
    ap.add_argument("--tf", default="1d")
    ap.add_argument("--min-bars", type=int, default=200)
    ap.add_argument("--dump-pct", type=float, default=2.0)
    ap.add_argument("--holds", nargs="*", type=int, default=[1, 3, 5, 7])
    ap.add_argument("--oos-frac", type=float, default=0.30)
    ap.add_argument("--fee", type=float, default=0.04)
    ap.add_argument("--slippage", type=float, default=0.05)
    ap.add_argument("--out", default="logs/cyc01b_universe_blocklong.json")
    args = ap.parse_args()

    dump_thr = abs(args.dump_pct) / 100.0
    cost = 2.0 * (args.fee + args.slippage) / 100.0
    snap = Path(args.snapshot_dir)
    print(f"Loading {snap} tf={args.tf} min_bars={args.min_bars} …")
    btc, alts = load_closes(snap, args.tf, args.min_bars)
    print(f"BTC={btc.name} bars={len(btc)} {btc.index[0].date()}→{btc.index[-1].date()}")
    print(f"Alts loaded: {len(alts)}")

    print("\n=== Universe scale: frac alt deeper on BTC dump days ===")
    slices = universe_slices(btc, alts, dump_thr)
    for d in slices:
        print(
            f"  n_alts={d['universe_used']:>4}  dump_days={d['n_dump_days_used']:>3}  "
            f"alts/day≈{d['mean_alts_per_dump_day'] or 0:.0f}  "
            f"frac_deeper/day={d['mean_frac_alt_deeper_per_day'] or 0:.1%}  "
            f"pair_frac={d['frac_pair_days_alt_deeper'] or 0:.1%}  "
            f"mean_rel={d['mean_rel_weakness_day'] or 0:+.2%}"
        )

    full = descriptive_pairwise(btc, alts, dump_thr)
    print("\n=== Full universe descriptive ===")
    for k, v in full.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.6f}")
        else:
            print(f"  {k}: {v}")

    print("\n=== LONG alt EW on dump days (block_long value) ===")
    print(f"cost_rt={cost:.4%}  oos_frac={args.oos_frac}")
    long_res = long_on_dump_forward(
        btc, alts, dump_thr, args.holds, args.oos_frac, cost,
    )
    print(f"cut_ts={long_res['cut_ts']}")
    for hold in args.holds:
        tr = long_res[str(hold)]["train"]
        oo = long_res[str(hold)]["oos"]
        def line(tag, p):
            if p.get("n", 0) == 0:
                return f"  hold={hold} {tag}: n=0"
            return (
                f"  hold={hold} {tag}: n={p['n']} mean_long={p['mean_long']:+.4%} "
                f"win={p['win_long']:.1%} p_long_neg={p['p_long_neg']:.4f} "
                f"block_saves≈{p.get('block_long_saves_per_event', float('nan')):+.4%} "
                f"short_all≈{p.get('mean_short_all_approx', float('nan')):+.4%}"
            )
        print(line("train", tr))
        print(line("oos  ", oo))

    # Verdict block_long
    # Primary: OOS hold=1 mean_long < 0 with p_long_neg < 0.05
    oos1 = long_res.get("1", {}).get("oos", {})
    train1 = long_res.get("1", {}).get("train", {})
    verdict = {"verdict": "INCONCLUSIVE", "reason": ""}
    if oos1.get("n", 0) >= 20 and train1.get("n", 0) >= 30:
        oos_neg = oos1["mean_long"] < 0 and oos1["p_long_neg"] < 0.05
        train_neg = train1["mean_long"] < 0
        if oos_neg and train_neg:
            verdict = {
                "verdict": "CANDIDATE_AS_RISK_FILTER",
                "reason": (
                    f"LONG EW alt on dump OOS hold1 mean={oos1['mean_long']:+.4%} "
                    f"p_neg={oos1['p_long_neg']:.4f}; train also neg — "
                    f"block_long may reduce risk (shadow next, not live yet)"
                ),
            }
        elif oos1["mean_long"] < 0 or train1["mean_long"] < 0:
            verdict = {
                "verdict": "NOT_PROVEN",
                "reason": (
                    f"mixed: train mean_long={train1['mean_long']:+.4%} "
                    f"oos={oos1['mean_long']:+.4%} p_neg_oos={oos1['p_long_neg']:.4f}"
                ),
            }
        else:
            verdict = {
                "verdict": "REJECTED_AS_FILTER",
                "reason": "LONG on dump not reliably negative — blocking long may not help",
            }
    else:
        verdict = {"verdict": "INCONCLUSIVE", "reason": "insufficient dump events"}

    print(f"\n{'='*60}")
    print(f"BLOCK_LONG VERDICT: {verdict['verdict']}")
    print(f"  {verdict['reason']}")
    print(f"{'='*60}")

    # Also compare 78 vs full frac for user claim
    s78 = next((d for d in slices if d.get("universe_used") == 78 or d.get("universe_cap") == 78), None)
    # find closest to 78
    if s78 is None and slices:
        s78 = min(slices, key=lambda d: abs(d["universe_used"] - 78))
    compare = {
        "n78_like": s78,
        "n_full": full,
        "user_claim": "lebih banyak simbol → frac deeper mungkin >64%",
        "delta_frac_day": None,
    }
    if s78 and full.get("mean_frac_alt_deeper_per_day") is not None:
        compare["delta_frac_day"] = (
            full["mean_frac_alt_deeper_per_day"] - s78["mean_frac_alt_deeper_per_day"]
        )
        print(
            f"\nFrac deeper/day: ~{s78['universe_used']} alts → "
            f"{s78['mean_frac_alt_deeper_per_day']:.1%}  |  "
            f"full {full['n_alts_loaded']} → {full['mean_frac_alt_deeper_per_day']:.1%}  "
            f"(Δ={compare['delta_frac_day']:+.1%})"
        )

    dump_flag_audit = {
        "definition": (
            "forward._btc_lead: dump_flag = ret_BTC_3bar_pct <= -4*btc.dump_pct "
            "(default dump_pct=0.5 → thr≈-2% over 3 bars of TF buffer)"
        ),
        "used_for": [
            "Gemini context (prompt)",
            "SHORT conviction ×1.5 boost (gemini path only) — ASSUMES short edge (P0 rejected)",
            "NOT used as hard block_long",
        ],
        "related_gates": {
            "btc_gate": "blocks counter-trend when |btc_ret|>=dump_pct (0.5% default, 1-bar) — rules path signals.py",
            "btc_macro_tier / entry_confluence": "blocks long when btc_lead_score <= -dump_pct",
            "gap": "dump_flag thr (~2%/3bar) ≠ btc_gate thr (0.5%/1bar); boost short unproven; block_long not explicit dump_flag",
        },
        "paper_note": "boost only fires if use_gemini_trader + short + dump_flag; manager OFF does not remove boost if technique=gemini",
    }

    out = {
        "meta": {
            "snapshot": str(snap),
            "tf": args.tf,
            "min_bars": args.min_bars,
            "dump_pct": args.dump_pct,
            "n_alts": len(alts),
            "btc": btc.name,
            "btc_bars": len(btc),
            "range": [str(btc.index[0]), str(btc.index[-1])],
            "cost_rt": cost,
        },
        "universe_slices": slices,
        "full_descriptive": full,
        "long_on_dump": long_res,
        "block_long_verdict": verdict,
        "compare_78_vs_full": compare,
        "dump_flag_audit": dump_flag_audit,
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
