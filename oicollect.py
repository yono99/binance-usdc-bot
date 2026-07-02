#!/usr/bin/env python3
"""Perekam Open Interest forward (H19/H29 — data-locked: Binance hanya simpan 30 hari).

  python oicollect.py                       # semua perp USDT+USDC, poll 1 jam
  python oicollect.py --interval 3600 --settle USDT

Output: data/oi/oi_<YYYYMMDD>.jsonl.gz — satu baris per (simbol, poll).
Seperti L2: data ini TIDAK tersedia historis — sekali tak direkam, hilang.
Uji H19 (crowding-freshness) baru layak setelah ≥6 bulan rekaman.
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from bot.config import load_settings
from bot.exchange import Exchange
from bot.logger import log

ROOT = Path(__file__).resolve().parent
OUTDIR = ROOT / "data" / "oi"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=3600.0, help="detik antar sweep")
    p.add_argument("--settle", nargs="*", default=["USDT", "USDC"])
    args = p.parse_args()

    ex = Exchange(load_settings())
    symbols = sorted(s for s, v in ex.markets.items()
                     if v.get("swap") and v.get("settle") in args.settle)
    OUTDIR.mkdir(parents=True, exist_ok=True)
    log.info(f"=== OI COLLECTOR {len(symbols)} perp, interval {args.interval}s -> {OUTDIR} ===")

    while True:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        n = 0
        try:
            with gzip.open(OUTDIR / f"oi_{day}.jsonl.gz", "at", encoding="utf-8") as fh:
                for sym in symbols:
                    try:
                        oi = ex.client.fetch_open_interest(sym)
                        fh.write(json.dumps({
                            "ts": oi.get("timestamp") or int(time.time() * 1000),
                            "symbol": sym,
                            "oi_amount": oi.get("openInterestAmount"),
                            "oi_value": oi.get("openInterestValue"),
                        }) + "\n")
                        n += 1
                    except Exception as e:  # boundary per-simbol
                        log.warning(f"OI {sym} gagal: {e}")
            log.info(f"OI sweep selesai: {n}/{len(symbols)} simbol.")
        except Exception as e:  # boundary — loop tak boleh mati
            log.error(f"OI sweep error: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
