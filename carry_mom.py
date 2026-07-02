#!/usr/bin/env python3
"""Fase 4 — H25 carry × momentum double-sort.

  python carry_mom.py --symbols <...> --snapshot-dir data/snap_smallcap1400

Carry cross-sectional (income funding realized + PnL harga) dengan GERBANG:
hanya simbol yang residual-momentum-nya berlawanan arah funding yang eligible —
menarget failure mode carry terdokumentasi (short funding-tinggi kelindas pump).
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from bot import carry, xs_signals as xss, xsectional as xs
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
    p.add_argument("--tf", default="1d")
    p.add_argument("--bars", type=int, default=1400)
    p.add_argument("--train", type=int, default=300)
    p.add_argument("--test", type=int, default=120)
    p.add_argument("--quantile", type=float, default=0.3)
    p.add_argument("--fee", type=float, default=0.02)
    p.add_argument("--slippage", type=float, default=0.05)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--smooth", type=int, default=3)
    p.add_argument("--mom-lookbacks", nargs="*", type=int, default=[5, 10])
    p.add_argument("--holds", nargs="*", type=int, default=[3, 7])
    p.add_argument("--beta-win", type=int, default=60)
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
            fundings[sym] = fetch_funding(ex, sym, int(df.index[0].timestamp() * 1000),
                                          max_points=3 * args.bars + 100)
            dfs[sym] = df
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")

    panel = xs.align_close_panel(dfs)
    btc_cols = [i for i, c in enumerate(panel.columns) if c.startswith("BTC")]
    if not btc_cols or panel.shape[1] < 8:
        console.print("[red]Butuh BTC di panel + >=8 simbol.[/red]")
        return
    close = panel.to_numpy()
    level, cumf = carry.align_funding(fundings, panel.index, list(panel.columns))
    log.info(f"Panel: {panel.shape[1]} simbol × {len(panel)} bar ({args.tf}).")

    r = xss.returns_panel(close)
    beta = xss.rolling_beta(r, r[:, btc_cols[0]], args.beta_win)
    resid = xss.residual_returns(r, r[:, btc_cols[0]], beta)
    mom_panels = {f"m{lb}": xss._roll_sum(resid, lb) for lb in args.mom_lookbacks}

    cost_frac = 4 * (args.fee + args.slippage) / 100 * args.stress_mult
    n_trials = len(mom_panels) * len(args.holds)
    windows, oos = carry.walk_forward_carry_mom(close, level, cumf, mom_panels,
                                                args.holds, args.smooth, args.quantile,
                                                cost_frac, args.train, args.test)
    log.info(f"[H25 carry×mom] {len(windows)} window, grid={n_trials}, "
             f"cost/rebal={cost_frac:.4%}")

    tbl = Table(title=f"H25 carry×momentum — {args.tf}, q={args.quantile}")
    for c in ["window", "mom/hold", "IS Sharpe", "IS n", "OOS mean", "OOS n"]:
        tbl.add_column(c, justify="right")
    for i, w in enumerate(windows, 1):
        tbl.add_row(str(i), f"{w.params['mom']}/{w.params['hold']}",
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
