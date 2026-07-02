#!/usr/bin/env python3
"""Jalankan backtest strategi di data historis Binance USDC-M.

  python backtest.py                                   # default config.yaml
  python backtest.py --symbols BTC/USDC:USDC --bars 3000 --tf 15m
  python backtest.py --bars 5000 --equity 1000 --fee 0.04 --slippage 0.02 --csv out.csv

Metrik kunci = expectancy (R). Edge ADA bila expectancy_r > 0 SETELAH biaya.
"""
from __future__ import annotations

import argparse
import csv as csvmod

from rich.console import Console
from rich.table import Table

from bot.backtest import Backtester, compute_metrics, fetch_history
from bot.config import load_settings
from bot.exchange import Exchange
from bot.logger import log

console = Console()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", help="override whitelist config")
    p.add_argument("--tf", help="timeframe (default config.yaml)")
    p.add_argument("--bars", type=int, default=3000)
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--fee", type=float, default=0.04, help="fee taker per sisi (%)")
    p.add_argument("--slippage", type=float, default=0.02, help="slippage per sisi (%)")
    p.add_argument("--csv", help="dump trades ke file CSV")
    return p.parse_args()


def fmt(m: dict) -> dict:
    pf = m["profit_factor"]
    return {
        "trades": str(m["trades"]),
        "win%": f"{m['win_rate']:.1f}",
        "exp_R": f"{m['expectancy_r']:+.3f}",
        "PF": ("∞" if pf == float("inf") else f"{pf:.2f}"),
        "avgW": f"{m['avg_win_r']:+.2f}",
        "avgL": f"{m['avg_loss_r']:+.2f}",
        "maxDD%": f"{m['max_drawdown_pct']:.1f}",
        "ret%": f"{m['return_pct']:+.1f}",
        "tp%": f"{m['tp_rate']:.0f}",
    }


def main() -> None:
    args = parse_args()
    settings = load_settings()
    cfg = settings.raw
    cfg["risk"]["sl_atr_mult"]  # validasi keberadaan
    tf = args.tf or cfg["market"]["timeframe"]
    symbols = args.symbols or cfg["market"].get("whitelist") or ["BTC/USDC:USDC"]

    ex = Exchange(settings)
    bt = Backtester(cfg, fee_pct=args.fee, slippage_pct=args.slippage)

    # Dominansi BTC (mother coin): muat close BTC sekali untuk gerbang direction-aware.
    btc_close = None
    bcfg = cfg.get("btc", {})
    if bcfg.get("enabled", True):
        try:
            btc_close = fetch_history(ex, bcfg.get("symbol", "BTC/USDC:USDC"), tf, args.bars)["close"]
        except Exception as e:  # boundary — gagal muat BTC → gerbang nonaktif
            log.warning(f"BTC-gate nonaktif (muat BTC gagal): {e}")

    log.info(f"Backtest tf={tf} bars={args.bars} fee={args.fee}% slip={args.slippage}% symbols={symbols}")

    all_trades = []
    table = Table(title=f"Backtest per simbol — {tf}, {args.bars} bar")
    for col in ["symbol", "trades", "win%", "exp_R", "PF", "avgW", "avgL", "maxDD%", "ret%", "tp%"]:
        table.add_column(col, justify="right")

    for sym in symbols:
        try:
            df = fetch_history(ex, sym, tf, args.bars)
        except Exception as e:  # boundary
            log.error(f"fetch {sym} gagal: {e}")
            continue
        trades = bt.run_symbol(sym, df, btc_close=btc_close)
        all_trades += trades
        m = compute_metrics(trades, cfg, args.equity)
        if m["trades"] == 0:
            table.add_row(sym, "0", *["-"] * 8)
            continue
        f = fmt(m)
        table.add_row(sym, f["trades"], f["win%"], f["exp_R"], f["PF"], f["avgW"],
                      f["avgL"], f["maxDD%"], f["ret%"], f["tp%"])

    console.print(table)

    combined = compute_metrics(all_trades, cfg, args.equity)
    if combined["trades"]:
        c = fmt(combined)
        ct = Table(title="GABUNGAN semua simbol (pool trades)")
        for col in ["trades", "win%", "exp_R", "PF", "avgW", "avgL", "maxDD%", "ret%", "tp%"]:
            ct.add_column(col, justify="right")
        ct.add_row(c["trades"], c["win%"], c["exp_R"], c["PF"], c["avgW"], c["avgL"],
                   c["maxDD%"], c["ret%"], c["tp%"])
        console.print(ct)

        verdict = combined["expectancy_r"]
        if verdict > 0.05:
            console.print(f"[green]Edge POSITIF: expectancy {verdict:+.3f}R/trade setelah biaya.[/green]")
        elif verdict > 0:
            console.print(f"[yellow]Edge tipis ({verdict:+.3f}R) — rapuh terhadap biaya/asumsi. Belum layak live.[/yellow]")
        else:
            console.print(f"[red]TIDAK ada edge ({verdict:+.3f}R). Jangan live; tuning strategi dulu.[/red]")

    if args.csv and all_trades:
        with open(args.csv, "w", newline="", encoding="utf-8") as fp:
            w = csvmod.writer(fp)
            w.writerow(["symbol", "side", "entry_time", "exit_time", "entry", "exit", "sl", "tp", "r", "bars_held", "reason"])
            for t in all_trades:
                w.writerow([t.symbol, t.side, t.entry_time, t.exit_time, t.entry, t.exit, t.sl, t.tp, f"{t.r:.4f}", t.bars_held, t.reason])
        log.info(f"Trades disimpan: {args.csv} ({len(all_trades)} baris)")


if __name__ == "__main__":
    main()
