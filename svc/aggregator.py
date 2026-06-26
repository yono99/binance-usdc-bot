"""Agregasi candle 1m (dari core) menjadi timeframe target (mis. 15m)."""
from __future__ import annotations


def tf_to_ms(tf: str) -> int:
    unit = tf[-1].lower()
    n = int(tf[:-1])
    mult = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}.get(unit)
    if mult is None:
        raise ValueError(f"timeframe tidak didukung: {tf}")
    return n * mult


class TFAggregator:
    """Gabungkan candle 1m menjadi satu bar timeframe. Kembalikan bar TF
    yang baru SELESAI saat bucket bergulir (None bila masih di bucket sama)."""

    def __init__(self, tf_ms: int):
        self.tf_ms = tf_ms
        self.cur: dict | None = None

    def update(self, c: dict) -> dict | None:
        bucket = (c["open_time"] // self.tf_ms) * self.tf_ms
        if self.cur is not None and self.cur["open_time"] == bucket:
            self.cur["high"] = max(self.cur["high"], c["high"])
            self.cur["low"] = min(self.cur["low"], c["low"])
            self.cur["close"] = c["close"]
            self.cur["volume"] += c["volume"]
            return None

        finished = self.cur
        self.cur = {
            "symbol": c["symbol"],
            "open_time": bucket,
            "open": c["open"],
            "high": c["high"],
            "low": c["low"],
            "close": c["close"],
            "volume": c["volume"],
        }
        return finished
