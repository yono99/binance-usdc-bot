"""Layer 6 — eksekusi order + SL/TP. Dry: simulasi. Test/Live: order nyata."""
from __future__ import annotations

from .exchange import Exchange
from .logger import journal, log
from .risk import RiskDecision
from .signals import Signal


class Executor:
    def __init__(self, ex: Exchange, cfg: dict):
        self.ex = ex
        self.cfg = cfg
        self.slippage_guard_pct = 0.15

    def open_position(self, sig: Signal, dec: RiskDecision) -> dict | None:
        side = "buy" if sig.side == "long" else "sell"
        sym = sig.symbol
        qty = self._round_amount(sym, dec.qty)
        if qty <= 0:
            log.warning(f"{sym}: qty terlalu kecil setelah pembulatan, skip")
            return None

        payload = {"symbol": sym, "side": sig.side, "qty": qty, "entry": sig.price,
                   "sl": dec.sl, "tp": dec.tp, "conf": sig.confidence, "notional": dec.notional}

        if self.ex.settings.is_dry:
            log.info(f"[DRY] OPEN {sig.side.upper()} {sym} qty={qty} @~{sig.price} SL={dec.sl} TP={dec.tp}")
            journal("open_dry", payload)
            return {**payload, "id": "dry", "filled": sig.price}

        try:
            self.ex.set_leverage(sym, self.cfg["risk"]["leverage"])
            order = self.ex.client.create_order(sym, "market", side, qty)
            filled = float(order.get("average") or sig.price)
            if abs(filled - sig.price) / sig.price * 100 > self.slippage_guard_pct:
                log.warning(f"{sym}: slippage {filled} vs {sig.price} > guard — pasang proteksi tetap")
            self._place_protection(sym, sig.side, qty, dec.sl, dec.tp)
            journal("open", {**payload, "id": order.get("id"), "filled": filled})
            log.info(f"OPEN {sig.side.upper()} {sym} qty={qty} fill={filled} SL={dec.sl} TP={dec.tp}")
            return {**payload, "id": order.get("id"), "filled": filled}
        except Exception as e:  # boundary
            log.error(f"open_position {sym} gagal: {e}")
            journal("open_error", {**payload, "error": str(e)})
            return None

    def _place_protection(self, sym: str, side: str, qty: float, sl: float, tp: float) -> None:
        close_side = "sell" if side == "long" else "buy"
        try:
            self.ex.client.create_order(sym, "STOP_MARKET", close_side, qty, None,
                                        {"stopPrice": sl, "reduceOnly": True})
            self.ex.client.create_order(sym, "TAKE_PROFIT_MARKET", close_side, qty, None,
                                        {"stopPrice": tp, "reduceOnly": True})
        except Exception as e:  # boundary
            log.error(f"pasang SL/TP {sym} gagal: {e}")

    def close_position(self, sym: str, side: str, qty: float) -> None:
        if self.ex.settings.is_dry:
            log.info(f"[DRY] CLOSE {sym}")
            return
        close_side = "sell" if side == "long" else "buy"
        try:
            self.ex.client.create_order(sym, "market", close_side, qty, None, {"reduceOnly": True})
            self.ex.client.cancel_all_orders(sym)
        except Exception as e:  # boundary
            log.error(f"close {sym} gagal: {e}")

    def _round_amount(self, sym: str, qty: float) -> float:
        try:
            return float(self.ex.client.amount_to_precision(sym, qty))
        except Exception:
            return round(qty, 3)
