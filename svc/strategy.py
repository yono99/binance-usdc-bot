"""Layer 2-4 (Python): jaga buffer TF dari feed core, hasilkan intent.

Risk gate & eksekusi ada di Rust core — di sini hanya strategi:
screening pasif (rotate slot/cooldown) + sinyal + veto Gemini."""
from __future__ import annotations

import pandas as pd

from bot.config import Settings
from bot.gemini_layer import GeminiLayer
from bot.logger import log
from bot.rotate import Rotator
from bot.signals import evaluate

from .aggregator import TFAggregator, tf_to_ms


def core_to_ccxt(sym: str) -> str:
    base = sym[:-4] if sym.upper().endswith("USDC") else sym
    return f"{base.upper()}/USDC:USDC"


class Strategy:
    def __init__(self, settings: Settings, symbols_core: list[str], max_bars: int = 300):
        self.settings = settings
        self.cfg = settings.raw
        self.tf = self.cfg["market"]["timeframe"]
        self.tf_ms = tf_to_ms(self.tf)
        self.max_bars = max_bars
        self.symbols = [s.upper() for s in symbols_core]
        self.agg = {s: TFAggregator(self.tf_ms) for s in self.symbols}
        self.df: dict[str, pd.DataFrame] = {}
        self.rotator = Rotator(self.cfg)
        self.gemini = GeminiLayer(settings, self.cfg)
        self.open: set[str] = set()
        self.max_open = self.cfg["rotate"]["max_open_positions"]

    def seed(self, ex) -> None:
        """Isi awal buffer TF via REST agar sinyal bisa dihitung dari t=0."""
        for sym in self.symbols:
            try:
                self.df[sym] = ex.ohlcv(core_to_ccxt(sym), self.tf, limit=self.max_bars)
                log.info(f"seed {sym}: {len(self.df[sym])} bar {self.tf}")
            except Exception as e:  # boundary
                log.warning(f"seed {sym} gagal: {e}")
                self.df[sym] = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def _append(self, sym: str, bar: dict) -> None:
        ts = pd.to_datetime(bar["open_time"], unit="ms", utc=True)
        self.df[sym].loc[ts] = [bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]]
        if len(self.df[sym]) > self.max_bars:
            self.df[sym] = self.df[sym].iloc[-self.max_bars:]

    def on_candle(self, c: dict) -> dict | None:
        sym = c["symbol"].upper()
        if sym not in self.agg:
            return None
        finished = self.agg[sym].update(c)
        if finished is None:
            return None  # bar TF belum tertutup
        self._append(sym, finished)

        if len(self.df[sym]) < self.cfg["signals"]["ema_slow"] + 5:
            return None
        if self.max_open - len(self.open) <= 0 or sym in self.open or not self.rotator.available(sym):
            return None

        sig = evaluate(sym, self.df[sym], self.cfg)
        if not sig.actionable:
            return None

        snapshot = {"price": sig.price, "atr": sig.atr, "conf": sig.confidence, "reason": sig.reason}
        if not self.gemini.allows(sym, snapshot):
            log.info(f"{sym}: di-veto Gemini (regime buruk)")
            return None

        log.info(f"INTENT {sig.side.upper()} {sym} conf={sig.confidence} price={sig.price} atr={sig.atr:.6f}")
        return {"symbol": sym, "side": sig.side, "confidence": sig.confidence,
                "price": sig.price, "atr": sig.atr}

    def on_event(self, ev: dict) -> None:
        sym, kind = ev.get("symbol", "?"), ev.get("kind", "?")
        if kind == "open":
            self.open.add(sym)
            log.info(f"EVENT open {sym} qty={ev.get('qty')} sl={ev.get('sl')} tp={ev.get('tp')}")
        elif kind == "close":
            self.open.discard(sym)
            was_sl = str(ev.get("note", "")).upper().startswith("SL")
            self.rotator.on_close(sym, was_sl)
            log.info(f"EVENT close {sym} note={ev.get('note')}")
        elif kind == "reject":
            log.info(f"EVENT reject {sym}: {ev.get('note')}")
        elif kind == "error":
            log.error(f"EVENT error {sym}: {ev.get('note')}")
