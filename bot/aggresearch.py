"""H30 langkah 1-2 di FILL MAKER NYATA (aggTrades Binance Vision).

Lebih kuat dari snapshot L2: setiap baris aggTrades adalah eksekusi sungguhan.
- is_buyer_maker=True  → seseorang terisi sebagai MAKER-BUY di bid (print bid).
- is_buyer_maker=False → maker-SELL terisi di ask (print ask).

Metrik per simbol:
- effective spread : per menit yang punya print DUA sisi: (mean_ask − mean_bid)/mid.
- adverse selection: untuk tiap fill maker, harga referensi = MEDIAN harga trade
  pada [t+30s, t+90s]; adverse = pergerakan MELAWAN posisi maker (beli di bid →
  harga lanjut turun = adverse positif). Dua sisi digabung.
- edge kotor       = eff_spread_med/2 − adverse_mean  (bps).

Verdict memakai gerbang pra-registrasi yang sama (l2research.verdict):
≥28 hari data → verdict nyata; edge terbaik < 3 bps → H30 mati (bunuh-cepat).
Catatan jujur yang MELEKAT pada hasil: fill orang lain ≠ fill kita (posisi
antrian tak terukur) → angka ini BATAS ATAS peluang maker; kalau batas atasnya
saja mati, H30 mati; kalau hidup, lanjut simulasi konservatif (langkah 3).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def effective_spread_bps(tr: pd.DataFrame) -> pd.Series:
    """Spread efektif per MENIT (hanya menit dgn print bid & ask). Series bps."""
    minute = tr["ts"] // 60_000
    px, side = tr["price"], tr["is_buyer_maker"]
    g = pd.DataFrame({"m": minute, "px": px, "bid_side": side})
    bid = g[g["bid_side"]].groupby("m")["px"].mean()
    ask = g[~g["bid_side"]].groupby("m")["px"].mean()
    both = pd.concat([bid.rename("b"), ask.rename("a")], axis=1).dropna()
    mid = (both["a"] + both["b"]) / 2
    sp = (both["a"] - both["b"]) / mid * 1e4
    return sp[sp > 0]                                   # menit ber-noise negatif dibuang


def adverse_bps(tr: pd.DataFrame, ref_lo_ms: int = 30_000, ref_hi_ms: int = 90_000,
                max_fills: int = 200_000) -> tuple[float | None, int]:
    """Adverse selection rata-rata (bps) atas fill maker nyata, dua sisi.
    Referensi masa depan = median harga trade di [t+lo, t+hi]. (mean, n)."""
    ts = tr["ts"].to_numpy(dtype="int64")
    px = tr["price"].to_numpy(dtype=float)
    bid_side = tr["is_buyer_maker"].to_numpy(dtype=bool)
    n = len(tr)
    if n < 1000:
        return None, 0
    idx = np.arange(n)
    if n > max_fills:                                    # sampling merata (hemat memori)
        idx = idx[:: n // max_fills + 1]
    lo = np.searchsorted(ts, ts[idx] + ref_lo_ms, side="left")
    hi = np.searchsorted(ts, ts[idx] + ref_hi_ms, side="right")
    out = []
    for i, a, b in zip(idx, lo, hi):
        if b - a < 3:                                    # butuh ≥3 trade utk median stabil
            continue
        ref = float(np.median(px[a:b]))
        f = px[i]
        adv = (f - ref) / f * 1e4 if bid_side[i] else (ref - f) / f * 1e4
        out.append(adv)
    if not out:
        return None, 0
    return float(np.mean(out)), len(out)


def analyze_trades(tr: pd.DataFrame) -> dict:
    days = float((tr["ts"].iloc[-1] - tr["ts"].iloc[0]) / 86400_000) if len(tr) > 1 else 0.0
    sp = effective_spread_bps(tr)
    adv, n_f = adverse_bps(tr)
    spread_med = round(float(sp.median()), 3) if len(sp) else None
    edge = (round(spread_med / 2 - adv, 3)
            if spread_med is not None and adv is not None else None)
    fills_h = round(len(tr) / max(days * 24, 1e-9), 1)
    return {"days": round(days, 1), "trades": int(len(tr)),
            "spread_med_bps": spread_med, "fill_rate_per_hour": fills_h,
            "adverse_bps": round(adv, 3) if adv is not None else None,
            "n_adverse_samples": n_f, "edge_gross_bps": edge,
            "half_life_snaps": None, "fills": int(len(tr))}
