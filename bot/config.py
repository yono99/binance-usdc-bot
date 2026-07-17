"""Pemuat konfigurasi: .env (rahasia) + config.yaml (strategi)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    mode: str
    raw: dict
    gemini_keys: list[str] = field(default_factory=list)
    gemini_enabled: bool = False

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_dry(self) -> bool:
        # 'test' = paper (testnet Binance futures sudah deprecated) → diperlakukan seperti dry
        return self.mode in ("dry", "test")

    def credentials(self) -> tuple[str, str]:
        if self.mode == "live":
            return os.getenv("BINANCE_LIVE_KEY", ""), os.getenv("BINANCE_LIVE_SECRET", "")
        return "", ""   # dry & test = paper, tak butuh kredensial

    def __getitem__(self, key: str):
        return self.raw[key]


def _warn_bad_gemini_keys(keys: list[str]) -> None:
    """Log-warning key Gemini yang cacat: bukan format Google (AIzaSy + 39 char) atau duplikat.
    Key# = index 0-based (sama dgn kolom 'Per key' dashboard & log_gemini_usage)."""
    from .logger import log
    seen: dict[str, int] = {}
    for i, k in enumerate(keys):
        # Dua format valid: legacy 'AIzaSy…' (39 char) & auth-key baru 'AQ.Ab8…' (Google 2026,
        # jalan di endpoint native generativelanguage). Selain itu = kemungkinan typo/kepotong.
        if not ((len(k) == 39 and k.startswith("AIzaSy")) or k.startswith("AQ.")):
            log.warning(f"GEMINI_API_KEYS Key#{i} cacat: len={len(k)} awalan={k[:6]!r} "
                        "(harus 'AIzaSy'+39char ATAU 'AQ.…') — kemungkinan kepotong/typo, akan selalu error.")
        if k in seen:
            log.warning(f"GEMINI_API_KEYS Key#{i} DUPLIKAT dari Key#{seen[k]} — tak menambah kuota RPM.")
        else:
            seen[k] = i


def load_settings() -> Settings:
    # Tahap 1 (plan-sess): mode efective dibaca dari KV 'active_mode' (pilihan UI)
    # bila tersedia; bila tak ada → fallback ke .env. Tanpa patch ini, dashboard
    # yang import dari .config selalu kena .env MODE=live walau UI pilih 'dry'.
    load_dotenv(ROOT / ".env")  # pindah ke dalam fungsi supaya monkeypatch test kerja
    # Tahap 1 (plan-sess): mode efective dibaca dari KV 'active_mode' (pilihan UI)
    try:
        from .store import get_kv
        kv_mode = (get_kv("active_mode") or {}).get("mode", "") or ""
    except Exception:
        kv_mode = ""
    env_mode = (os.getenv("MODE", "dry") or "dry").strip().lower()
    
    # Validasi mode SEBELUM memutuskan sumber — agar test MODE=bogus selalu gagal
    mode = kv_mode or env_mode
    if mode not in ("dry", "test", "live"):
        raise ValueError(f"MODE tidak valid: {mode!r} (dry|test|live)")

    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    keys = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
    _warn_bad_gemini_keys(keys)
    enabled = os.getenv("GEMINI_ENABLED", "false").lower() == "true" and bool(keys)

    return Settings(mode=mode, raw=raw, gemini_keys=keys, gemini_enabled=enabled)
