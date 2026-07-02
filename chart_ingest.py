#!/usr/bin/env python3
"""Produk data chart — tarik OHLCV ke SQLite (data/market.db), inkremental.

  python chart_ingest.py                                  # whitelist config, 15m+1h+1d
  python chart_ingest.py --symbols "BTC/USDC:USDC" --tf 15m --bars 5000
  python chart_ingest.py --all-usdc --loop 900            # daemon: refresh tiap 15 mnt

Baca hasilnya: dashboard GET /api/candles?symbol=...&tf=...  atau
  from bot import chartstore; chartstore.load("BTC/USDC:USDC", "15m")
"""
from __future__ import annotations

import argparse
import time

from bot import chartstore
from bot.config import load_settings
from bot.exchange import Exchange
from bot.logger import log


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--all-usdc", action="store_true", help="semua perp USDC")
    p.add_argument("--tf", nargs="*", default=["15m", "1h", "1d"])
    p.add_argument("--bars", type=int, default=3000, help="backfill awal per timeframe")
    p.add_argument("--loop", type=float, default=0, help="detik antar refresh (0 = sekali)")
    args = p.parse_args()

    settings = load_settings()
    ex = Exchange(settings)
    symbols = (args.symbols or
               (ex.usdc_symbols() if args.all_usdc else
                settings.raw["market"].get("whitelist") or ["BTC/USDC:USDC"]))
    log.info(f"=== CHART INGEST: {len(symbols)} simbol × {args.tf} -> {chartstore.DB_PATH} ===")

    while True:
        total = 0
        for sym in symbols:
            for tf in args.tf:
                try:
                    n = chartstore.ingest(ex, sym, tf, bars=args.bars)
                    total += n
                    if n:
                        log.info(f"{sym} {tf}: +{n} bar")
                except Exception as e:  # boundary — satu simbol gagal ≠ berhenti
                    log.warning(f"{sym} {tf} gagal: {e}")
        cov = chartstore.coverage()
        log.info(f"Ingest selesai: +{total} bar baru; store: {len(cov)} seri.")
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
