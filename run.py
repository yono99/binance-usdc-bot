#!/usr/bin/env python3
"""Entry point. Mode diambil dari .env (MODE=dry|test|live).

  python run.py            # jalankan loop sesuai MODE
  python run.py --once     # satu tick lalu keluar (uji cepat)
  python run.py --check    # cek koneksi & universe, tanpa trading
"""
from __future__ import annotations

import sys

from bot.config import load_settings
from bot.engine import Engine
from bot.logger import log


def main() -> None:
    settings = load_settings()
    engine = Engine(settings)

    if "--check" in sys.argv:
        log.info(f"Mode: {settings.mode} | equity~{engine.ex.equity_usdc():.2f} USDC")
        log.info(f"Universe: {engine.universe()}")
        return

    if settings.is_live:
        log.warning("=========================================")
        log.warning(" MODE LIVE — UANG NYATA. Ctrl+C 5 detik ke depan untuk batal.")
        log.warning("=========================================")
        import time
        time.sleep(5)

    if "--once" in sys.argv:
        engine.tick()
        return

    engine.run()


if __name__ == "__main__":
    main()
