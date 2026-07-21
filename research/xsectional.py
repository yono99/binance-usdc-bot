#!/usr/bin/env python3
"""Fase 2 — cross-sectional momentum di universe USDC perp (edge RELATIF antar-pair).

  python xsectional.py --tf 1h --bars 8000 --train 1500 --test 400
  python xsectional.py --tf 15m --bars 12000 --fee 0 --slippage 0.05 --stress-mult 2

Verdict = mean OOS long-short setelah biaya, DENGAN koreksi multiple-testing (jujur).
Rank seluruh universe tiap rebalance → LONG kuantil terkuat, SHORT terlemah.
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from bot import xsectional as xs
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log

console = Console()

# Universe majors USDC perp (yang tak tersedia otomatis dilewati saat fetch).
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
    p.add_argument("--quantile", type=float, default=0.3, help="fraksi kuantil long & short")
    p.add_argument("--lookbacks", nargs="*", type=int, default=[24, 48, 96])
    p.add_argument("--holds", nargs="*", type=int, default=[6, 12, 24])
    p.add_argument("--fee", type=float, default=0.0, help="% per sisi (USDC promo = 0)")
    p.add_argument("--slippage", type=float, default=0.03)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--min-coverage", type=float, default=0.9)
    p.add_argument("--reverse", action="store_true", help="mean-reversion: long terlemah, short terkuat")
    p.add_argument("--regime", action="store_true",
                   help="regime-conditional: hanya trading saat dispersi > median-train")
    p.add_argument("--snapshot-dir", default=None)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    cfg = settings.raw
    tf = args.tf or cfg["market"]["timeframe"]
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
        except Exception as e:  # boundary — pair tak tersedia → lewati
            log.warning(f"lewati {sym}: {e}")

    panel = xs.align_close_panel(dfs, min_coverage=args.min_coverage)
    if panel.shape[1] < 5 or len(panel) < args.train + args.test + max(args.lookbacks):
        console.print(f"[red]Panel tak cukup: {panel.shape[1]} simbol × {len(panel)} bar.[/red]")
        return
    log.info(f"Panel: {panel.shape[1]} simbol × {len(panel)} bar selaras ({tf}). "
             f"Simbol: {', '.join(s.split('/')[0] for s in panel.columns)}")

    # Biaya round-trip cross-sectional: long+short (2× exposure), masuk+keluar → 4×.
    cost_frac = 4 * (args.fee + args.slippage) / 100 * args.stress_mult
    grid = xs.build_grid(args.lookbacks, args.holds)
    close = panel.to_numpy()
    wf = xs.walk_forward_xs_regime if args.regime else xs.walk_forward_xs
    windows, oos = wf(close, grid, args.quantile, cost_frac,
                      args.train, args.test, reverse=args.reverse)
    base = "REVERSAL (long lemah/short kuat)" if args.reverse else "MOMENTUM (long kuat/short lemah)"
    mode = base + (" + REGIME(dispersi>median)" if args.regime else "")
    log.info(f"Walk-forward [{mode}]: {len(windows)} window, grid={len(grid)} "
             f"(lookbacks={args.lookbacks} holds={args.holds}) cost/rebal={cost_frac:.4%}")

    tbl = Table(title=f"Cross-sectional momentum — {tf}, quantile={args.quantile}")
    for c in ["window", "lookback/hold", "IS Sharpe", "IS n", "OOS mean", "OOS n"]:
        tbl.add_column(c, justify="right")
    for i, w in enumerate(windows, 1):
        tbl.add_row(str(i), f"{w.params['lookback']}/{w.params['hold']}",
                    f"{w.is_sharpe:+.3f}", str(w.is_n),
                    f"{w.oos_mean:+.4%}", str(w.oos_n))
    console.print(tbl)

    v = xs.verdict(oos, n_trials=len(grid))
    tbl2 = Table(title="GABUNGAN OUT-OF-SAMPLE")
    for c in ["rebalances", "win%", "mean/rebal", "Sharpe", "p_adj"]:
        tbl2.add_column(c, justify="right")
    tbl2.add_row(str(v["n"]), f"{v.get('win_rate', 0) * 100:.1f}",
                 f"{v['mean']:+.4%}", str(v.get("sharpe", "-")), str(v.get("p_adj", "-")))
    console.print(tbl2)
    color = "green" if v["ok"] else "red"
    console.print(f"[{color}]Verdict: {'LOLOS' if v['ok'] else 'DITOLAK'} — {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
