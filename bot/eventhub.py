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

Tahap 4 (plan-sess): tiap source event diberi label `mode` agar frontend filter ke
mode aktif UI. Zerodha-style: tiap proses forwardtest.py--mode berbeda mem-broadcast
listenKey terpisah; namun karena EventHub dipakai GLOBAL (1 proses dashboard), mode
di-infer dari sumber: ACCOUNT_UPDATE hanya dr WS live (mode=live), event ZMQ ke-core
bisa di-strip dr process context (lihat _mode_label_for_src).

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
    """In-memory pub/sub: subscribe() → Queue, broadcast() → fan-out semua subscriber.

    Tahap 4 (plan-sess): tiap broadcast top-level diberi label `mode`:
      - SSE order_update/account_update dr Binance WS → mode='live' (sumber hanya ada di live)
      - SSE trade/status dr SQLite watcher → mode='<active_mode from kv>' (per-mode)
      - SSE candle ZMQ → mode='*' (global market data, tak per-mode — bukan trade-source).
    Frontend frontend/backend bisa filter client-side sesuai mode UI aktif."""

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

    async def broadcast(self, event_type: str, data: Any,
                        mode: str | None = None) -> None:
        """Fan-out ke semua subscriber. Drop kalau queue penuh (client lambat).

        Tahap 4: `mode` opsional — default akan di-resolve oleh storage_publisher
        (lihat ZMQ sub handlers)."""
        if not self._subs:
            return
        payload = {"type": event_type, "data": data, "ts": time.time()}
        if mode is not None:
            payload["mode"] = mode
        frame = json.dumps(payload, default=str)
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
        """SUB ke Rust core EventPub — OrderEvent {open,reject,error}.

        Tahap 4: label mode='*' (ZMQ events global — bukan per-mode). Mode spesifik
        di-pasang oleh core konteks upstream (lihat core.rs)."""
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
                    # mode dari msg.upstream_mode jika ada
                    mode = msg.get("mode") or "*"
                    payload = dict(msg)
                    payload.pop("mode", None)
                    await self.broadcast(f"order_{kind}", payload, mode=mode)
                except (json.JSONDecodeError, Exception) as e:
                    log.debug(f"EventHub zmq-event parse: {e}")
        except Exception as e:
            log.warning(f"EventHub ZMQ-event gagal (Rust core mati?): {e}")

    # ---------- sumber 2: ZMQ Candle (:5556) ----------
    async def _zmq_market_sub(self) -> None:
        """SUB ke Rust core MarketPub — Candle. Throttle: max 1/s. Mode='*' (global)."""
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
                    await self.broadcast("candle", {"symbol": sym, **msg}, mode="*")
                except (json.JSONDecodeError, Exception):
                    pass
        except Exception as e:
            log.warning(f"EventHub ZMQ-market gagal: {e}")

    # ---------- sumber 3: Binance User Data WS (label mode='live') ----------
    async def _binance_userdata(self) -> None:
        """WS Binance user data stream — account/order/position real-time (LIVE only).

        Tahap 4: broadcast ber-label mode='live' (sumber ini HANYA aktif di mode live).
        Dry/test → skip. Forward multi-proses per mode: tiap mode live yang listen akan
        stream terpisah (default: 1 EventHub global, namun kalau dipakai multi-proses
        per mode, masing-masing bot instance mem-broadcast ke SSE via webhook)."""
        from .config import load_settings
        from .settings_store import _env_mode
        cur_mode = _env_mode()
        if cur_mode != "live":
            log.info(f"EventHub: Binance user-data WS skip (mode={cur_mode}, no WS broadcast)")
            return   # Tahap 4: hanya live yang subscribe WS — hemat resource
        try:
            import websockets
        except ImportError:
            log.warning("EventHub: lib 'websockets' belum diinstall — "
                        "pip install websockets (user-data WS skip)")
            return

        from .config import load_settings as _cfg_load
        settings = _cfg_load()
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
                log.info("EventHub: Binance user-data WS connect (mode=live)")
                async with websockets.connect(url, ping_interval=20) as ws:
                    refresh = asyncio.create_task(self._refresh_listenkey(ex))
                    try:
                        async for raw in ws:
                            msg = json.loads(raw)
                            etype = msg.get("e")
                            if etype == "ORDER_TRADE_UPDATE":
                                await self.broadcast("order_update", msg.get("o"), mode="live")
                            elif etype == "ACCOUNT_UPDATE":
                                await self.broadcast("account_update", msg.get("a"), mode="live")
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

    # ---------- sumber 4: SQLite watcher (label mode dari kv active_mode) ----------
    async def _sqlite_watcher(self) -> None:
        """Poll SQLite kv table — broadcast status change. Fallback kalau webhook gagal.

        Tahap 4: broadcast diberi label `mode` (dr active_mode kv) sehingga SSE client
        filter sesuai mode UI aktif."""
        from . import store
        from .settings_store import get_active_mode
        last_status = None
        last_balance = None
        last_mode: str | None = None
        log.info(f"EventHub: SQLite watcher poll={SQLITE_POLL_S}s")
        while self._running:
            try:
                # mode yang sedang dilihat UI (Tahap 4 — broadcast label)
                mode = get_active_mode() or (store.get_kv("active_mode") or {}).get("mode")
                if not mode:
                    mode = "dry"
                cur_mode = mode
                # status per-mode aktif
                key = f"status:{cur_mode}"
                st = store.get_kv(key) or store.get_kv("status")
                if st and st != last_status:
                    last_status = st
                    await self.broadcast("status", st, mode=cur_mode)
                # balance state (forward.py:804 write)
                bal = store.get_kv("balance_state")
                if bal and bal != last_balance:
                    last_balance = bal
                    await self.broadcast("balance", bal, mode=cur_mode)
                # broadcast mode change ke subscriber (konsistensi UI)
                if cur_mode != last_mode:
                    last_mode = cur_mode
                    await self.broadcast("mode", {"mode": cur_mode})
            except Exception as e:
                log.debug(f"EventHub sqlite watcher: {e}")
            await asyncio.sleep(SQLITE_POLL_S)


# singleton global — dibuat saat import, start saat FastAPI lifespan
hub = EventHub()
