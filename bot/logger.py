"""Logging terpusat + jurnal trade ke file JSONL."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.logging import RichHandler

try:  # paksa UTF-8 di console Windows agar simbol tidak crash
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%H:%M:%S]",
    handlers=[
        RichHandler(rich_tracebacks=True, show_path=False),
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
    ],
)

log = logging.getLogger("bot")


def journal(event: str, payload: dict) -> None:
    """Catat satu event trade. Dual-write: JSONL (audit/post-mortem, append-only) +
    SQLite (sumber query/hapus untuk dashboard). Kegagalan SQLite tak boleh menjatuhkan
    bot — JSONL tetap jadi cadangan."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **payload}
    with open(LOG_DIR / "trades.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    try:
        from .store import insert_event
        insert_event(event, payload, ts=rec["ts"])
    except Exception as e:  # boundary — jangan ganggu hot-path trading
        log.warning(f"store insert gagal (JSONL tetap aman): {e}")
