"""Evaluator paper-test H28 — t-test PRA-REGISTRASI dalam satu fungsi.

Aturan (dikunci 2026-07-02, penutup RESEARCH_HYPOTHESES_PHASE4.md):
- Verdict HANYA setelah ≥ MIN_CYCLES siklus tertutup. Sebelum itu: PREVIEW
  (progres ditampilkan, kesimpulan DILARANG).
- LOLOS bila mean pnl_net > 0 DAN t-test satu-sisi p < 0.05 (SATU trial —
  tanpa koreksi, karena semua parameter beku sejak awal).
- LOLOS → Tahap 2 (mikro-live ≤$50 + kill-switch). GAGAL → terminal.

Dipakai oleh: CLI `h28_eval.py` dan API dashboard `/api/h28` (status PREVIEW).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .xsectional import t_pvalue

ROOT = Path(__file__).resolve().parent.parent
TRADES = ROOT / "data" / "h28_forward" / "trades.jsonl"
STATE = ROOT / "data" / "h28_forward" / "state.json"
MIN_CYCLES = 15
ALPHA = 0.05


def load_trades(path: Path = TRADES) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def evaluate(rows: list[dict], min_cycles: int = MIN_CYCLES, alpha: float = ALPHA) -> dict:
    """PURE. Kembalikan status lengkap: PREVIEW sebelum min_cycles; sesudahnya
    verdict final LOLOS/GAGAL sesuai pra-registrasi."""
    pnls = np.asarray([float(r["pnl_net"]) for r in rows], dtype=float)
    n = len(pnls)
    base = {"cycles": n, "min_cycles": min_cycles,
            "progress": f"{n}/{min_cycles}",
            "mean_net": round(float(pnls.mean()), 5) if n else None,
            "win_rate": round(float((pnls > 0).mean()), 3) if n else None,
            "sum_net": round(float(pnls.sum()), 5) if n else None}
    if n < min_cycles:
        return {**base, "status": "PREVIEW",
                "verdict": None,
                "note": (f"BELUM BOLEH DINILAI — baru {n}/{min_cycles} siklus. "
                         "Parameter beku; jangan diubah; jangan disimpulkan.")}
    p = t_pvalue(pnls)
    ok = bool(pnls.mean() > 0 and p < alpha)
    return {**base, "status": "FINAL", "p_value": round(float(p), 4),
            "verdict": "LOLOS_TAHAP_1" if ok else "GAGAL",
            "note": ("LOLOS pra-registrasi → Tahap 2: mikro-live ≤$50 + kill-switch "
                     "(lihat penutup RESEARCH_HYPOTHESES_PHASE4.md)." if ok else
                     "GAGAL pra-registrasi → kondisi terminal berlaku: tanpa live, "
                     "tanpa varian, tanpa negosiasi ulang.")}


def preview_status() -> dict:
    """Status PREVIEW lengkap untuk API/dashboard: state daemon + evaluasi."""
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    ev = evaluate(load_trades())
    return {"engine": "H28 VRP-DVOL", "mode": "PREVIEW (paper-only - TIDAK trading "
            "uang sampai LOLOS_TAHAP_1)", "gate": "gap DVOL-RV30 > 0.10, hold 10 hari",
            "daemon_state": {"open_basket": state.get("open"),
                             "last_eval": state.get("last_eval")},
            "evaluation": ev}
