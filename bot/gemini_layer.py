"""Layer opsional — Gemini sebagai konfirmasi/veto regime pasar.

PENTING: ini BUKAN mesin sinyal. Perannya menilai konteks/regime
(trending vs choppy, risiko berita) dan memberi skor 0..1.
Entry tetap ditentukan rules deterministik di signals.py.
Rotasi banyak API key bila tersedia."""
from __future__ import annotations

import json

from .config import Settings
from .gemini_client import GeminiClient
from .logger import log


class GeminiLayer:
    def __init__(self, settings: Settings, cfg: dict):
        self.settings = settings
        self.cfg = cfg["gemini"]
        self.client = GeminiClient(settings.gemini_keys, self.cfg.get("model", "gemini-2.5-flash"))
        self.enabled = (settings.gemini_enabled and self.cfg.get("role", "off") != "off"
                        and self.client.available)

    def regime_score(self, symbol: str, snapshot: dict) -> float:
        """1.0 = kondisi bagus untuk entry, 0.0 = hindari. Default 1.0 jika off/gagal."""
        if not self.enabled:
            return 1.0
        prompt = (
            "Kamu analis risiko pasar kripto. Nilai apakah KONDISI saat ini layak "
            "untuk membuka posisi futures jangka pendek. Balas HANYA JSON: "
            '{"score": <0..1>, "regime": "trend|range|chaos", "note": "<singkat>"}.\n'
            f"Pair: {symbol}\nData: {json.dumps(snapshot)}"
        )
        text = self.client.generate(prompt, purpose="regime")
        if not text:
            return 1.0
        try:
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
            score = float(data.get("score", 1.0))
            log.info(f"Gemini regime {symbol}: {score:.2f} ({data.get('regime')}) {data.get('note','')}")
            return max(0.0, min(score, 1.0))
        except Exception as e:  # boundary — jangan blokir trading karena AI error
            log.warning(f"Gemini regime {symbol} parse gagal, abaikan: {e}")
            return 1.0

    def allows(self, symbol: str, snapshot: dict) -> bool:
        if not self.enabled or self.cfg.get("role") != "veto":
            return True
        return self.regime_score(symbol, snapshot) >= self.cfg.get("min_regime_score", 0.4)
