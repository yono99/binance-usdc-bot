"""H28 MIKRO-LIVE — logika keputusan (murni, teruji) untuk daemon h28_live.py.

Addendum pemilik 2026-07-02 (RESEARCH_HYPOTHESES_PHASE4.md): override sadar atas
urutan Tahap 1→2. Basket diciutkan 5+5 kaki, total ≤ $50, dan KILL-SWITCH
PRA-REGISTRASI yang TIDAK BISA DINEGOSIASI:
- drawdown kumulatif > 15% dari total notional  → MATI PERMANEN
- 6 siklus berturut-turut negatif               → MATI PERMANEN
State "dead" disimpan; daemon menolak hidup lagi walau di-restart.

Paper-test Tahap 1 tetap berjalan paralel — dialah hakim ilmiahnya. Jalur ini
hanya membeli data slippage nyata lebih awal, dengan uang saku.
"""
from __future__ import annotations

import numpy as np

KILL_DD_FRAC = 0.15          # DD kumulatif > 15% notional → mati permanen
KILL_CONSEC_NEG = 6          # 6 siklus negatif beruntun → mati permanen
LEGS = 5                     # 5 long + 5 short
TOTAL_NOTIONAL = 50.0        # plafon total ($)
MIN_NOTIONAL = 5.0           # minimum order Binance futures per kaki ($)


def kill_switch(trades: list[dict], total_notional: float = TOTAL_NOTIONAL) -> tuple[bool, str]:
    """PURE. trades = [{'pnl_usd': float}, ...] kronologis. (dead, alasan)."""
    if not trades:
        return False, ""
    pnl = np.asarray([float(t["pnl_usd"]) for t in trades], dtype=float)
    cum = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    dd = float(np.max(peak - cum))
    if dd > KILL_DD_FRAC * total_notional:
        return True, (f"KILL-SWITCH: drawdown ${dd:.2f} > "
                      f"{KILL_DD_FRAC:.0%} × ${total_notional:.0f}")
    tail = pnl[-KILL_CONSEC_NEG:]
    if len(tail) >= KILL_CONSEC_NEG and bool((tail < 0).all()):
        return True, f"KILL-SWITCH: {KILL_CONSEC_NEG} siklus negatif beruntun"
    return False, ""


def select_legs(scores: dict[str, float], n: int = LEGS) -> tuple[list[str], list[str]]:
    """PURE. Skor tinggi = LONG (ivol rendah), skor rendah = SHORT.
    Kembalikan (longs, shorts); kosong bila kandidat < 2n."""
    valid = [(s, v) for s, v in scores.items() if np.isfinite(v)]
    if len(valid) < 2 * n:
        return [], []
    order = sorted(valid, key=lambda kv: kv[1])
    return [s for s, _ in order[-n:]], [s for s, _ in order[:n]]


def leg_notional(n_legs: int = 2 * LEGS, total: float = TOTAL_NOTIONAL,
                 min_notional: float = MIN_NOTIONAL) -> float:
    """Notional per kaki: total dibagi rata, dipaksa ≥ minimum exchange.
    (10 kaki × $5 = tepat plafon $50.)"""
    return max(total / n_legs, min_notional)


def basket_pnl_usd(entry: dict[str, float], exit_: dict[str, float],
                   longs: list[str], shorts: list[str], per_leg: float) -> float:
    """PURE. PnL USD basket dari harga fill entry/exit per simbol (kaki yang
    hilang harganya dilewati — konservatif: dianggap 0)."""
    pnl = 0.0
    for s in longs:
        if s in entry and s in exit_ and entry[s] > 0:
            pnl += (exit_[s] / entry[s] - 1.0) * per_leg
    for s in shorts:
        if s in entry and s in exit_ and entry[s] > 0:
            pnl += (1.0 - exit_[s] / entry[s]) * per_leg
    return float(pnl)
