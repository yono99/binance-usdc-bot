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
            # Binance MENGHAPUS futures testnet; ccxt tak lagi mendukung sandbox futures.
            # Jadi 'test' kini = PAPER di data LIVE (order disimulasi, tanpa uang nyata).
            log.warning("Exchange: TEST — Binance futures testnet sudah deprecated. "
                        "Berjalan sebagai PAPER di data LIVE (order disimulasi).")
        elif settings.mode == "live":
            log.warning("Exchange: LIVE — UANG NYATA")
        else:
            log.info("Exchange: DRY — data publik nyata, order disimulasi")
        self.markets = self.client.load_markets()

    def usdc_symbols(self) -> list[str]:
        """Semua pair USDC-M perpetual yang tersedia (untuk mode 'screening semua')."""
        return sorted(s for s, v in self.markets.items()
                      if v.get("settle") == "USDC" and v.get("swap"))

    def perp_symbols(self, settles: tuple[str, ...] = ("USDC",)) -> list[str]:
        """Perp aktif utk beberapa settle sekaligus (USDC + USDT satu platform
        USDS-M, satu client). KRIPTO MURNI saja: perp saham/komoditas ter-
        tokenisasi (MSTR/XAU/SOXL... underlyingType EQUITY/COMMODITY) dibuang —
        jam perdagangan & perilaku aset TradFi beda kelas, di luar mandat bot.
        Catatan fee (promo Binance USDC-M): USDC maker 0% / taker ~0.04%;
        USDT-M standar 0.02%/0.05%. Exit SL/TP = market = taker. Lihat RuntimeSettings.fee_rate."""
        return sorted(s for s, v in self.markets.items()
                      if v.get("swap") and v.get("settle") in settles
                      and v.get("active", True)
                      and (v.get("info", {}) or {}).get("underlyingType", "COIN") == "COIN")

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
    def balances(self, fallback: float = 1000.0) -> dict[str, float]:
        """Saldo margin TERPISAH per-quote (USDC-M & USDT-M dompet berbeda di Binance).
        Pair BTC/USDC butuh margin USDC, BTC/USDT butuh USDT → sizing per-quote (#1)."""
        if self.settings.is_dry:
            return {"USDC": fallback, "USDT": 0.0}
        try:
            total = self.client.fetch_balance().get("total", {})
            return {"USDC": float(total.get("USDC") or 0.0),
                    "USDT": float(total.get("USDT") or 0.0)}
        except Exception as e:  # boundary
            log.error(f"saldo fetch gagal: {e}")
            return {"USDC": fallback, "USDT": 0.0}

    def equity_usdc(self, fallback: float = 1000.0) -> float:
        """Equity TOTAL (USDC+USDT) — angka informatif/sizing global. Sizing per-quote
        yang benar-secara-margin ambil dari balances() (dikerjakan di #1)."""
        if self.settings.is_dry:
            return fallback
        b = self.balances(fallback)
        return b["USDC"] + b["USDT"]

    # ---------- Tahap 2 (plan-sess): MARGIN ISOLATED ----------
    def set_margin_isolated(self, symbol: str) -> bool:
        """Set marginType=ISOLATED untuk simbol ini. Idempotent: error -4046 "No need
        to change" diabaikan (sudah isolated). Dry → no-op. Return True kalau sukses/no-op.
        TAHAP 2: hanya dipanggil bila tak ada posisi terbuka di simbol (jika sudah ada,
        skip — catch error, log, lanjut entry). Cache per-simbol di pemanggil."""
        if self.settings.is_dry:
            return True
        try:
            self.client.fapiPrivatePostMarginType({"symbol": symbol, "marginType": "ISOLATED"})
            return True
        except Exception as e:
            # -4046 (No need to change) atau sudah isolated → no-op aman
            msg = str(e).lower()
            if "-4046" in msg or "no need to change" in msg:
                return True
            log.warning(f"set_margin_isolated {symbol}: {e}")
            return False

    def margin_type(self, symbol: str) -> str | None:
        """Cek marginType saat ini untuk simbol. Via fapiPrivateGetSymbolConfig (jika
        tersedia di ccxt). Return 'ISOLATED'/'CROSS'/None"""
        if self.settings.is_dry:
            return "ISOLATED"             # default paper
        try:
            res = self.client.fapiPrivateGetSymbolConfig({"symbol": symbol})
            if isinstance(res, dict):
                info = res.get("info") or {}
                mt = (info.get("marginType") or res.get("marginType") or "").upper()
                return mt or None
            return None
        except Exception as e:
            log.warning(f"margin_type {symbol}: {e}")
            return None

    def position_mode(self) -> str:
        """Asumsi awal: ONE-WAY (single side per symbol). Cross cek via fapiPrivateGetPositionSide
        bila perlu."""
        if self.settings.is_dry:
            return "one-way"
        try:
            res = self.client.fapiPrivateGetPositionSide()
            if isinstance(res, dict):
                return str(res.get("dualSidePosition") and "hedge" or "one-way")
            return "one-way"
        except Exception:
            return "one-way"

    def positions(self) -> list[dict]:
        if self.settings.is_dry:
            return []
        try:
            return [p for p in self.client.fetch_positions() if float(p.get("contracts") or 0) != 0]
        except Exception as e:  # boundary
            log.error(f"positions fetch gagal: {e}")
            return []

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        """Open order nyata dari Binance: LIMIT entry yang masih RESTING (post-only/GTX belum
        terisi) + SL/TP STOP_MARKET/TAKE_PROFIT_MARKET yang aktif. Dry → []."""
        if self.settings.is_dry:
            return []
        try:
            return self.client.fetch_open_orders(symbol) if symbol else self.client.fetch_open_orders()
        except Exception as e:  # boundary
            log.error(f"open_orders fetch gagal: {e}")
            return []

    def set_leverage(self, symbol: str, leverage: int) -> None:
        if self.settings.is_dry:
            return
        try:
            self.client.set_leverage(leverage, symbol)
        except Exception as e:  # boundary
            log.warning(f"set_leverage {symbol}: {e}")
