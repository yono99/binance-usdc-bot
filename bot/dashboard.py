"""Dashboard web monitoring (FastAPI) — baca jurnal forward-test, sajikan stats.

Terpisah dari bot: ForwardTester menulis logs/trades.jsonl, dashboard membacanya.
  GET /            -> halaman HTML (auto-refresh)
  GET /api/stats   -> JSON statistik berjalan
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from dataclasses import asdict

import csv as csvmod
import io

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .logger import log
from .settings_store import (PRESETS, RuntimeSettings, get_active_mode, load_settings,
                             save_settings, set_active_mode)
from . import store
from .eventhub import hub, KEEPALIVE_S

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "logs" / "trades.jsonl"
CLOSE_REQ = ROOT / "logs" / "close_requests.json"


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def compute_stats(path: Path | None = None, start_equity: float | None = None,
                  mode: str | None = None) -> dict:
    events = read_events(path) if path else store.all_events()
    opens = {}
    closes = []
    for e in events:
        if path is None and mode and e.get("mode", "dry") != mode:  # store gabung semua mode
            continue
        ev = e.get("event")
        if ev == "forward_open":
            opens[e["symbol"]] = e
        elif ev == "forward_close":
            opens.pop(e["symbol"], None)
            closes.append(e)

    rs = [float(e.get("r", 0)) for e in closes]
    n = len(rs)
    wins = [r for r in rs if r > 0]
    liquidations = sum(1 for e in closes if e.get("reason") == "liq")
    gross_w = sum(wins)
    gross_l = abs(sum(r for r in rs if r <= 0))

    # start_equity default = ekuitas pada close PERTAMA (return sejak trade pertama tercatat),
    # BUKAN 1000 hardcode yang bikin return_pct palsu (mis. -98.94%). Caller boleh override.
    if start_equity is None:
        start_equity = float(closes[0].get("equity", 1000.0)) if closes else 1000.0
    equity_curve = [start_equity] + [float(e.get("equity", start_equity)) for e in closes]
    # indeks titik likuidasi pada equity_curve (close ke-i -> titik i+1)
    liq_points = [i + 1 for i, e in enumerate(closes) if e.get("reason") == "liq"]

    per: dict[str, list[float]] = {}
    for e in closes:
        per.setdefault(e["symbol"], []).append(float(e.get("r", 0)))
    per_symbol = [
        {"symbol": s, "trades": len(v),
         "win_rate": round(sum(1 for r in v if r > 0) / len(v) * 100, 1),
         "sum_r": round(sum(v), 3)}
        for s, v in sorted(per.items())
    ]

    recent = [
        {"ts": e.get("ts"), "symbol": e.get("symbol"), "reason": e.get("reason"),
         "r": round(float(e.get("r", 0)), 3), "equity": round(float(e.get("equity", 0)), 2)}
        for e in closes[-25:][::-1]
    ]

    open_positions = [
        {"symbol": s, "side": o.get("side"), "entry": o.get("entry"),
         "sl": o.get("sl"), "tp": o.get("tp")}
        for s, o in opens.items()
    ]

    return {
        "trades": n,
        "liquidations": liquidations,
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "expectancy_r": round(sum(rs) / n, 4) if n else 0.0,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else (None if gross_w == 0 else float("inf")),
        "total_r": round(sum(rs), 3),
        "equity": round(equity_curve[-1], 2),
        "return_pct": round((equity_curve[-1] / start_equity - 1) * 100, 2) if start_equity else 0.0,
        "equity_curve": equity_curve,
        "liq_points": liq_points,
        "open_positions": open_positions,
        "per_symbol": per_symbol,
        "recent": recent,
    }


def _ui_mode() -> str | None:
    # mode yang sedang DILIHAT UI (pilihan /api/mode), fallback status bot lama
    return get_active_mode() or (store.get_kv("status") or {}).get("mode")


def build_trades(events: list[dict], mode: str | None = None) -> list[dict]:
    """Rekonstruksi trade lengkap: pasangkan forward_open dengan forward_close (per simbol)."""
    open_map: dict = {}
    trades = []
    for e in events:
        if mode and e.get("mode", "dry") != mode:        # legacy tanpa mode = paper (dry)
            continue
        ev = e.get("event")
        if ev == "forward_open":
            open_map[e["symbol"]] = e
        elif ev == "forward_close":
            o = open_map.pop(e["symbol"], {})
            trades.append({
                "id": e.get("id"), "symbol": e.get("symbol"), "side": o.get("side"),
                "entry": o.get("entry"), "exit": e.get("exit"),
                "sl": o.get("sl"), "tp": o.get("tp"), "liq": o.get("liq"),
                "lev": o.get("lev"), "bet": o.get("bet"),
                "r": e.get("r"), "pnl_usd": e.get("pnl_usd"),
                "reason": e.get("reason"), "equity": e.get("equity"),
                "open_ts": o.get("ts"), "close_ts": e.get("ts"),
            })
    return trades


def filter_trades(trades: list[dict], symbol=None, reason=None, dfrom=None, dto=None) -> list[dict]:
    out = []
    for t in trades:
        if symbol and symbol.lower() not in (t["symbol"] or "").lower():
            continue
        if reason and t["reason"] != reason:
            continue
        d = (t["close_ts"] or "")[:10]
        if dfrom and d < dfrom:
            continue
        if dto and d > dto:
            continue
        out.append(t)
    return out


_TRADE_COLS = ["close_ts", "symbol", "side", "reason", "r", "pnl_usd", "entry", "exit",
               "sl", "tp", "liq", "lev", "bet", "equity", "open_ts"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: jalankan EventHub (ZMQ + WS + SQLite watcher) + candle closer
    (Tahap 6c). Shutdown: hentikan bersih."""
    hub.start()
    _candle_job = asyncio.create_task(_candle_close_watcher(), name="sse-candle-watcher")
    try:
        yield
    finally:
        _candle_job.cancel()
        try:
            await _candle_job
        except (asyncio.CancelledError, Exception):
            pass
        await hub.stop()


# ---------- Tahap 6c (plan-sess): candle close watcher ----------
# Deteksi close candle tf tertentu (1h/1d/1w/1M) → broadcast SSE 'candle' dgn
# {symbol, tf, bar}. Subscribe di frontend PriceChart untuk update real-time
# tanpa polling REST tiap tick. Untuk tf intraday kecil (1m/5m/15m) tidak ada
# job (tetap pakai polling 30s di PriceChart—atau restart polling lebih responsif).
_CANDLE_WATCH_TFS = ("1h", "1d", "1w", "1M")
_CANDLE_WATCH_INTERVAL_S = float(os.getenv("SSE_CANDLE_WATCH_TICK", "5"))


async def _candle_close_watcher() -> None:
    """Periodik: periksa apakah bar terbaru tf high-timeframe (1h/1d/1w/1M) sudah
    ganti (close). Kalau ya → broadcast event 'candle'={symbol, tf, bar, emas, rsi}.
    Per simbol yg punya coverage di market.db. EMA/RSI dihitung server-side supaya
    frontend SSE update indikator tanpa perlu fetch REST ulang."""
    from . import chartstore
    from . import indicators as ind
    from .settings_store import load_settings
    if _CANDLE_WATCH_INTERVAL_S <= 0:
        return
    load_settings()                       # warm up runtime (side effect)
    from .config import load_settings as _load_cfg
    sig = _load_cfg().raw["signals"]
    ema_fast_p, ema_mid_p, ema_slow_p, rsi_p = (
        sig["ema_fast"], sig["ema_mid"], sig["ema_slow"], sig["rsi_period"]
    )
    while True:
        try:
            cov = chartstore.coverage()
            wanted = [c for c in cov if c["tf"] in _CANDLE_WATCH_TFS]
            for c in wanted:
                sym, tf = c["symbol"], c["tf"]
                try:
                    df = chartstore.load(sym, tf, limit=200)
                except Exception:
                    continue
                if df.empty or len(df) < max(ema_slow_p, rsi_p) + 5:
                    continue
                close = df["close"]
                ema_fast = ind.ema(close, ema_fast_p)
                ema_mid = ind.ema(close, ema_mid_p)
                ema_slow = ind.ema(close, ema_slow_p)
                rsi = ind.rsi(close, rsi_p)
                last_idx = -1
                bar_ts = int(df.index[last_idx].timestamp() * 1000)
                bar = {
                    "ts": bar_ts,
                    "open": float(df["open"].iloc[last_idx]),
                    "high": float(df["high"].iloc[last_idx]),
                    "low": float(df["low"].iloc[last_idx]),
                    "close": float(df["close"].iloc[last_idx]),
                    "volume": float(df["volume"].iloc[last_idx]),
                }
                emas = {
                    "fast": round(float(ema_fast.iloc[last_idx]), 6) if not pd.isna(ema_fast.iloc[last_idx]) else None,
                    "mid": round(float(ema_mid.iloc[last_idx]), 6) if not pd.isna(ema_mid.iloc[last_idx]) else None,
                    "slow": round(float(ema_slow.iloc[last_idx]), 6) if not pd.isna(ema_slow.iloc[last_idx]) else None,
                }
                rsi_val = round(float(rsi.iloc[last_idx]), 2) if not pd.isna(rsi.iloc[last_idx]) else None
                await hub.broadcast("candle",
                                     {"symbol": sym, "tf": tf, "bar": bar, "emas": emas, "rsi": rsi_val},
                                     mode="*")
        except Exception as e:
            log.debug(f"candle close watcher: {e}")
        await asyncio.sleep(_CANDLE_WATCH_INTERVAL_S)


app = FastAPI(title="Bot Monitor", lifespan=lifespan)


