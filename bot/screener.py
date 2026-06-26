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
