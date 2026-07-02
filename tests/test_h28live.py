"""H28 mikro-live: kill-switch (kedua pemicu + tidak salah tembak), seleksi kaki,
sizing min-notional, PnL basket."""
from bot import h28live as hl


def _t(*pnls):
    return [{"pnl_usd": float(x)} for x in pnls]


def test_kill_switch_drawdown():
    dead, why = hl.kill_switch(_t(-3, -3, -2), total_notional=50.0)   # DD $8 > $7.5
    assert dead and "drawdown" in why
    alive, _ = hl.kill_switch(_t(-3, -3, -1), total_notional=50.0)    # DD $7 < $7.5
    assert not alive


def test_kill_switch_consecutive_negatives():
    dead, why = hl.kill_switch(_t(5, -1, -1, -1, -1, -1, -1), total_notional=50.0)
    assert dead and "beruntun" in why
    alive, _ = hl.kill_switch(_t(-1, -1, -1, -1, -1, 0.5), total_notional=50.0)
    assert not alive


def test_kill_switch_no_false_positive_on_wins():
    assert not hl.kill_switch(_t(1, 2, -1, 3, -2, 4))[0]
    assert not hl.kill_switch([])[0]


def test_select_legs_and_insufficient():
    scores = {f"S{i}": float(i) for i in range(12)}
    longs, shorts = hl.select_legs(scores, n=5)
    assert longs == [f"S{i}" for i in range(7, 12)]
    assert shorts == [f"S{i}" for i in range(5)]
    assert hl.select_legs({f"S{i}": float(i) for i in range(9)}, n=5) == ([], [])


def test_leg_notional_respects_minimum_and_cap():
    assert hl.leg_notional() == 5.0                       # 50/10 = tepat minimum
    assert hl.leg_notional(n_legs=20, total=50.0) == 5.0  # dipaksa naik ke minimum


def test_basket_pnl():
    entry = {"A": 100.0, "B": 200.0, "C": 50.0}
    exit_ = {"A": 110.0, "B": 190.0, "C": 50.0}
    pnl = hl.basket_pnl_usd(entry, exit_, longs=["A"], shorts=["B", "C"], per_leg=5.0)
    assert abs(pnl - (0.10 * 5 + 0.05 * 5 + 0.0)) < 1e-9  # long A +10%, short B +5%
