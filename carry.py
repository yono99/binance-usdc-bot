#!/usr/bin/env python3
"""Fase 2 — funding carry cross-sectional di universe USDC perp.

  python carry.py --tf 1h --bars 8000 --train 1500 --test 400
  python carry.py --fee 0 --slippage 0.03 --stress-mult 2

SHORT pair funding-tinggi (terima funding), LONG funding-rendah, dollar-neutral.
Verdict = PnL OOS (income funding realized + harga − biaya) + koreksi multiple-testing.
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from bot import carry
from bot.altdata import fetch_funding
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log
from bot.xsectional import align_close_panel, verdict

console = Console()

DEFAULT_UNIVERSE = [
    "BTC/USDC:USDC", "ETH/USDC:USDC", "SOL/USDC:USDC", "BNB/USDC:USDC", "XRP/USDC:USDC",
    "DOGE/USDC:USDC", "ADA/USDC:USDC", "AVAX/USDC:USDC", "LINK/USDC:USDC", "LTC/USDC:USDC",
    "TRX/USDC:USDC", "DOT/USDC:USDC", "NEAR/USDC:USDC", "ATOM/USDC:USDC", "UNI/USDC:USDC",
]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--tf", default=None)
    p.add_argument("--bars", type=int, default=8000)
    p.add_argument("--train", type=int, default=1500)
    p.add_argument("--test", type=int, default=400)
    p.add_argument("--quantile", type=float, default=0.3)
    p.add_argument("--smooths", nargs="*", type=int, default=[1, 8, 24])
    p.add_argument("--holds", nargs="*", type=int, default=[8, 24, 72])
    p.add_argument("--fee", type=float, default=0.0)
    p.add_argument("--slippage", type=float, default=0.03)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--min-coverage", type=float, default=0.9)
    p.add_argument("--snapshot-dir", default=None)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    cfg = settings.raw
    tf = args.tf or cfg["market"]["timeframe"]
    symbols = args.symbols or DEFAULT_UNIVERSE
    ex = Exchange(settings)

    dfs, fundings = {}, {}
    for sym in symbols:
        try:
            df = load_ohlcv(args.snapshot_dir, sym, tf) if args.snapshot_dir else None
            if df is None:
                df = fetch_history(ex, sym, tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(df, args.snapshot_dir, sym, tf)
            since = int(df.index[0].timestamp() * 1000)
            fundings[sym] = fetch_funding(ex, sym, since)
            dfs[sym] = df
        except Exception as e:  # boundary — pair/funding tak tersedia → lewati
            log.warning(f"lewati {sym}: {e}")

    panel = align_close_panel(dfs, min_coverage=args.min_coverage)
    if panel.shape[1] < 5 or len(panel) < args.train + args.test + max(args.smooths):
        console.print(f"[red]Panel tak cukup: {panel.shape[1]} simbol × {len(panel)} bar.[/red]")
        return
    level, cumf = carry.align_funding(fundings, panel.index, list(panel.columns))
    nz = int((level != 0).any(axis=0).sum())
    log.info(f"Panel: {panel.shape[1]} simbol × {len(panel)} bar ({tf}); funding terisi {nz} simbol.")

    cost_frac = 4 * (args.fee + args.slippage) / 100 * args.stress_mult
    grid = carry.build_grid(args.smooths, args.holds)
    close = panel.to_numpy()
    windows, oos = carry.walk_forward_carry(close, level, cumf, grid, args.quantile,
                                            cost_frac, args.train, args.test)
    log.info(f"Walk-forward carry: {len(windows)} window, grid={len(grid)} "
             f"(smooths={args.smooths} holds={args.holds}) cost/rebal={cost_frac:.4%}")

    tbl = Table(title=f"Funding carry — {tf}, quantile={args.quantile}")
    for c in ["window", "smooth/hold", "IS Sharpe", "IS n", "OOS mean", "OOS n"]:
        tbl.add_column(c, justify="right")
    for i, w in enumerate(windows, 1):
        tbl.add_row(str(i), f"{w.params['smooth']}/{w.params['hold']}",
                    f"{w.is_sharpe:+.3f}", str(w.is_n), f"{w.oos_mean:+.4%}", str(w.oos_n))
    console.print(tbl)

    v = verdict(oos, n_trials=len(grid))
    tbl2 = Table(title="GABUNGAN OUT-OF-SAMPLE")
    for c in ["rebalances", "win%", "mean/rebal", "Sharpe", "p_adj"]:
        tbl2.add_column(c, justify="right")
    tbl2.add_row(str(v["n"]), f"{v.get('win_rate', 0) * 100:.1f}", f"{v['mean']:+.4%}",
                 str(v.get("sharpe", "-")), str(v.get("p_adj", "-")))
    console.print(tbl2)
    color = "green" if v["ok"] else "red"
    console.print(f"[{color}]Verdict: {'LOLOS' if v['ok'] else 'DITOLAK'} — {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
