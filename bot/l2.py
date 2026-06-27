"""L2 orderbook collector — snapshot depth terkompresi + fitur microstructure.

Tujuan: kumpulkan data forward yang TIDAK tersedia historis (orderbook depth),
agar riset microstructure masa depan mungkin TANPA beli data mahal.

Desain (hasil diskusi metodologi):
- Simpan snapshot RAW (depth N level) — tak bisa di-reconstruct bila dibuang.
- Plus fitur turunan (imbalance, micro-price, spread) untuk pemakaian langsung.
- REST poll periodik (timing snapshot konsisten → tanpa bias self-collected).
- Output gzip JSONL per (simbol, tanggal): ~50–200 MB/hari/pair pada 10 level/1s.

CATATAN RATE-LIMIT (temuan operasional): REST-poll banyak simbol/frekuensi tinggi bisa
kena ban IP (HTTP 418). Binance menyarankan WebSocket partial-depth (`@depth20@100ms`)
untuk live updates. Untuk SKALA, ganti REST di sini dengan WS stream. Untuk beberapa
simbol pada interval ≥1s, REST biasanya aman.
"""
from __future__ import annotations

import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .exchange import Exchange
from .logger import log

ROOT = Path(__file__).resolve().parent.parent


def imbalance(bids: list, asks: list, n: int) -> float:
    """Order-book imbalance top-n ∈ [0,1]. >0.5 = tekanan beli."""
    b = sum(q for _, q in bids[:n])
    a = sum(q for _, q in asks[:n])
    tot = b + a
    return b / tot if tot else 0.5


def micro_price(bids: list, asks: list) -> float:
    """Harga tertimbang likuiditas: condong ke sisi yang lebih tipis."""
    bp, bq = bids[0]
    ap, aq = asks[0]
    tot = bq + aq
    return (bp * aq + ap * bq) / tot if tot else (bp + ap) / 2


def spread_bps(bids: list, asks: list) -> float:
    bp, ap = bids[0][0], asks[0][0]
    mid = (bp + ap) / 2
    return (ap - bp) / mid * 1e4 if mid else 0.0


def snapshot_record(symbol: str, bids: list, asks: list, ts_ms: int) -> dict:
    bids = [[float(p), float(q)] for p, q in bids]
    asks = [[float(p), float(q)] for p, q in asks]
    return {
        "ts": ts_ms,
        "symbol": symbol,
        "mid": (bids[0][0] + asks[0][0]) / 2,
        "spread_bps": round(spread_bps(bids, asks), 4),
        "micro": micro_price(bids, asks),
        "imb5": round(imbalance(bids, asks, 5), 5),
        "imb10": round(imbalance(bids, asks, 10), 5),
        "bids": bids,
        "asks": asks,
    }


class L2Collector:
    def __init__(self, settings, symbols: list[str], levels: int = 10,
                 interval: float = 1.0, outdir: Path | None = None):
        self.ex = Exchange(settings)
        self.symbols = symbols
        self.levels = levels
        self.interval = interval
        self.outdir = outdir or (ROOT / "data" / "l2")
        self.outdir.mkdir(parents=True, exist_ok=True)
        self._handles: dict[str, tuple[str, object]] = {}

    def _writer(self, symbol: str):
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        safe = symbol.replace("/", "_").replace(":", "")
        cur = self._handles.get(symbol)
        if cur and cur[0] == day:
            return cur[1]
        if cur:
            cur[1].close()
        path = self.outdir / f"{safe}_{day}.jsonl.gz"
        fh = gzip.open(path, "at", encoding="utf-8")
        self._handles[symbol] = (day, fh)
        return fh

    def snapshot(self, symbol: str) -> dict | None:
        try:
            ob = self.ex.client.fetch_order_book(symbol, limit=self.levels)
        except Exception as e:  # boundary
            log.warning(f"L2 {symbol} gagal: {e}")
            return None
        if not ob["bids"] or not ob["asks"]:
            return None
        ts = ob.get("timestamp") or int(time.time() * 1000)
        return snapshot_record(symbol, ob["bids"], ob["asks"], ts)

    def run(self) -> None:
        log.info(f"=== L2 COLLECTOR symbols={self.symbols} levels={self.levels} "
                 f"interval={self.interval}s -> {self.outdir} ===")
        n = 0
        while True:
            try:
                for sym in self.symbols:
                    rec = self.snapshot(sym)
                    if rec:
                        self._writer(sym).write(json.dumps(rec) + "\n")
                        n += 1
                if n % 60 == 0:
                    for _, (_, fh) in self._handles.items():
                        fh.flush()
                    log.info(f"L2 snapshots tersimpan: {n}")
            except KeyboardInterrupt:
                break
            except Exception as e:  # boundary — loop tak boleh mati
                log.error(f"L2 cycle error: {e}")
            time.sleep(self.interval)
        for _, (_, fh) in self._handles.items():
            fh.close()
        log.info(f"L2 collector berhenti. Total {n} snapshot.")
