"""Notifikasi SSE dashboard — fire-and-forget POST loopback ke /internal/notify.

Kenapa file terpisah dari notify.py: notify.py Telegram (outbound ke user),
ini loopback internal (bot→dashboard di mesin sama). Beda transport, beda lifecycle.

Fire-and-forget: dibuang ke thread daemon, gak pernah blok caller (hot-path trading).
Kalau dashboard mati / port tutup → silent fail (SQLite watcher jadi fallback).
"""
from __future__ import annotations

import os
import threading
from typing import Any

import urllib.request

# endpoint dashboard loopback (default mesin lokal)
DASHBOARD_URL = os.getenv("DASHBOARD_NOTIFY_URL", "http://127.0.0.1:8000/internal/notify")
_TIMEOUT = 2.0   # detik — gak boleh lama; dashboard harus responsif


def _post(payload: bytes) -> None:
    try:
        req = urllib.request.Request(DASHBOARD_URL, data=payload,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        urllib.request.urlopen(req, timeout=_TIMEOUT).read()
    except Exception:
        pass   # dashboard mati? abaikan — SQLite watcher catch up


def notify(kind: str, data: Any) -> None:
    """POST ke /internal/notify — non-blocking (thread daemon)."""
    import json
    body = json.dumps({"kind": kind, "data": data}, default=str).encode("utf-8")
    threading.Thread(target=_post, args=(body,), daemon=True).start()
