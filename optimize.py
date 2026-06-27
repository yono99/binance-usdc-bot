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

from bot.backtest import Backtester, compute_metrics, fetch_history
from bot.config import load_settings
from bot.exchange import Exchange
from bot.logger import log
from bot.optimize import build_grid, walk_forward

console = Console()


def parse_args():
    p = argparse.ArgumentParser()
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
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    cfg = settings.raw
    tf = args.tf or cfg["market"]["timeframe"]
    symbols = args.symbols or cfg["market"].get("whitelist") or ["BTC/USDC:USDC"]

    ex = Exchange(settings)
    bt = Backtester(cfg, fee_pct=args.fee, slippage_pct=args.slippage)
    grid = build_grid(args.conf, args.sl, args.tp)
    log.info(f"Walk-forward tf={tf} bars={args.bars} train={args.train} test={args.test} "
             f"grid={len(grid)} kombinasi/window symbols={symbols}")

    all_oos = []
    chosen = Counter()

    for sym in symbols:
        try:
            df = fetch_history(ex, sym, tf, args.bars)
        except Exception as e:  # boundary
            log.error(f"fetch {sym} gagal: {e}")
            continue

        results, oos = walk_forward(df, cfg, grid, bt, args.train, args.test, args.min_trades)
        all_oos += oos
        if not results:
            log.warning(f"{sym}: data kurang untuk walk-forward")
            continue

        tbl = Table(title=f"{sym} — walk-forward ({len(results)} window)")
        for c in ["window", "params (conf/sl/tp)", "IS exp_R", "IS n", "OOS exp_R", "OOS n"]:
            tbl.add_column(c, justify="right")
        for i, w in enumerate(results):
            p = w.params
            chosen[(p["entry_confidence"], p["sl_atr_mult"], p["tp_atr_mult"])] += 1
            tbl.add_row(
                str(i + 1),
                f"{p['entry_confidence']}/{p['sl_atr_mult']}/{p['tp_atr_mult']}",
                f"{w.is_exp:+.3f}", str(w.is_n),
                f"{w.oos_exp:+.3f}", str(w.oos_n),
            )
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
