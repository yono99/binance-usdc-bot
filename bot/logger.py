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


_JOURNAL_MODE: str | None = None


def set_journal_mode(mode: str) -> None:
    """Pisahkan jurnal per mode (dry/test/live): file & stempel payload.
    Dipanggil sekali oleh bot saat init — mencegah riwayat lintas-mode bercampur."""
    global _JOURNAL_MODE
    _JOURNAL_MODE = mode


def journal(event: str, payload: dict) -> None:
    """Catat satu event trade. Dual-write: JSONL (audit/post-mortem, append-only) +
    SQLite (sumber query/hapus untuk dashboard). Kegagalan SQLite tak boleh menjatuhkan
    bot — JSONL tetap jadi cadangan."""
    if _JOURNAL_MODE:
        payload = {**payload, "mode": _JOURNAL_MODE}
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **payload}
    fname = f"trades_{_JOURNAL_MODE}.jsonl" if _JOURNAL_MODE else "trades.jsonl"
    with open(LOG_DIR / fname, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    try:
        from .store import insert_event
        insert_event(event, payload, ts=rec["ts"])
    except Exception as e:  # boundary — jangan ganggu hot-path trading
        log.warning(f"store insert gagal (JSONL tetap aman): {e}")
    # push real-time ke dashboard SSE (fire-and-forget; gak blok)
    try:
        from .notify_sse import notify
        # map event trade → tipe SSE yang dimengerti frontend
        kind = ("trade" if event in ("forward_open", "forward_close",
                                     "forward_open_filled", "forward_open_pending")
                else "screen" if event == "forward_skip"
                else "event")
        notify(kind, rec)
    except Exception:
        pass   # dashboard SSE opsional — jangan ganggu hot-path
