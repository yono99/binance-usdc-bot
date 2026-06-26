"""Wrapper Binance USDC-M Futures via ccxt. Mendukung dry/test/live."""
from __future__ import annotations

import ccxt
import pandas as pd

from .config import Settings
from .logger import log


class Exchange:
    def __init__(self, settings: Settings):
        self.settings = settings
        key, secret = settings.credentials()
        # binanceusdm = USDⓈ-M Futures (margin USDT/USDC)
        self.client = ccxt.binanceusdm({
            "apiKey": key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        if settings.mode == "test":
            self.client.set_sandbox_mode(True)
            log.info("Exchange: TESTNET (uang palsu)")
        elif settings.mode == "live":
            log.warning("Exchange: LIVE — UANG NYATA")
        else:
            log.info("Exchange: DRY — data publik nyata, order disimulasi")
        self.markets = self.client.load_markets()

    # ---------- data publik (tidak butuh key) ----------
    def ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        raw = self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        return df.set_index("time")

    def ticker(self, symbol: str) -> dict:
        return self.client.fetch_ticker(symbol)

    def spread_pct(self, symbol: str) -> float:
        ob = self.client.fetch_order_book(symbol, limit=5)
        if not ob["bids"] or not ob["asks"]:
            return 999.0
        bid, ask = ob["bids"][0][0], ob["asks"][0][0]
        return (ask - bid) / ((ask + bid) / 2) * 100

    # ---------- akun (butuh key; di dry pakai fallback) ----------
    def equity_usdc(self, fallback: float = 1000.0) -> float:
        if self.settings.is_dry:
            return fallback
        try:
            bal = self.client.fetch_balance()
            total = bal.get("total", {})
            return float(total.get("USDC") or total.get("USDT") or fallback)
        except Exception as e:  # boundary
            log.error(f"equity fetch gagal: {e}")
            return fallback

    def positions(self) -> list[dict]:
        if self.settings.is_dry:
            return []
        try:
            return [p for p in self.client.fetch_positions() if float(p.get("contracts") or 0) != 0]
        except Exception as e:  # boundary
            log.error(f"positions fetch gagal: {e}")
            return []

    def set_leverage(self, symbol: str, leverage: int) -> None:
        if self.settings.is_dry:
            return
        try:
            self.client.set_leverage(leverage, symbol)
        except Exception as e:  # boundary
            log.warning(f"set_leverage {symbol}: {e}")
