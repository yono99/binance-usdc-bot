"""EventHub — fan-out real-time events ke SSE client dashboard.

SSE butuh PUSH (SQLite WAL tidak bisa). EventHub menggabung 4 sumber event:
  1. ZMQ SUB :5558 (OrderEvent dari Rust core)            — real-time trade lifecycle
  2. ZMQ SUB :5556 (Candle market data dari Rust core)    — real-time price/ohlcv
  3. Binance User Data WS (account/order/position update) — exchange-backed real-time
  4. HTTP webhook /internal/notify dari forward.py        — SQLite-backed changes (status/trades)
  5. SQLite watcher (poll kv 2s)                          — fallback kalau webhook gagal

Setiap sumber berjalan di asyncio task terpisah, fail-open: satu sumber mati
tidak menjatuhkan SSE stream. Broadcast men-drop ke slow client (QueueFull) —
tidak memblokir producer.

Lifecycle: dibuat saat startup FastAPI (lifespan), dihentikan saat shutdown.
Diakses endpoint /api/stream via subscribe()/unsubscribe().
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any, AsyncIterator

# Windows: zmq.asyncio butuh SelectorEventLoop (Proactor default gak implement
# add_reader). Set policy sekali saat import — aman di Linux (no-op).
# Production deploy (Proxmox/Debian) tidak terpengaruh.
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from .logger import log

# --- konfigurasi (env, fallback aman) ---
ZMQ_EVENT_PUB = os.getenv("ZMQ_EVENT_PUB", "tcp://127.0.0.1:5558")
ZMQ_MARKET_PUB = os.getenv("ZMQ_MARKET_PUB", "tcp://127.0.0.1:5556")
CANDLE_THROTTLE_S = float(os.getenv("SSE_CANDLE_THROTTLE", "1.0"))   # min 1/detik per client
SQLITE_POLL_S = float(os.getenv("SSE_SQLITE_POLL", "2.0"))
USERDATA_RECONNECT_S = float(os.getenv("SSE_WS_RECONNECT", "5.0"))
KEEPALIVE_S = 25.0   # bawah proxy idle timeout (nginx 60s)

_QUEUE_MAX = 200   # per-client; QueueFull → drop (client lambat)


class EventHub:
    """In-memory pub/sub: subscribe() → Queue, broadcast() → fan-out semua subscriber."""

    def __init__(self) -> None:
        self._subs: list[asyncio.Queue] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False

    # ---------- subscriber management ----------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    async def broadcast(self, event_type: str, data: Any) -> None:
        """Fan-out ke semua subscriber. Drop kalau queue penuh (client lambat)."""
        if not self._subs:
            return
        frame = json.dumps({"type": event_type, "data": data, "ts": time.time()},
                           default=str)
        for q in self._subs:
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                pass   # client lambat → drop, jangan blok producer

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        loop = asyncio.get_event_loop()
        self._tasks = [
            loop.create_task(self._zmq_event_sub(), name="sse-zmq-event"),
            loop.create_task(self._zmq_market_sub(), name="sse-zmq-market"),
            loop.create_task(self._binance_userdata(), name="sse-binance-ws"),
            loop.create_task(self._sqlite_watcher(), name="sse-sqlite"),
        ]
        log.info(f"EventHub start: {len(self._tasks)} source task aktif")

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self._subs.clear()
        log.info("EventHub stop")

    # ---------- sumber 1: ZMQ OrderEvent (:5558) ----------
    async def _zmq_event_sub(self) -> None:
        """SUB ke Rust core EventPub — OrderEvent {open,reject,error}."""
        try:
            import zmq.asyncio   # pyzmq
            ctx = zmq.asyncio.Context()
            sock = ctx.socket(zmq.SUB)
            sock.connect(ZMQ_EVENT_PUB)
            sock.setsockopt(zmq.SUBSCRIBE, b"")   # semua topik
            log.info(f"EventHub: ZMQ-event SUB {ZMQ_EVENT_PUB}")
            while self._running:
                raw = await sock.recv()
                try:
                    msg = json.loads(raw)
                    # Rust OrderEvent → event type SSE
                    kind = msg.get("kind") or msg.get("type") or "order_event"
                    await self.broadcast(f"order_{kind}", msg)
                except (json.JSONDecodeError, Exception) as e:
                    log.debug(f"EventHub zmq-event parse: {e}")
        except Exception as e:
            log.warning(f"EventHub ZMQ-event gagal (Rust core mati?): {e}")

    # ---------- sumber 2: ZMQ Candle (:5556) ----------
    async def _zmq_market_sub(self) -> None:
        """SUB ke Rust core MarketPub — Candle. Throttle: max 1/s."""
        last_push = 0.0
        try:
            import zmq.asyncio
            ctx = zmq.asyncio.Context()
            sock = ctx.socket(zmq.SUB)
            sock.connect(ZMQ_MARKET_PUB)
            sock.setsockopt(zmq.SUBSCRIBE, b"")
            log.info(f"EventHub: ZMQ-market SUB {ZMQ_MARKET_PUB}")
            while self._running:
                raw = await sock.recv()
                now = time.time()
                if now - last_push < CANDLE_THROTTLE_S:
                    continue   # throttle: skip tick terlalu rapat
                last_push = now
                try:
                    msg = json.loads(raw)
                    sym = msg.get("symbol") or msg.get("s") or "?"
                    await self.broadcast("candle", {"symbol": sym, **msg})
                except (json.JSONDecodeError, Exception):
                    pass
        except Exception as e:
            log.warning(f"EventHub ZMQ-market gagal: {e}")

    # ---------- sumber 3: Binance User Data WS ----------
    async def _binance_userdata(self) -> None:
        """WS Binance user data stream — account/order/position real-time.

        Butuh listenKey (api+secret). Kalau mode dry (no creds) atau WS gagal,
        skip — fail-open, SSE tetap jalan dari sumber lain.
        """
        from .config import load_settings
        settings = load_settings()
        if settings.is_dry:
            log.info("EventHub: Binance user-data WS skip (dry mode, no creds)")
            return   # paper mode — account/order dari SQLite watcher saja
        try:
            import websockets
        except ImportError:
            log.warning("EventHub: lib 'websockets' belum diinstall — "
                        "pip install websockets (user-data WS skip)")
            return

        api_key, api_secret = settings.credentials()
        if not api_key:
            log.warning("EventHub: no API creds — user-data WS skip")
            return

        import ccxt
        ex = ccxt.binanceusdm({"apiKey": api_key, "secret": api_secret,
                               "options": {"defaultType": "future"}})
        while self._running:
            try:
                lk = ex.fapiPrivatePostListenKey().get("listenKey")
                if not lk:
                    await asyncio.sleep(USERDATA_RECONNECT_S)
                    continue
                url = f"wss://fstream.binance.com/ws/{lk}"
                log.info("EventHub: Binance user-data WS connect")
                async with websockets.connect(url, ping_interval=20) as ws:
                    # task refresh listenKey tiap 30 menit
                    refresh = asyncio.create_task(self._refresh_listenkey(ex))
                    try:
                        async for raw in ws:
                            msg = json.loads(raw)
                            etype = msg.get("e")
                            if etype == "ORDER_TRADE_UPDATE":
                                await self.broadcast("order_update", msg.get("o"))
                            elif etype == "ACCOUNT_UPDATE":
                                await self.broadcast("account_update", msg.get("a"))
                            elif etype == "listenKeyExpired":
                                log.warning("EventHub: listenKey expired → reconnect")
                                break
                    finally:
                        refresh.cancel()
            except Exception as e:
                log.debug(f"EventHub user-data WS: {e}")
            await asyncio.sleep(USERDATA_RECONNECT_S)

    async def _refresh_listenkey(self, ex) -> None:
        """Refresh listenKey tiap 30 menit (Binance expiry 60m)."""
        while self._running:
            await asyncio.sleep(1800)
            try:
                ex.fapiPrivatePostListenKey()
            except Exception:
                pass

    # ---------- sumber 4: SQLite watcher (fallback) ----------
    async def _sqlite_watcher(self) -> None:
        """Poll SQLite kv table — broadcast status change. Fallback kalau webhook gagal."""
        from . import store
        last_status = None
        last_balance = None
        log.info(f"EventHub: SQLite watcher poll={SQLITE_POLL_S}s")
        while self._running:
            try:
                # status per-mode aktif (mirip dashboard._ui_mode)
                mode = store.get_kv("active_mode") or {}
                m = mode.get("mode") if isinstance(mode, dict) else None
                key = f"status:{m}" if m else "status"
                st = store.get_kv(key) or store.get_kv("status")
                if st and st != last_status:
                    last_status = st
                    await self.broadcast("status", st)
                # balance state (forward.py:804 write)
                bal = store.get_kv("balance_state")
                if bal and bal != last_balance:
                    last_balance = bal
                    await self.broadcast("balance", bal)
            except Exception as e:
                log.debug(f"EventHub sqlite watcher: {e}")
            await asyncio.sleep(SQLITE_POLL_S)


# singleton global — dibuat saat import, start saat FastAPI lifespan
hub = EventHub()
