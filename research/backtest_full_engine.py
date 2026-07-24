#!/usr/bin/env python3
"""Backtest FULL STRATEGY ENGINE — multi-arm comparison on snap data.

Arms:
  A) RULES_ENGINE     — bot.signals.evaluate + Backtester (same as live rules path)
  B) RULES+G2_FILTER  — same rules, only take trades aligned with G2 quality bucket
  C) G2_BOOK_ENGINE   — Path A LS book rebalance (full G2 strategy, not rules)

All use cost-aware metrics, chronological 70/30 OOS, pure majors where applicable.

  PYTHONPATH=. python research/backtest_full_engine.py
  PYTHONPATH=. python research/backtest_full_engine.py --tf 1d
  PYTHONPATH=. python research/backtest_full_engine.py --tf 15m

Output: logs/backtest_full_engine.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "research")]

from bot.backtest import Backtester, Trade, compute_metrics  # noqa: E402
from bot.config import load_settings  # noqa: E402
from bot.dataset import load_ohlcv  # noqa: E402
from g2_book_paper import pack as pack_rs, run_book  # noqa: E402
from g2_quality_mom_shadow import ARM as G2_ARM, load_panel, quality_score  # noqa: E402
from edge_hunt_multifamily_v2 import PURE_MAJORS, load_named  # noqa: E402

OUT = ROOT / "logs" / "backtest_full_engine.json"


def base_of(sym: str) -> str:
    return sym.split("/")[0].upper().replace("1000", "").replace("1M", "")


def load_symbol_df(snap: Path, base: str, tf: str) -> tuple[str, pd.DataFrame] | None:
    """Prefer USDT dual, then USDC."""
    for settle in ("USDT", "USDC"):
        sym = f"{base}/{settle}:{settle}"
        df = load_ohlcv(snap, sym, tf)
        if df is not None and len(df) >= 200:
            df = df.sort_index()
            df = df[~df.index.duplicated(keep="last")]
            return sym, df
    # glob fallback
    for p in snap.glob(f"{base}_*__{tf}.pkl"):
        if "1000" in p.name or "BTCDOM" in p.name:
            continue
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if len(df) < 200:
            continue
        stem = p.stem.replace(f"__{tf}", "")
        parts = stem.split("_")
        if len(parts) >= 3 and parts[-1] == parts[-2]:
            sym = f"{'_'.join(parts[:-2])}/{parts[-2]}:{parts[-2]}"
        else:
            sym = stem
        df = df.sort_index()
        return sym, df[~df.index.duplicated(keep="last")]
    return None


def rank_history_1d(snap: Path) -> pd.DataFrame:
    """Daily rank_pct table (index=date, columns=base)."""
    panel = load_panel(snap)
    score = quality_score(panel, G2_ARM["lookback"])
    ranks = score.rank(axis=1, pct=True)
    # columns may be full symbols or bases depending on close_panel — normalize to base
    ranks = ranks.copy()
    ranks.columns = [base_of(str(c)) for c in ranks.columns]
    ranks = ranks.loc[:, ~ranks.columns.duplicated()]
    ranks.index = pd.to_datetime(ranks.index).tz_localize(None)
    return ranks


def g2_allows(side: str, rank_pct: float | None, top_q: float) -> str:
    """Return aligned|misaligned|neutral|unknown."""
    if rank_pct is None or not np.isfinite(rank_pct):
        return "unknown"
    if rank_pct >= 1.0 - top_q:
        bucket = "top"
    elif rank_pct <= top_q:
        bucket = "bottom"
    else:
        return "neutral"
    if side == "long":
        return "aligned" if bucket == "top" else "misaligned"
    if side == "short":
        return "aligned" if bucket == "bottom" else "misaligned"
    return "unknown"


def trades_to_r_list(trades: list[Trade]) -> list[float]:
    return [float(t.r) for t in trades]


def split_chrono(trades: list[Trade], frac_oos: float = 0.30):
    if not trades:
        return [], []
    trades = sorted(trades, key=lambda t: t.entry_time)
    k = int(len(trades) * (1 - frac_oos))
    return trades[:k], trades[k:]


def metrics_from_r(rs: list[float]) -> dict:
    return pack_rs(rs)


def run_rules_arm(
    snap: Path,
    bases: list[str],
    tf: str,
    cfg: dict,
    ranks_1d: pd.DataFrame | None,
    apply_g2_filter: bool,
    top_q: float,
) -> dict:
    bt = Backtester(cfg, fee_pct=0.04, slippage_pct=0.05)  # ~0.18% RT with 2 legs
    # load BTC for gate
    btc_pair = load_symbol_df(snap, "BTC", tf)
    btc_close = btc_pair[1]["close"] if btc_pair else None

    all_trades: list[Trade] = []
    filtered_trades: list[Trade] = []
    per_sym = {}
    n_denied = 0

    for base in bases:
        got = load_symbol_df(snap, base, tf)
        if not got:
            continue
        sym, df = got
        try:
            trades = bt.run_symbol(sym, df, btc_close=btc_close)
        except Exception as e:
            per_sym[base] = {"error": str(e)}
            continue
        all_trades.extend(trades)
        kept = []
        for t in trades:
            if not apply_g2_filter or ranks_1d is None:
                kept.append(t)
                continue
            # rank on calendar day of entry
            day = pd.Timestamp(t.entry_time)
            if day.tzinfo is not None:
                day = day.tz_localize(None)
            day = day.normalize()
            col = base
            rp = None
            if col in ranks_1d.columns:
                sub = ranks_1d.loc[:day, col].dropna()
                if len(sub):
                    rp = float(sub.iloc[-1])
            lab = g2_allows(t.side, rp, top_q)
            if lab == "aligned" or lab == "neutral":
                # solid filter option: only aligned (stricter). For "engine with G2 filter"
                # research default: deny misaligned only; allow neutral+aligned
                kept.append(t)
            elif lab == "unknown":
                kept.append(t)  # fail-open outside
            else:
                n_denied += 1
        if apply_g2_filter:
            # stricter: ONLY aligned (not neutral) — better test of G2 as entry filter
            kept2 = []
            for t in trades:
                day = pd.Timestamp(t.entry_time)
                if day.tzinfo is not None:
                    day = day.tz_localize(None)
                day = day.normalize()
                rp = None
                if base in ranks_1d.columns:
                    sub = ranks_1d.loc[:day, base].dropna()
                    if len(sub):
                        rp = float(sub.iloc[-1])
                lab = g2_allows(t.side, rp, top_q)
                if lab == "aligned":
                    kept2.append(t)
                elif lab == "misaligned":
                    n_denied += 1
                # neutral/unknown skip for strict arm (don't count as kept)
            filtered_trades.extend(kept2)
            per_sym[base] = {"n_raw": len(trades), "n_kept": len(kept2)}
        else:
            filtered_trades.extend(trades)
            per_sym[base] = {"n_raw": len(trades), "n_kept": len(trades)}

    use = filtered_trades if apply_g2_filter else all_trades
    tr, oos = split_chrono(use, 0.30)
    # also full metrics via compute_metrics
    full_m = compute_metrics(use, cfg, 1000.0) if use else {}
    tr_m = compute_metrics(tr, cfg, 1000.0) if tr else {}
    oos_m = compute_metrics(oos, cfg, 1000.0) if oos else {}

    return {
        "n_symbols": len(per_sym),
        "n_trades": len(use),
        "n_denied_g2": n_denied if apply_g2_filter else 0,
        "all": {
            "expectancy_r": full_m.get("expectancy_r"),
            "win_rate": full_m.get("win_rate"),
            "profit_factor": full_m.get("profit_factor"),
            "trades": full_m.get("trades"),
            "max_drawdown_pct": full_m.get("max_drawdown_pct"),
            "return_pct": full_m.get("return_pct"),
            "mean_r": metrics_from_r(trades_to_r_list(use)).get("mean"),
        },
        "train_70": {
            "expectancy_r": tr_m.get("expectancy_r"),
            "win_rate": tr_m.get("win_rate"),
            "trades": tr_m.get("trades"),
            "mean_r": metrics_from_r(trades_to_r_list(tr)).get("mean"),
        },
        "oos_30": {
            "expectancy_r": oos_m.get("expectancy_r"),
            "win_rate": oos_m.get("win_rate"),
            "trades": oos_m.get("trades"),
            "profit_factor": oos_m.get("profit_factor"),
            "mean_r": metrics_from_r(trades_to_r_list(oos)).get("mean"),
            "max_drawdown_pct": oos_m.get("max_drawdown_pct"),
        },
        "per_symbol_sample": dict(list(per_sym.items())[:12]),
    }


def run_g2_book(snap: Path) -> dict:
    panel = load_panel(snap)
    rows = run_book(panel, G2_ARM["hold"], G2_ARM["top_q"], G2_ARM["cost_rt"])
    rows2 = run_book(panel, G2_ARM["hold"], G2_ARM["top_q"], G2_ARM["cost_rt"] * 2)
    r = [float(x["r_net"]) for x in rows]
    r2 = [float(x["r_net"]) for x in rows2]
    k = int(len(r) * 0.70)
    i1, i2 = int(len(r) * 0.50), int(len(r) * 0.80)
    eq = np.cumsum(r) if r else np.array([])
    maxdd = float((eq - np.maximum.accumulate(eq)).min()) if len(eq) else None
    return {
        "arm": dict(G2_ARM),
        "n_rebalances": len(r),
        "all": metrics_from_r(r),
        "train_70": metrics_from_r(r[:k]),
        "oos_30": metrics_from_r(r[k:]),
        "lockbox_20": metrics_from_r(r[i2:]),
        "oos_cost2x": metrics_from_r(r2[k:] if len(r2) >= k else r2),
        "max_drawdown_sumR": maxdd,
        "panel": list(panel.shape),
        "range": [str(panel.index.min().date()), str(panel.index.max().date())],
    }


def verdict_rules(oos: dict) -> str:
    exp = oos.get("expectancy_r")
    n = oos.get("trades") or 0
    if n is None or n < 30:
        return "INCONCLUSIVE"
    if exp is None:
        return "INCONCLUSIVE"
    if exp > 0:
        return "POSITIVE_OOS_CHECK_PF"
    return "REJECTED_OOS_NEG"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=str(ROOT / "data" / "snap"))
    ap.add_argument("--tf", default="1d", help="timeframe for rules arms (1d or 15m)")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    snap = Path(args.snap)
    settings = load_settings()
    cfg = settings.raw

    bases = sorted(PURE_MAJORS)
    print(f"FULL ENGINE BACKTEST tf={args.tf} bases={len(bases)}")

    ranks = None
    try:
        ranks = rank_history_1d(snap)
        print("G2 ranks", ranks.shape)
    except Exception as e:
        print("ranks fail", e)

    print("=== A RULES_ENGINE ===")
    arm_a = run_rules_arm(snap, bases, args.tf, cfg, ranks, False, G2_ARM["top_q"])
    print("  trades", arm_a["n_trades"], "oos exp_R", arm_a["oos_30"].get("expectancy_r"))

    print("=== B RULES+G2_FILTER (aligned only) ===")
    arm_b = run_rules_arm(snap, bases, args.tf, cfg, ranks, True, G2_ARM["top_q"])
    print(
        "  trades", arm_b["n_trades"], "denied", arm_b["n_denied_g2"],
        "oos exp_R", arm_b["oos_30"].get("expectancy_r"),
    )

    print("=== C G2_BOOK_ENGINE ===")
    arm_c = run_g2_book(snap)
    print("  n", arm_c["n_rebalances"], "oos mean", arm_c["oos_30"].get("mean"), "lock", arm_c["lockbox_20"].get("mean"))

    out = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tf_rules": args.tf,
            "fee_pct_per_side": 0.04,
            "slippage_pct_per_side": 0.05,
            "note": (
                "A/B use bot.signals.evaluate + Backtester (live rules path). "
                "C is G2 LS book full engine. Overlays CE/ReAct/news NOT in A/B event sim."
            ),
            "pure_majors_n": len(bases),
        },
        "A_RULES_ENGINE": {
            **arm_a,
            "verdict_oos": verdict_rules(arm_a["oos_30"]),
        },
        "B_RULES_G2_FILTER": {
            **arm_b,
            "verdict_oos": verdict_rules(arm_b["oos_30"]),
        },
        "C_G2_BOOK_ENGINE": {
            **arm_c,
            "verdict": (
                "PASS"
                if (arm_c["oos_30"].get("mean") or 0) > 0
                and (arm_c["train_70"].get("mean") or 0) > 0
                and (arm_c["lockbox_20"].get("mean") or 0) > 0
                else "FAIL"
            ),
        },
        "comparison": {
            "best_for_entry_signal": None,
            "best_for_full_engine": "C_G2_BOOK" if (arm_c["oos_30"].get("mean") or 0) > 0 else None,
            "g2_filter_helps_rules": None,
        },
    }

    # Does G2 filter improve rules OOS exp?
    ea = arm_a["oos_30"].get("expectancy_r")
    eb = arm_b["oos_30"].get("expectancy_r")
    if ea is not None and eb is not None:
        out["comparison"]["g2_filter_helps_rules"] = bool(eb > ea)
        out["comparison"]["delta_oos_exp_R"] = eb - ea
        if eb > ea and eb > 0:
            out["comparison"]["best_for_entry_signal"] = "B_RULES_G2_FILTER"
        elif ea > 0:
            out["comparison"]["best_for_entry_signal"] = "A_RULES"
        else:
            out["comparison"]["best_for_entry_signal"] = "NONE_POSITIVE"

    Path(args.out).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("\n=== SUMMARY ===")
    print(json.dumps(out["comparison"], indent=2))
    print("A oos", out["A_RULES_ENGINE"]["oos_30"], out["A_RULES_ENGINE"]["verdict_oos"])
    print("B oos", out["B_RULES_G2_FILTER"]["oos_30"], out["B_RULES_G2_FILTER"]["verdict_oos"])
    print("C oos", out["C_G2_BOOK_ENGINE"]["oos_30"], out["C_G2_BOOK_ENGINE"]["verdict"])
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