@app.get("/api/trades")
def api_trades(symbol: str = None, reason: str = None, dfrom: str = None,
               dto: str = None, page: int = 1, page_size: int = 5) -> JSONResponse:
    """Riwayat trade dengan pagination server-side.
    - Urutan: NEWEST first (DESC by close_ts/id).
    - page (1-indexed), page_size ∈ {5, 10, 20, 30, 100}, default 5.
    Response: {total, page, max_page, page_size, total_pages, trades}.
    """
    allowed_page_sizes = {5, 10, 20, 30, 100}
    page_size = page_size if page_size in allowed_page_sizes else 5
    trades = filter_trades(build_trades(store.all_events(), _ui_mode()), symbol, reason, dfrom, dto)
    total = len(trades)
    page = max(1, page)
    desc = trades[::-1]  # newest first, BARU slice
    max_page = max(1, (total + page_size - 1) // page_size)
    page = min(page, max_page)
    start = (page - 1) * page_size
    end = start + page_size
    paginated = desc[start:end]
    return JSONResponse({
        "total": total,
        "page": page,
        "max_page": max(1, (total + page_size - 1) // page_size),
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "trades": paginated
    })


@app.get("/api/trades.csv")
def api_trades_csv(symbol: str = None, reason: str = None, dfrom: str = None,
                   dto: str = None) -> PlainTextResponse:
    trades = filter_trades(build_trades(store.all_events(), _ui_mode()), symbol, reason, dfrom, dto)
    buf = io.StringIO()
    w = csvmod.writer(buf)
    w.writerow(_TRADE_COLS)
    for t in trades:
        w.writerow([t.get(c) for c in _TRADE_COLS])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=trades.csv"})


@app.get("/api/stats")
def api_stats() -> JSONResponse:
    # _json_safe: profit_factor bisa inf (ada win, belum ada loss — PASTI terjadi
    # di awal riwayat live) → tanpa sanitasi endpoint ini 500 tepat saat dibutuhkan.
    return JSONResponse(_json_safe(compute_stats(mode=_ui_mode())))


@app.get("/api/settings")
def api_get_settings(mode: str = None) -> JSONResponse:
    s = load_settings(mode)        # ?mode=live → preview setting mode itu; default = mode aktif
    d = asdict(s)
    d["techniques"] = list(PRESETS)
    d["timeframe"] = s.timeframe()
    d["liq_pct"] = round(s.liquidation_frac() * 100, 3)
    return JSONResponse(d)


@app.get("/api/status")
def api_bot_status(mode: str = None) -> JSONResponse:
    """Status bot per-mode ('status:<mode>'). Tanpa ?mode= → mode aktif UI.
    NO FALLBACK to old 'status' key for live mode (old format has balance_usd).
    For dry/test: convert legacy balance_usd to balance_usdt/balance_usdc if needed."""
    from .settings_store import _env_mode
    m = mode if mode in ("dry", "test", "live") else (get_active_mode() or _env_mode())
    st = store.get_kv(f"status:{m}") or {}
    if m == "live":
return JSONResponse(st)


@app.get("/api/setup-status")
def api_setup_status() -> JSONResponse:
    """Return v8 signal engine setup status: which setups are ACTIVE vs DISABLED."""
    # v8 = Pure Trend Following only
    active setups
    setups = {
        "trend_continuation": {
            "status": "ACTIVE",
            "description": "Pullback complete + momentum resumes (EMA9/21/50 align + ADX + RSI<35/>65 + MACD 2-bar + volume+retest)",
            "engine": "signals_v8.py",
            "risk": {"sl_atr": 1.75, "tp_atr": 2.6, "rr": 1.49}
        },
        "trend_pullback": {
            "status": "DISABLED",
            "reason": "Removed — expectancy -1.25R",
            "engine": "signals_v8.py (killed)"
        },
        "range_fade": {
            "status": "DISABLED",
            "reason": "Removed — range trading disabled (adx_range=999)",
            "engine": "signals_v8.py (killed)"
        },
        "scalp_range": {
            "status": "DISABLED",
            "reason": "Removed — range scalping disabled",
            "engine": "signals_v8.py (killed)"
        },
        "breakout_continuation": {
            "status": "DISABLED",
            "reason": "Removed",
            "engine": "signals_v8.py (killed)"
        }
    }
    return JSONResponse({
        "engine": "signals_v8.py (Pure Trend Following)",
        "active_count": sum(1 for s in setups.values() if s.get("status") == "ACTIVE"),
        "disabled_count": sum(1 for s in setups.values() if s.get("status") == "DISABLED"),
        "setups": setups
    })
    # dry/test: fallback to legacy 'status' key, but convert legacy format
    legacy = store.get_kv("status") or {}
    if legacy and not st:
        st = legacy
    # Convert legacy balance_usd → balance_usdt/balance_usdc (50/50 split)
    if st and "balance_usd" in st and ("balance_usdt" not in st or "balance_usdc" not in st):
        legacy_bal = float(st.get("balance_usd", 0.0))
        if legacy_bal > 0:
            st["balance_usdt"] = round(legacy_bal / 2.0, 2)
            st["balance_usdc"] = round(legacy_bal / 2.0, 2)
    # Convert legacy day_pnl → day_pnl_usdt/day_pnl_usdc
    if st and "day_pnl" in st and ("day_pnl_usdt" not in st or "day_pnl_usdc" not in st):
        legacy_pnl = float(st.get("day_pnl", 0.0))
        st["day_pnl_usdt"] = round(legacy_pnl / 2.0, 2)
        st["day_pnl_usdc"] = round(legacy_pnl / 2.0, 2)
    return JSONResponse(st)


# ---------- SSE: real-time push ----------
def _sse_snapshot() -> dict:
    """Snapshot awal saat client connect — state lengkap (stats/status/orders).
    Tanpa ini client hanya lihat delta dari titik connect, bukan state sekarang."""
    from .settings_store import _env_mode
    m = get_active_mode() or _env_mode()
    if m == "live":
        status = store.get_kv(f"status:{m}") or {}
    else:
        status = store.get_kv(f"status:{m}") or store.get_kv("status") or {}
    try:
        stats = _json_safe(compute_stats(mode=m))
    except Exception:
        stats = {}
    return {"status": status, "stats": stats, "mode": m}


@app.get("/api/stream")
async def sse_stream(request: Request):
    """SSE multiplex: satu koneksi untuk semua event type.
    Event: snapshot, status, stats, trade, order_update, account_update,
           candle, balance, ping. Keep-alive 25s (bawah proxy idle timeout)."""
    async def gen():
        q = hub.subscribe()
        try:
            # 1) snapshot awal (client perlu state lengkap saat connect)
            yield f"event: snapshot\ndata: {json.dumps(_sse_snapshot(), default=str)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
                    yield f"data: {frame}\n\n"
                except asyncio.TimeoutError:
                    yield f"event: ping\ndata: {{}}\n\n"   # keep-alive
        finally:
            hub.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, transform",
                                      "X-Accel-Buffering": "no",   # disable nginx buffering
                                      "Connection": "keep-alive"})


@app.post("/internal/notify")
async def internal_notify(payload: dict):
    """Webhook dari forward.py setelah set_kv/insert_event.
    Payload: {kind: 'status'|'trade'|'balance', data: {...}}.
    Dipanggil loopback (bot→dashboard) untuk push real-time tanpa poll SQLite."""
    kind = payload.get("kind", "update")
    data = payload.get("data", payload)
    await hub.broadcast(kind, data)
    return {"ok": True}


_acct = {"ts": 0.0, "data": None}


@app.get("/api/account")
def api_account() -> JSONResponse:
    import os
    import time
    if _acct["data"] and time.time() - _acct["ts"] < 30:
        return JSONResponse(_acct["data"])
    from .settings_store import load_settings
    s = load_settings()
    if s.mode == "live" and os.getenv("BINANCE_LIVE_KEY"):
        try:
            from .exchange import Exchange
            b = Exchange(s).balances(0.0)
            # Return full precision (8 decimals) for live mode
            data = {"mode": "live", "api_valid": True,
                    "balance_usdc": b["USDC"], "balance_usdt": b["USDT"],
                    "balance_total": b["USDC"] + b["USDT"],
                    "gemini_enabled": s.gemini_enabled, "gemini_keys": len(s.gemini_keys)}
        except Exception as e:  # boundary
            data = {"mode": "live", "api_valid": False, "error": str(e)[:140],
                    "gemini_enabled": s.gemini_enabled, "gemini_keys": len(s.gemini_keys)}
    else:
        data = {"mode": s.mode, "api_valid": None, "paper": True,
                "gemini_enabled": s.gemini_enabled, "gemini_keys": len(s.gemini_keys)}
    _acct.update(ts=time.time(), data=data)
    return JSONResponse(data)


@app.get("/api/live-balance")
def api_live_balance() -> JSONResponse:
    """Ambil saldo USDT & USDC REAL dari Binance LIVE (mode=live).
    HANYA berfungsi di mode LIVE. Return Decimal string untuk presisi penuh.
    Digunakan frontend untuk auto-fill & disable input manual."""
    from .settings_store import load_settings
    s = load_settings()
    if s.mode != "live":
        return JSONResponse({"valid": False, "error": "Hanya mode LIVE", "mode": s.mode})
    try:
        from .settings_store import fetch_live_balances
        bal = fetch_live_balances()
        return JSONResponse({"valid": True, "balance_usdt": bal["USDT"], "balance_usdc": bal["USDC"],
                             "balance_total": str(Decimal(bal["USDT"]) + Decimal(bal["USDC"])),
                             "mode": "live"})
    except Exception as e:
        return JSONResponse({"valid": False, "error": str(e)[:160], "mode": "live"})


_ex_cache: dict = {"ex": None}
_ohlcv_cache: dict = {}


def _get_ex():
    if _ex_cache["ex"] is None:
        from .settings_store import load_settings
        from .exchange import Exchange
        _ex_cache["ex"] = Exchange(load_settings())
    return _ex_cache["ex"]


_symbols_cache: dict = {"ts": 0.0, "data": None}


@app.get("/api/symbols")
def api_symbols() -> JSONResponse:
    """Daftar pair perpetual aktif yang tersedia (USDC + USDT, tipe COIN) untuk
    pemilih & pencarian di UI. Cache 10 menit. Filter underlyingType=COIN agar
    saham/komoditas tokenisasi (MSTR/XAU/SOXL…) tak muncul — berbeda kelas aset.
    """
    import time
    if _symbols_cache["data"] and time.time() - _symbols_cache["ts"] < 600:
        return JSONResponse(_symbols_cache["data"])
    try:
        m = _get_ex().client.markets
        syms = sorted(
            s for s, v in m.items()
            if v.get("swap")
            and v.get("settle") in ("USDC", "USDT")
            and v.get("active", True)
            and (v.get("info", {}) or {}).get("underlyingType", "COIN") == "COIN"
        )
    except Exception as e:  # boundary
        return JSONResponse({"symbols": [], "error": str(e)[:140]})
    data = {"symbols": syms}
    _symbols_cache.update(ts=time.time(), data=data)
    return JSONResponse(data)


@app.get("/api/ohlcv")
def api_ohlcv(symbol: str, tf: str = "15m", limit: int = 120) -> JSONResponse:
    import time
    ck = (symbol, tf, limit)
    c = _ohlcv_cache.get(ck)
    if c and time.time() - c[0] < 30:
        return JSONResponse(c[1])
    try:
        df = _get_ex().ohlcv(symbol, tf, limit=limit)
        bars = [{"x": int(i.timestamp() * 1000), "o": float(o), "h": float(h),
                 "l": float(low), "c": float(c)}
                for i, o, h, low, c in zip(df.index, df["open"], df["high"], df["low"], df["close"])]
        from . import indicators as ind
        from .settings_store import load_settings
        load_settings()                       # warm up KV/runtime (side effect)
        # settings_store.RuntimeSettings TIDAK punya .raw (kontrak cfg ada di bot.config.Settings).
        # Pakai config.load_settings() yang menjamin akses settings.yaml ["signals"].
        from .config import load_settings as _load_cfg
        sig = _load_cfg().raw["signals"]
        close = df["close"]
        rnd = lambda s: [round(float(x), 6) for x in s]
        data = {"symbol": symbol, "tf": tf, "bars": bars,
                "ema_fast": rnd(ind.ema(close, sig["ema_fast"])),
                "ema_mid": rnd(ind.ema(close, sig["ema_mid"])),
                "ema_slow": rnd(ind.ema(close, sig["ema_slow"])),
                "rsi": [round(float(x), 2) for x in ind.rsi(close, sig["rsi_period"])],
                "periods": {"fast": sig["ema_fast"], "mid": sig["ema_mid"],
                            "slow": sig["ema_slow"], "rsi": sig["rsi_period"]}}
        _ohlcv_cache[ck] = (time.time(), data)
        return JSONResponse(data)
    except Exception as e:  # boundary
        return JSONResponse({"symbol": symbol, "error": str(e)[:140], "bars": []})


@app.get("/api/h28")
def api_h28() -> JSONResponse:
    """MESIN H28 — STATUS PREVIEW (paper-only). Terlihat di semua mode; TIDAK
    men-trade uang sampai LOLOS_TAHAP_1 (pra-registrasi). Progres + t-test."""
    try:
        from . import h28eval, h28live
        status = h28eval.preview_status()
        status["mikro_live_toggle"] = {
            "enabled": h28live.is_enabled(),
            "note": ("Toggle ini HANYA start/stop evaluasi basket mikro-live. "
                     "Uang nyata vs simulasi ditentukan flag CLI --live saat daemon "
                     "h28_live.py dinyalakan, BUKAN oleh toggle ini."),
        }
        return JSONResponse(status)
    except Exception as e:  # boundary
        return JSONResponse({"error": str(e)[:140]})


@app.post("/api/h28/toggle")
def api_h28_toggle(payload: dict) -> JSONResponse:
    """Nyalakan/matikan EVALUASI basket mikro-live H28 (Opsi B). Tidak pernah
    menyalakan uang nyata sendirian — itu tetap butuh --live di CLI daemon."""
    try:
        from . import h28live
        h28live.set_enabled(bool(payload.get("enabled", False)))
        return JSONResponse({"ok": True, "enabled": h28live.is_enabled()})
    except Exception as e:  # boundary
        return JSONResponse({"ok": False, "error": str(e)[:140]})


