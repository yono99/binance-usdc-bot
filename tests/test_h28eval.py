"""Evaluator H28: gerbang PREVIEW, verdict LOLOS/GAGAL sesuai pra-registrasi."""
import numpy as np

from bot import h28eval


def _rows(pnls):
    return [{"pnl_net": float(x)} for x in pnls]


def test_preview_before_min_cycles():
    ev = h28eval.evaluate(_rows([0.01] * 14))
    assert ev["status"] == "PREVIEW" and ev["verdict"] is None
    assert ev["progress"] == "14/15"


def test_pass_when_positive_and_significant():
    rng = np.random.default_rng(1)
    ev = h28eval.evaluate(_rows(rng.normal(0.02, 0.01, 20)))
    assert ev["status"] == "FINAL" and ev["verdict"] == "LOLOS_TAHAP_1"
    assert ev["p_value"] < 0.05


def test_fail_when_noise_or_negative():
    rng = np.random.default_rng(2)
    assert h28eval.evaluate(_rows(rng.normal(0.0, 0.02, 20)))["verdict"] == "GAGAL"
    assert h28eval.evaluate(_rows(rng.normal(-0.01, 0.01, 20)))["verdict"] == "GAGAL"
