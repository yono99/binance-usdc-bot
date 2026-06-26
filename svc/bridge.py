"""Jembatan ZeroMQ ke Rust core (pyzmq, kompatibel ZMTP).

Core mem-BIND; Python meng-CONNECT:
  SUB  market (candle)  <- ZMQ_MARKET_PUB  (default 5556)
  SUB  event  (order)   <- ZMQ_EVENT_PUB   (default 5558)
  PUSH intent (sinyal)  -> ZMQ_SIGNAL_PUSH (default 5557)
"""
from __future__ import annotations

import json
import os

import zmq
import zmq.asyncio


class ZmqBridge:
    def __init__(self):
        self.ctx = zmq.asyncio.Context.instance()

        self.market = self.ctx.socket(zmq.SUB)
        self.market.connect(os.getenv("ZMQ_MARKET_PUB", "tcp://127.0.0.1:5556"))
        self.market.setsockopt_string(zmq.SUBSCRIBE, "")

        self.events = self.ctx.socket(zmq.SUB)
        self.events.connect(os.getenv("ZMQ_EVENT_PUB", "tcp://127.0.0.1:5558"))
        self.events.setsockopt_string(zmq.SUBSCRIBE, "")

        self.intent = self.ctx.socket(zmq.PUSH)
        self.intent.connect(os.getenv("ZMQ_SIGNAL_PUSH", "tcp://127.0.0.1:5557"))

    async def recv_candle(self) -> dict:
        return json.loads(await self.market.recv_string())

    async def recv_event(self) -> dict:
        return json.loads(await self.events.recv_string())

    async def send_intent(self, intent: dict) -> None:
        await self.intent.send_string(json.dumps(intent))

    def close(self) -> None:
        for s in (self.market, self.events, self.intent):
            s.close(0)