@app.get("/api/candles")
def api_candles(symbol: str, tf: str = "15m", limit: int = 500) -> JSONResponse:
    """Candle dari SQLITE STORE (data/market.db) — sumber chart persisten, tanpa
    memukul exchange. Isi/refresh via `python chart_ingest.py`.

    Tahap 6 (plan-sess): whitelist timeframe (5m/15m/30m/1h/2h/4h/1d/1w/1M) — '1w' & '1M'
    ditambahkan untuk chart makro. Limit max 5000 (default 500 untuk hemat render)."""
    ALLOWED_TF = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h",
                  "1d", "3d", "1w", "1M"}
    if tf not in ALLOWED_TF:
        return JSONResponse({"symbol": symbol, "tf": tf,
                              "error": f"tf tak dikenal: {tf}", "candles": []})
    try:
        from . import chartstore
        df = chartstore.load(symbol, tf, limit=min(int(limit), 5000))
        candles = [[int(i.timestamp() * 1000), float(o), float(h), float(lo), float(c), float(v)]
                   for i, o, h, lo, c, v in zip(df.index, df["open"], df["high"],
                                                df["low"], df["close"], df["volume"])]
        return JSONResponse({"symbol": symbol, "tf": tf, "n": len(candles), "candles": candles})
    except Exception as e:  # boundary
        return JSONResponse({"symbol": symbol, "error": str(e)[:140], "candles": []})


@app.get("/api/candles/coverage")
def api_candles_coverage() -> JSONResponse:
    """Ringkasan isi chart store per (symbol, tf)."""
    try:
        from . import chartstore
        return JSONResponse({"coverage": chartstore.coverage()})
    except Exception as e:  # boundary
        return JSONResponse({"error": str(e)[:140], "coverage": []})


@app.post("/api/validate-key")
def api_validate_key(payload: dict) -> JSONResponse:
    """Validasi key/secret (transien — TIDAK disimpan/di-log). Kosong = pakai .env live."""
    import os
    key = (payload.get("key") or os.getenv("BINANCE_LIVE_KEY", "")).strip()
    secret = (payload.get("secret") or os.getenv("BINANCE_LIVE_SECRET", "")).strip()
    if not key or not secret:
        return JSONResponse({"valid": False, "error": "key/secret kosong (isi form atau .env)"})
    try:
        import ccxt
        c = ccxt.binanceusdm({"apiKey": key, "secret": secret, "enableRateLimit": True,
                              "options": {"defaultType": "future"}})
        total = c.fetch_balance().get("total", {})
        usdc = float(total.get("USDC") or 0)
        usdt = float(total.get("USDT") or 0)
        return JSONResponse({"valid": True, "balance_usdc": usdc,
                             "balance_usdt": usdt,
                             "balance_total": usdc + usdt})
    except Exception as e:  # boundary
        return JSONResponse({"valid": False, "error": str(e)[:160]})


@app.get("/api/live-balance")
def api_live_balance() -> JSONResponse:
    """Ambil saldo LIVE (USDT & USDC) dari Binance dengan presisi Decimal penuh.
    HANYA untuk mode=live. Return string Decimal agar tidak kehilangan presisi."""
    import os
    from .settings_store import fetch_live_balances
    s = load_settings()
    if s.mode != "live":
        return JSONResponse({"error": "Hanya untuk mode=live"}, status_code=400)
    try:
        bal = fetch_live_balances()
        return JSONResponse({"mode": "live", "balance_usdt": bal["USDT"], "balance_usdc": bal["USDC"],
                             "balance_total": str(Decimal(bal["USDT"]) + Decimal(bal["USDC"]))})
    except Exception as e:  # boundary
        return JSONResponse({"mode": "live", "error": str(e)[:140]}, status_code=500)


@app.post("/api/close")
def api_close(payload: dict) -> JSONResponse:
    """Antrekan permintaan tutup posisi; engine memprosesnya siklus berikutnya."""
    sym = payload.get("symbol")
    if not sym:
        return JSONResponse({"ok": False, "error": "symbol kosong"})
    reqs = []
    if CLOSE_REQ.exists():
        try:
            reqs = json.loads(CLOSE_REQ.read_text(encoding="utf-8"))
        except Exception:
            reqs = []
    if sym not in reqs:
        reqs.append(sym)
    CLOSE_REQ.write_text(json.dumps(reqs), encoding="utf-8")
    return JSONResponse({"ok": True, "queued": sym})


@app.post("/api/notify-test")
def api_notify_test() -> JSONResponse:
    from .notify import TelegramNotifier
    ok, err = TelegramNotifier().send_sync("✅ Test notifikasi dari dashboard bot USDC.")
    return JSONResponse({"ok": ok, "error": err})


@app.post("/api/close-all")
def api_close_all() -> JSONResponse:
    CLOSE_REQ.write_text(json.dumps(["*"]), encoding="utf-8")
    return JSONResponse({"ok": True})


_open_orders_cache: dict = {"ts": 0.0, "data": None}


def _normalize_open_order(o: dict) -> dict:
    """Standarkan field order Binance → ringkas utk dashboard. Tahap 3 (plan-sess): tambah
    field `kind` (ENTRY_PENDING/SL/TP/UNKNOWN) memetakan tipe order ke kategori posisi
    supaya UI bisa menampilkan linkage posisi ↔ order reduce-only dengan jelas."""
    try:
        info = o.get("info") or {}
        otype = (o.get("type") or info.get("type") or "").upper()
        # STOP_MARKET / TAKE_PROFIT_MARKET punya stopPrice; LIMIT punya price biasa
        trigger = o.get("stopPrice") or info.get("stopPrice") or o.get("price") or 0.0
        reduce_only = bool(o.get("reduceOnly") or info.get("reduceOnly"))
        # Tahap 3: klasifikasi order — SL/TP/ENTRY_PENDING/UNKNOWN. Entry LIMIT resting
        # belum filled → ENTRY_PENDING (bukan posisi). Order reduce-only yang muncul
        # SL/TP → SL atau TP berdasar tipe. Lainnya UNKNOWN.
        if otype in ("STOP_MARKET", "STOP", "STOP_LIMIT"):
            kind = "SL"
        elif otype in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"):
            kind = "TP"
        elif otype == "LIMIT" and not reduce_only:
            kind = "ENTRY_PENDING"
        elif otype == "MARKET":
            # MARKET order yg masih open biasanya reduce-only exit atau dust — tak tampil sbg entry.
            kind = "EXIT_PENDING" if reduce_only else "UNKNOWN"
        else:
            kind = "UNKNOWN"
        return {
            "symbol": o.get("symbol"),
            "order_id": o.get("id"),
            "type": str(otype).upper(),
            "kind": kind,
            "side": str(o.get("side") or "").upper(),
            "price": float(trigger or 0.0),
            "qty": float(o.get("amount") or o.get("contracts") or 0.0),
            "filled": float(o.get("filled") or 0.0),
            "status": str(o.get("status") or "").lower(),
            "reduce_only": reduce_only,
            "timestamp": o.get("timestamp"),
        }
    except Exception:
        return {"symbol": o.get("symbol"), "order_id": o.get("id"),
                "type": str(o.get("type") or ""), "kind": "UNKNOWN",
                "side": str(o.get("side") or ""),
                "price": 0.0, "qty": 0.0, "filled": 0.0, "status": "",
                "reduce_only": False, "timestamp": o.get("timestamp")}


def _link_orders_to_positions(orders: list[dict]) -> None:
    """Tahap 3 (plan-sess): mutate `orders` in-place menambah metadata `linked_symbol`
    + `linked_kind`. Sumber kebenaran: order reduce-only SL/TP → cocokkan dgn posisi yang
    pair-nya sama. Order ENTRY_PENDING (LIMIT, non-reduce) → link ke posisi masa-depan
    yg belum self.open (dr botstate_<mode>: pending_orders). Untuk dry/paper, hanya
    LIMIT yg ditelusuri engine di self.pending → cocokkan simbol + order_id."""
    sym_to_sl: dict[str, list[dict]] = {}
    sym_to_tp: dict[str, list[dict]] = {}
    for o in orders:
        sym = o.get("symbol")
        if not sym:
            continue
        o["linked_symbol"] = sym
        if o.get("kind") == "SL":
            sym_to_sl.setdefault(sym, []).append(o)
            o["linked_kind"] = "SL"
        elif o.get("kind") == "TP":
            sym_to_tp.setdefault(sym, []).append(o)
            o["linked_kind"] = "TP"
        elif o.get("kind") == "ENTRY_PENDING":
            o["linked_kind"] = "ENTRY_PENDING"
        else:
            o["linked_kind"] = o.get("kind", "UNKNOWN")


@app.get("/api/open-orders")
def api_open_orders() -> JSONResponse:
    """Order aktif nyata dari Binance (LIMIT resting entry + SL/TP reduce-only).
    LIVE: fetch_open_orders langsung + klasifikasi kind (ENTRY_PENDING/SL/TP) untuk linkage
    posisi ↔ order di frontend. DRY: tak ada order exchange → baca pending_orders dari
    status kv (LIMIT resting yg ditelusuri engine). Cache 8 dtk."""
    import os
    import time as _t
    if _open_orders_cache["data"] and _t.time() - _open_orders_cache["ts"] < 8:
        return JSONResponse(_open_orders_cache["data"])
    from .settings_store import load_settings
    s = load_settings()
    if s.is_live and os.environ.get("BINANCE_LIVE_KEY"):
        try:
            raw = _get_ex().open_orders()
            orders = [_normalize_open_order(o) for o in raw]
            _link_orders_to_positions(orders)         # Tahap 3: linkage posisi↔order
            data = {"orders": orders}
        except Exception as e:  # boundary
            data = {"orders": [], "error": str(e)[:140]}
    else:
        # DRY/paper: ambil pending_orders dari status kv (ditulis engine tiap siklus)
        m = get_active_mode() or "dry"
        st = store.get_kv(f"status:{m}") or store.get_kv("status") or {}
        raw_pending = st.get("pending_orders") or []
        # Normalisasi + tandai ENTRY_PENDING utk linkage UI.
        orders = [{
            "symbol": p.get("symbol"),
            "order_id": p.get("order_id"),
            "type": "LIMIT",
            "kind": "ENTRY_PENDING",
            "side": "BUY" if p.get("side") == "buy" else "SELL",
            "price": p.get("price", 0.0),
            "qty": p.get("qty", 0.0),
            "filled": 0.0,
            "status": "open",
            "reduce_only": False,
            "timestamp": p.get("opened_ts"),
        } for p in raw_pending]
        _link_orders_to_positions(orders)
        data = {"orders": orders, "paper": True}
    _open_orders_cache.update(ts=_t.time(), data=data)
    return JSONResponse(data)


@app.post("/api/cancel-order")
def api_cancel_order(payload: dict) -> JSONResponse:
    """Batalkan SATU order di Binance (live). {symbol, order_id}. Dry → tolak."""
    sym = payload.get("symbol")
    oid = payload.get("order_id")
    if not sym or not oid:
        return JSONResponse({"ok": False, "error": "symbol & order_id wajib"})
    import os
    from .settings_store import load_settings
    s = load_settings()
    if not s.is_live or not os.environ.get("BINANCE_LIVE_KEY"):
        return JSONResponse({"ok": False, "error": "cancel-order hanya berlaku di mode live"})
    try:
        _get_ex().client.cancel_order(oid, sym)
        _open_orders_cache["ts"] = 0.0          # invalidate cache agar UI segera segar
        return JSONResponse({"ok": True, "symbol": sym, "order_id": oid})
    except Exception as e:  # boundary
        return JSONResponse({"ok": False, "error": str(e)[:140]})


_positions_cache: dict = {"ts": 0.0, "data": None}


