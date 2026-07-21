#!/usr/bin/env python3
"""Fase 4 — H32 TSMOM harian per-simbol (penutup lubang formal).

  python tsmom_alpha.py --symbols <...> --snapshot-dir data/snap_smallcap1400
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from bot import tsmom, xsectional as xs
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log

console = Console()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", required=True)
    p.add_argument("--tf", default="1d")
    p.add_argument("--bars", type=int, default=1400)
    p.add_argument("--train", type=int, default=300)
    p.add_argument("--test", type=int, default=120)
    p.add_argument("--lookbacks", nargs="*", type=int, default=[30, 60, 90])
    p.add_argument("--hold", type=int, default=5)
    p.add_argument("--fee", type=float, default=0.02)
    p.add_argument("--slippage", type=float, default=0.05)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--snapshot-dir", default=None)
    args = p.parse_args()

    ex = Exchange(load_settings())
    dfs = {}
    for sym in args.symbols:
        try:
            df = load_ohlcv(args.snapshot_dir, sym, args.tf) if args.snapshot_dir else None
            if df is None:
                df = fetch_history(ex, sym, args.tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(df, args.snapshot_dir, sym, args.tf)
            dfs[sym] = df
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")

    panel = xs.align_close_panel(dfs)
    close = panel.to_numpy()
    log.info(f"Panel: {panel.shape[1]} simbol × {len(panel)} bar ({args.tf}).")

    cost_frac = 2 * (args.fee + args.slippage) / 100 * args.stress_mult  # round-trip/leg
    windows, oos = tsmom.walk_forward_tsmom(close, args.lookbacks, args.hold, cost_frac,
                                            args.train, args.test)
    n_trials = len(args.lookbacks)
    log.info(f"[H32 tsmom] {len(windows)} window, grid={n_trials}, cost/rebal={cost_frac:.4%}")

    tbl = Table(title=f"H32 TSMOM — {args.tf}, hold={args.hold}")
    for c in ["window", "lookback", "IS Sharpe", "IS n", "OOS mean", "OOS n"]:
        tbl.add_column(c, justify="right")
    for i, w in enumerate(windows, 1):
        tbl.add_row(str(i), str(w.params["lookback"]), f"{w.is_sharpe:+.3f}",
                    str(w.is_n), f"{w.oos_mean:+.4%}", str(w.oos_n))
    console.print(tbl)

    v = xs.verdict(oos, n_trials)
    console.print(f"OOS: n={v['n']}, mean={v['mean']:+.4%}, win={v.get('win_rate', 0):.1%}, "
                  f"Sharpe={v.get('sharpe', '-')}, p_adj={v.get('p_adj', '-')}")
    color = "green" if v["ok"] else "red"
    console.print(f"[{color}]Verdict: {'LOLOS' if v['ok'] else 'DITOLAK'} — {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
