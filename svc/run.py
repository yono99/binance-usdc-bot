#!/usr/bin/env python3
"""Entry point layanan Python. Menyambung ke Rust core via ZeroMQ.

  python -m svc.run

Alur:
  core PUB candle (5556) -> Strategy.on_candle -> intent -> PUSH core (5557)
  core PUB event  (5558) -> Strategy.on_event  (lacak posisi/cooldown)

Catatan: jalankan `core` (Rust) lebih dulu agar socket-nya sudah BIND.
"""
from __future__ import annotations

import asyncio
import os

from bot.config import load_settings
from bot.exchange import Exchange
from bot.logger import log

from .bridge import ZmqBridge
from .strategy import Strategy


async def market_loop(bridge: ZmqBridge, strategy: Strategy) -> None:
    while True:
        candle = await bridge.recv_candle()
        try:
            intent = strategy.on_candle(candle)
            if intent:
                await bridge.send_intent(intent)
        except Exception as e:  # boundary — loop tidak boleh mati
            log.error(f"market_loop: {e}")


async def event_loop(bridge: ZmqBridge, strategy: Strategy) -> None:
    while True:
        ev = await bridge.recv_event()
        try:
            strategy.on_event(ev)
        except Exception as e:  # boundary
            log.error(f"event_loop: {e}")


async def main() -> None:
    settings = load_settings()
    symbols = [s.strip() for s in os.getenv("SYMBOLS", "btcusdc,ethusdc,bnbusdc,solusdc").split(",") if s.strip()]

    strategy = Strategy(settings, symbols)
    strategy.seed(Exchange(settings))  # REST seed agar sinyal siap dari awal

    bridge = ZmqBridge()
    log.info(f"=== svc START mode={settings.mode} tf={strategy.tf} symbols={symbols} ===")
    log.info("menyambung ke core (market 5556 / signal 5557 / event 5558)…")

    try:
        await asyncio.gather(market_loop(bridge, strategy), event_loop(bridge, strategy))
    except KeyboardInterrupt:
        log.info("Dihentikan pengguna.")
    finally:
        bridge.close()


if __name__ == "__main__":
    asyncio.run(main())