@app.get("/api/positions")
def api_positions() -> JSONResponse:
    """Posisi nyata dari Binance (LIVE) atau dipinjam dari Engine status (DRY).

    Tahap 3 (plan-sess): setiap posisi distempel `margin_type` (di mesin internal). Di
    LIVE metadata margin_type berasal dari Exchange.margin_type(symbol). Posisi CROSS
    lawas ditandai 'CROSS — tutup manual dulu' (lihat Tahap 3 angka 5c)."""
    import os
    import time as _t
    if _positions_cache["data"] and _t.time() - _positions_cache["ts"] < 6:
        return JSONResponse(_positions_cache["data"])
    from .settings_store import load_settings
    s = load_settings()
    if s.is_live and os.environ.get("BINANCE_LIVE_KEY"):
        try:
            ex = _get_ex()
            raw = ex.positions()
            items = []
            for p in raw:
                sym = p.get("symbol")
                items.append({
                    "symbol": sym,
                    "side": "long" if float(p.get("contracts") or 0) > 0 else "short",
                    "entry": float(p.get("entryPrice") or 0),
                    "qty": abs(float(p.get("contracts") or 0)),
                    "liq": float(p.get("liquidationPrice") or 0),
                    "leverage": int(p.get("leverage") or 0),
                    "margin_type": (ex.margin_type(sym) or "?").upper(),
                    "unrealized_pnl": float(p.get("unrealizedPnl") or 0),
                    "margin": float(p.get("initialMargin") or 0),
                })
            # CROSS posisi lawas → flag admin (tak auto-buka posisi baru di simbol tsb
            # sampai user tutup manual atau migrate ke isolated).
            for it in items:
                if it["margin_type"] == "CROSS":
                    it["warning"] = "CROSS — tutup manual dulu (migrate ke ISOLATED)"
            data = {"positions": items, "source": "binance"}
        except Exception as e:  # boundary
            data = {"positions": [], "error": str(e)[:140], "source": "binance"}
    else:
        # DRY: ambil dari status kv (ditulis engine tiap siklus — lengkap dgn metadata).
        m = get_active_mode() or "dry"
        st = store.get_kv(f"status:{m}") or store.get_kv("status") or {}
        syms = st.get("symbols") or []
        items = []
        for s2 in syms:
            p = s2.get("position")
            if not p:
                continue
            items.append({
                "symbol": s2["symbol"],
                "side": p["side"], "entry": p["entry"],
                "qty": p["qty"], "liq": p["liq"],
                "leverage": st.get("leverage"),
                "margin_type": "ISOLATED",   # paper default; engine metadata bisa override
                "unrealized_pnl": p.get("pnl_usd", 0),
                "margin": p.get("bet"),
            })
        data = {"positions": items, "source": "engine", "paper": True}
    _positions_cache.update(ts=_t.time(), data=data)
    return JSONResponse(data)


@app.get("/api/news-log")
def api_news_log(limit: int = 100) -> JSONResponse:
    """Histori keputusan news veto (hanya saat berubah)."""
    return JSONResponse({"log": store.news_log(min(max(1, limit), 100))})


@app.get("/api/screen-log")
def api_screen_log(symbol: str = None, limit: int = 100) -> JSONResponse:
    """Histori screening per pair (sinyal/alasan tak-entry, hanya saat berubah)."""
    return JSONResponse({"log": store.screen_log(symbol, min(max(1, limit), 100))})


@app.get("/api/gemini-usage")
def api_gemini_usage(recent: int = 100) -> JSONResponse:
    """Pemantauan token Gemini: total, hari ini, per-model/key/tujuan, panggilan terakhir."""
    return JSONResponse(store.gemini_usage_stats(min(max(1, recent), 100)))


@app.post("/api/gemini-usage/reset")
def api_gemini_usage_reset(payload: dict = None) -> JSONResponse:
    """Reset pemantauan token Gemini (kosongkan tabel gemini_usage). Hanya counter
    pemantauan — keputusan/kalibrasi/pelajaran TAK terhapus."""
    removed = store.reset_gemini_usage()
    return JSONResponse({"ok": True, "removed": removed})


def _json_safe(o):
    """NaN/inf → None rekursif. Statistik (division) bisa menghasilkan float
    non-JSON pada kombinasi data tertentu → 500 di endpoint (terjadi nyata di
    /api/gemini-trader 2026-07-02). Sanitasi di boundary, sekali utk selamanya."""
    import math
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):          # tuple ikut: json.dumps menserialisasinya
        return [_json_safe(v) for v in o]     # sbg list, inf di dalamnya tetap meledak
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


@app.get("/api/gemini-trader")
def api_gemini_trader(mode: str | None = None) -> JSONResponse:
    """Track record Gemini trader: verdict signifikansi, per-setup, playbook aktif, keputusan.

    Tahap 0 (plan-sess): mode opsional ?mode=live → filter per-mode (default=lintas-
    mode untuk back-compat). share_across_modes untuk opt-in admin via config."""
    from .gemini_trader import track_record
    gcfg = load_settings().__class__   # gunakan cfg: ambil dari settings_store cache path
    # share flag via config helper ringan (lihat bot/config.get gemini.share_lessons_across_modes)
    from .settings_store import load_settings as _load_cfg_settings
    settings = _load_cfg_settings()
    cfg = settings.raw if hasattr(settings, "raw") and settings.raw else {}
    share = bool(cfg.get("gemini", {}).get("share_lessons_across_modes", False))
    return JSONResponse(_json_safe(track_record(mode=mode, share_across_modes=share)))


from .gemini_client import FALLBACK_MODELS as _STATIC_GEMINI_MODELS  # selaras elearning
_gemini_models_cache: dict = {"ts": 0.0, "data": None}


@app.get("/api/gemini-models")
def api_gemini_models() -> JSONResponse:
    """Daftar model Gemini tersedia (dari API key bila ada; fallback daftar statis)."""
    import time
    if _gemini_models_cache["data"] and time.time() - _gemini_models_cache["ts"] < 600:
        return JSONResponse(_gemini_models_cache["data"])
    models: list[str] = []
    try:
        from .settings_store import load_settings as _load
        from google import genai
        s = _load()
        if s.gemini_keys:
            client = genai.Client(api_key=s.gemini_keys[0])
            for m in client.models.list():
                name = (getattr(m, "name", "") or "").replace("models/", "")
                if "gemini" in name.lower() and "embedding" not in name.lower():
                    models.append(name)
    except Exception as e:  # boundary
        log.warning(f"list model Gemini gagal, pakai daftar statis: {e}")
    models = sorted(set(models) or set(_STATIC_GEMINI_MODELS))
    data = {"models": models}
    _gemini_models_cache.update(ts=time.time(), data=data)
    return JSONResponse(data)


@app.delete("/api/trades/{trade_id}")
def api_delete_trade(trade_id: int) -> JSONResponse:
    """Hapus satu trade dari riwayat (event close + open pasangannya)."""
    removed = store.delete_trade(trade_id)
    return JSONResponse({"ok": removed > 0, "removed": removed})


@app.post("/api/trades/clear")
def api_clear_trades() -> JSONResponse:
    """Kosongkan seluruh riwayat trade."""
    return JSONResponse({"ok": True, "removed": store.clear_events()})


@app.post("/api/settings")
def api_set_settings(payload: dict) -> JSONResponse:
    """Simpan pengaturan ke BUCKET MODE yang dikirim form (payload['mode']);
    tanpa 'mode' → bucket mode aktif. NON-DESTRUKTIF: field yang tak dikirim
    dipertahankan dari nilai TERSIMPAN (patch, bukan reset ke default).
    Mode AKTIF tak pernah berubah di sini — ganti mode HANYA via POST /api/mode.
    Insiden 2026-07-02 (tab basi menimpa mode) + bug ON-di-dry-tersimpan-ke-live:
    form kini menulis ke bucket mode yang SEDANG DILIHAT, bukan mode aktif."""
    known = set(RuntimeSettings().__dict__) - {"mode"}
    if isinstance(payload.get("symbols"), str):
        payload["symbols"] = [x.strip() for x in payload["symbols"].split(",") if x.strip()]
    req = payload.get("mode")
    target = req if req in ("", "dry", "test", "live") else None   # None = mode aktif
    s = load_settings(target)                 # basis: nilai TERSIMPAN bucket target
    for k, v in payload.items():
        if k in known:
            setattr(s, k, v)
    s = s.clamp()
    save_settings(s, set_active=False)
    d = asdict(s)
    d["techniques"] = list(PRESETS)
    d["timeframe"] = s.timeframe()
    d["liq_pct"] = round(s.liquidation_frac() * 100, 3)
    return JSONResponse(d)


@app.post("/api/dd-reset")
def api_dd_reset(payload: dict = None) -> JSONResponse:
    """Lepas DRAWDOWN LOCK secara SENGAJA (two-factor: bot mengunci otomatis,
    manusia yang memutuskan lanjut). Bot memproses permintaan ini di siklus
    berikutnya: kunci lepas + puncak saldo di-set ulang ke saldo sekarang."""
    from datetime import datetime, timezone
    mode = (payload or {}).get("mode") or _ui_mode() or "dry"
    store.set_kv(f"dd_reset_{mode}", {"ts": datetime.now(timezone.utc).isoformat()})
    return JSONResponse({"ok": True, "mode": mode,
                         "note": "diproses bot di siklus berikutnya (≤ poll_seconds)"})


@app.get("/api/calibration")
def api_calibration(mode: str = None, n: int = 50, days: int = 14) -> JSONResponse:
    """Rolling Brier score per mode (default: 50 trade terakhir + 14 hari).
    Brier 0.25 = setara koin; makin kecil = confidence makin jujur."""
    from .settings_store import _env_mode
    m = mode if mode in ("dry", "test", "live") else (get_active_mode() or _env_mode())
    return JSONResponse(store.calibration_report(m, last_n=max(1, int(n)),
                                                 days=max(1, int(days))))


@app.get("/api/mtf")
def api_mtf(mode: str = None, sample: int = 100) -> JSONResponse:
    """Report shadow gerbang kesepakatan multi-TF per mode (agree vs disagree).
    verdict INSUFFICIENT sampai total ≥ sample. Tak memblokir apa pun (shadow)."""
    from . import mtf
    from .settings_store import _env_mode
    m = mode if mode in ("dry", "test", "live") else (get_active_mode() or _env_mode())
    return JSONResponse(mtf.report(m, sample=max(1, int(sample))))


@app.get("/api/flat-shadow")
def api_flat_shadow(mode: str = None) -> JSONResponse:
    """Report shadow keputusan FLAT Gemini: miss-rate (gerakan tradeable yang
    terlewat) keseluruhan / per-regime / per-conviction + verdict pra-registrasi.
    Tak memblokir apa pun (shadow)."""
    from . import flat_shadow
    from .settings_store import load_settings
    from .settings_store import _env_mode
    m = mode if mode in ("dry", "test", "live") else (get_active_mode() or _env_mode())
    return JSONResponse(_json_safe(flat_shadow.report(m, load_settings().raw)))


@app.get("/api/mode")
def api_get_mode() -> JSONResponse:
    """Mode trading AKTIF (dry/test/live) — satu sumber kebenaran dibaca bot &
    dashboard. Diubah HANYA lewat POST /api/mode (tindakan sengaja, terpisah
    dari /api/settings supaya form basi tak bisa menimpanya diam-diam)."""
    return JSONResponse({"mode": get_active_mode() or "(ikut .env)"})


@app.post("/api/mode")
def api_set_mode(payload: dict) -> JSONResponse:
    """Satu-satunya jalur resmi ganti mode trading aktif. mode="" = ikut .env."""
    mode = str(payload.get("mode", "")).strip().lower()
    if mode not in ("dry", "test", "live", ""):
        return JSONResponse({"ok": False, "error": "mode harus salah satu: dry|test|live"},
                            status_code=400)
    set_active_mode(mode)
    return JSONResponse({"ok": True, "mode": mode or "(ikut .env)"})


