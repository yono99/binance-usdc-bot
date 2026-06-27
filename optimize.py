#!/usr/bin/env python3
"""Sweep + walk-forward: cari parameter ber-edge yang lolos OUT-OF-SAMPLE.

  python optimize.py --symbols "BTC/USDC:USDC" --bars 5000 --tf 15m
  python optimize.py --bars 6000 --train 1000 --test 300

Verdict memakai expectancy OUT-OF-SAMPLE (jujur). Jika OOS positif & stabil,
barulah parameter layak dipertimbangkan untuk testnet.
"""
from __future__ import annotations

import argparse
from collections import Counter

from rich.console import Console
from rich.table import Table

from bot.altdata import align, fetch_funding, fetch_oi, funding_zscore, oi_delta
from bot.backtest import Backtester, compute_metrics, fetch_history
from bot.config import load_settings
from bot.exchange import Exchange
from bot.logger import log
from bot.optimize import build_grid, walk_forward
from bot.orderflow import cvd_features
from bot.strategy_lab import (
    build_grid_v2,
    build_grid_v3,
    build_grid_v4,
    build_grid_v5,
    walk_forward_v2,
    walk_forward_v3,
    walk_forward_v4,
    walk_forward_v5,
)

console = Console()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", choices=["v1", "v2", "v3", "v4", "v5"], default="v2",
                   help="v1=trend, v2=HTF+regime+sesi, v3=+funding+OI, v4=+orderflow/CVD, v5=+event guard")
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--tf")
    p.add_argument("--bars", type=int, default=5000)
    p.add_argument("--train", type=int, default=1000)
    p.add_argument("--test", type=int, default=300)
    p.add_argument("--min-trades", type=int, default=15)
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--fee", type=float, default=0.04)
    p.add_argument("--slippage", type=float, default=0.02)
    p.add_argument("--conf", nargs="*", type=float, default=[0.50, 0.55, 0.60, 0.65, 0.70])
    p.add_argument("--sl", nargs="*", type=float, default=[1.0, 1.5, 2.0])
    p.add_argument("--tp", nargs="*", type=float, default=[1.5, 2.0, 2.5, 3.0])
    p.add_argument("--htf-mult", type=int, help="override strategy.htf_mult")
    p.add_argument("--sessions", nargs="*", type=int, help="jam UTC diizinkan (v2)")
    return p.parse_args()


def params_str(p: dict) -> str:
    base = f"{p['entry_confidence']}/{p['sl_atr_mult']}/{p['tp_atr_mult']}"
    if "use_htf" in p:
        base += f" htf={int(p['use_htf'])} reg={int(p['regime'])}"
    if "use_funding" in p:
        base += f" fnd={int(p['use_funding'])} oi={int(p['use_oi'])}"
    if "use_of" in p:
        base += f" of={int(p['use_of'])}"
    if "use_event" in p:
        base += f" ev={int(p['use_event'])}"
    return base


