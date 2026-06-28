"""Reproducibility & lockbox holdout — pertahanan terakhir sebelum live.

Dua fungsi:
1. SNAPSHOT — simpan OHLCV persis yang dipakai (+ hash) agar sebuah candidate bisa
   diverifikasi ulang BIT-FOR-BIT. Tanpa ini, data di-fetch live tiap run → angka
   bergeser, candidate tak bisa direproduksi (sumber 'silent' false confidence).
2. LOCKBOX HOLDOUT — sisihkan ekor histori (mis. 20% terbaru) yang TIDAK PERNAH
   dilihat selama riset/tuning. Dipakai SEKALI sebagai ujian final: bila edge nyata,
   ia bertahan di data yang belum pernah memengaruhi pemilihan parameter.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd


def _safe(symbol: str, tf: str) -> str:
    return symbol.replace("/", "_").replace(":", "_") + f"__{tf}"


def df_hash(df: pd.DataFrame) -> str:
    """Hash deterministik isi OHLCV (untuk provenance & verifikasi reproduksi)."""
    cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    blob = pd.util.hash_pandas_object(df[cols], index=True).values.tobytes()
    return hashlib.sha256(blob).hexdigest()[:16]


def snapshot_path(directory: str | Path, symbol: str, tf: str) -> Path:
    # pickle: dependency-free & menyimpan dtype/index PERSIS (untuk hash bit-for-bit).
    return Path(directory) / f"{_safe(symbol, tf)}.pkl"


def save_ohlcv(df: pd.DataFrame, directory: str | Path, symbol: str, tf: str) -> Path:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    p = snapshot_path(d, symbol, tf)
    df.to_pickle(p)
    return p


def load_ohlcv(directory: str | Path, symbol: str, tf: str) -> pd.DataFrame | None:
    p = snapshot_path(directory, symbol, tf)
    if not p.exists():
        return None
    return pd.read_pickle(p)


def split_holdout(df: pd.DataFrame, frac: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(research, lockbox). Lockbox = `frac` bagian PALING AKHIR (terbaru), causal."""
    if frac <= 0 or len(df) == 0:
        return df, df.iloc[0:0]
    frac = min(frac, 0.9)
    cut = int(len(df) * (1.0 - frac))
    return df.iloc[:cut], df.iloc[cut:]
