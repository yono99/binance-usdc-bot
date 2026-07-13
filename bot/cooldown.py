"""Tahap 4 (plan-sess) — Cooldown & blacklist per-mode PERSISTENT di SQLite.

Layer Layer-3 Rotator lama (in-memory only, tak dipakai ForwardTester produksi).
Pindah ke modul ini: stateless, key kv `cooldown_<mode>` dengan sub-key
`cooldown_until` (symbol → epoch detik), `sl_streak`, `blacklist_until`.

Idempoten & non-blocking. Race? Loader cooldown di-run setiap masuk `_open_usd`;
persist perubahan tiap kali ada close (anti restart hilang).
"""
from __future__ import annotations

import json
import time
from typing import Optional

from . import store


def _cooldown_key(mode: str) -> str:
    return f"cooldown_{mode}"


def _load_state(mode: str) -> dict:
    raw = store.get_kv(_cooldown_key(mode)) or {}
    if not raw:
        return {"cooldown_until": {}, "sl_streak": {}, "blacklist_until": {}}
    return raw


def available(mode: str, symbol: str, now: float | None = None) -> bool:
    """True kalau simbol boleh di-entry di mode ini (Tahap 4a)."""
    now = now if now is not None else time.time()
    st = _load_state(mode)
    if st["cooldown_until"].get(symbol, 0) > now:
        return False
    if st["blacklist_until"].get(symbol, 0) > now:
        return False
    return True


def cooldown_for(mode: str, symbol: str, minutes: float, now: float | None = None):
    """Set cooldown per-mode (in-memory + persist via SQLite). Minutes<=0 = no-op."""
    if minutes <= 0:
        return
    now = now if now is not None else time.time()
    st = _load_state(mode)
    st["cooldown_until"][symbol] = now + minutes * 60
    store.set_kv(_cooldown_key(mode), st)


def blacklist_for(mode: str, symbol: str, hours: float, now: float | None = None):
    st = _load_state(mode)
    now = now if now is not None else time.time()
    st["blacklist_until"][symbol] = now + hours * 3600
    sl_streak = st["sl_streak"].get(symbol, 0)
    if sl_streak:
        st["sl_streak"][symbol] = 0   # reset streak setelah blacklist
    store.set_kv(_cooldown_key(mode), st)


def record_close(mode: str, symbol: str, was_sl: bool, *,
                 cooldown_minutes: float = 0,
                 blacklist_after_sl: int = 0,
                 blacklist_hours: float = 6.0,
                 now: float | None = None):
    """Panggil saat close. Update cooldown (default: semua close dgn cooldown X menit, 0=nonaktif)
    dan sl_streak; bila streak >= blacklist_after_sl → blacklist X jam."""
    now = now if now is not None else time.time()
    st = _load_state(mode)
    if cooldown_minutes > 0:
        st["cooldown_until"][symbol] = now + cooldown_minutes * 60
    if was_sl:
        st["sl_streak"][symbol] = st["sl_streak"].get(symbol, 0) + 1
        if blacklist_after_sl > 0 and st["sl_streak"][symbol] >= blacklist_after_sl:
            st["blacklist_until"][symbol] = now + blacklist_hours * 3600
            st["sl_streak"][symbol] = 0
    else:
        st["sl_streak"][symbol] = 0
    store.set_kv(_cooldown_key(mode), st)


def clear(mode: str) -> None:
    """Reset cooldown/blacklist satu mode (untuk debug/admin)."""
    store.set_kv(_cooldown_key(mode), {})


def snapshot(mode: str) -> dict:
    """Cek state cooldown mode ini (untuk UI/debugging)."""
    return _load_state(mode)