PAGE = """<!doctype html>
<html lang="id"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bot Monitor — Forward Test</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3"></script>
<script src="https://cdn.jsdelivr.net/npm/luxon@3"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-chart-financial@0.2.1"></script>
<style>
  :root{--bg:#0b1220;--card:#131c2e;--bd:#243049;--fg:#e2e8f0;--mut:#8aa0c0;
        --green:#22c55e;--red:#ef4444;--accent:#6366f1}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,sans-serif}
  header{padding:18px 24px;border-bottom:1px solid var(--bd);display:flex;
         justify-content:space-between;align-items:center}
  h1{font-size:18px;margin:0}.sub{color:var(--mut);font-size:12px}
  .wrap{padding:24px;max-width:1100px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px}
  .card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px}
  .card .lbl{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
  .card .val{font-size:26px;font-weight:700;margin-top:6px}
  .pos{color:var(--green)}.neg{color:var(--red)}
  .panel{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;margin-top:18px}
  .panel h2{font-size:14px;margin:0 0 12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:right;padding:8px 10px;border-bottom:1px solid var(--bd)}
  th:first-child,td:first-child{text-align:left}
  th{color:var(--mut);font-weight:600;font-size:12px}
  .empty{color:var(--mut);padding:18px;text-align:center}
  tr.liqrow td{background:rgba(239,68,68,.18);color:#fecaca;font-weight:700}
  tr.liqrow td:first-child{box-shadow:inset 3px 0 0 var(--red)}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:12px}
  label{display:flex;flex-direction:column;gap:4px;color:var(--mut);font-size:12px}
  input,select{background:#0b1220;border:1px solid var(--bd);color:var(--fg);border-radius:8px;padding:8px;font-size:14px}
  button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:9px 16px;font-weight:600;cursor:pointer}
  button:hover{opacity:.9}
  button.del{background:transparent;color:#fca5a5;padding:2px 7px;font-size:13px;border-radius:6px}
  button.del:hover{background:rgba(239,68,68,.15)}
  .danger{background:rgba(239,68,68,.12);border:1px solid var(--red);color:#fca5a5;padding:10px 12px;border-radius:8px;margin-bottom:12px;font-size:13px}
  .ok{background:rgba(34,197,94,.1);border:1px solid var(--green);color:#86efac;padding:10px 12px;border-radius:8px;margin-bottom:12px;font-size:13px}
  .line{font-size:14px;line-height:1.9}.line b{color:var(--fg)}
  .btnsm{background:var(--red);padding:4px 10px;font-size:12px;border-radius:6px}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:6px;animation:p 2s infinite}
  @keyframes p{50%{opacity:.3}}
</style></head>
<body>
<header><div><h1>Bot Monitor</h1><div class="sub">Forward-test (paper) · data live</div></div>
  <div class="sub"><span class="dot"></span><span id="upd">memuat…</span></div></header>
<div class="wrap">
  <div class="panel" id="ctl">
    <h2>Kontrol Bot (paper)</h2>
    <div id="warn"></div>
    <div class="grid">
      <label>Status<select id="enabled"><option value="false">OFF</option><option value="true">ON (buka posisi)</option></select></label>
      <label>Teknik<select id="technique"></select></label>
      <label>Pair (pisah koma)<input id="symbols" placeholder="BTC/USDC:USDC,ETH/USDC:USDC"></label>
      <label>Leverage (x)<input id="leverage" type="number" min="1" max="125"></label>
      <label>Bet / margin (USD)<input id="bet_usd" type="number" min="0.1" step="0.1"></label>
      <label>Saldo USDT<input id="balance_usdt" type="number" min="0" step="0.1"></label>
      <label>Saldo USDC<input id="balance_usdc" type="number" min="0" step="0.1"></label>
      <label>Target profit % (0=ATR)<input id="target_profit_pct" type="number" min="0" step="0.1"></label>
      <label>Max posisi terbuka<input id="max_open_positions" type="number" min="1" max="20" step="1"></label>
      <label>Stop-loss harian % (0=off)<input id="daily_max_loss_pct" type="number" min="0" max="100" step="0.1"></label>
      <label>Max trade harian (0=off)<input id="daily_max_trades" type="number" min="0" max="1000" step="1"></label>
      <label>Interval screening (dtk)<input id="poll_seconds" type="number" min="5" max="3600" step="1"></label>
      <label>Timeframe (otomatis)<input id="tf" disabled></label>
    </div>
    <details style="margin-top:14px"><summary style="cursor:pointer;color:#9aa0a6"><b>Confidence Gate (Gemini sizing)</b> &mdash; klik untuk buka</summary>
      <div class="grid" style="margin-top:10px">
        <label>Confidence min (0-1)<input id="conf_min" type="number" min="0" max="1" step="0.01"></label>
        <label>Confidence full (0-1)<input id="conf_full" type="number" min="0" max="1" step="0.01"></label>
        <label>Reduced size mult (0-1)<input id="conf_reduced_mult" type="number" min="0" max="1" step="0.01"></label>
      </div>
      <div class="sub" style="margin-top:6px">
        Tier gerbang SIZE saat pakai Gemini trader: <code><conf_min</code> = ABSTAIN,
        <code>&ge;conf_full</code> = 1.0&times;bet, di antaranya = <code>conf_reduced_mult</code>&times;bet.<br>
        Bypass (full size selalu): set <code>conf_min=0</code>, <code>conf_full=1</code>, <code>conf_reduced_mult=1</code>.
      </div>
    </details>
    <button id="save" style="margin-top:14px">Simpan pengaturan</button>
    <span id="saved" class="sub"></span>
  </div>
  <div class="panel"><h2>Akun / API</h2>
    <div id="acct" class="line"></div>
    <div class="grid" style="margin-top:10px">
      <label>API Key (validasi)<input id="vkey" placeholder="kosong = pakai .env live"></label>
      <label>API Secret<input id="vsecret" type="password" placeholder="kosong = pakai .env live"></label>
    </div>
    <button id="vbtn">Validasi API Key</button> <span id="vres" class="sub"></span>
    <div style="margin-top:8px"><button id="tgbtn">Test Telegram</button> <span id="tgres" class="sub"></span></div>
  </div>
  <div class="panel"><h2>Status Bot</h2><div id="botstatus" class="line"></div></div>
  <div class="panel"><h2>Setup Status (v8 Engine)</h2><div id="setup-status"></div></div>
  <div class="panel"><h2>Aktivitas per Pair — screening & sinyal
    <button class="btnsm" style="float:right" onclick="closeAll()">Close All</button></h2>
    <div id="pairs"></div></div>
  <div class="panel"><h2>Chart Harga per Pair</h2>
    <div style="margin-bottom:10px;display:flex;gap:8px">
      <select id="chartsym"></select>
      <select id="charttf">
        <option>5m</option><option selected>15m</option><option>1h</option><option>4h</option>
      </select>
    </div>
    <canvas id="px" height="90"></canvas>
    <div class="sub" id="emacap" style="margin-top:6px"></div>
    <canvas id="rsi" height="34" style="margin-top:10px"></canvas></div>
  <div class="cards" id="cards"></div>
  <div class="panel"><h2>Kurva Equity</h2><canvas id="eq" height="90"></canvas></div>
  <div class="panel"><h2>Posisi Terbuka</h2><div id="open"></div></div>
  <div class="panel"><h2>Open Orders</h2><div id="open-orders"></div></div>
  <div class="panel"><h2>
  <div class="panel"><h2>Per Simbol</h2><div id="sym"></div></div>
  <div class="panel"><h2>Trade Terakhir</h2><div id="recent"></div></div>
  <div class="panel"><h2>Riwayat Trade
    <a id="fcsv" href="/api/trades.csv" style="float:right;font-size:13px">⬇ Export CSV</a></h2>
    <div class="grid" style="margin-bottom:12px">
      <label>Pair<input id="fsym" placeholder="mis. BTC"></label>
      <label>Reason<select id="freason"><option value="">semua</option><option>tp</option><option>sl</option><option>liq</option><option>manual</option><option>eod</option></select></label>
      <label>Dari<input id="ffrom" type="date"></label>
      <label>Sampai<input id="fto" type="date"></label>
    </div>
    <button id="fbtn">Filter</button> <button id="clrhist" class="danger">Hapus semua</button> <span id="tcount" class="sub"></span>
    <div id="thist" style="margin-top:10px"></div>
    <div id="thist-pagination" style="margin-top:10px"></div>
  </div>
</div>
<script>
let chart;
const f=(n,d=2)=>Number(n).toFixed(d);
const fbal=(n)=>Number(n).toFixed(8);
const cls=v=>v>0?'pos':(v<0?'neg':'');
function card(lbl,val,c=''){return `<div class="card"><div class="lbl">${lbl}</div><div class="val ${c}">${val}</div></div>`}
function table(cols,rows,rowCls){
  if(!rows.length)return '<div class="empty">Belum ada data — jalankan forwardtest.py</div>';
  const h='<tr>'+cols.map(c=>`<th>${c.t}</th>`).join('')+'</tr>';
  const b=rows.map(r=>`<tr class="${rowCls?rowCls(r):''}">`+cols.map(c=>`<td class="${c.cls?c.cls(r):''}">${c.f?c.f(r):r[c.k]}</td>`).join('')+'</tr>').join('');
  return `<table>${h}${b}</table>`
}
async function load(){
  const s=await (await fetch('/api/stats')).json();
  const pf=s.profit_factor==null?'—':(s.profit_factor>1e6?'∞':f(s.profit_factor));
  document.getElementById('cards').innerHTML=
    card('Trades',s.trades)+
    card('Liquidations',s.liquidations||0,(s.liquidations>0?'neg':''))+
    card('Win Rate',f(s.win_rate,1)+'%')+
    card('Expectancy R',(s.expectancy_r>0?'+':'')+f(s.expectancy_r,3),cls(s.expectancy_r))+
    card('Profit Factor',pf)+
    card('Equity',f(s.equity,2))+
    card('Return',(s.return_pct>0?'+':'')+f(s.return_pct,2)+'%',cls(s.return_pct));
  document.getElementById('open').innerHTML=table(
    [{t:'Simbol',k:'symbol'},{t:'Sisi',k:'side',cls:r=>r.side==='long'?'pos':'neg'},
     {t:'Entry',f:r=>f(r.entry,4)},{t:'SL',f:r=>f(r.sl,4)},{t:'TP',f:r=>f(r.tp,4)}],s.open_positions);
  document.getElementById('sym').innerHTML=table(
    [{t:'Simbol',k:'symbol'},{t:'Trades',k:'trades'},{t:'Win%',f:r=>f(r.win_rate,1)},
     {t:'Σ R',f:r=>(r.sum_r>0?'+':'')+f(r.sum_r,3),cls:r=>cls(r.sum_r)}],s.per_symbol);
  document.getElementById('recent').innerHTML=table(
    [{t:'Waktu',f:r=>(r.ts||'').slice(11,19)},{t:'Simbol',k:'symbol'},
     {t:'Alasan',f:r=>r.reason==='liq'?'⚠ LIKUIDASI':r.reason},
     {t:'R',f:r=>(r.r>0?'+':'')+f(r.r,3),cls:r=>cls(r.r)},{t:'Equity',f:r=>f(r.equity,2)}],
    s.recent, r=>r.reason==='liq'?'liqrow':'');
  const ctx=document.getElementById('eq');
  const data=s.equity_curve, labels=data.map((_,i)=>i);
  const liq=new Set(s.liq_points||[]);
  const radii=data.map((_,i)=>liq.has(i)?5:0);
  const colors=data.map((_,i)=>liq.has(i)?'#ef4444':'#6366f1');
  if(chart){
    chart.data.labels=labels;
    const d=chart.data.datasets[0];
    d.data=data;d.pointRadius=radii;d.pointBackgroundColor=colors;d.pointBorderColor=colors;
    chart.update();
  } else chart=new Chart(ctx,{type:'line',data:{labels,datasets:[{data,borderColor:'#6366f1',
    backgroundColor:'rgba(99,102,241,.15)',fill:true,tension:.25,pointRadius:radii,
    pointBackgroundColor:colors,pointBorderColor:colors,pointHoverRadius:6,borderWidth:2}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>
      (liq.has(c.dataIndex)?'⚠ LIKUIDASI · ':'')+'equity '+Number(c.parsed.y).toFixed(2)}}},
      scales:{x:{display:false},y:{grid:{color:'#243049'},ticks:{color:'#8aa0c0'}}}}});
  document.getElementById('upd').textContent='diperbarui '+new Date().toLocaleTimeString();
}
function riskWarn(lev, liq){
  const w=document.getElementById('warn');
  if(lev>=50) w.innerHTML=`<div class="danger">⚠ Leverage ${lev}x: gerakan melawan ~${liq}% = LIKUIDASI (modal habis). SL berbasis ATR biasanya lebih lebar, jadi posisi kena likuidasi lebih dulu. Ini judi, bukan trading. Backtest strategi ini masih impas.</div>`;
  else if(lev>=20) w.innerHTML=`<div class="danger">⚠ Leverage ${lev}x berisiko tinggi: likuidasi pada gerakan ~${liq}%.</div>`;
  else w.innerHTML='';
}
async function loadSettings(){
  const s=await (await fetch('/api/settings')).json();
  const sel=document.getElementById('technique');
  sel.innerHTML=s.techniques.map(t=>`<option value="${t}">${t}</option>`).join('');
  document.getElementById('enabled').value=String(s.enabled);
  sel.value=s.technique;
  document.getElementById('symbols').value=(s.symbols||[]).join(',');
  document.getElementById('leverage').value=s.leverage;
  document.getElementById('bet_usd').value=s.bet_usd;
  
  // LIVE MODE: saldo diambil OTOMATIS dari Binance (tidak bisa diinput manual)
  const isLive = s.mode === 'live';
  const balUsdtEl = document.getElementById('balance_usdt');
  const balUsdcEl = document.getElementById('balance_usdc');
  
  if (isLive) {
    balUsdtEl.disabled = true;
    balUsdcEl.disabled = true;
    balUsdtEl.title = "LIVE: Saldo diambil otomatis dari Binance API";
    balUsdcEl.title = "LIVE: Saldo diambil otomatis dari Binance API";
    // Auto-fetch live balances
    try {
      const r = await (await fetch('/api/live-balance')).json();
      if (r.valid) {
        balUsdtEl.value = r.balance_usdt;
        balUsdcEl.value = r.balance_usdc;
        // Add live indicator
        balUsdtEl.style.backgroundColor = '#1e3a2e';
        balUsdcEl.style.backgroundColor = '#1e3a2e';
        balUsdtEl.style.color = '#86efac';
        balUsdcEl.style.color = '#86efac';
      }
    } catch (e) {
      console.warn('Gagal fetch live balance:', e);
    }
  } else {
    balUsdtEl.disabled = false;
    balUsdcEl.disabled = false;
    balUsdtEl.value = s.balance_usdt;
    balUsdcEl.value = s.balance_usdc;
    balUsdtEl.style.backgroundColor = '';
    balUsdcEl.style.backgroundColor = '';
    balUsdtEl.style.color = '';
    balUsdcEl.style.color = '';
    balUsdtEl.title = '';
    balUsdcEl.title = '';
  }
  
  document.getElementById('target_profit_pct').value=s.target_profit_pct;
  document.getElementById('max_open_positions').value=s.max_open_positions;
  document.getElementById('daily_max_loss_pct').value=s.daily_max_loss_pct;
  document.getElementById('daily_max_trades').value=s.daily_max_trades;
  document.getElementById('poll_seconds').value=s.poll_seconds;
  document.getElementById('tf').value=s.timeframe;
  if(document.getElementById('conf_min')) document.getElementById('conf_min').value=s.conf_min;
  if(document.getElementById('conf_full')) document.getElementById('conf_full').value=s.conf_full;
  if(document.getElementById('conf_reduced_mult')) document.getElementById('conf_reduced_mult').value=s.conf_reduced_mult;
  riskWarn(s.leverage, s.liq_pct);
  const csel=document.getElementById('chartsym');
  if(!csel.options.length && s.symbols && s.symbols.length)
    csel.innerHTML=s.symbols.map(x=>`<option>${x}</option>`).join('');
}
let pxChart, rsiChart;
async function loadChart(){
  const sym=document.getElementById('chartsym').value; if(!sym)return;
  const tf=document.getElementById('charttf').value||'15m';
  const d=await (await fetch('/api/ohlcv?symbol='+encodeURIComponent(sym)+'&tf='+tf+'&limit=120')).json();
  if(!d.bars||!d.bars.length)return;
  const ds=[{label:sym,type:'candlestick',data:d.bars,
             color:{up:'#22c55e',down:'#ef4444',unchanged:'#94a3b8'},
             borderColor:{up:'#22c55e',down:'#ef4444',unchanged:'#94a3b8'}}];
  const x0=d.bars[0].x, x1=d.bars[d.bars.length-1].x;
  const mkema=(arr,col)=>({type:'line',label:'',data:arr.map((y,i)=>({x:d.bars[i].x,y})),
    borderColor:col,borderWidth:1,pointRadius:0,tension:.2});
  if(d.ema_fast){
    ds.push(mkema(d.ema_fast,'#eab308'),mkema(d.ema_mid,'#3b82f6'),mkema(d.ema_slow,'#a855f7'));
    document.getElementById('emacap').innerHTML=
      `EMA${d.periods.fast} <span style="color:#eab308">━</span>  EMA${d.periods.mid} <span style="color:#3b82f6">━</span>  EMA${d.periods.slow} <span style="color:#a855f7">━</span>`;
  }
  const st=window.lastStatus;
  const sm=st&&st.symbols&&st.symbols.find(x=>x.symbol===sym&&x.in_position);
  if(sm&&sm.position){const p=sm.position;
    const hl=(v,col,dash)=>({type:'line',label:'',data:[{x:x0,y:v},{x:x1,y:v}],
      borderColor:col,borderWidth:1,pointRadius:0,borderDash:dash||[]});
    ds.push(hl(p.entry,'#94a3b8',[4,3]),hl(p.sl,'#ef4444'),hl(p.tp,'#22c55e'),hl(p.liq,'#b91c1c',[2,2]));
  }
  const opts={plugins:{legend:{display:false},tooltip:{enabled:true}},
    scales:{x:{type:'time',grid:{display:false},ticks:{color:'#8aa0c0',maxRotation:0}},
            y:{grid:{color:'#243049'},ticks:{color:'#8aa0c0'}}}};
  if(pxChart){pxChart.data.datasets=ds;pxChart.update();}
  else pxChart=new Chart(document.getElementById('px'),{type:'candlestick',data:{datasets:ds},options:opts});
  if(d.rsi){
    const rd=d.rsi.map((y,i)=>({x:d.bars[i].x,y}));
    const flat=v=>[{x:x0,y:v},{x:x1,y:v}];
    const rds=[{label:'RSI',data:rd,borderColor:'#06b6d4',borderWidth:1.2,pointRadius:0,tension:.2},
      {label:'',data:flat(70),borderColor:'#ef4444',borderWidth:.6,pointRadius:0,borderDash:[3,3]},
      {label:'',data:flat(30),borderColor:'#22c55e',borderWidth:.6,pointRadius:0,borderDash:[3,3]}];
    const ropts={plugins:{legend:{display:false},tooltip:{enabled:false}},
      scales:{x:{type:'time',display:false},y:{min:0,max:100,grid:{color:'#243049'},ticks:{color:'#8aa0c0',stepSize:50}}}};
    if(rsiChart){rsiChart.data.datasets=rds;rsiChart.update();}
    else rsiChart=new Chart(document.getElementById('rsi'),{type:'line',data:{datasets:rds},options:ropts});
  }
}
async function closePos(sym){
  if(!confirm('Tutup paksa posisi '+sym+'? (diproses ≤1 siklus)'))return;
  await fetch('/api/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:sym})});
  loadStatus();
}
async function closeAll(){
  if(!confirm('Tutup SEMUA posisi? (diproses ≤1 siklus)'))return;
  await fetch('/api/close-all',{method:'POST'});
  loadStatus();
}
document.getElementById('tgbtn').addEventListener('click',async()=>{
  const el=document.getElementById('tgres'); el.textContent='mengirim…';
  try{
    const r=await (await fetch('/api/notify-test',{method:'POST'})).json();
    el.innerHTML=r.ok?'<span class="pos">terkirim ✓ (cek Telegram)</span>':`<span class="neg">${r.error||'gagal'}</span>`;
  }catch(e){el.innerHTML='<span class="neg">error koneksi</span>';}
});
document.getElementById('chartsym').addEventListener('change',loadChart);
document.getElementById('charttf').addEventListener('change',loadChart);
document.getElementById('vbtn').addEventListener('click',async()=>{
  const body={key:document.getElementById('vkey').value.trim(),secret:document.getElementById('vsecret').value.trim()};
  const el=document.getElementById('vres'); el.textContent='memvalidasi…';
  try{
    const r=await (await fetch('/api/validate-key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    el.innerHTML=r.valid?`<span class="pos">VALID — saldo $${f(r.balance_usdc,2)}</span>`:`<span class="neg">INVALID: ${r.error||'gagal'}</span>`;
  }catch(e){el.innerHTML='<span class="neg">error koneksi</span>';}
});
document.getElementById('leverage').addEventListener('input',e=>{
  const lev=+e.target.value||1; riskWarn(lev, (Math.max(1/lev-0.005,0.0005)*100).toFixed(3));
});
document.getElementById('save').addEventListener('click',async()=>{
  const body={
    enabled:document.getElementById('enabled').value==='true',
    technique:document.getElementById('technique').value,
    symbols:document.getElementById('symbols').value,
    leverage:+document.getElementById('leverage').value,
    bet_usd:+document.getElementById('bet_usd').value,
    balance_usdt:+document.getElementById('balance_usdt').value,
    balance_usdc:+document.getElementById('balance_usdc').value,
    target_profit_pct:+document.getElementById('target_profit_pct').value,
    max_open_positions:+document.getElementById('max_open_positions').value,
    daily_max_loss_pct:+document.getElementById('daily_max_loss_pct').value,
    daily_max_trades:+document.getElementById('daily_max_trades').value,
    poll_seconds:+document.getElementById('poll_seconds').value,
    conf_min:+document.getElementById('conf_min').value,
    conf_full:+document.getElementById('conf_full').value,
    conf_reduced_mult:+document.getElementById('conf_reduced_mult').value,
  };
  const s=await (await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  window.pendingBalUsdt=s.balance_usdt;
  window.pendingBalUsdc=s.balance_usdc;
  document.getElementById('tf').value=s.timeframe;
  riskWarn(s.leverage, s.liq_pct);
  const el=document.getElementById('saved'); el.textContent=' tersimpan ✓ (bot menerapkan tiap siklus)';
  setTimeout(()=>el.textContent='',4000);
});
async function loadStatus(){
  const a=await (await fetch('/api/account')).json();
  const s=await (await fetch('/api/status')).json();
  window.lastStatus=s;
  // form Saldo = saldo hidup (termasuk PnL); jangan timpa saat user mengetik
  // atau saat masih menunggu bot menerapkan nilai yang baru disimpan (pendingBal*).
  const balUsdtEl=document.getElementById('balance_usdt');
  const balUsdcEl=document.getElementById('balance_usdc');
  if(window.pendingBalUsdt!=null && Math.abs((s.balance_usdt??0)-window.pendingBalUsdt)<1e-9) window.pendingBalUsdt=null;
  if(window.pendingBalUsdc!=null && Math.abs((s.balance_usdc??0)-window.pendingBalUsdc)<1e-9) window.pendingBalUsdc=null;
  if(s.balance_usdt!=null && document.activeElement!==balUsdtEl && window.pendingBalUsdt==null) balUsdtEl.value=s.balance_usdt;
  if(s.balance_usdc!=null && document.activeElement!==balUsdcEl && window.pendingBalUsdc==null) balUsdcEl.value=s.balance_usdc;
  const api=a.api_valid===true?'<span class="pos">VALID</span>':(a.api_valid===false?'<span class="neg">INVALID</span>':'paper (tanpa key)');
  let bal=a.balance_usdc!=null||a.balance_usdt!=null
    ?('USDT $'+fbal(a.balance_usdt)+' · USDC $'+fbal(a.balance_usdc)+(a.balance_total!=null?(' · Total $'+fbal(a.balance_total)):''))
    :('USDT $'+fbal(s.balance_usdt)+' · USDC $'+fbal(s.balance_usdc)+(s.balance_usdt!=null||s.balance_usdc!=null?(' <span class="sub">paper</span>'):'—'));
  document.getElementById('acct').innerHTML=
    `Mode: <b>${a.mode}</b> · API: ${api} · Saldo: <b>${bal}</b> · `+
    `Gemini: ${a.gemini_enabled?('<span class="pos">on</span>, '+a.gemini_keys+' key'):'<span class="sub">off</span>'}`+
    (a.error?`<div class="danger">${a.error}</div>`:'');
  if(!s.ts){
    document.getElementById('botstatus').innerHTML='<div class="empty">Bot belum jalan — `python forwardtest.py --poll 30 --use-store`</div>';
    document.getElementById('pairs').innerHTML='';return;
  }
  const nv=s.news_veto&&s.news_veto.active?`<span class="neg">VETO (${s.news_veto.note})</span>`:'<span class="pos">clear</span>';
  document.getElementById('botstatus').innerHTML=
    `Status: ${s.enabled?'<span class="pos">ON</span>':'<span class="neg">OFF</span>'} · Teknik: <b>${s.technique}</b> · `+
    `TF: ${s.timeframe} · Leverage: <b>${s.leverage}x</b> · Bet: $${f(s.bet_usd,2)} · Saldo: <b>USDT $${fbal(s.balance_usdt)} · USDC $${fbal(s.balance_usdc)}</b> · `+
    `Posisi: ${s.open_count}/${s.max_open} · News: ${nv} · <span class="sub">update ${(s.ts||'').slice(11,19)} UTC</span>`;
  document.getElementById('pairs').innerHTML=table(
    [{t:'Pair',k:'symbol'},
     {t:'Harga',f:r=>r.price!=null?f(r.price,4):'—'},
     {t:'ATR%',f:r=>r.atr_pct!=null?f(r.atr_pct,2):'—'},
     {t:'Sinyal',f:r=>r.signal||'—',cls:r=>r.signal==='LONG'?'pos':(r.signal==='SHORT'?'neg':'')},
     {t:'Posisi (PnL)',f:r=>r.in_position?`${r.position.side.toUpperCase()} ${(r.position.pnl_usd>=0?'+':'')+f(r.position.pnl_usd,2)}`:'—',
      cls:r=>r.in_position?(r.position.pnl_usd>=0?'pos':'neg'):''},
     {t:'Keterangan',f:r=>r.blocked||'—'},
     {t:'Aksi',f:r=>r.in_position?`<button class="btnsm" onclick="closePos('${r.symbol}')">Close</button>`:'—'}],
    s.symbols||[]);

  loadOpenOrders();}
async function loadSetupStatus(){
  try{
    const r=await fetch('/api/setup-status');
    const data=await r.json();
    if(!data.setups) return;
    const rows=[];
    for(const [name,info] of Object.entries(data.setups)){
      const active=info.status==='ACTIVE';
      rows.push({
        setup:name,
        status:active?'<span class="pos">ACTIVE</span>':'<span class="neg">DISABLED</span>',
        reason:info.reason||'—',
        engine:info.engine||'—',
        risk:info.risk?`SL ${info.risk.sl_atr}×ATR / TP ${info.risk.tp_atr}×ATR (RR ${info.risk.rr})`:'—'
      });
    }
    document.getElementById('setup-status').innerHTML=table(
      [{t:'Setup',k:'setup'},{t:'Status',k:'status',cls:r=>r.status.includes('ACTIVE')?'pos':'neg'},
       {t:'Reason',k:'reason'},{t:'Engine',k:'engine'},{t:'Risk',k:'risk'}],
      rows);
  }catch(e){
    document.getElementById('setup-status').innerHTML='<div class="danger">Gagal load setup status</div>';
  }
}
async function loadOpenOrders(){
  try{
    const d=await (await fetch('/api/open-orders')).json();
    const orders=d.orders||[];
    if(!orders.length){
      document.getElementById('open-orders').innerHTML='<div class="empty">Tidak ada order terbuka</div>';
      return;
    }
    document.getElementById('open-orders').innerHTML=table(
      [{t:'Pair',k:'symbol'},
       {t:'Side',f:r=>r.side,cls:r=>r.side==='BUY'?'pos':(r.side==='SELL'?'neg':'')},
       {t:'Type',k:'type'},
       {t:'Kind',k:'kind'},
       {t:'Price',f:r=>f(r.price,4)},
       {t:'Qty',f:r=>f(r.qty,4)},
       {t:'Filled',f:r=>f(r.filled,4)},
       {t:'Status',k:'status'},
       {t:'Reduce',f:r=>r.reduce_only?'YA':'TIDAK'}],
      orders);
}


function tradeQ(){
  const p=new URLSearchParams();
  const s=document.getElementById('fsym').value.trim(); if(s)p.set('symbol',s);
  const r=document.getElementById('freason').value; if(r)p.set('reason',r);
  const a=document.getElementById('ffrom').value; if(a)p.set('dfrom',a);
  const b=document.getElementById('fto').value; if(b)p.set('dto',b);
  return p.toString();
}
async function loadTrades(page = 1, pageSize = 5){
  const q = tradeQ();
  q.set('page', page);
  q.set('page_size', pageSize);
  document.getElementById('fcsv').href = '/api/trades.csv' + (q.toString() ? '?' + q.toString() : '');
  const d = await (await fetch('/api/trades?' + q.toString())).json();
  document.getElementById('tcount').textContent = d.total + ' trade';
  document.getElementById('thist').innerHTML = table(
    [{t:'Close',f:r=>(r.close_ts||'').slice(0,16).replace('T',' ')},
     {t:'Pair',k:'symbol'},
     {t:'Side',f:r=>(r.side||'').toUpperCase(),cls:r=>r.side==='long'?'pos':(r.side==='short'?'neg':'')},
     {t:'Reason',f:r=>r.reason==='liq'?'⚠ LIQ':(r.reason||'—')},
     {t:'R',f:r=>r.r!=null?((r.r>0?'+':'')+f(r.r,3)):'—',cls:r=>cls(r.r||0)},
     {t:'PnL$',f:r=>r.pnl_usd!=null?((r.pnl_usd>=0?'+':'')+f(r.pnl_usd,2)):'—',cls:r=>r.pnl_usd!=null?(r.pnl_usd>=0?'pos':'neg'):''},
     {t:'Entry',f:r=>r.entry!=null?f(r.entry,4):'—'},
     {t:'Exit',f:r=>r.exit!=null?f(r.exit,4):'—'},
     {t:'Equity',f:r=>r.equity!=null?f(r.equity,2):'—'},
     {t:'',f:r=>r.id!=null?`<button class="del" onclick="delTrade(${r.id})" title="Hapus trade ini">✕</button>`:''}],
    d.trades, r=>r.reason==='liq'?'liqrow':'');
  renderPagination(d, 'thist', 'loadTrades');
}


  // Generic pagination renderer
  function renderPagination(data, containerId, loadFn) {
    const totalPages = data.total_pages || 1;
    const page = data.page || 1;
    const pageSize = data.page_size || 5;
    const total = data.total || 0;
    const pageSizeOptions = [5, 10, 20, 30, 100];
    const paginationHtml = `
      <div style="display:flex;justify-content:center;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center;">
        <button class="btnsm" onclick="${loadFn}(${page - 1}, ${pageSize})" ${page <= 1 ? 'disabled' : ''}>← Prev</button>
        <span style="align-self:center;color:#8aa0c0;">Page ${page} / ${totalPages} (${total} total)</span>
        <button class="btnsm" onclick="${loadFn}(${page + 1}, ${pageSize})" ${page >= totalPages ? 'disabled' : ''}>Next →</button>
        <label style="margin-left:16px;color:#8aa0c0;font-size:12px;">Page size:
          <select onchange="${loadFn}(1, parseInt(this.value))" style="margin-left:4px;background:#0b1220;border:1px solid #243049;color:#e2e8f0;border-radius:4px;padding:2px 6px;">
            ${pageSizeOptions.map(s => `<option value="${s}" ${s === pageSize ? 'selected' : ''}>${s}</option>`).join('')}
          </select>
        </label>
      </div>`;
    document.getElementById(containerId + '-pagination').innerHTML = paginationHtml;
  }

async function delTrade(id){
  if(!confirm('Hapus trade ini dari riwayat?'))return;
  await fetch('/api/trades/'+id,{method:'DELETE'});
  loadTrades();load();
}
async function clearTrades(){
  if(!confirm('Hapus SELURUH riwayat trade? Tidak bisa dibatalkan.'))return;
  await fetch('/api/trades/clear',{method:'POST'});
  loadTrades();load();
}
document.getElementById('fbtn').addEventListener('click',loadTrades);
document.getElementById('clrhist').addEventListener('click',clearTrades);
loadSettings();
function refresh(){load();loadStatus();loadSetupStatus();loadChart();loadTrades();}
refresh();setInterval(refresh,10000);
</script></body></html>"""


