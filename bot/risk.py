"""Layer 5 — risk gate: sizing, exposure, circuit breaker harian."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .logger import log
from .signals import Signal


@dataclass
class RiskDecision:
    ok: bool
    qty: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    notional: float = 0.0
    reason: str = ""


@dataclass
class DailyState:
    day: date = field(default_factory=date.today)
    realized_pnl: float = 0.0
    trades: int = 0
    halted: bool = False

    def roll(self) -> None:
        if self.day != date.today():
            self.day, self.realized_pnl, self.trades, self.halted = date.today(), 0.0, 0, False


class RiskGate:
    def __init__(self, cfg: dict):
        self.cfg = cfg["risk"]
        self.daily = DailyState()

    def breaker_tripped(self, equity: float) -> bool:
        self.daily.roll()
        if self.daily.halted:
            return True
        max_loss = -abs(self.cfg["daily_max_loss_pct"]) / 100 * equity
        if self.daily.realized_pnl <= max_loss:
            self.daily.halted = True
            log.error(f"CIRCUIT BREAKER: PnL harian {self.daily.realized_pnl:.2f} <= {max_loss:.2f}. STOP.")
            return True
        if self.daily.trades >= self.cfg["daily_max_trades"]:
            log.warning("Batas jumlah trade harian tercapai.")
            return True
        return False

    def evaluate(self, sig: Signal, equity: float, open_notional: float) -> RiskDecision:
        if sig.atr <= 0:
            return RiskDecision(False, reason="ATR nol")

        mult = self.cfg["sl_atr_mult"]
        tp_mult = self.cfg["tp_atr_mult"]
        if sig.side == "long":
            sl = sig.price - sig.atr * mult
            tp = sig.price + sig.atr * tp_mult
        else:
            sl = sig.price + sig.atr * mult
            tp = sig.price - sig.atr * tp_mult

        risk_per_unit = abs(sig.price - sl)
        if risk_per_unit <= 0:
            return RiskDecision(False, reason="jarak SL nol")

        risk_budget = self.cfg["account_risk_pct"] / 100 * equity
        qty = risk_budget / risk_per_unit
        notional = qty * sig.price

        # cap exposure portofolio
        max_expo = self.cfg["max_portfolio_exposure_pct"] / 100 * equity
        if open_notional + notional > max_expo:
            allowed = max(max_expo - open_notional, 0)
            if allowed < notional * 0.5:
                return RiskDecision(False, reason="exposure cap")
            qty = allowed / sig.price
            notional = qty * sig.price

        if qty <= 0:
            return RiskDecision(False, reason="qty nol")

        return RiskDecision(True, qty=qty, sl=round(sl, 6), tp=round(tp, 6),
                            notional=round(notional, 2), reason="ok")

    def record_close(self, pnl: float) -> None:
        self.daily.roll()
        self.daily.realized_pnl += pnl
        self.daily.trades += 1
