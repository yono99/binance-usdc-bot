"""Memori lintas-tick (point 4) — working memory jangka pendek yang bertahan ANTAR-siklus.

Agen tak lagi menyelidiki dari nol tiap tick: ia mengingat observasi tool & keputusan
terakhir per simbol (time & size-bounded) → penalaran berkesinambungan
("tadi funding tinggi & OI naik; sekarang harga breakout → konsisten").

In-memory (hidup selama proses). ForwardTester meng-snapshot ke SQLite agar tahan
restart (best-effort). Bukan pengganti decision_log (audit permanen) — ini ingatan kerja.
"""
from __future__ import annotations

import time
from collections import deque


class AgentMemory:
    def __init__(self, maxlen: int = 200, max_age_s: int = 3600):
        self.notes: deque = deque(maxlen=maxlen)
        self.max_age_s = max_age_s

    def remember(self, kind: str, symbol: str, data) -> None:
        self.notes.append({"ts": time.time(), "kind": str(kind), "symbol": symbol, "data": data})

    def recall(self, symbol: str | None = None, kind: str | None = None, limit: int = 5) -> list[dict]:
        now = time.time()
        out: list[dict] = []
        for n in reversed(self.notes):                 # terbaru dulu
            if now - n["ts"] > self.max_age_s:
                continue
            if symbol is not None and n["symbol"] != symbol:
                continue
            if kind is not None and n["kind"] != kind:
                continue
            out.append(n)
            if len(out) >= max(0, limit):
                break
        return out

    def summary(self, symbol: str, limit: int = 5) -> list[dict]:
        """Ringkas untuk disuntik ke prompt: umur (detik), jenis, data — terbaru dulu."""
        now = time.time()
        return [{"age_s": int(now - n["ts"]), "kind": n["kind"], "data": n["data"]}
                for n in self.recall(symbol=symbol, limit=limit)]

    def snapshot(self) -> list[dict]:
        return list(self.notes)

    def restore(self, notes) -> None:
        if notes:
            self.notes = deque(notes, maxlen=self.notes.maxlen)
