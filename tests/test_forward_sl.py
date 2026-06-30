"""Guardrail SL Gemini-trader: SL usulan AI WAJIB di sisi benar & DI DALAM likuidasi.
Helper murni (staticmethod) → diuji tanpa instansiasi ForwardTester / jaringan."""
from bot.forward import ForwardTester
from bot.settings_store import liquidation_price

_valid = ForwardTester._valid_entry_sl


def test_long_sl_must_be_below_entry():
    liq = liquidation_price(100.0, True, 0.05)        # likuidasi long di 95.0
    assert _valid(True, 100.0, 102.0, liq) is None    # SL di atas entry → tolak
    assert _valid(True, 100.0, 100.0, liq) is None    # SL = entry → tolak


def test_short_sl_must_be_above_entry():
    liq = liquidation_price(100.0, False, 0.05)       # likuidasi short di 105.0
    assert _valid(False, 100.0, 98.0, liq) is None
    assert _valid(False, 100.0, 100.0, liq) is None


def test_long_sl_clamped_inside_liquidation():
    liq = liquidation_price(100.0, True, 0.05)        # 95.0
    # SL Gemini lebih jauh dari likuidasi (94 < 95) → di-clamp NAIK ke dalam likuidasi
    out = _valid(True, 100.0, 94.0, liq)
    assert out is not None and out > liq and out < 100.0


def test_short_sl_clamped_inside_liquidation():
    liq = liquidation_price(100.0, False, 0.05)       # 105.0
    out = _valid(False, 100.0, 106.0, liq)            # 106 > 105 → clamp TURUN
    assert out is not None and out < liq and out > 100.0


def test_valid_sl_passes_unchanged():
    liq = liquidation_price(100.0, True, 0.05)        # 95.0
    assert _valid(True, 100.0, 97.0, liq) == 97.0     # di dalam likuidasi → dipakai apa adanya


def test_invalid_input_rejected():
    liq = liquidation_price(100.0, True, 0.05)
    assert _valid(True, 100.0, None, liq) is None
    assert _valid(True, 100.0, "x", liq) is None


def test_no_room_when_leverage_too_high():
    # likuidasi sangat dekat entry (lev ekstrem) → tak ada ruang SL valid
    liq = liquidation_price(100.0, True, 0.0002)      # ~99.98, lebih dekat dari buffer
    assert _valid(True, 100.0, 99.0, liq) is None
