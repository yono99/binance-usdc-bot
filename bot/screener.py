"""Layer 2 — penyaring pair: likuiditas, spread, volatilitas."""
from __future__ import annotations

from .exchange import Exchange
from .indicators import atr
from .logger import log


def discover_usdc_pairs(ex: Exchange, limit: int = 40) -> list[str]:
    """Auto-pilih perp USDC-margined paling likuid bila whitelist kosong."""
    syms = [
        m["symbol"] for m in ex.markets.values()
        if m.get("swap") and m.get("quote") == "USDC" and m.get("active")
    ]
    return syms[:limit]


def prefilter_volume(ex: Exchange, symbols: list[str], min_qv: float,
                     top_n: int | None = None) -> list[str]:
    """Pangkas universe BESAR (±800 perp USDT+USDC) dgn SATU panggilan
    fetch_tickers sebelum screen detail (yang butuh ±2 call/pair — 800 pair
    tanpa prefilter = ±2400 call/15mnt, tak masuk akal). SEMUA pair >= ambang
    volume 24h lolos ke screen detail (top_n=None = tanpa batas rangking —
    penyaringan pertama tak boleh membuang yang sudah lolos ambang likuiditas
    hanya karena rangking). top_n opsional utk pembatasan eksplisit bila perlu.
    Gagal batch → fail-open potong 60 pertama saja (fallback aman, bukan janji likuiditas)."""
    try:
        tks = ex.client.fetch_tickers(symbols)
    except Exception as e:  # boundary
        fallback_n = 60 if top_n is None else top_n
        log.warning(f"prefilter tickers gagal ({e}) — pakai {fallback_n} pertama")
        return symbols[:fallback_n]
    rows = []
    for s in symbols:
        qv = float((tks.get(s) or {}).get("quoteVolume") or 0)
        if qv >= min_qv:
            rows.append((s, qv))
    rows.sort(key=lambda r: -r[1])
    return [s for s, _ in (rows if top_n is None else rows[:top_n])]


def dedup_prefer_usdc(symbols: list[str]) -> list[str]:
    """Satu koin bisa lolos dua kali (BTC/USDT & BTC/USDC) → trading keduanya =
    eksposur DOBEL diam-diam ke aset yang sama. Simpan satu per base; prefer
    USDC (fee promo 0%), USDT hanya bila tak ada kembaran USDC-nya."""
    by_base: dict[str, str] = {}
    for s in symbols:
        base = s.split("/")[0]
        cur = by_base.get(base)
        if cur is None or (":USDC" in s and ":USDT" in cur):
            by_base[base] = s
    return sorted(by_base.values())


def screen(ex: Exchange, symbols: list[str], cfg: dict, timeframe: str) -> list[str]:
    s = cfg["screener"]
    passed: list[str] = []
    for sym in symbols:
        try:
            t = ex.ticker(sym)
            qv = float(t.get("quoteVolume") or 0)
            if qv < s["min_quote_volume_24h"]:
                continue
            spread = ex.spread_pct(sym)
            if spread > s["max_spread_pct"]:
                continue
            df = ex.ohlcv(sym, timeframe, limit=60)
            if len(df) < 30:
                continue
            atr_pct = float(atr(df).iloc[-1] / df["close"].iloc[-1] * 100)
            if not (s["min_atr_pct"] <= atr_pct <= s["max_atr_pct"]):
                continue
            passed.append(sym)
        except Exception as e:  # boundary
            log.warning(f"screen {sym} skip: {e}")
    log.info(f"Screener lolos {len(passed)}/{len(symbols)}: {passed}")
    return passed
