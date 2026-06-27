"""News veto via Gemini — veto entry saat ada berita high-impact (real-time).

PENTING & jujur:
- Ini layer REAL-TIME, **tidak bisa di-backtest** (tak ada histori headline berlabel).
  Gunanya menambah keamanan di forward-test, BUKAN bukti edge.
- Sumber: RSS publik domain tetap (tanpa API key, aman SSRF).
- Gemini menilai headline → {veto, score, note}. Aktif hanya bila GEMINI diaktifkan.
- Gagal jaringan/Gemini → ALLOW (jangan pernah blokir trading karena error infra).
"""
from __future__ import annotations

import json
import urllib.request
import xml.etree.ElementTree as ET
from time import time

from .config import Settings
from .gemini_client import GeminiClient
from .logger import log

FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]


def parse_titles(xml_bytes: bytes, limit: int = 15) -> list[str]:
    """Ambil judul dari RSS/Atom (pure, untuk test offline)."""
    titles: list[str] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return titles
    for tag in (".//item/title", ".//{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}title"):
        for el in root.findall(tag):
            if el.text and el.text.strip():
                titles.append(el.text.strip())
    return titles[:limit]


class NewsVeto:
    def __init__(self, settings: Settings, cfg: dict):
        gcfg = cfg.get("gemini", {})
        self.client = GeminiClient(settings.gemini_keys, gcfg.get("model", "gemini-2.5-flash"))
        self.enabled = (settings.gemini_enabled and bool(gcfg.get("news_veto", False))
                        and self.client.available)
        self.ttl = 900  # cache 15 menit (hemat token, berita tak berubah tiap detik)
        self._cache: tuple[float, bool, str] | None = None

    def _headlines(self) -> list[str]:
        out: list[str] = []
        for url in FEEDS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 bot"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    out += parse_titles(r.read())
            except Exception as e:  # boundary
                log.warning(f"news feed gagal ({url}): {e}")
        return out[:20]

    def check(self) -> tuple[bool, str]:
        """(veto, note). veto=True → jangan buka posisi siklus ini."""
        if not self.enabled:
            return False, "off"
        if self._cache and time() - self._cache[0] < self.ttl:
            return self._cache[1], self._cache[2]

        headlines = self._headlines()
        if not headlines:
            return False, "no-news"

        prompt = (
            "Kamu analis risiko kripto. Dari headline berikut, apakah ADA berita "
            "high-impact yang berisiko memicu volatilitas tajam dalam beberapa jam ke depan "
            "(mis. FOMC/CPI, regulasi/SEC, hack besar, delisting, kebangkrutan exchange)? "
            'Balas HANYA JSON: {"veto": true|false, "score": <0..1>, "note": "<singkat>"}.\n'
            + "\n".join(f"- {h}" for h in headlines)
        )
        text = self.client.generate(prompt, purpose="news_veto")
        if not text:
            return False, "error"
        try:
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
            veto = bool(data.get("veto", False))
            note = str(data.get("note", ""))[:80]
            self._cache = (time(), veto, note)
            log.info(f"News veto={veto} ({note})")
            return veto, note
        except Exception as e:  # boundary — jangan blokir trading karena AI error
            log.warning(f"news veto parse gagal, allow: {e}")
            return False, "error"
