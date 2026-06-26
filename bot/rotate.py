"""Layer 3 — smart rotate: ranking kandidat, slot, cooldown, blacklist."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .signals import Signal


@dataclass
class Rotator:
    cfg: dict
    cooldown_until: dict[str, float] = field(default_factory=dict)
    sl_streak: dict[str, int] = field(default_factory=dict)
    blacklist_until: dict[str, float] = field(default_factory=dict)

    def _r(self) -> dict:
        return self.cfg["rotate"]

    def available(self, symbol: str) -> bool:
        now = time.time()
        if self.cooldown_until.get(symbol, 0) > now:
            return False
        if self.blacklist_until.get(symbol, 0) > now:
            return False
        return True

    def rank(self, signals: list[Signal], open_symbols: set[str]) -> list[Signal]:
        cands = [
            s for s in signals
            if s.actionable and s.symbol not in open_symbols and self.available(s.symbol)
        ]
        return sorted(cands, key=lambda s: s.confidence, reverse=True)

    def slots_free(self, open_count: int) -> int:
        return max(self._r()["max_open_positions"] - open_count, 0)

    def on_close(self, symbol: str, was_sl: bool) -> None:
        self.cooldown_until[symbol] = time.time() + self._r()["cooldown_minutes"] * 60
        if was_sl:
            self.sl_streak[symbol] = self.sl_streak.get(symbol, 0) + 1
            if self.sl_streak[symbol] >= self._r()["blacklist_after_sl"]:
                self.blacklist_until[symbol] = time.time() + 6 * 3600
                self.sl_streak[symbol] = 0
        else:
            self.sl_streak[symbol] = 0
