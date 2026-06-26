"""Layer opsional — Gemini sebagai konfirmasi/veto regime pasar.

PENTING: ini BUKAN mesin sinyal. Perannya menilai konteks/regime
(trending vs choppy, risiko berita) dan memberi skor 0..1.
Entry tetap ditentukan rules deterministik di signals.py.
Rotasi banyak API key bila tersedia."""
from __future__ import annotations

import itertools
import json

from .config import Settings
from .logger import log

try:
    from google import genai
except Exception:  # SDK belum terpasang
    genai = None


class GeminiLayer:
    def __init__(self, settings: Settings, cfg: dict):
        self.settings = settings
        self.cfg = cfg["gemini"]
        self.enabled = settings.gemini_enabled and self.cfg.get("role", "off") != "off" and genai is not None
        self._keys = itertools.cycle(settings.gemini_keys) if settings.gemini_keys else None
        if settings.gemini_enabled and genai is None:
            log.warning("google-genai belum terpasang; Gemini layer dinonaktifkan")

    def regime_score(self, symbol: str, snapshot: dict) -> float:
        """1.0 = kondisi bagus untuk entry, 0.0 = hindari. Default 1.0 jika off/gagal."""
        if not self.enabled or self._keys is None:
            return 1.0
        prompt = (
            "Kamu analis risiko pasar kripto. Nilai apakah KONDISI saat ini layak "
            "untuk membuka posisi futures jangka pendek. Balas HANYA JSON: "
            '{"score": <0..1>, "regime": "trend|range|chaos", "note": "<singkat>"}.\n'
            f"Pair: {symbol}\nData: {json.dumps(snapshot)}"
        )
        try:
            client = genai.Client(api_key=next(self._keys))
            resp = client.models.generate_content(
                model=self.cfg.get("model", "gemini-2.5-flash"),
                contents=prompt,
            )
            text = (resp.text or "").strip()
            start, end = text.find("{"), text.rfind("}")
            data = json.loads(text[start:end + 1])
            score = float(data.get("score", 1.0))
            log.info(f"Gemini regime {symbol}: {score:.2f} ({data.get('regime')}) {data.get('note','')}")
            return max(0.0, min(score, 1.0))
        except Exception as e:  # boundary — jangan blokir trading karena AI error
            log.warning(f"Gemini regime {symbol} gagal, abaikan: {e}")
            return 1.0

    def allows(self, symbol: str, snapshot: dict) -> bool:
        if not self.enabled or self.cfg.get("role") != "veto":
            return True
        return self.regime_score(symbol, snapshot) >= self.cfg.get("min_regime_score", 0.4)
