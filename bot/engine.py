"""Orkestrasi 7 layer menjadi satu loop trading."""
from __future__ import annotations

import time

from .config import Settings
from .exchange import Exchange
from .execution import Executor
from .gemini_layer import GeminiLayer
from .logger import log
from .position import Position, PositionManager
from .react_agent import ReactAgent
from .risk import RiskGate
from .rotate import Rotator
from .screener import discover_usdc_pairs, screen
from .signals import evaluate


class Engine:
    def __init__(self, settings: Settings):
        self.s = settings
        self.cfg = settings.raw
        self.ex = Exchange(settings)
        self.risk = RiskGate(self.cfg)
        self.rotator = Rotator(self.cfg)
        self.executor = Executor(self.ex, self.cfg)
        self.pm = PositionManager(self.ex, self.executor, self.cfg)
        self.gemini = GeminiLayer(settings, self.cfg)
        # ReAct agent menggantikan veto pasif; veto lama dipakai sbg fallback deterministik.
        self.agent = ReactAgent(settings, self.cfg, veto=self.gemini)
        self.tf = self.cfg["market"]["timeframe"]
        self._universe: list[str] = []
        self._last_screen = 0.0

    def universe(self) -> list[str]:
        wl = self.cfg["market"].get("whitelist") or []
        base = wl if wl else discover_usdc_pairs(self.ex)
        if time.time() - self._last_screen > 300 or not self._universe:
            self._universe = screen(self.ex, base, self.cfg, self.tf)
            self._last_screen = time.time()
        return self._universe

    def _price(self, sym: str) -> float:
        return float(self.ex.ticker(sym)["last"])

    def tick(self) -> None:
        equity = self.ex.equity_usdc()

        # Layer 7 dulu: kelola posisi terbuka & catat exit
        for sym, pnl, was_sl in self.pm.monitor(self._price):
            self.risk.record_close(pnl)
            self.rotator.on_close(sym, was_sl)

        # Layer 5: circuit breaker harian
        if self.risk.breaker_tripped(equity):
            return

        slots = self.rotator.slots_free(len(self.pm.open))
        if slots <= 0:
            return

        # Layer 2-4: screen -> sinyal -> rank
        signals = []
        for sym in self.universe():
            if sym in self.pm.symbols:
                continue
            try:
                df = self.ex.ohlcv(sym, self.tf, limit=200)
                signals.append(evaluate(sym, df, self.cfg))
            except Exception as e:  # boundary
                log.warning(f"sinyal {sym} gagal: {e}")

        # PnL harian dalam R (1R ≈ account_risk_pct dari equity) — untuk konteks agen.
        risk_budget = self.cfg["risk"]["account_risk_pct"] / 100 * equity
        daily_pnl_r = self.risk.daily.realized_pnl / risk_budget if risk_budget else 0.0
        n_open = len(self.pm.open)
        max_positions = n_open + slots

        ranked = self.rotator.rank(signals, self.pm.symbols)
        for sig in ranked[:slots]:
            # ReAct: OBSERVE→REASON→ACT→RECORD. LLM gagal → fallback deterministik (tak blokir).
            # alt (funding/OI/CVD) & lessons di-wire di fase berikut; kini None/[] (tetap aman).
            decision = self.agent.decide(
                sig, regime=getattr(sig, "regime", "unknown"), alt=None,
                n_positions=n_open, max_positions=max_positions,
                daily_pnl_r=daily_pnl_r, lessons=[])
            if not decision.permits(sig):
                log.info(f"{sig.symbol}: agent {decision.action} [{decision.source}] — {decision.reasoning}")
                continue
            dec = self.risk.evaluate(sig, equity, self.pm.notional)
            if not dec.ok:
                log.info(f"{sig.symbol}: risk gate tolak ({dec.reason})")
                continue
            res = self.executor.open_position(sig, dec)
            if res:
                self.pm.add(Position(sig.symbol, sig.side, res["qty"], res["filled"],
                                     dec.sl, dec.tp, peak=res["filled"]))

    def run(self) -> None:
        interval = self.cfg["market"]["poll_seconds"]
        log.info(f"=== START mode={self.s.mode} tf={self.tf} poll={interval}s ===")
        if self.s.is_live:
            log.warning("MODE LIVE AKTIF — order menggunakan UANG NYATA.")
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                log.info("Dihentikan pengguna.")
                break
            except Exception as e:  # boundary — loop tidak boleh mati
                log.error(f"tick error: {e}")
            time.sleep(interval)
