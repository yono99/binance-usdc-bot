"""Layer 7 — position manager: lacak posisi, deteksi exit."""
from __future__ import annotations

from .execution import Executor
from .exchange import Exchange
from .logger import journal, log


@dataclass
class Position:
    symbol: str
    side: str       # long|short
    qty: float
    entry: float
    sl: float
    tp: float


class PositionManager:
    """Untuk dry: posisi virtual disimulasi penuh.
    Untuk test/live: SL/TP dieksekusi exchange; manager hanya rekonsiliasi."""

    def __init__(self, ex: Exchange, executor: Executor, cfg: dict):
        self.ex = ex
        self.ex_exec = executor
        self.cfg = cfg["risk"]
        self.open: dict[str, Position] = {}

    @property
    def symbols(self) -> set[str]:
        return set(self.open.keys())

    @property
    def notional(self) -> float:
        return sum(p.qty * p.entry for p in self.open.values())

    def add(self, pos: Position) -> None:
        self.open[pos.symbol] = pos

    def _pnl(self, p: Position, price: float) -> float:
        d = price - p.entry if p.side == "long" else p.entry - price
        return d * p.qty

    def monitor(self, price_of) -> list[tuple[str, float, bool]]:
        """Kembalikan list (symbol, pnl, was_sl) untuk posisi yang ditutup di tick ini."""
        closed: list[tuple[str, float, bool]] = []
        for sym, p in list(self.open.items()):
            try:
                price = price_of(sym)
            except Exception as e:  # boundary
                log.warning(f"harga {sym} gagal: {e}")
                continue

            hit_sl = price <= p.sl if p.side == "long" else price >= p.sl
            hit_tp = price >= p.tp if p.side == "long" else price <= p.tp
            if hit_sl or hit_tp:
                pnl = self._pnl(p, price)
                if not self.ex.settings.is_dry:
                    self.ex_exec.close_position(sym, p.side, p.qty)
                tag = "SL" if hit_sl else "TP"
                log.info(f"CLOSE {tag} {sym} @ {price:.6f} pnl={pnl:.2f}")
                journal("close", {"symbol": sym, "exit": price, "pnl": pnl, "tag": tag})
                closed.append((sym, pnl, hit_sl))
                del self.open[sym]
        return closed
