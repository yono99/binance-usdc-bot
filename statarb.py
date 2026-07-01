#!/usr/bin/env python3
"""Fase 2 / H21 — statistical arbitrage (pairs trading) di universe crypto perp.

  python statarb.py --tf 1d --bars 2000 --train 400 --test 150
  python statarb.py --entry-z 2.0 2.5 --hl-max 15 30 --fee 0 --slippage 0.05

Cari pasangan spread mean-reverting (half-life OU), fade z ekstrem. Riset di USDT
(histori panjang), eksekusi USDC. Verdict = mean OOS per-trade + multiple-testing.
"""
from __future__ import annotations

import argparse

import numpy as np
from rich.console import Console
from rich.table import Table

from bot import statarb as sa
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log
from bot.xsectional import align_close_panel, verdict

console = Console()

DEFAULT_UNIVERSE = [f"{s}/USDT:USDT" for s in (
    "BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "SOL", "DOT", "LTC", "LINK", "AVAX",
    "ATOM", "XLM", "TRX", "ETC", "FIL", "AAVE", "UNI", "NEAR", "ALGO", "SAND",
    "MANA", "AXS", "EGLD", "THETA")]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--tf", default="1d")
    p.add_argument("--bars", type=int, default=2000)
    p.add_argument("--train", type=int, default=400)
    p.add_argument("--test", type=int, default=150)
    p.add_argument("--entry-z", nargs="*", type=float, default=[2.0, 2.5])
    p.add_argument("--exit-z", type=float, default=0.0)
    p.add_argument("--stop-z", type=float, default=float("inf"),
                   help="stop-loss level z (cut saat spread melebar; inf = tanpa stop)")
    p.add_argument("--hl-max", nargs="*", type=float, default=[15, 30])
    p.add_argument("--fee", type=float, default=0.0)
    p.add_argument("--slippage", type=float, default=0.05)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--snapshot-dir", default=None)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    tf = args.tf
    symbols = args.symbols or DEFAULT_UNIVERSE
    ex = Exchange(settings)

    dfs = {}
    for sym in symbols:
        try:
            df = load_ohlcv(args.snapshot_dir, sym, tf) if args.snapshot_dir else None
            if df is None:
                df = fetch_history(ex, sym, tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(df, args.snapshot_dir, sym, tf)
            dfs[sym] = df
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")

    panel = align_close_panel(dfs)
    if panel.shape[1] < 4 or len(panel) < args.train + args.test:
        console.print(f"[red]Panel tak cukup: {panel.shape[1]} simbol × {len(panel)} bar.[/red]")
        return
    logp = np.log(panel.to_numpy())
    log.info(f"Panel: {panel.shape[1]} simbol × {len(panel)} bar ({tf}) → "
             f"{panel.shape[1] * (panel.shape[1] - 1) // 2} pasangan kandidat.")

    cost = 4 * (args.fee + args.slippage) / 100 * args.stress_mult
    grid = sa.build_grid(args.entry_z, args.hl_max)
    windows, oos = sa.walk_forward_statarb(logp, grid, cost, args.train, args.test,
                                           args.exit_z, args.stop_z)
    log.info(f"Walk-forward stat-arb: {len(windows)} window, grid={len(grid)} "
             f"(entry_z={args.entry_z} hl_max={args.hl_max}) cost/trade={cost:.4%}")

    tbl = Table(title=f"Stat-arb pairs — {tf}")
    for c in ["window", "entry/hl", "#pairs", "IS Sharpe", "OOS mean", "OOS n"]:
        tbl.add_column(c, justify="right")
    for i, w in enumerate(windows, 1):
        tbl.add_row(str(i), f"{w['params']['entry_z']}/{w['params']['hl_max']}",
                    str(w["n_pairs"]), f"{w['is_sharpe']:+.3f}",
                    f"{w['oos_mean']:+.4%}", str(w["oos_n"]))
    console.print(tbl)

    v = verdict(oos, len(grid))
    t2 = Table(title="GABUNGAN OOS (per-trade)")
    for c in ["trades", "win%", "mean/trade", "Sharpe", "p_adj"]:
        t2.add_column(c, justify="right")
    t2.add_row(str(v["n"]), f"{v.get('win_rate', 0) * 100:.1f}", f"{v['mean']:+.4%}",
               str(v.get("sharpe", "-")), str(v.get("p_adj", "-")))
    console.print(t2)
    color = "green" if v["ok"] else "red"
    console.print(f"[{color}]Verdict: {'LOLOS' if v['ok'] else 'DITOLAK'} — {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