# ---------- Phase 6: panel Agent (ReAct/lessons/evolution) ----------
# Endpoint JSON + halaman /agent mandiri (tak menyentuh SPA React → panel lama aman).

@app.get("/api/decisions")
def api_decisions(page: int = 1, page_size: int = 5) -> JSONResponse:
    """Keputusan ReactAgent terakhir (alasan, confidence, sumber, outcome R).
    Pagination: page (1-indexed), page_size (allowed: 5, 10, 20, 30, 100, default 5)."""
    from . import decision_log
    allowed_page_sizes = {5, 10, 20, 30, 100}
    page_size = page_size if page_size in allowed_page_sizes else 5
    rows = decision_log.recent(200)
    total = len(rows)
    page = max(1, page)
    start = (page - 1) * page_size
    end = start + page_size
    paginated = rows[start:end]
    return JSONResponse({
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "decisions": paginated
    })


@app.get("/api/lessons")
def api_lessons(page: int = 1, page_size: int = 5) -> JSONResponse:
    """Pelajaran aktif + akurasi (times_correct/triggered) & berapa kali dipicu.
    Pagination: page (1-indexed), page_size (allowed: 5, 10, 20, 30, 100, default 5)."""
    from . import lessons
    allowed_page_sizes = {5, 10, 20, 30, 100}
    page_size = page_size if page_size in allowed_page_sizes else 5
    rows = [l for l in lessons.load_all() if not l.get("retired")]
    rows.sort(key=lambda l: l.get("created_at", ""), reverse=True)
    total = len(rows)
    page = max(1, page)
    start = (page - 1) * page_size
    end = start + page_size
    paginated = rows[start:end]
    return JSONResponse({
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "lessons": paginated
    })


