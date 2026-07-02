"""MTF agreement gate (Phase 3, shadow) — direction read, evaluate, report split."""
import numpy as np
import pandas as pd

from bot import mtf

CFG = {"signals": {"ema_fast": 9, "ema_slow": 50}, "mtf": {"mode": "shadow", "mults": [8, 16]}}


def _df(trend: float, n: int = 4000) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    close = 100 + trend * np.arange(n)
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": 1.0}, index=idx)


def test_direction_up_and_down():
    up = _df(0.05)
    assert mtf.htf_direction(up, "15m", 8, CFG) == 1
    down = _df(-0.05)
    assert mtf.htf_direction(down, "15m", 8, CFG) == -1


def test_direction_zero_when_insufficient_data():
    assert mtf.htf_direction(_df(0.05, n=10), "15m", 16, CFG) == 0


def test_evaluate_agree_vs_opposed():
    g = mtf.MTFAgree(CFG)
    up = _df(0.05)
    r_long = g.evaluate(up, "15m", 1)               # HTF naik, trade long → setuju
    assert r_long["mtf_agree"] and r_long["mtf_opposed"] == 0
    r_short = g.evaluate(up, "15m", -1)             # HTF naik, trade short → menentang
    assert not r_short["mtf_agree"] and r_short["mtf_opposed"] == 2


def test_stamp_empty_when_off():
    g = mtf.MTFAgree({"signals": CFG["signals"], "mtf": {"mode": "off"}})
    assert g.stamp(_df(0.05), "15m", 1) == {}


# ---------- report: split agree/disagree + Brier ----------

def _rows():
    # agree: 3 menang dari 4 (win_rate 75), disagree: 1 dari 3
    return (
        [{"mtf_agree": True, "win": 1, "r": 1.0, "conviction": 0.8, "mode": "dry"}] * 3 +
        [{"mtf_agree": True, "win": 0, "r": -1.0, "conviction": 0.8, "mode": "dry"}] +
        [{"mtf_agree": False, "win": 1, "r": 1.0, "conviction": 0.8, "mode": "dry"}] +
        [{"mtf_agree": False, "win": 0, "r": -1.0, "conviction": 0.8, "mode": "dry"}] * 2
    )


def test_analyze_splits_and_scores():
    res = mtf.analyze(_rows())
    assert res["agree"]["n"] == 4 and res["agree"]["win_rate"] == 75.0
    assert res["disagree"]["n"] == 3 and round(res["disagree"]["win_rate"], 1) == 33.3
    assert res["agree"]["brier"] is not None       # ada conviction → Brier terhitung


def test_report_insufficient_until_sample(tmp_path):
    p = tmp_path / "mtf.jsonl"
    import json
    p.write_text("\n".join(json.dumps(r) for r in _rows()), encoding="utf-8")
    res = mtf.report("dry", sample=100, path=p)
    assert res["verdict"] == "INSUFFICIENT"
    res2 = mtf.report("dry", sample=5, path=p)     # cukup sampel → ada verdict nyata
    assert res2["verdict"] in ("MTF_AGREEMENT_HELPS", "NOT_PROVEN")
