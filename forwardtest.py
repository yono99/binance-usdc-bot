#!/usr/bin/env python3
"""Forward-test (paper) strategi v4 di data LIVE real-time. Tanpa uang.

  python forwardtest.py                       # whitelist config, parameter default
  python forwardtest.py --symbols "BTC/USDC:USDC" --poll 30
  python forwardtest.py --once                # satu siklus (uji cepat)

Parameter TETAP selama jalan (tidak re-optimize). Hasil di logs/forward_trades.jsonl.
Jalankan berhari-hari; bila expectancy R tetap > 0 di sampel besar, baru ada bukti edge.
"""
from __future__ import annotations

import argparse

from bot.config import load_settings
from bot.forward import ForwardTester, default_params
from bot.logger import log


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--poll", type=int, default=30)
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--conf", type=float)
    p.add_argument("--sl", type=float)
    p.add_argument("--tp", type=float)
    p.add_argument("--no-htf", action="store_true")
    p.add_argument("--no-regime", action="store_true")
    p.add_argument("--no-funding", action="store_true")
    p.add_argument("--oi", action="store_true")
    p.add_argument("--no-of", action="store_true")
    p.add_argument("--once", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    symbols = args.symbols or settings.raw["market"].get("whitelist") or ["BTC/USDC:USDC"]

    params = default_params()
    if args.conf is not None:
        params["entry_confidence"] = args.conf
    if args.sl is not None:
        params["sl_atr_mult"] = args.sl
    if args.tp is not None:
        params["tp_atr_mult"] = args.tp
    params["use_htf"] = not args.no_htf
    params["regime"] = not args.no_regime
    params["use_funding"] = not args.no_funding
    params["use_oi"] = args.oi
    params["use_of"] = not args.no_of

    ft = ForwardTester(settings, symbols, params, equity=args.equity)

    if args.once:
        ft.seed()
        log.info(f"params={params}")
        ft.on_cycle()
        log.info(f"stats: {ft.stats()}")
        return

    ft.run(poll_s=args.poll)


if __name__ == "__main__":
    main()
