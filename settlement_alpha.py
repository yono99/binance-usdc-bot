#!/usr/bin/env python3
"""Fase 4 — H24 seasonality settlement funding.

  python settlement_alpha.py --symbols <...> --bars 17520 --snapshot-dir data/snap1h

Rebalance tepat di bar pra-settlement (00/08/16 UTC − offset); skor = −funding
level; PnL = harga + funding yang dibebankan. Walk-forward + Bonferroni.
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from bot import carry, settlement as st, xsectional as xs
from bot.altdata import fetch_funding
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log

console = Console()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", required=True)
    p.add_argument("--tf", default="1h")
    p.add_argument("--bars", type=int, default=17520)
    p.add_argument("--train", type=int, default=6000)
    p.add_argument("--test", type=int, default=2000)
    p.add_argument("--quantile", type=float, default=0.3)
    p.add_argument("--fee", type=float, default=0.0)
    p.add_argument("--slippage", type=float, default=0.03)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--offsets", nargs="*", type=int, default=[0, 1])
    p.add_argument("--holds", nargs="*", type=int, default=[1, 4, 8])
    p.add_argument("--snapshot-dir", default=None)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    ex = Exchange(load_settings())
    dfs, fundings = {}, {}
    for sym in args.symbols:
        try:
            df = load_ohlcv(args.snapshot_dir, sym, args.tf) if args.snapshot_dir else None
            if df is None:
                df = fetch_history(ex, sym, args.tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(df, args.snapshot_dir, sym, args.tf)
            fundings[sym] = fetch_funding(ex, sym, int(df.index[0].timestamp() * 1000))
            dfs[sym] = df
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")

    panel = xs.align_close_panel(dfs)
    if panel.shape[1] < 8:
        console.print("[red]Butuh >=8 simbol selaras.[/red]")
        return
    close = panel.to_numpy()
    level, cumf = carry.align_funding(fundings, panel.index, list(panel.columns))
    log.info(f"Panel: {panel.shape[1]} simbol × {len(panel)} bar (1h).")

    cost_frac = 4 * (args.fee + args.slippage) / 100 * args.stress_mult
    n_trials = len(args.offsets) * len(args.holds)
    windows, oos = st.walk_forward_settlement(close, level, cumf, panel.index,
                                              args.offsets, args.holds, args.quantile,
                                              cost_frac, args.train, args.test)
    log.info(f"[H24 settlement] {len(windows)} window, grid={n_trials}, "
             f"cost/rebal={cost_frac:.4%}")

    tbl = Table(title=f"H24 settlement — q={args.quantile}")
    for c in ["window", "offset/hold", "IS Sharpe", "IS n", "OOS mean", "OOS n"]:
        tbl.add_column(c, justify="right")
    for i, w in enumerate(windows, 1):
        tbl.add_row(str(i), f"{w.params['offset']}/{w.params['hold']}",
                    f"{w.is_sharpe:+.3f}", str(w.is_n), f"{w.oos_mean:+.4%}", str(w.oos_n))
    console.print(tbl)

    v = xs.verdict(oos, n_trials)
    t2 = Table(title="GABUNGAN OOS")
    for c in ["rebalances", "win%", "mean/rebal", "Sharpe", "p_adj"]:
        t2.add_column(c, justify="right")
    t2.add_row(str(v["n"]), f"{v.get('win_rate', 0) * 100:.1f}", f"{v['mean']:+.4%}",
               str(v.get("sharpe", "-")), str(v.get("p_adj", "-")))
    console.print(t2)
    color = "green" if v["ok"] else "red"
    console.print(f"[{color}]Verdict: {'LOLOS' if v['ok'] else 'DITOLAK'} — {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
