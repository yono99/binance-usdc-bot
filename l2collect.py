#!/usr/bin/env python3
"""Kumpulkan snapshot orderbook L2 (data forward untuk riset microstructure).

  python l2collect.py                                  # whitelist config
  python l2collect.py --symbols "BTC/USDC:USDC" --levels 10 --interval 1

Output: data/l2/<symbol>_<YYYYMMDD>.jsonl.gz (gzip, ~50-200 MB/hari/pair).
Data ini TIDAK tersedia historis dari exchange — sekali tak direkam, hilang.
"""
from __future__ import annotations

import argparse

from bot.config import load_settings
from bot.l2 import L2Collector


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--levels", type=int, default=10)
    p.add_argument("--interval", type=float, default=1.0)
    args = p.parse_args()

    settings = load_settings()
    symbols = args.symbols or settings.raw["market"].get("whitelist") or ["BTC/USDC:USDC"]
    L2Collector(settings, symbols, levels=args.levels, interval=args.interval).run()


if __name__ == "__main__":
    main()
