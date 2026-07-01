#!/usr/bin/env python3
"""Fase 3 — H14 listing-age lifecycle: uji mispricing pair baru listing.

  python lifecycle.py --tf 1d --bars 3000 --snapshot-dir data/snap
  python lifecycle.py --settle USDT --max-age 90 --fee 0.02 --slippage 0.05

Alur:
1. Ambil universe perp (default: SEMUA settle USDT — histori panjang; eksekusi
   nantinya tetap USDC sesuai trik riset).
2. Fetch histori daily penuh; buang simbol tersensor (histori mentok batas fetch
   → bar pertama bukan listing sungguhan).
3. `dispersion_report` — bila tanggal listing terkumpul batch sempit, STOP.
4. Cohort walk-forward: (window umur, arah) dipilih di kohort listing AWAL,
   diuji di kohort listing AKHIR. Verdict via xsectional.verdict (Bonferroni).
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from bot import lifecycle as lc, xsectional as xs
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log

console = Console()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=None,
                   help="override universe; default = semua perp settle --settle")
    p.add_argument("--settle", default="USDT", choices=["USDT", "USDC"])
    p.add_argument("--tf", default="1d")
    p.add_argument("--bars", type=int, default=3000,
                   help="fetch maksimal; simbol dgn len==bars dianggap tersensor & dibuang")
    p.add_argument("--max-age", type=int, default=120, help="umur maksimal dianalisis (hari)")
    p.add_argument("--starts", nargs="*", type=int, default=[1, 8, 15, 30, 60],
                   help="grid awal window umur (hari sejak listing)")
    p.add_argument("--lengths", nargs="*", type=int, default=[7, 14, 30],
                   help="grid panjang window (hari)")
    p.add_argument("--train-frac", type=float, default=0.6)
    p.add_argument("--fee", type=float, default=0.0)
    p.add_argument("--slippage", type=float, default=0.05)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--min-span-days", type=int, default=365)
    p.add_argument("--min-symbols", type=int, default=20)
    p.add_argument("--snapshot-dir", default=None)
    return p.parse_args()


def perp_universe(ex: Exchange, settle: str) -> list[str]:
    return sorted(s for s, v in ex.markets.items()
                  if v.get("settle") == settle and v.get("swap"))


def main() -> None:
    args = parse_args()
    settings = load_settings()
    ex = Exchange(settings)
    symbols = args.symbols or perp_universe(ex, args.settle)
    log.info(f"Universe: {len(symbols)} perp settle {args.settle}.")

    dfs = {}
    for sym in symbols:
        try:
            df = load_ohlcv(args.snapshot_dir, sym, args.tf) if args.snapshot_dir else None
            if df is None:
                df = fetch_history(ex, sym, args.tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(df, args.snapshot_dir, sym, args.tf)
            if len(df):
                dfs[sym] = df
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")

    censored = len(dfs)
    dfs = lc.uncensored(dfs, args.bars)
    log.info(f"{len(dfs)} simbol lolos sensor-kiri ({censored - len(dfs)} dibuang: "
             f"histori mentok batas {args.bars} bar).")

    dates = lc.listing_dates(dfs)
    rep = lc.dispersion_report(dates, args.min_span_days, args.min_symbols)
    console.print(f"Sebaran listing: n={rep['n']}, span={rep['span_days']:.0f} hari — {rep['reason']}")
    if not rep["ok"]:
        console.print("[red]STOP: sebaran listing tak layak untuk cohort walk-forward.[/red]")
        return

    age_rets, syms = lc.age_return_panel(dfs, args.max_age)
    grid = lc.build_grid(args.starts, args.lengths)
    cost_frac = 2 * (args.fee + args.slippage) / 100 * args.stress_mult  # 1 round-trip/trade
    res = lc.cohort_walk_forward(age_rets, syms, dates, grid, cost_frac, args.train_frac)
    if res is None:
        console.print("[red]Kohort train/test terlalu kecil — tak bisa uji jujur.[/red]")
        return

    tbl = Table(title=f"H14 listing-age — {args.tf}, cost/trade={cost_frac:.4%}")
    for c in ["window umur", "arah", "train mean", "train n", "test mean", "test n"]:
        tbl.add_column(c, justify="right")
    te = res.test_returns
    tbl.add_row(f"{res.params['start']}–{res.params['start'] + res.params['length']}hr",
                "LONG" if res.direction > 0 else "SHORT",
                f"{res.train_mean:+.4%}", str(res.train_n),
                f"{te.mean():+.4%}" if len(te) else "-", str(len(te)))
    console.print(tbl)

    v = xs.verdict(te, res.n_trials)
    color = "green" if v["ok"] else "red"
    console.print(f"[{color}]Verdict: {'LOLOS' if v['ok'] else 'DITOLAK'} — {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
