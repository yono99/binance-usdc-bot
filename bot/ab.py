"""A/B harness — UKUR (bukan tebak) apakah layer ReAct menambah nilai.

Metode (offline, jujur): jalankan ReAct mode SHADOW → rules tetap mengeksekusi SEMUA
entry, ReAct hanya mencatat verdict (`react_action`) tanpa memblokir. Karena tiap trade
benar-benar diambil, kita punya outcome R untuk SEMUA — termasuk yang ReAct ingin tolak.

  Arm A (kontrol)   = rules-saja          → exp_R semua trade.
  Arm B (perlakuan) = rules + ReAct       → exp_R subset yang ReAct SETUJUI (ENTER).
  Denied            = yang ReAct TOLAK    → bila exp_R-nya lebih buruk, veto-nya berguna.

Verdict: ReAct menambah nilai HANYA bila exp_R(B) > exp_R(A) DAN kept signifikan > denied
(permutation test, p<0.05). Kalau tidak → terima jujur: ReAct tak terbukti membantu.
"""
from __future__ import annotations

import numpy as np

from . import decision_log
from .evolve import permutation_pvalue


def collect(path=decision_log.DECISION_LOG) -> list[dict]:
    """Trade tertutup (ENTER, sudah ber-outcome) yang punya verdict shadow ReAct."""
    out = []
    for r in decision_log.read_all(path):
        if (r.get("outcome_r") is not None
                and str(r.get("action", "")).startswith("ENTER")
                and r.get("react_action")):            # hanya baris hasil mode shadow
            out.append(r)
    return out


def _mean(xs):
    return float(np.mean(xs)) if xs else None


def analyze(rows: list[dict], *, alpha: float = 0.05) -> dict:
    """Bandingkan rules-saja vs rules+ReAct. PURE → mudah diuji."""
    a = [float(r["outcome_r"]) for r in rows]
    kept = [float(r["outcome_r"]) for r in rows
            if str(r.get("react_action", "")).startswith("ENTER")]
    denied = [float(r["outcome_r"]) for r in rows
              if not str(r.get("react_action", "")).startswith("ENTER")]

    exp_a, exp_b, exp_d = _mean(a), _mean(kept), _mean(denied)
    base = {"n_total": len(a), "n_kept": len(kept), "n_denied": len(denied),
            "exp_r_rules": round(exp_a, 4) if exp_a is not None else None,
            "exp_r_rules_react": round(exp_b, 4) if exp_b is not None else None,
            "exp_r_denied": round(exp_d, 4) if exp_d is not None else None}

    if not rows:
        return {**base, "verdict": "NO_DATA",
                "reason": "belum ada data shadow (set agent.ab_shadow: true & kumpulkan trade)"}
    if not kept or not denied:
        return {**base, "verdict": "INSUFFICIENT",
                "reason": "butuh trade di KEDUA sisi (disetujui & ditolak ReAct)"}

    improvement = exp_b - exp_a
    p = permutation_pvalue(kept, denied)               # H0: kept ≤ denied
    significant = bool(improvement > 0 and p < alpha)
    return {**base, "improvement": round(improvement, 4),
            "p_value": round(p, 4), "significant": significant,
            "verdict": "REACT_ADDS_VALUE" if significant else "NOT_PROVEN",
            "reason": ("kept signifikan > denied (OOS)" if significant
                       else "ReAct tak terbukti memperbaiki exp_R")}


def report(path=decision_log.DECISION_LOG) -> dict:
    return analyze(collect(path))