def main() -> None:
    args = parse_args()
    settings = load_settings()
    cfg = settings.raw
    tf = args.tf or cfg["market"]["timeframe"]
    symbols = args.symbols or cfg["market"].get("whitelist") or ["BTC/USDC:USDC"]

    ex = Exchange(settings)
    bt = Backtester(cfg, fee_pct=args.fee, slippage_pct=args.slippage)

    htf_mult = args.htf_mult or cfg["strategy"]["htf_mult"]
    sessions = set(args.sessions) if args.sessions else (set(cfg["strategy"]["sessions"]) or None)
    if args.strategy == "v5":
        grid = build_grid_v5(args.conf, args.sl, args.tp, [True], [True, False],
                             [False, True], [False], [False, True], [False, True])

        def run_wf(df, sym):
            since = int(df.index[0].timestamp() * 1000)
            fz = funding_zscore(fetch_funding(ex, sym, since), cfg["strategy"]["funding_z_window"])
            funding_z = align(df.index, fz, 0.0)
            oid = oi_delta(df.index, fetch_oi(ex, sym, tf, since), cfg["strategy"]["oi_delta_lookback"])
            imb, div = cvd_features(ex, sym, tf, df, cfg["strategy"]["cvd_lookback"])
            return walk_forward_v5(df, cfg, grid, bt, args.train, args.test, args.min_trades,
                                   htf_mult, sessions, funding_z, oid, imb, div)
    elif args.strategy == "v4":
        grid = build_grid_v4(args.conf, args.sl, args.tp, [True], [True, False],
                             [False, True], [False], [False, True])

        def run_wf(df, sym):
            since = int(df.index[0].timestamp() * 1000)
            fz = funding_zscore(fetch_funding(ex, sym, since), cfg["strategy"]["funding_z_window"])
            funding_z = align(df.index, fz, 0.0)
            oid = oi_delta(df.index, fetch_oi(ex, sym, tf, since), cfg["strategy"]["oi_delta_lookback"])
            imb, div = cvd_features(ex, sym, tf, df, cfg["strategy"]["cvd_lookback"])
            log.info(f"{sym}: funding {int((funding_z!=0).sum())}/{len(df)}, "
                     f"OI {int((oid!=0).sum())}/{len(df)}, CVD {int((imb!=0).sum())}/{len(df)} bar")
            return walk_forward_v4(df, cfg, grid, bt, args.train, args.test, args.min_trades,
                                   htf_mult, sessions, funding_z, oid, imb, div)
    elif args.strategy == "v3":
        grid = build_grid_v3(args.conf, args.sl, args.tp, [True], [True, False],
                             [False, True], [False, True])

        def run_wf(df, sym):
            since = int(df.index[0].timestamp() * 1000)
            fz = funding_zscore(fetch_funding(ex, sym, since), cfg["strategy"]["funding_z_window"])
            funding_z = align(df.index, fz, 0.0)
            oid = oi_delta(df.index, fetch_oi(ex, sym, tf, since), cfg["strategy"]["oi_delta_lookback"])
            nz_f = int((funding_z != 0).sum())
            nz_o = int((oid != 0).sum())
            log.info(f"{sym}: funding terisi {nz_f}/{len(df)} bar, OI terisi {nz_o}/{len(df)} bar")
            return walk_forward_v3(df, cfg, grid, bt, args.train, args.test, args.min_trades,
                                   htf_mult, sessions, funding_z, oid)
    elif args.strategy == "v2":
        grid = build_grid_v2(args.conf, args.sl, args.tp, [False, True], [False, True])

        def run_wf(df, sym):
            return walk_forward_v2(df, cfg, grid, bt, args.train, args.test,
                                   args.min_trades, htf_mult, sessions)
    else:
        grid = build_grid(args.conf, args.sl, args.tp)

        def run_wf(df, sym):
            return walk_forward(df, cfg, grid, bt, args.train, args.test, args.min_trades)

    log.info(f"Walk-forward strategy={args.strategy} tf={tf} bars={args.bars} "
             f"train={args.train} test={args.test} grid={len(grid)}/window symbols={symbols}")

    all_oos = []
    chosen = Counter()

    for sym in symbols:
        try:
            df = fetch_history(ex, sym, tf, args.bars)
        except Exception as e:  # boundary
            log.error(f"fetch {sym} gagal: {e}")
            continue

        results, oos = run_wf(df, sym)
        all_oos += oos
        if not results:
            log.warning(f"{sym}: data kurang untuk walk-forward")
            continue

        tbl = Table(title=f"{sym} — walk-forward ({len(results)} window)")
        for c in ["window", "params", "IS exp_R", "IS n", "OOS exp_R", "OOS n"]:
            tbl.add_column(c, justify="right")
        for i, w in enumerate(results):
            p = w.params
            chosen[params_str(p)] += 1
            tbl.add_row(str(i + 1), params_str(p), f"{w.is_exp:+.3f}", str(w.is_n),
                        f"{w.oos_exp:+.3f}", str(w.oos_n))
        console.print(tbl)

    if not all_oos:
        console.print("[red]Tak ada trade OOS — perbesar --bars atau longgarkan grid.[/red]")
        return

    m = compute_metrics(all_oos, cfg, args.equity)
    summary = Table(title="GABUNGAN OUT-OF-SAMPLE (semua window, semua simbol)")
    for c in ["OOS trades", "win%", "exp_R", "PF", "maxDD%", "ret%"]:
        summary.add_column(c, justify="right")
    pf = m["profit_factor"]
    summary.add_row(str(m["trades"]), f"{m['win_rate']:.1f}", f"{m['expectancy_r']:+.3f}",
                    ("∞" if pf == float("inf") else f"{pf:.2f}"),
                    f"{m['max_drawdown_pct']:.1f}", f"{m['return_pct']:+.1f}")
    console.print(summary)

    if chosen:
        common = chosen.most_common(3)
        console.print("Parameter paling sering terpilih (conf/sl/tp): " +
                      ", ".join(f"{k} ×{v}" for k, v in common))

    e = m["expectancy_r"]
    if e > 0.05:
        console.print(f"[green]OOS POSITIF ({e:+.3f}R). Kandidat layak diuji di testnet — "
                      f"set parameter tersering ke config.yaml lalu MODE=test.[/green]")
    elif e > 0:
        console.print(f"[yellow]OOS tipis ({e:+.3f}R) — belum meyakinkan. Perluas data/grid atau "
                      f"perbaiki logika sinyal sebelum live.[/yellow]")
    else:
        console.print(f"[red]OOS NEGATIF ({e:+.3f}R). Strategi belum punya edge yang general. "
                      f"JANGAN live; perbaiki fitur sinyal (bukan sekadar tuning).[/red]")


if __name__ == "__main__":
    main()
