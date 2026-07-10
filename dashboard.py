#!/usr/bin/env python3
"""Jalankan dashboard monitoring web.

  python dashboard.py                 # http://127.0.0.1:8000
  python dashboard.py --port 8080 --host 0.0.0.0

Membaca logs/trades.jsonl yang ditulis forwardtest.py. Buka di browser; auto-refresh.
"""
from __future__ import annotations

import argparse

import uvicorn

from bot.settings_store import reset_all_enabled


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    reset_all_enabled()
    print(f"Dashboard: http://{args.host}:{args.port}")
    print("Startup: SEMUA mode di-reset ke OFF — nyalakan dari dashboard.")
    uvicorn.run("bot.dashboard:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
