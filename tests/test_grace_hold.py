"""Grace period anti-whipsaw: manajer Gemini (_gemini_manage) TAK boleh exit sebelum
posisi ditahan >= _min_hold_s. Bukti lapangan: gemini_exit dini memotong posisi yg lalu
PULIH di atas entry. SL/TP di jalur lain tetap jaga selama grace (tak diuji di sini).

Deteksi "lolos guard" = apakah ex.ticker tersentuh (guard return SEBELUM ticker)."""
import types

import pandas as pd

from bot.forward import ForwardTester


def _run(held_s: int, min_hold: int = 300) -> bool:
    """Return True bila _gemini_manage MELEWATI grace (menyentuh ticker)."""
    reached = {"ticker": False}

    def tk(_s):
        reached["ticker"] = True
        raise RuntimeError("stop di sini")   # ditangkap boundary → return bersih; cukup rekam flag

    ft = ForwardTester.__new__(ForwardTester)
    ft._min_hold_s = min_hold
    ft.gtrader = object()                        # cukup non-None (guard: gdecision + gtrader)
    ft.ex = types.SimpleNamespace(ticker=tk)
    opened = (pd.Timestamp.utcnow() - pd.Timedelta(seconds=held_s)).isoformat()
    ft.open = {"X/USDT:USDT": {"gdecision": 1, "opened_ts": opened, "side": "long",
                               "entry": 1.0, "sl": 0.98, "tp": 1.05}}
    ft._gemini_manage("X/USDT:USDT", pd.DataFrame())
    return reached["ticker"]


def test_grace_blokir_exit_saat_posisi_masih_baru():
    assert _run(held_s=10) is False          # baru 10s < grace 300 → guard return sebelum ticker


def test_grace_lolos_setelah_min_hold():
    assert _run(held_s=600) is True           # 600s > 300 → lanjut kelola


def test_grace_nonaktif_saat_nol():
    assert _run(held_s=1, min_hold=0) is True  # grace 0 = mati → langsung kelola walau baru
