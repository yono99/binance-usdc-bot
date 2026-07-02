"""Phase 2 — siklus hidup decision log (logs/decision_log.jsonl).

Satu keputusan = satu baris JSON saat ENTRY (outcome=null). Saat posisi tutup,
baris ENTER yang cocok (simbol, outcome masih null) DIPERBARUI dengan hasil R →
tiap trade bisa ditelusuri dari ALASAN ENTRY sampai OUTCOME R.

Dipakai bersama: ReactAgent (append), engine (record_outcome saat close),
LessonsEngine & dashboard (read_all/recent).
"""
from __future__ import annotations

import json
from pathlib import Path

from .logger import log

DECISION_LOG = Path("logs/decision_log.jsonl")
_PATH = DECISION_LOG


def set_mode(mode: str | None) -> None:
    """Pisahkan decision log per mode — keputusan paper & live tak boleh
    bercampur (merusak integritas A/B & lessons). mode=None → reset default."""
    global _PATH
    _PATH = Path(f"logs/decision_log_{mode}.jsonl") if mode else DECISION_LOG


def current_path() -> Path:
    return _PATH


def append(row: dict, path: Path | str | None = None) -> None:
    """Tambah satu baris keputusan. Boundary: gagal tulis ≠ ganggu trading."""
    try:
        p = Path(path or _PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
    except Exception as e:  # boundary
        log.warning(f"append decision_log gagal: {e}")


def read_all(path: Path | str | None = None) -> list[dict]:
    """Semua baris valid (baris korup dilewati, tak meledak)."""
    p = Path(path or _PATH)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # boundary — baris korup dilewati
            continue
    return out


def recent(n: int = 20, path: Path | str | None = None) -> list[dict]:
    """n keputusan terbaru (urut terbaru dulu)."""
    return list(reversed(read_all(path)))[:max(0, n)]


def get(decision_id: str, path: Path | str | None = None) -> dict | None:
    """Ambil satu baris keputusan berdasarkan id (untuk LessonsEngine)."""
    for row in read_all(path):
        if row.get("id") == decision_id:
            return row
    return None


def record_outcome(symbol: str, outcome: str, outcome_r: float, *,
                   filled_at_close: bool = True, extras: dict | None = None,
                   path: Path | str | None = None) -> str | None:
    """Perbarui baris ENTER terakhir utk `symbol` yang outcome-nya masih null.
    Kembalikan id keputusan yang diperbarui, atau None bila tak ada yang cocok.
    Menulis ulang file (volume paper-trade kecil → aman)."""
    p = Path(path or _PATH)
    rows = read_all(p)
    if not rows:
        return None
    matched_id = None
    for row in reversed(rows):                          # entri terbaru dulu
        if (row.get("symbol") == symbol
                and str(row.get("action", "")).startswith("ENTER")
                and row.get("outcome") is None):
            row["outcome"] = outcome
            row["outcome_r"] = round(float(outcome_r), 4)
            row["filled_at_close"] = bool(filled_at_close)
            if extras:                              # mis. mae_pct/mfe_pct (Fix B)
                row.update(extras)
            matched_id = row.get("id")
            break
    if matched_id is None:
        return None
    try:
        with p.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, default=str) + "\n")
    except Exception as e:  # boundary
        log.warning(f"tulis ulang decision_log gagal: {e}")
        return None
    return matched_id
