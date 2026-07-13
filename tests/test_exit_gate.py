"""Hard gate + kill-switch gemini_exit (fix_exit_gemini.md).

Lapis 1 — progress gate: Gemini exit-review HANYA dipanggil bila posisi PERNAH
mencapai >=50% ke TP DAN reversal signifikan >=15pp dari puncak.

Lapis 2 — kill-switch empiris: gemini_exit DIBLOKIR bila exit_track_record
menunjukkan n>=10 dan exp_r<0 (terbukti -0.253 dari data live).

5 test case sesuai spesifikasi fix_exit_gemini.md."""
import types
from unittest.mock import patch

import pandas as pd

from bot.forward import ForwardTester


def _mock_gtrader(manage_called: list, manage_action: dict | None = None) -> object:
    """Buat mock gtrader dengan manage yang catat pemanggilan."""
    def _manage(ctx):
        manage_called.append(True)
        return manage_action or {"action": "tighten_stop"}
    return types.SimpleNamespace(manage=_manage)


def _run_lapis1(
    peak_tp_prog: float,
    price: float,
    entry: float = 1.0,
    tp: float = 1.05,
    sl: float = 0.98,
    held_s: int = 600,
    min_hold: int = 300,
) -> bool:
    """Return True bila Gemini manage DIPANGGIL (lolos Lapis 1)."""
    manage_called = []
    ft = ForwardTester.__new__(ForwardTester)
    ft._min_hold_s = min_hold
    ft.gtrader = _mock_gtrader(manage_called)
    ft.cfg = {}
    ft.balance_usdc = 100.0
    ft.balance_usdt = 0.0
    ft.max_open = 6
    ft.live = False
    ft.ex = types.SimpleNamespace(ticker=lambda _s: {"last": price})
    opened = (pd.Timestamp.utcnow() - pd.Timedelta(seconds=held_s)).isoformat()
    ft.open = {
        "X/USDT:USDT": {
            "gdecision": 1,
            "opened_ts": opened,
            "side": "long",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "peak_tp_prog": peak_tp_prog,
        }
    }
    ft._gemini_manage("X/USDT:USDT", pd.DataFrame())
    return len(manage_called) > 0


def _prog(price: float, entry: float = 1.0, tp: float = 1.05) -> float:
    """Hitung progress ke TP (sama dgn ForwardTester._tp_progress)."""
    dist = abs(tp - entry) or 1e-9
    return (price - entry) / dist


# ── Lapis 1 tests ──

def test_lapis1_peak_below_threshold():
    """peak_tp_prog < 0.5 → Gemini TIDAK dipanggil."""
    peak = 0.3
    # prog = 0.6 (price=1.03) → peak-prog=-0.3, tetap skip krn peak<0.5
    assert _run_lapis1(peak_tp_prog=peak, price=1.03) is False


def test_lapis1_no_reversal():
    """peak_tp_prog >= 0.5 tapi reversal < 15pp → Gemini TIDAK dipanggil."""
    peak = 0.7
    # prog=0.6 (price=1.03) → reversal = 0.7-0.6 = 0.1 < 0.15 → skip
    assert _run_lapis1(peak_tp_prog=peak, price=1.03) is False


def test_lapis1_reversal_signifikan():
    """peak_tp_prog >= 0.5 DAN reversal >= 15pp → Gemini DIPANGGIL."""
    peak = 0.7
    with patch("bot.gemini_trader._market_summary", return_value={"price": 1.0}):
        assert _run_lapis1(peak_tp_prog=peak, price=1.01) is True


def test_lapis1_no_tp_returns_early():
    """Bila tp/entry None (prog=None) → gate skip total, return tanpa manage."""
    manage_called = []
    ft = ForwardTester.__new__(ForwardTester)
    ft._min_hold_s = 0
    ft.gtrader = _mock_gtrader(manage_called)
    ft.ex = types.SimpleNamespace(ticker=lambda _s: 1.0)
    ft.open = {
        "X/USDT:USDT": {
            "gdecision": 1,
            "side": "long",
            "entry": 1.0,
        }
    }
    ft._gemini_manage("X/USDT:USDT", pd.DataFrame())
    assert len(manage_called) == 0


# ── Lapis 2 tests ──

def _run_lapis2(
    exit_record: list | None = None,
    action: str = "exit",
) -> bool:
    """Return True bila _close_usd dipanggil (exit dieksekusi)."""
    closed = {"called": False}

    def _mock_close(_sym, _price, _reason):
        closed["called"] = True

    ft = ForwardTester.__new__(ForwardTester)
    ft.gtrader = types.SimpleNamespace(
        _exit_track_record=lambda: exit_record or []
    )
    ft._close_usd = _mock_close
    ft.open = {"X/USDT:USDT": {"side": "long", "sl": 0.98, "entry": 1.0, "tp": 1.05}}
    ft.live = False

    ft._apply_manage("X/USDT:USDT", {"action": action, "reason": "test"}, 1.0)
    return closed["called"]


def test_lapis2_blokir_exit_saat_ev_buruk():
    """exit_track_record gemini_exit n>=10, exp_r<0 → exit DIBLOKIR."""
    rec = [{"reason": "gemini_exit", "n": 15, "exp_r": -0.253, "sum_r": -2.785}]
    assert _run_lapis2(exit_record=rec) is False


def test_lapis2_izinkan_exit_saat_n_kecil():
    """gemini_exit n<10 → exit tetap jalan (belum cukup sampel)."""
    rec = [{"reason": "gemini_exit", "n": 5, "exp_r": -0.5, "sum_r": -2.5}]
    assert _run_lapis2(exit_record=rec) is True


def test_lapis2_izinkan_exit_saat_ev_positif():
    """gemini_exit exp_r>=0 → exit jalan (edge sudah membaik)."""
    rec = [{"reason": "gemini_exit", "n": 20, "exp_r": 0.1, "sum_r": 2.0}]
    assert _run_lapis2(exit_record=rec) is True


def test_lapis2_izinkan_tighten_bukan_exit():
    """Action tighten_stop (bukan exit) → tidak kena Lapis 2, tetap jalan tanpa error."""
    ft = ForwardTester.__new__(ForwardTester)
    ft.gtrader = types.SimpleNamespace(
        _exit_track_record=lambda: [{"reason": "gemini_exit", "n": 15, "exp_r": -0.253}]
    )
    ft.open = {"X/USDT:USDT": {"side": "long", "sl": 0.98, "entry": 1.0, "tp": 1.05}}
    ft.live = False
    # tighten_stop tanpa new_sl → valid_tighten return False, tidak crash
    ft._apply_manage("X/USDT:USDT", {"action": "tighten_stop", "reason": "test"}, 1.0)
    # Tidak crash = pass


def test_lapis2_tak_ada_exit_record():
    """exit_track_record kosong → exit jalan normal."""
    assert _run_lapis2(exit_record=[]) is True
