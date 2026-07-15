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
from bot.settings_store import reset_all_enabled


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
    p.add_argument("--use-store", action="store_true",
                   help="baca pengaturan dari UI (runtime.json) tiap siklus")
    p.add_argument("--mode", choices=["dry", "test", "live"],
                   help="KUNCI proses ke satu mode (abaikan mode aktif UI). "
                        "Jalankan satu proses per mode untuk multi-mode paralel.")
    p.add_argument("--once", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    # Skip reset_all_enabled in production (PM2) - use SKIP_ENABLED_RESET=1 env var
    import os
    if not os.getenv("SKIP_ENABLED_RESET"):
        reset_all_enabled()
        log.info("Startup: SEMUA mode di-reset ke OFF — nyalakan dari dashboard.")
    else:
        log.info("Startup: SKIP enabled reset (SKIP_ENABLED_RESET=1)")
    settings = load_settings()
    if args.mode:
        import os
        from dataclasses import replace
        if args.mode == "live" and not (os.getenv("BINANCE_LIVE_KEY") and os.getenv("BINANCE_LIVE_SECRET")):
            log.error("--mode live butuh BINANCE_LIVE_KEY/SECRET di .env — berhenti.")
            return
        settings = replace(settings, mode=args.mode)
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

    ft = ForwardTester(settings, symbols, params, equity=args.equity,
                       use_store=args.use_store, pin_mode=bool(args.mode))

    if args.once:
        ft.seed()
        log.info(f"params={params}")
        ft.on_cycle()
        log.info(f"stats: {ft.stats()}")
        return

    ft.run(poll_s=args.poll)


if __name__ == "__main__":
    main()
