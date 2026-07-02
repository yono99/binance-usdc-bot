"""H30 langkah 3 — simulator replay maker KONSERVATIF di atas aggTrades nyata.

Aturan (pra-registrasi; semua memihak LAWAN kita):
- Quote bid/ask simetris di sekitar referensi EMA-waktu (τ default 30s), di-refresh
  tiap `requote_ms`. Antara refresh, level DIAM (order resting).
- FILL hanya bila trade print MENEMBUS level (p < bid / p > ask, STRIKT) —
  menyentuh tidak dihitung (antrian di depan kita dianggap selalu penuh).
- Maks 1 fill per sisi per interval re-quote (ukuran 1 unit notional).
- Inventori dibatasi ±max_inv unit; sisi yang penuh berhenti quoting.
- Lot menua > max_hold_ms DIPAKSA unwind menyeberang spread: eksekusi di
  p×(1 ∓ unwind_cost_bps/1e4) — bayar half-spread efektif + fee taker (stress).
- FIFO: fill berlawanan menutup lot terlama dulu (realized), sisanya buka lot baru.

Output: realized bps per round-trip + statistik. Verdict di CLI (h30_sim.py):
varian PALING KONSERVATIF harus tetap positif di pair kandidat → PROCEED_TO_PAPER.
"""
from __future__ import annotations

from collections import deque
from math import exp

import numpy as np
import pandas as pd


def simulate(tr: pd.DataFrame, offset_bps: float, requote_ms: int = 5000,
             max_inv: int = 3, max_hold_ms: int = 300_000,
             unwind_cost_bps: float = 5.0, ref_tau_ms: float = 30_000.0) -> dict:
    ts = tr["ts"].to_numpy(dtype="int64")
    px = tr["price"].to_numpy(dtype=float)
    n = len(tr)
    if n < 1000:
        return {"n_trades": n, "round_trips": 0, "mean_bps": None}

    ref = px[0]
    last_q = ts[0]
    off = offset_bps / 1e4
    bid = ref * (1 - off)
    ask = ref * (1 + off)
    bid_live, ask_live = True, True
    longs: deque = deque()          # lot beli menunggu ditutup (price, ts)
    shorts: deque = deque()         # lot jual menunggu ditutup
    realized: list[float] = []      # bps per round-trip
    unwinds = 0

    for i in range(1, n):
        t, p = ts[i], px[i]
        # referensi EMA-waktu (causal: pakai trade sebelumnya dulu utk quote)
        dt = t - ts[i - 1]
        ref += (1 - exp(-dt / ref_tau_ms)) * (px[i - 1] - ref)
        if t - last_q >= requote_ms:                     # re-quote periodik
            bid, ask = ref * (1 - off), ref * (1 + off)
            bid_live, ask_live = True, True
            last_q = t

        # paksa unwind lot menua (menyeberang spread — konservatif)
        while longs and t - longs[0][1] > max_hold_ms:
            ep, _ = longs.popleft()
            xp = p * (1 - unwind_cost_bps / 1e4)
            realized.append((xp - ep) / ep * 1e4)
            unwinds += 1
        while shorts and t - shorts[0][1] > max_hold_ms:
            ep, _ = shorts.popleft()
            xp = p * (1 + unwind_cost_bps / 1e4)
            realized.append((ep - xp) / ep * 1e4)
            unwinds += 1

        inv = len(longs) - len(shorts)
        if bid_live and p < bid and inv < max_inv:       # TEMBUS bid → kita beli
            if shorts:                                    # tutup short terlama (FIFO)
                ep, _ = shorts.popleft()
                realized.append((ep - bid) / ep * 1e4)
            else:
                longs.append((bid, t))
            bid_live = False                              # 1 fill/sisi/interval
        if ask_live and p > ask and inv > -max_inv:      # TEMBUS ask → kita jual
            if longs:
                ep, _ = longs.popleft()
                realized.append((ask - ep) / ep * 1e4)
            else:
                shorts.append((ask, t))
            ask_live = False

    # sisa inventori di akhir: unwind paksa di harga terakhir (konservatif)
    for ep, _ in longs:
        realized.append((px[-1] * (1 - unwind_cost_bps / 1e4) - ep) / ep * 1e4)
        unwinds += 1
    for ep, _ in shorts:
        realized.append((ep - px[-1] * (1 + unwind_cost_bps / 1e4)) / ep * 1e4)
        unwinds += 1

    days = (ts[-1] - ts[0]) / 86400_000
    r = np.asarray(realized, dtype=float)
    return {"n_trades": n, "days": round(float(days), 1),
            "round_trips": int(len(r)), "unwinds": unwinds,
            "unwind_frac": round(unwinds / max(len(r), 1), 3),
            "mean_bps": round(float(r.mean()), 3) if len(r) else None,
            "rt_per_day": round(len(r) / max(days, 1e-9), 1),
            "bps_per_day": round(float(r.sum()) / max(days, 1e-9), 1),
            "win_rate": round(float((r > 0).mean()), 3) if len(r) else None}
