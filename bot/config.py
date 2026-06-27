"""Pemuat konfigurasi: .env (rahasia) + config.yaml (strategi)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


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


def load_settings() -> Settings:
    mode = (os.getenv("MODE", "dry") or "dry").strip().lower()
    if mode not in ("dry", "test", "live"):
        raise ValueError(f"MODE tidak valid: {mode!r} (dry|test|live)")

    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    keys = [k.strip() for k in os.getenv("GEMINI_API_KEYS", "").split(",") if k.strip()]
    enabled = os.getenv("GEMINI_ENABLED", "false").lower() == "true" and bool(keys)

    return Settings(mode=mode, raw=raw, gemini_keys=keys, gemini_enabled=enabled)
