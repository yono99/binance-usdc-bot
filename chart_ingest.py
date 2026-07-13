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
    p.add_argument("--tf", nargs="*", default=["15m", "1h", "1d"],
                   help="timeframe list; gunakan '1w'/'1M' utk backfill panjang (10y)")
    p.add_argument("--bars", type=int, default=3000, help="backfill awal per timeframe")
    p.add_argument("--loop", type=float, default=0, help="detik antar refresh (0 = sekali)")
    p.add_argument("--paginate-back", action="store_true",
                   help="Tahap 6: paginasikan mundur 1w/1M hingga ~10 tahun historis")
    args = p.parse_args()

    settings = load_settings()
    ex = Exchange(settings)
    symbols = (args.symbols or
               (ex.usdc_symbols() if args.all_usdc else
                settings.raw["market"].get("whitelist") or ["BTC/USDC:USDC"]))
    # Tahap 6: high-timeframe 1w/1M butuh bars lebih banyak (1500 default = ±2y 1w; 12y 1M).
    # Pakai horizon khusus: 1w→3120 (~6y), 1M→180 (~15y, max API Binance).
    tf_bars = {}
    for tf in args.tf:
        if tf == "1w":
            tf_bars[tf] = max(args.bars, 3120)
        elif tf == "1M":
            tf_bars[tf] = max(args.bars, 180)
        else:
            tf_bars[tf] = args.bars
    log.info(f"=== CHART INGEST: {len(symbols)} simbol × {args.tf} -> {chartstore.DB_PATH} ===")

    while True:
        total = 0
        for sym in symbols:
            for tf in args.tf:
                try:
                    n = chartstore.ingest(ex, sym, tf, bars=tf_bars[tf],
                                          extra_paginate=args.paginate_back)
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
