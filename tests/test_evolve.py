"""Phase 4 — evolusi threshold: HANYA terapkan bila perbaikan OOS signifikan (p<0.05)."""
import json

from bot import decision_log as dl
from bot import evolve


def _trades_separable(n_pairs=20):
    """Interleave winner(score 0.8,+1R) & loser(score 0.55,-1R) → threshold ketat menolong."""
    out = []
    for _ in range(n_pairs):
        out.append((0.8, 1.0))
        out.append((0.55, -1.0))
    return out                       # 2*n_pairs trade


def _trades_noise(n=40):
    """Skor sama (0.6), outcome selang-seling → tak ada threshold yang menolong."""
    return [(0.6, 1.0 if i % 2 else -1.0) for i in range(n)]


# ---------- gating ----------

def test_not_enough_total():
    ev = evolve.evaluate([(0.6, 1.0)] * 10, 0.5)
    assert ev["applied"] is False and "20" in ev["reason"]


def test_test_set_too_small():
    # 24 trade → test = 7 (<10) → tak valid OOS
    ev = evolve.evaluate([(0.6, 0.1)] * 24, 0.5)
    assert ev["applied"] is False and ev["n_test"] < evolve.MIN_TEST


# ---------- inti: hanya terapkan bila OOS signifikan ----------

def test_applies_when_oos_significant():
    ev = evolve.evaluate(_trades_separable(20), current_threshold=0.5)
    assert ev["applied"] is True and ev["significant"] is True
    assert ev["new"] > ev["old"]                    # hanya mengetatkan
    assert ev["improvement"] > 0 and ev["p_value"] < 0.05


def test_no_apply_when_noise():
    ev = evolve.evaluate(_trades_noise(40), current_threshold=0.5)
    assert ev["applied"] is False                   # tak ada edge OOS → jangan ubah


# ---------- permutation test ----------

def test_permutation_separated_is_significant():
    p = evolve.permutation_pvalue([1.0] * 8, [-1.0] * 8)
    assert p < 0.05


def test_permutation_overlapping_not_significant():
    p = evolve.permutation_pvalue([0.1, -0.1, 0.2, -0.2], [0.0, 0.1, -0.1, 0.05])
    assert p > 0.05


# ---------- collect + run (I/O) ----------

def _row(score, r, action="ENTER_LONG", outcome="TP_HIT"):
    return {"id": "x", "action": action, "signal_scores": {"long": score, "short": 0.1},
            "outcome": outcome, "outcome_r": r}


def test_collect_filters_open_and_non_enter(tmp_path):
    p = tmp_path / "d.jsonl"
    dl.append(_row(0.8, 1.0), path=p)
    dl.append({**_row(0.7, None), "outcome": None, "outcome_r": None}, path=p)   # belum tutup
    dl.append(_row(0.6, -1.0, action="SKIP"), path=p)                            # bukan ENTER
    trades = evolve.collect_trades(p)
    assert trades == [(0.8, 1.0)]


def test_run_applies_and_logs(tmp_path):
    p = tmp_path / "d.jsonl"
    for score, r in _trades_separable(20):
        dl.append(_row(score, r, action=("ENTER_LONG" if score > 0.6 else "ENTER_LONG")), path=p)
    cfg = {"signals": {"entry_confidence": 0.5}}
    evo_path = tmp_path / "evolution_log.jsonl"
    ev = evolve.run(cfg, decision_path=p, evo_path=evo_path, apply=True)
    assert ev["applied"] is True
    assert cfg["signals"]["entry_confidence"] == ev["new"]          # diterapkan in-memory
    logged = [json.loads(l) for l in evo_path.read_text(encoding="utf-8").splitlines()]
    assert logged and logged[-1]["applied"] is True
