"""Phase 3 — Lessons engine (gaya Meridian, paralel dgn evidence-gate store lama).

Alur:
  1. Setelah posisi tutup → ambil baris decision_log trade itu.
  2. Gemini menurunkan SATU pelajaran konkret: 'IF [kondisi] THEN [aksi] BECAUSE [alasan]'.
  3. Simpan ke lessons.json dgn metadata (regime, outcome_r, akurasi, dst).
  4. 10 pelajaran terbaru disuntik ke prompt ReactAgent.
  5. Tiap pelajaran yang DIPICU di keputusan → lacak akurasi (correct/triggered).
  6. Skoring berkala: pensiunkan pelajaran <0.4 akurasi setelah ≥10 pemicu.

HARD CONSTRAINT: kegagalan LLM tak boleh memblokir apa pun. Bila Gemini mati,
pelajaran tetap diturunkan secara DETERMINISTIK (kerangka dari outcome) — sistem
tetap berjalan, hanya tanpa narasi natural-language.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .gemini_client import GeminiClient
from .logger import log

LESSONS_PATH = Path("lessons.json")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_all(path: Path | str = LESSONS_PATH) -> list[dict]:
    """Baca lessons.json tanpa perlu instance (untuk dashboard). Korup → []."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # boundary
        return []


def _side_from_action(action: str) -> str:
    if action == "ENTER_LONG":
        return "long"
    if action == "ENTER_SHORT":
        return "short"
    return "flat"


class LessonsEngine:
    def __init__(self, settings: Settings, cfg: dict, path: Path | str = LESSONS_PATH):
        gcfg = cfg.get("gemini", {})
        self.client = GeminiClient(settings.gemini_keys, gcfg.get("model", "gemini-2.5-flash"))
        self.enabled = bool(settings.gemini_enabled and self.client.available)
        self.path = Path(path)

    # ---------------------- persistensi ----------------------
    def all(self) -> list[dict]:
        return load_all(self.path)

    def _save(self, lessons: list[dict]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(lessons, indent=2, default=str), encoding="utf-8")
        except Exception as e:  # boundary
            log.warning(f"tulis lessons.json gagal: {e}")

    def active(self) -> list[dict]:
        return [l for l in self.all() if not l.get("retired")]

    def recent(self, n: int = 10) -> list[dict]:
        """n pelajaran AKTIF terbaru (untuk disuntik ke prompt agen) — ringkas {id, lesson}."""
        act = sorted(self.active(), key=lambda l: l.get("created_at", ""), reverse=True)
        return [{"id": l["id"], "lesson": l["lesson"]} for l in act[:max(0, n)]]

    # ---------------------- derivasi ----------------------
    def derive_from_trade(self, row: dict) -> dict | None:
        """Turunkan & simpan satu pelajaran dari baris decision_log yang sudah ber-outcome.
        Kembalikan lesson dict, atau None bila baris tak layak (mis. belum ada outcome)."""
        if row.get("outcome") is None:
            return None
        text = self._llm_lesson(row) if self.enabled else None
        source = "LLM"
        if not text or "IF" not in text.upper():
            text = self._deterministic_lesson(row)     # fail-open / fallback
            source = "deterministic"
        return self.add(text, derived_from=row.get("id"), outcome_r=row.get("outcome_r"),
                        regime=(row.get("market_state") or {}).get("regime", "unknown"),
                        source=source)

    def _llm_lesson(self, row: dict) -> str | None:
        prompt = (
            "Kamu pelatih trading. Dari SATU trade ini (alasan entry, kondisi pasar, hasil), "
            "turunkan SATU pelajaran konkret yang DAPAT DIUJI. "
            "Format WAJIB persis: 'IF [kondisi] THEN [aksi] BECAUSE [alasan]'. "
            "Balas HANYA satu kalimat itu, tanpa tambahan.\n"
            f"Entry reasoning: {row.get('reasoning')}\n"
            f"Market state: {json.dumps(row.get('market_state'), default=str)}\n"
            f"Signal scores: {json.dumps(row.get('signal_scores'), default=str)}\n"
            f"Outcome: {row.get('outcome')} ({row.get('outcome_r')}R)"
        )
        text = self.client.generate(prompt, purpose="lesson")
        return text.strip() if text else None

    @staticmethod
    def _deterministic_lesson(row: dict) -> str:
        regime = (row.get("market_state") or {}).get("regime", "unknown")
        side = _side_from_action(str(row.get("action", "")))
        r = row.get("outcome_r")
        verdict = "favour" if (isinstance(r, (int, float)) and r > 0) else "avoid"
        return (f"IF regime={regime} AND signal={side} THEN {verdict} entry "
                f"BECAUSE last such trade closed {row.get('outcome')} ({r}R)")

    def add(self, lesson_text: str, *, derived_from=None, outcome_r=None,
            regime="unknown", source="LLM") -> dict:
        lesson = {
            "id": uuid.uuid4().hex,
            "lesson": lesson_text.strip()[:300],
            "derived_from_trade": derived_from,
            "outcome_r": outcome_r,
            "market_regime": regime,
            "confidence": 0.0,
            "times_triggered": 0,
            "times_correct": 0,
            "created_at": _utcnow(),
            "retired": False,
            "source": source,
        }
        lessons = self.all()
        lessons.append(lesson)
        self._save(lessons)
        return lesson

    # ---------------------- akurasi & pensiun ----------------------
    def record_trigger(self, lesson_id: str, correct: bool) -> bool:
        """Catat satu pemicu pelajaran + apakah hasilnya benar. Update akurasi (confidence)."""
        if not lesson_id:
            return False
        lessons = self.all()
        for l in lessons:
            if l.get("id") == lesson_id:
                l["times_triggered"] = int(l.get("times_triggered", 0)) + 1
                if correct:
                    l["times_correct"] = int(l.get("times_correct", 0)) + 1
                t = l["times_triggered"]
                l["confidence"] = round(l["times_correct"] / t, 3) if t else 0.0
                self._save(lessons)
                return True
        return False

    def score_and_retire(self, min_triggers: int = 10, min_acc: float = 0.4) -> int:
        """Pensiunkan pelajaran berakurasi < min_acc setelah ≥ min_triggers pemicu.
        Kembalikan jumlah yang dipensiunkan. Dipanggil berkala (mingguan)."""
        lessons = self.all()
        retired = 0
        for l in lessons:
            if l.get("retired"):
                continue
            t = int(l.get("times_triggered", 0))
            if t >= min_triggers and (l.get("times_correct", 0) / t) < min_acc:
                l["retired"] = True
                l["retired_at"] = _utcnow()
                retired += 1
        if retired:
            self._save(lessons)
            log.info(f"Lessons: {retired} pelajaran dipensiunkan (akurasi < {min_acc}).")
        return retired
