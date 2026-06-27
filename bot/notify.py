"""Notifikasi Telegram — kirim saat open/close/likuidasi.

Aktif bila TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID ada di .env. Tanpa itu → no-op.
Pengiriman fire-and-forget (thread) agar tak memblokir loop trading; gagal → diabaikan.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request

from .logger import log


class TelegramNotifier:
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = bool(self.token and self.chat)

    def _post_raw(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = json.dumps({"chat_id": self.chat, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=8).read()

    def send(self, text: str) -> bool:
        """Non-blocking. True bila terjadwal kirim, False bila notifier non-aktif."""
        if not self.enabled:
            return False

        def _run():
            try:
                self._post_raw(text)
            except Exception as e:  # boundary — jangan ganggu trading
                log.warning(f"telegram gagal: {e}")

        threading.Thread(target=_run, daemon=True).start()
        return True

    def send_sync(self, text: str) -> tuple[bool, str | None]:
        """Sinkron (untuk tombol Test) — kembalikan (ok, error)."""
        if not self.enabled:
            return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi di .env"
        try:
            self._post_raw(text)
            return True, None
        except Exception as e:  # boundary
            return False, str(e)[:140]
