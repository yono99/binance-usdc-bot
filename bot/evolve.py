"""Phase 4 — evolusi threshold dengan VALIDASI OOS (yang tak dimiliki Meridian).

Prinsip walk-forward DITERAPKAN ke performa LIVE (bukan data backtest):
  1. Kumpulkan trade tertutup dari decision_log (skor sinyal + outcome R).
  2. Split kronologis: train (70% tertua) / test (30% terbaru).
  3. Dari TRAIN: cari threshold entry_confidence yang memaksimalkan exp_R.
  4. Validasi di TEST (OOS): apakah threshold usulan menaikkan exp_R?
  5. Terapkan HANYA bila perbaikan OOS signifikan (permutation test p < 0.05).
  6. Catat tiap event ke logs/evolution_log.jsonl (metrik before/after).
  7. JANGAN terapkan bila test n_trades < 10 atau total < 20.

Keterbatasan jujur: kita hanya punya outcome untuk trade yang BENAR diambil → evolusi
hanya bisa MENGETATKAN threshold (memfilter), tak bisa mengevaluasi pelonggaran.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import decision_log
from .logger import log

EVOLUTION_LOG = Path("logs/evolution_log.jsonl")
MIN_TOTAL = 20      # butuh ≥20 trade tertutup sebelum evolusi
MIN_TEST = 10       # OOS tak valid bila test < 10
MIN_KEEP_FRAC = 0.3  # jangan over-filter: pertahankan ≥30% trade train


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_trades(path: Path | str = decision_log.DECISION_LOG) -> list[tuple[float, float]]:
    """(score_arah, outcome_r) untuk tiap ENTER tertutup, urut kronologis."""
    out: list[tuple[float, float]] = []
    for row in decision_log.read_all(path):
        action = str(row.get("action", ""))
        if row.get("outcome_r") is None or not action.startswith("ENTER"):
            continue
        ss = row.get("signal_scores") or {}
        score = ss.get("long") if action == "ENTER_LONG" else ss.get("short")
        if score is None:
            continue
        try:
            out.append((float(score), float(row["outcome_r"])))
        except (TypeError, ValueError):
            continue
    return out


def permutation_pvalue(a, b, n_perm: int = 5000, seed: int = 12345) -> float:
    """p satu-sisi H0: mean(a) ≤ mean(b). Dependency-free, deterministik (seed tetap)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.size == 0 or b.size == 0:
        return 1.0
    obs = a.mean() - b.mean()
    if obs <= 0:
        return 1.0
    pool = np.concatenate([a, b])
    na = a.size
    rng = np.random.default_rng(seed)
    cnt = 0
    for _ in range(n_perm):
        rng.shuffle(pool)
        if (pool[:na].mean() - pool[na:].mean()) >= obs:
            cnt += 1
    return (cnt + 1) / (n_perm + 1)


def evaluate(trades: list[tuple[float, float]], current_threshold: float,
             *, alpha: float = 0.05) -> dict:
    """PURE: usulkan & validasi threshold baru. Tak menyentuh file/cfg → mudah diuji."""
    n = len(trades)
    base = {"ts": _utcnow(), "param": "entry_confidence", "old": round(float(current_threshold), 4),
            "new": None, "n_total": n, "applied": False, "significant": False}
    if n < MIN_TOTAL:
        return {**base, "reason": f"total {n} < {MIN_TOTAL}"}
    split = int(n * 0.7)
    train, test = trades[:split], trades[split:]
    if len(test) < MIN_TEST:
        return {**base, "n_train": len(train), "n_test": len(test),
                "reason": f"test {len(test)} < {MIN_TEST}"}

    # --- TRAIN: cari threshold yang memaksimalkan exp_R (hanya MENGETATKAN) ---
    min_keep = max(5, int(MIN_KEEP_FRAC * len(train)))
    best_tau, best_exp = None, None
    for tau in sorted({round(s, 3) for s, _ in train}):
        if tau <= current_threshold:
            continue                                   # hanya pertimbangkan pengetatan
        kept = [r for s, r in train if s >= tau]
        if len(kept) < min_keep:
            continue
        exp = float(np.mean(kept))
        if best_exp is None or exp > best_exp:
            best_tau, best_exp = tau, exp
    if best_tau is None:
        return {**base, "n_train": len(train), "n_test": len(test),
                "reason": "tak ada threshold lebih ketat yang membaik di train"}

    # --- TEST (OOS): bandingkan kept vs dropped ---
    base_r = [r for _, r in test]
    kept_r = [r for s, r in test if s >= best_tau]
    drop_r = [r for s, r in test if s < best_tau]
    base_exp = float(np.mean(base_r))
    prop_exp = float(np.mean(kept_r)) if kept_r else float("-inf")
    improvement = prop_exp - base_exp
    p = permutation_pvalue(kept_r, drop_r)
    significant = bool(len(kept_r) >= 5 and improvement > 0 and p < alpha)

    return {**base, "new": round(float(best_tau), 4), "n_train": len(train), "n_test": len(test),
            "train_exp_r_kept": round(best_exp, 4),
            "test_exp_r_baseline": round(base_exp, 4),
            "test_exp_r_proposed": round(prop_exp, 4) if kept_r else None,
            "improvement": round(improvement, 4) if kept_r else None,
            "n_test_kept": len(kept_r), "n_test_dropped": len(drop_r),
            "p_value": round(p, 4), "significant": significant, "applied": significant,
            "reason": "OOS signifikan" if significant else "OOS tak signifikan"}


def run(cfg: dict, *, decision_path: Path | str = decision_log.DECISION_LOG,
        evo_path: Path | str = EVOLUTION_LOG, apply: bool = True) -> dict:
    """Jalankan satu siklus evolusi: baca log → evaluasi → (opsional) terapkan in-memory → catat."""
    cur = float(cfg["signals"]["entry_confidence"])
    event = evaluate(collect_trades(decision_path), cur)
    if event.get("applied") and apply:
        cfg["signals"]["entry_confidence"] = event["new"]   # terapkan ke cfg HIDUP (bukan file)
        log.info(f"EVOLUTION: entry_confidence {cur} → {event['new']} "
                 f"(OOS +{event['improvement']}R, p={event['p_value']})")
    # Catat HANYA event yang punya kesimpulan OOS (ada n_test) — hindari spam 'belum cukup data'.
    if "n_test" in event:
        try:
            Path(evo_path).parent.mkdir(parents=True, exist_ok=True)
            with Path(evo_path).open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str) + "\n")
        except Exception as e:  # boundary
            log.warning(f"tulis evolution_log gagal: {e}")
    return event