@app.get("/api/agent-health")
def api_agent_health(limit: int = 300) -> JSONResponse:
    """Rasio LLM tersedia vs fallback, dihitung dari sumber keputusan di decision_log."""
    from collections import Counter
    from . import decision_log
    rows = decision_log.recent(min(max(1, limit), 1000))
    total = len(rows)
    by_source = Counter(r.get("source", "?") for r in rows)
    llm = by_source.get("LLM", 0) + by_source.get("LLM_TOOL", 0)   # tool-loop = LLM aktif
    fallbacks = total - llm
    return JSONResponse({
        "total": total, "llm": llm, "fallbacks": fallbacks,
        "fallback_rate": round(fallbacks / total, 3) if total else 0.0,
        "llm_available_rate": round(llm / total, 3) if total else 0.0,
        "by_source": dict(by_source),
    })


@app.get("/api/evolution")
def api_evolution(page: int = 1, page_size: int = 5) -> JSONResponse:
    """Riwayat evolusi threshold (before/after, p-value, applied).
    Pagination: page (1-indexed), page_size (allowed: 5, 10, 20, 30, 100, default 5)."""
    from . import evolve
    allowed_page_sizes = {5, 10, 20, 30, 100}
    page_size = page_size if page_size in allowed_page_sizes else 5
    rows = evolve.recent_events(200)
    total = len(rows)
    page = max(1, page)
    start = (page - 1) * page_size
    end = start + page_size
    paginated = rows[start:end]
    return JSONResponse({
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "events": paginated
    })


@app.get("/api/ab")
def api_ab() -> JSONResponse:
    """A/B: rules-saja vs rules+ReAct (butuh agent.ab_shadow & data shadow)."""
    from . import ab
    return JSONResponse(ab.report())


_AGENT_FLAGS = ("agent_full_auto", "agent_tool_loop", "agent_autonomous", "agent_planner",
                "agent_ab_shadow", "agent_manager_mode", "news_veto")


@app.get("/api/agent-settings")
def api_get_agent_settings(mode: str = None) -> JSONResponse:
    """Status flag agent (per-mode). Toggle dari UI → hot-reload di bot tanpa restart."""
    s = load_settings(mode)
    return JSONResponse({k: getattr(s, k) for k in _AGENT_FLAGS})


@app.post("/api/agent-settings")
def api_set_agent_settings(payload: dict) -> JSONResponse:
    """Patch HANYA flag agent (non-destruktif: setting lain tak tersentuh)."""
    s = load_settings(payload.get("mode"))
    for k in _AGENT_FLAGS:
        if k in payload:
            setattr(s, k, bool(payload[k]))
    save_settings(s)
    return JSONResponse({k: getattr(s, k) for k in _AGENT_FLAGS})


@app.get("/api/plan")
def api_plan() -> JSONResponse:
    """Rencana sesi terakhir (planner): stance/bias/kuota."""
    from . import decision_log
    for row in decision_log.recent(200):
        if row.get("symbol") == "*PLAN*":
            return JSONResponse((row.get("market_state") or {}).get("plan", {}))
    return JSONResponse({})


