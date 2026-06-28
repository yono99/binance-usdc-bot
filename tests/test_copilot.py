"""Gerbang verdict deterministik — KOMPONEN KESELAMATAN.

CANDIDATE kini wajib lolos: konsistensi (exp>0.05, n≥30, ≥3 window, ≥3 simbol)
DAN signifikansi statistik (bootstrap Bonferroni + effective-n). Gemini tak boleh
melonggarkannya.
"""
import numpy as np

from bot.copilot import CycleResult, verdict


def _wins(values, params="p1/1.5/2.5"):
    # params stabil (sama di tiap window) kecuali diberi list → untuk uji stabilitas
    if isinstance(params, list):
        return [{"oos_exp": v, "oos_n": 40, "params": params[i % len(params)]}
                for i, v in enumerate(values)]
    return [{"oos_exp": v, "oos_n": 40, "params": params} for v in values]


def _strong_per_symbol():
    return {s: _wins([0.1, 0.08, 0.07]) for s in ("BTC", "ETH", "SOL")}


def _signif_rs(n=90, mean=0.2, std=0.5, seed=1):
    return list(np.random.default_rng(seed).normal(mean, std, n))


def _cycle(per_symbol, exp, n, oos_r=None, trials=1):
    return CycleResult(strategy="t", hypothesis="t", per_symbol=per_symbol,
                       aggregate={"expectancy_r": exp, "trades": n},
                       oos_r=oos_r or [], trials=trials)


def test_rejected_when_negative():
    c = _cycle({"BTC": _wins([-0.1, 0.2]), "ETH": _wins([-0.3])}, exp=-0.12, n=200)
    assert verdict(c)[0] == "REJECTED"


def test_weak_when_thin_positive():
    c = _cycle({"BTC": _wins([0.2, 0.2, 0.2]), "ETH": _wins([-0.1])}, exp=0.03, n=200)
    assert verdict(c)[0] == "WEAK"


def test_candidate_requires_significance():
    # konsistensi lolos + oos_r signifikan + trials kecil → CANDIDATE
    c = _cycle(_strong_per_symbol(), exp=0.2, n=90, oos_r=_signif_rs(), trials=1)
    label, reason = verdict(c)
    assert label == "CANDIDATE"
    assert "BUKAN live" in reason          # tetap tak menyuruh live

def test_consistent_but_no_oos_r_is_weak():
    # lolos konsistensi tapi tak ada data R untuk uji signifikansi → JANGAN CANDIDATE
    c = _cycle(_strong_per_symbol(), exp=0.2, n=90, oos_r=[], trials=1)
    assert verdict(c)[0] == "WEAK"


def test_consistent_but_insignificant_is_weak():
    # exp>0.05 & konsisten TAPI R sangat berisik → bootstrap tak signifikan → WEAK
    noisy = list(np.random.default_rng(2).normal(0.06, 2.0, 40))
    c = _cycle(_strong_per_symbol(), exp=0.06, n=40, oos_r=noisy, trials=1)
    assert verdict(c)[0] == "WEAK"


def test_bonferroni_blocks_many_trials():
    # R yang sama signifikan pada 1 trial, TAPI dengan ribuan trial → p_adj gagal → WEAK
    rs = _signif_rs()
    assert verdict(_cycle(_strong_per_symbol(), 0.2, 90, oos_r=rs, trials=1))[0] == "CANDIDATE"
    assert verdict(_cycle(_strong_per_symbol(), 0.2, 90, oos_r=rs, trials=100000))[0] == "WEAK"


def test_candidate_blocked_by_low_trade_count():
    c = _cycle(_strong_per_symbol(), exp=0.2, n=20, oos_r=_signif_rs(n=20), trials=1)
    assert verdict(c)[0] != "CANDIDATE"


def test_candidate_blocked_by_unstable_params():
    # signifikan & konsisten TAPI parameter beda tiap window → WEAK (overfit)
    per = {s: _wins([0.1, 0.08, 0.07], params=["a/1/2", "b/2/3", "c/1.5/2.5"])
           for s in ("BTC", "ETH", "SOL")}
    c = _cycle(per, exp=0.2, n=90, oos_r=_signif_rs(), trials=1)
    assert verdict(c)[0] == "WEAK"


def test_candidate_blocked_by_two_symbols_only():
    per = {s: _wins([0.1, 0.08, 0.07]) for s in ("BTC", "ETH")}
    per["SOL"] = _wins([-0.2, -0.1, -0.3])
    c = _cycle(per, exp=0.2, n=90, oos_r=_signif_rs(), trials=1)
    assert verdict(c)[0] != "CANDIDATE"
