"""Dashboard web monitoring (FastAPI) — baca jurnal forward-test, sajikan stats.

Terpisah dari bot: ForwardTester menulis logs/trades.jsonl, dashboard membacanya.
  GET /            -> halaman HTML (auto-refresh)
  GET /api/stats   -> JSON statistik berjalan
"""
from __future__ import annotations

import json
from pathlib import Path

from dataclasses import asdict

import csv as csvmod
import io

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .settings_store import (PRESETS, RuntimeSettings, get_active_mode, load_settings,
                             save_settings, set_active_mode)
from . import store

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


def compute_stats(path: Path | None = None, start_equity: float = 1000.0,
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
        "return_pct": round((equity_curve[-1] / start_equity - 1) * 100, 2),
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


app = FastAPI(title="Bot Monitor")


@app.get("/api/trades")
def api_trades(symbol: str = None, reason: str = None, dfrom: str = None,
               dto: str = None, limit: int = 100) -> JSONResponse:
    limit = min(max(1, limit), 100)        # data dari SQLite maksimal 100
    trades = filter_trades(build_trades(store.all_events(), _ui_mode()), symbol, reason, dfrom, dto)
    return JSONResponse({"count": len(trades), "trades": trades[-limit:][::-1]})


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
    Fallback ke kv 'status' lama (bot lama/single-process)."""
    from .settings_store import _env_mode
    m = mode if mode in ("dry", "test", "live") else (get_active_mode() or _env_mode())
    return JSONResponse(store.get_kv(f"status:{m}") or store.get_kv("status") or {})


_acct = {"ts": 0.0, "data": None}


@app.get("/api/account")
def api_account() -> JSONResponse:
    import os
    import time
    if _acct["data"] and time.time() - _acct["ts"] < 30:
        return JSONResponse(_acct["data"])
    from .config import load_settings
    s = load_settings()
    if s.mode == "live" and os.getenv("BINANCE_LIVE_KEY"):
        try:
            from .exchange import Exchange
            bal = Exchange(s).client.fetch_balance()
            total = bal.get("total", {})
            usdc = float(total.get("USDC") or total.get("USDT") or 0)
            data = {"mode": "live", "api_valid": True, "balance_usdc": round(usdc, 2),
                    "gemini_enabled": s.gemini_enabled, "gemini_keys": len(s.gemini_keys)}
        except Exception as e:  # boundary
            data = {"mode": "live", "api_valid": False, "error": str(e)[:140],
                    "gemini_enabled": s.gemini_enabled, "gemini_keys": len(s.gemini_keys)}
    else:
        data = {"mode": s.mode, "api_valid": None, "paper": True,
                "gemini_enabled": s.gemini_enabled, "gemini_keys": len(s.gemini_keys)}
    _acct.update(ts=time.time(), data=data)
    return JSONResponse(data)


_ex_cache: dict = {"ex": None}
_ohlcv_cache: dict = {}


def _get_ex():
    if _ex_cache["ex"] is None:
        from .config import load_settings
        from .exchange import Exchange
        _ex_cache["ex"] = Exchange(load_settings())
    return _ex_cache["ex"]


_symbols_cache: dict = {"ts": 0.0, "data": None}


@app.get("/api/symbols")
def api_symbols() -> JSONResponse:
    """Daftar pair USDC-M perpetual yang tersedia (untuk pemilih + pencarian di UI)."""
    import time
    if _symbols_cache["data"] and time.time() - _symbols_cache["ts"] < 600:
        return JSONResponse(_symbols_cache["data"])
    try:
        m = _get_ex().client.markets
        syms = sorted(s for s, v in m.items() if v.get("settle") == "USDC" and v.get("swap"))
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
        from .config import load_settings
        sig = load_settings().raw["signals"]
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
    memukul exchange. Isi/refresh via `python chart_ingest.py`."""
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
        bal = c.fetch_balance()
        total = bal.get("total", {})
        usdc = float(total.get("USDC") or total.get("USDT") or 0)
        return JSONResponse({"valid": True, "balance_usdc": round(usdc, 2)})
    except Exception as e:  # boundary
        return JSONResponse({"valid": False, "error": str(e)[:160]})


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
def api_gemini_trader() -> JSONResponse:
    """Track record Gemini trader: verdict signifikansi, per-setup, playbook aktif, keputusan."""
    from .gemini_trader import track_record
    return JSONResponse(_json_safe(track_record()))


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
        from .config import load_settings as _load
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
      <label>Saldo (USD)<input id="balance_usd" type="number" min="0" step="0.1"></label>
      <label>Target profit % (0=ATR)<input id="target_profit_pct" type="number" min="0" step="0.1"></label>
      <label>Max posisi terbuka<input id="max_open_positions" type="number" min="1" max="20" step="1"></label>
      <label>Stop-loss harian % (0=off)<input id="daily_max_loss_pct" type="number" min="0" max="100" step="0.1"></label>
      <label>Max trade harian (0=off)<input id="daily_max_trades" type="number" min="0" max="1000" step="1"></label>
      <label>Interval screening (dtk)<input id="poll_seconds" type="number" min="5" max="3600" step="1"></label>
      <label>Timeframe (otomatis)<input id="tf" disabled></label>
    </div>
    <button id="save">Simpan pengaturan</button>
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
  </div>
</div>
<script>
let chart;
const f=(n,d=2)=>Number(n).toFixed(d);
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
  document.getElementById('balance_usd').value=s.balance_usd;
  document.getElementById('target_profit_pct').value=s.target_profit_pct;
  document.getElementById('max_open_positions').value=s.max_open_positions;
  document.getElementById('daily_max_loss_pct').value=s.daily_max_loss_pct;
  document.getElementById('daily_max_trades').value=s.daily_max_trades;
  document.getElementById('poll_seconds').value=s.poll_seconds;
  document.getElementById('tf').value=s.timeframe;
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
    balance_usd:+document.getElementById('balance_usd').value,
    target_profit_pct:+document.getElementById('target_profit_pct').value,
    max_open_positions:+document.getElementById('max_open_positions').value,
    daily_max_loss_pct:+document.getElementById('daily_max_loss_pct').value,
    daily_max_trades:+document.getElementById('daily_max_trades').value,
    poll_seconds:+document.getElementById('poll_seconds').value,
  };
  const s=await (await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  window.pendingBal=s.balance_usd;   // tahan tampilan saldo sampai bot menerapkan
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
  // atau saat masih menunggu bot menerapkan nilai yang baru disimpan (pendingBal).
  const balEl=document.getElementById('balance_usd');
  if(window.pendingBal!=null && Math.abs((s.balance_usd??0)-window.pendingBal)<1e-9) window.pendingBal=null;
  if(s.balance_usd!=null && document.activeElement!==balEl && window.pendingBal==null) balEl.value=s.balance_usd;
  const api=a.api_valid===true?'<span class="pos">VALID</span>':(a.api_valid===false?'<span class="neg">INVALID</span>':'paper (tanpa key)');
  let bal=a.balance_usdc!=null?('$'+f(a.balance_usdc,2)):(s.balance_usd!=null?('$'+f(s.balance_usd,2)+' <span class="sub">paper</span>'):'—');
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
    `TF: ${s.timeframe} · Leverage: <b>${s.leverage}x</b> · Bet: $${f(s.bet_usd,2)} · Saldo: <b>$${f(s.balance_usd,2)}</b> · `+
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
}
function tradeQ(){
  const p=new URLSearchParams();
  const s=document.getElementById('fsym').value.trim(); if(s)p.set('symbol',s);
  const r=document.getElementById('freason').value; if(r)p.set('reason',r);
  const a=document.getElementById('ffrom').value; if(a)p.set('dfrom',a);
  const b=document.getElementById('fto').value; if(b)p.set('dto',b);
  return p.toString();
}
async function loadTrades(){
  const q=tradeQ();
  document.getElementById('fcsv').href='/api/trades.csv'+(q?'?'+q:'');
  const d=await (await fetch('/api/trades'+(q?'?'+q:''))).json();
  document.getElementById('tcount').textContent=d.count+' trade';
  document.getElementById('thist').innerHTML=table(
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
function refresh(){load();loadStatus();loadChart();loadTrades();}
refresh();setInterval(refresh,10000);
</script></body></html>"""


# ---------- Phase 6: panel Agent (ReAct/lessons/evolution) ----------
# Endpoint JSON + halaman /agent mandiri (tak menyentuh SPA React → panel lama aman).

@app.get("/api/decisions")
def api_decisions(limit: int = 20) -> JSONResponse:
    """Keputusan ReactAgent terakhir (alasan, confidence, sumber, outcome R)."""
    from . import decision_log
    return JSONResponse({"decisions": decision_log.recent(min(max(1, limit), 200))})


@app.get("/api/lessons")
def api_lessons() -> JSONResponse:
    """Pelajaran aktif + akurasi (times_correct/triggered) & berapa kali dipicu."""
    from . import lessons
    rows = [l for l in lessons.load_all() if not l.get("retired")]
    rows.sort(key=lambda l: l.get("created_at", ""), reverse=True)
    return JSONResponse({"count": len(rows), "lessons": rows})


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
def api_evolution(limit: int = 50) -> JSONResponse:
    """Riwayat evolusi threshold (before/after, p-value, applied)."""
    from . import evolve
    return JSONResponse({"events": evolve.recent_events(min(max(1, limit), 200))})


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
   <th>aksi</th><th>conf</th><th>sumber</th><th>alasan</th><th>outcome</th><th>R</th></tr></thead><tbody></tbody></table></div>
 <div class="card"><h2>Pelajaran Aktif</h2><table id="les"><thead><tr><th>pelajaran</th><th>regime</th>
   <th>akurasi</th><th>dipicu</th><th>sumber</th></tr></thead><tbody></tbody></table></div>
 <div class="card"><h2>Evolusi Threshold (OOS)</h2><table id="evo"><thead><tr><th>waktu</th><th>param</th>
   <th>lama→baru</th><th>OOS base→prop</th><th>p</th><th>applied</th></tr></thead><tbody></tbody></table></div>
</div>
<script>
const $=s=>document.querySelector(s);
const rcls=v=>v>0?'pos':(v<0?'neg':'');
const esc=s=>(s==null?'':String(s)).replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
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
 const d=await j('/api/decisions?limit=20');
 if(d){$('#dec tbody').innerHTML=(d.decisions||[]).map(x=>`<tr><td class="mut">${esc((x.ts||'').slice(0,19))}</td>`+
   `<td>${esc(x.symbol)}</td><td><span class="pill">${esc(x.action)}</span></td><td>${(x.confidence??0)}</td>`+
   `<td class="mut">${esc(x.source)}</td><td>${esc(x.reasoning)}</td><td>${esc(x.outcome??'')}</td>`+
   `<td class="${rcls(x.outcome_r)}">${x.outcome_r==null?'':x.outcome_r}</td></tr>`).join('')||'<tr><td colspan=8 class=mut>belum ada keputusan</td></tr>';}
 const l=await j('/api/lessons');
 if(l){$('#les tbody').innerHTML=(l.lessons||[]).map(x=>{const t=x.times_triggered||0,c=x.times_correct||0;
   const acc=t?(c/t*100).toFixed(0)+'%':'—';return `<tr><td>${esc(x.lesson)}</td><td class="mut">${esc(x.market_regime)}</td>`+
   `<td>${acc} <span class="mut">(${c}/${t})</span></td><td>${t}</td><td class="mut">${esc(x.source)}</td></tr>`;}).join('')
   ||'<tr><td colspan=5 class=mut>belum ada pelajaran</td></tr>';}
 const e=await j('/api/evolution?limit=30');
 if(e){$('#evo tbody').innerHTML=(e.events||[]).map(x=>`<tr><td class="mut">${esc((x.ts||'').slice(0,19))}</td>`+
   `<td>${esc(x.param)}</td><td>${esc(x.old)} → ${esc(x.new??'—')}</td>`+
   `<td>${esc(x.test_exp_r_baseline??'—')} → ${esc(x.test_exp_r_proposed??'—')}</td>`+
   `<td>${esc(x.p_value??'—')}</td><td>${x.applied?'<span class=pos>YA</span>':'<span class=mut>tidak</span>'}</td></tr>`).join('')
   ||'<tr><td colspan=6 class=mut>belum ada evolusi</td></tr>';}
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