AGENT_PAGE = """<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Agent — ReAct/Lessons</title>
<style>
 :root{--bg:#0b1220;--card:#131c2e;--bd:#243049;--fg:#e2e8f0;--mut:#8aa0c0;--green:#22c55e;--red:#ef4444;--accent:#6366f1}
 *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:13px/1.5 system-ui,Segoe UI,sans-serif}
 header{padding:16px 22px;border-bottom:1px solid var(--bd);display:flex;justify-content:space-between;align-items:center}
 h1{font-size:17px;margin:0}a{color:var(--accent)}.wrap{padding:20px;max-width:1100px;margin:0 auto;display:grid;gap:18px}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:14px}
 h2{font-size:14px;margin:0 0 10px}.mut{color:var(--mut)}
 table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:6px 8px;border-bottom:1px solid var(--bd);text-align:left;vertical-align:top}
 th{color:var(--mut);font-weight:600}.pos{color:var(--green)}.neg{color:var(--red)}
 .pill{display:inline-block;padding:1px 7px;border-radius:9px;background:#1e293b;font-size:11px}
 .chips span{display:inline-block;margin:2px 6px 2px 0;padding:2px 8px;border-radius:9px;background:#1e293b}
</style></head><body>
<header><h1>🤖 Agent Monitor <span class="mut">— ReAct / Lessons / Evolution</span></h1>
<span class="mut"><a href="/">← dashboard utama</a> · auto-refresh 10s</span></header>
<div class="wrap">
 <div class="card"><h2>Agent Health</h2><div id="health" class="chips mut">memuat…</div></div>
 <div class="card"><h2>Kontrol Agent <span class="mut">(hot-reload, tanpa restart)</span></h2>
   <div id="agentctl" class="chips">memuat…</div>
   <div class="mut" style="margin-top:8px">full_auto = tool_loop + autonomous + planner. LIVE FLAT tetap butuh allow_live_trader.</div></div>
 <div class="card"><h2>A/B — rules vs rules+ReAct</h2><div id="ab" class="mut">memuat…</div></div>
<div class="card"><h2>Keputusan Terakhir</h2><table id="dec"><thead><tr><th>waktu</th><th>simbol</th>
    <th>aksi</th><th>conf</th><th>sumber</th><th>alasan</th><th>outcome</th><th>R</th></tr></thead><tbody></tbody></table>
    <div id="dec-pagination" style="margin-top:10px"></div></div>
<div class="card"><h2>Pelajaran Aktif</h2><table id="les"><thead><tr><th>pelajaran</th><th>regime</th>
    <th>akurasi</th><th>dipicu</th><th>sumber</th></tr></thead><tbody></tbody></table>
    <div id="les-pagination" style="margin-top:10px"></div></div>
  <div class="card"><h2>Evolusi Threshold (OOS)</h2><table id="evo"><thead><tr><th>waktu</th><th>param</th>
    <th>lama→baru</th><th>OOS base→prop</th><th>p</th><th>applied</th></tr></thead><tbody></tbody></table>
    <div id="evo-pagination" style="margin-top:10px"></div></div>
</div>
<script>
const $=s=>document.querySelector(s);
const rcls=v=>v>0?'pos':(v<0?'neg':'');
const esc=s=>(s==null?'':String(s)).replace(/[<>&]/g,c=>({'<':'<','>':'>','&':'&'}[c]));
async function j(u){try{const r=await fetch(u);return await r.json()}catch(e){return null}}
async function load(){
  const h=await j('/api/agent-health');
  if(h){$('#health').innerHTML=`<span>total: ${h.total}</span><span>LLM: ${h.llm}</span>`+
    `<span>fallback: ${h.fallbacks}</span><span>fallback rate: ${(h.fallback_rate*100).toFixed(1)}%</span>`+
    Object.entries(h.by_source||{}).map(([k,v])=>`<span>${esc(k)}: ${v}</span>`).join('');}
  const ab=await j('/api/ab');
  if(ab){const sig=ab.significant?'<span class=pos>YA</span>':'<span class=mut>tidak</span>';
    $('#ab').innerHTML=`<b>verdict:</b> ${esc(ab.verdict)} <span class="mut">(${esc(ab.reason||'')})</span><br>`+
    `rules: exp_R <b>${ab.exp_r_rules??'—'}</b> (n=${ab.n_total??0}) · `+
    `rules+ReAct: exp_R <b>${ab.exp_r_rules_react??'—'}</b> (n=${ab.n_kept??0}) · `+
    `ditolak: exp_R ${ab.exp_r_denied??'—'} (n=${ab.n_denied??0})<br>`+
    `improvement: ${ab.improvement??'—'} · p=${ab.p_value??'—'} · signifikan: ${sig}<br>`+
    `<b>risiko (Jalan A):</b> drawdown rules ${ab.risk_rules?ab.risk_rules.max_drawdown_r:'—'}R → `+
    `rules+ReAct ${ab.risk_react?ab.risk_react.max_drawdown_r:'—'}R · `+
    `kurangi risiko: ${ab.reduces_risk?'<span class=pos>YA</span>':'<span class=mut>tidak</span>'}`;}
  await loadDecisions(1, 5);
  await loadLessons(1, 5);
  await loadEvolution(1, 5);
}

  // Generic pagination renderer
  function renderPagination(data, containerId, loadFn) {
    const totalPages = data.total_pages || 1;
    const page = data.page || 1;
    const pageSize = data.page_size || 5;
    const total = data.total || 0;
    const pageSizeOptions = [5, 10, 20, 30, 100];
    const paginationHtml = `
      <div style="display:flex;justify-content:center;gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center;">
        <button class="btnsm" onclick="${loadFn}(${page - 1}, ${pageSize})" ${page <= 1 ? 'disabled' : ''}>← Prev</button>
        <span style="align-self:center;color:#8aa0c0;">Page ${page} / ${totalPages} (${total} total)</span>
        <button class="btnsm" onclick="${loadFn}(${page + 1}, ${pageSize})" ${page >= totalPages ? 'disabled' : ''}>Next →</button>
        <label style="margin-left:16px;color:#8aa0c0;font-size:12px;">Page size:
          <select onchange="${loadFn}(1, parseInt(this.value))" style="margin-left:4px;background:#0b1220;border:1px solid #243049;color:#e2e8f0;border-radius:4px;padding:2px 6px;">
            ${pageSizeOptions.map(s => `<option value="${s}" ${s === pageSize ? 'selected' : ''}>${s}</option>`).join('')}
          </select>
        </label>
      </div>`;
    $(`#${containerId}-pagination`).innerHTML = paginationHtml;
  }

  // Load decisions with pagination
  async function loadDecisions(page = 1, pageSize = 5) {
    const d = await j(`/api/decisions?page=${page}&page_size=${pageSize}`);
    if(d) {
      $('#dec tbody').innerHTML = (d.decisions||[]).map(x=>`<tr><td class="mut">${esc((x.ts||'').slice(0,19))}</td>`+
        `<td>${esc(x.symbol)}</td><td><span class="pill">${esc(x.action)}</span></td><td>${(x.confidence??0)}</td>`+
        `<td class="mut">${esc(x.source)}</td><td>${esc(x.reasoning)}</td><td>${esc(x.outcome??'')}</td>`+
        `<td class="${rcls(x.outcome_r)}">${x.outcome_r==null?'':x.outcome_r}</td></tr>`).join('')
        ||'<tr><td colspan=8 class=mut>belum ada keputusan</td></tr>';
      renderPagination(d, 'dec', 'loadDecisions');
    }
  }

  // Load lessons with pagination
  async function loadLessons(page = 1, pageSize = 5) {
    const l = await j(`/api/lessons?page=${page}&page_size=${pageSize}`);
    if(l) {
      $('#les tbody').innerHTML = (l.lessons||[]).map(x=>{const t=x.times_triggered||0,c=x.times_correct||0;
        const acc=t?(c/t*100).toFixed(0)+'%':'—';return `<tr><td>${esc(x.lesson)}</td><td class="mut">${esc(x.market_regime)}</td>`+
        `<td>${acc} <span class="mut">(${c}/${t})</span></td><td>${t}</td><td class="mut">${esc(x.source)}</td></tr>`;}).join('')
        ||'<tr><td colspan=5 class=mut>belum ada pelajaran</td></tr>';
      renderPagination(l, 'les', 'loadLessons');
    }
  }

  // Load evolution with pagination
  async function loadEvolution(page = 1, pageSize = 5) {
    const e = await j(`/api/evolution?page=${page}&page_size=${pageSize}`);
    if(e) {
      $('#evo tbody').innerHTML = (e.events||[]).map(x=>`<tr><td class="mut">${esc((x.ts||'').slice(0,19))}</td>`+
        `<td>${esc(x.param)}</td><td>${esc(x.old)} → ${esc(x.new??'—')}</td>`+
        `<td>${esc(x.test_exp_r_baseline??'—')} → ${esc(x.test_exp_r_proposed??'—')}</td>`+
        `<td>${esc(x.p_value??'—')}</td><td>${x.applied?'<span class=pos>YA</span>':'<span class=mut>tidak</span>'}</td></tr>`).join('')
        ||'<tr><td colspan=6 class=mut>belum ada evolusi</td></tr>';
      renderPagination(e, 'evo', 'loadEvolution');
    }
  }
const AGFLAGS=[["agent_manager_mode","Manager-mode"],["agent_full_auto","Full-auto"],
  ["agent_tool_loop","Tool-loop"],["agent_autonomous","Autonomous"],["agent_planner","Planner"],
  ["agent_ab_shadow","A/B shadow"],["news_veto","News-veto"]];
const AGWARN={agent_manager_mode:{on:"Manager-mode (Jalan A): agent = MANAJER DISIPLIN. Arah dari RULES (mematikan teknik gemini), planner+autonomous ON, tool-loop OFF (hemat token). Lanjut?"},
  agent_full_auto:{on:"Full-auto = tool-loop+autonomous+planner. Tool-loop = BANYAK panggilan Gemini (bisa 429 free-tier). LIVE FLAT butuh allow_live_trader. Lanjut?"},
  agent_tool_loop:{on:"Tool-loop: panggilan Gemini jauh lebih banyak tiap keputusan (bisa 429). Lanjut?"},
  agent_autonomous:{on:"Autonomous: agen boleh TUTUP SEMUA posisi (FLAT)/geser stop otomatis. LIVE FLAT butuh allow_live_trader. Lanjut?"},
  agent_planner:{on:"Planner bisa MEMBATASI entry (kuota/eksposur/risk-off). Lanjut?"},
  agent_ab_shadow:{on:"A/B shadow: ReAct catat verdict tanpa memblokir (rules tetap eksekusi). Lanjut?"},
  news_veto:{off:"Matikan News-veto: entry TETAP jalan walau ada berita high-impact. Lanjut?"}};
async function loadAgentCtl(){
  const s=await j('/api/agent-settings'); if(!s)return;
  $('#agentctl').innerHTML=AGFLAGS.map(([k,lbl])=>
    `<label style="margin-right:14px"><input type="checkbox" data-k="${k}" ${s[k]?'checked':''}> ${esc(lbl)}</label>`).join('')+
    '<span id="agnote" class="pos" style="margin-left:8px"></span>';
  document.querySelectorAll('#agentctl input').forEach(el=>el.addEventListener('change',async e=>{
    const k=e.target.dataset.k, v=e.target.checked;
    const w=v?(AGWARN[k]||{}).on:(AGWARN[k]||{}).off;
    if(w && !window.confirm(w)){ loadAgentCtl(); return; }   // batal → kembalikan centang
    await fetch('/api/agent-settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[k]:v})});
    await loadAgentCtl();
    const n=document.getElementById('agnote'); if(n){ n.textContent='✓ '+k+' '+(v?'ON':'OFF')+' diterapkan'; setTimeout(()=>{if(n)n.textContent='';},4000); }
  }));
}
loadAgentCtl();
load();setInterval(load,10000);
</script></body></html>"""


@app.get("/agent", response_class=HTMLResponse)
def agent_page() -> str:
    return AGENT_PAGE


# ---------- penyajian frontend ----------
# Jika build React/Vite ada (web/dist), sajikan SPA itu; jika belum, fallback ke
# halaman HTML lama (PAGE). API /api/* di atas tetap diprioritaskan (terdaftar lebih dulu).
DIST = ROOT / "web" / "dist"
if (DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="spa")
else:
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE
