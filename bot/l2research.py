"""Pipeline analisis H30 (spread capture) — SIAP-JALAN, menunggu data L2 matang.

Langkah pra-registrasi (RESEARCH_HYPOTHESES_PHASE4.md §B):
1. UKUR: distribusi spread, half-life spread, frekuensi fill-proxy, adverse move.
2. BUNUH-CEPAT: edge kotor = spread/2 − adverse; bila pair TERBAIK < 3 bps → H30 mati.
3. (bila lolos) simulasi replay konservatif; 4. paper-quote.

Fill-proxy KONSERVATIF dari snapshot 2s: maker-buy dianggap terisi hanya bila mid
snapshot berikutnya MENEMBUS ≤ bid saat ini (harga trade lewat level kita).
Adverse selection = seberapa jauh mid `horizon` detik kemudian berada DI BAWAH
harga fill kita (kita beli tepat sebelum harga lanjut turun).
Semua metrik simetris-proxy dari sisi bid (buku dua sisi ~simetris di median).
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
L2_DIR = ROOT / "data" / "l2"
MIN_DAYS = 28                     # gerbang kecukupan data (pra-registrasi ≥4 minggu)
KILL_BPS = 3.0                    # bunuh-cepat: edge kotor pair terbaik < 3 bps → mati


def load_symbol(symbol_safe: str, l2_dir: Path = L2_DIR) -> pd.DataFrame:
    """Muat semua snapshot satu simbol (nama file aman: BTC_USDCUSDC). Kolom:
    ts, mid, spread_bps, bid1, ask1. Diurutkan waktu."""
    rows = []
    for p in sorted(l2_dir.glob(f"{symbol_safe}_*.jsonl.gz")):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                for line in fh:
                    r = json.loads(line)
                    rows.append((r["ts"], r["mid"], r["spread_bps"],
                                 r["bids"][0][0], r["asks"][0][0]))
        except Exception:  # boundary — file hari berjalan bisa terpotong
            continue
    if not rows:
        return pd.DataFrame(columns=["ts", "mid", "spread_bps", "bid1", "ask1"])
    df = pd.DataFrame(rows, columns=["ts", "mid", "spread_bps", "bid1", "ask1"])
    return df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


def symbols_available(l2_dir: Path = L2_DIR) -> list[str]:
    return sorted({p.name.rsplit("_", 1)[0] for p in l2_dir.glob("*.jsonl.gz")})


def span_days(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    return float((df["ts"].iloc[-1] - df["ts"].iloc[0]) / 86400_000)


def spread_half_life(spread: np.ndarray) -> float | None:
    """Half-life AR(1) deviasi spread dari mean (dalam # snapshot). None bila
    tak mean-reverting (phi≥1 / data kurang)."""
    s = np.asarray(spread, dtype=float)
    s = s[np.isfinite(s)]
    if len(s) < 100:
        return None
    x = s - s.mean()
    denom = float(np.dot(x[:-1], x[:-1]))
    if denom <= 0:
        return None
    phi = float(np.dot(x[1:], x[:-1]) / denom)
    if not (0 < phi < 1):
        return None
    return float(-np.log(2) / np.log(phi))


def fill_proxy_stats(df: pd.DataFrame, horizon_snaps: int = 15) -> dict:
    """Fill-proxy maker-buy konservatif: mid[t+1] ≤ bid[t] → 'terisi' di bid[t].
    adverse_bps = (fill − mid[t+h])/fill × 1e4 (positif = harga lanjut turun
    setelah kita terisi = adverse selection yang kita bayar)."""
    mid = df["mid"].to_numpy(dtype=float)
    bid = df["bid1"].to_numpy(dtype=float)
    n = len(df)
    if n < horizon_snaps + 2:
        return {"n_snaps": n, "fills": 0, "fill_rate_per_hour": 0.0, "adverse_bps": None}
    filled = np.where(mid[1:n - horizon_snaps] <= bid[:n - horizon_snaps - 1])[0]
    if len(filled) == 0:
        return {"n_snaps": n, "fills": 0, "fill_rate_per_hour": 0.0, "adverse_bps": None}
    f_price = bid[filled]
    f_mark = mid[filled + 1 + horizon_snaps]
    adverse = (f_price - f_mark) / f_price * 1e4
    hours = max(span_days(df) * 24, 1e-9)
    return {"n_snaps": n, "fills": int(len(filled)),
            "fill_rate_per_hour": round(len(filled) / hours, 2),
            "adverse_bps": round(float(np.mean(adverse)), 3)}


def analyze_symbol(df: pd.DataFrame, horizon_snaps: int = 15) -> dict:
    """Metrik lengkap satu simbol + edge kotor = spread_median/2 − adverse."""
    sp = df["spread_bps"].to_numpy(dtype=float)
    base = {"days": round(span_days(df), 2),
            "spread_med_bps": round(float(np.median(sp)), 3) if len(sp) else None,
            "spread_p25_bps": round(float(np.percentile(sp, 25)), 3) if len(sp) else None,
            "half_life_snaps": spread_half_life(sp)}
    fp = fill_proxy_stats(df, horizon_snaps)
    edge = None
    if fp["adverse_bps"] is not None and base["spread_med_bps"] is not None:
        edge = round(base["spread_med_bps"] / 2 - fp["adverse_bps"], 3)
    return {**base, **fp, "edge_gross_bps": edge}


def verdict(per_symbol: dict[str, dict], min_days: float = MIN_DAYS,
            kill_bps: float = KILL_BPS) -> dict:
    """Verdict pra-registrasi. days < min_days → PREVIEW (dilarang menyimpulkan)."""
    if not per_symbol:
        return {"verdict": "NO_DATA", "reason": "belum ada data L2"}
    days = max(v.get("days", 0.0) for v in per_symbol.values())
    edges = {s: v["edge_gross_bps"] for s, v in per_symbol.items()
             if v.get("edge_gross_bps") is not None}
    best = max(edges.items(), key=lambda kv: kv[1]) if edges else (None, None)
    if days < min_days:
        return {"verdict": "PREVIEW", "days": round(days, 1), "best": best,
                "reason": f"data {days:.1f} hari < {min_days} — TOOLING CHECK, bukan kesimpulan"}
    if best[1] is None or best[1] < kill_bps:
        return {"verdict": "REJECTED", "days": round(days, 1), "best": best,
                "reason": f"edge kotor terbaik {best[1]} bps < {kill_bps} — bunuh-cepat (langkah 2)"}
    return {"verdict": "PROCEED_TO_SIM", "days": round(days, 1), "best": best,
            "reason": f"edge kotor terbaik {best[1]} bps ≥ {kill_bps} — lanjut simulasi konservatif"}
