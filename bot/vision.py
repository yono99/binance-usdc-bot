"""Downloader arsip publik Binance Vision (data historis yang TIDAK ada di REST).

- aggTrades  : setiap trade (price, qty, is_buyer_maker) — fill maker NYATA →
               bahan H30 (effective spread + adverse selection dari eksekusi).
- metrics    : open interest 5-menit sejak ~2021 — membuka H19 tanpa menunggu
               6 bulan rekaman (batas 30 hari hanya di REST API).

Cache: data/vision/<path>.zip (idempotent; unduh sekali). Parser pure → mudah diuji.
"""
from __future__ import annotations

import io
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from .logger import log

BASE = "https://data.binance.vision/data/futures/um"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "vision"


def _safe_sym(symbol: str) -> str:
    """'FIL/USDC:USDC' → 'FILUSDC' (format arsip)."""
    return symbol.split(":")[0].replace("/", "")


def download(url: str, cache: Path = CACHE) -> Path | None:
    """Unduh zip → cache. None bila 404/gagal (file hari itu bisa belum terbit)."""
    rel = url.replace(BASE + "/", "").replace("/", "_")
    p = cache / rel
    if p.exists() and p.stat().st_size > 0:
        return p
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 bot"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        cache.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p
    except Exception as e:  # boundary — 404 utk tanggal libur/pair belum listing
        log.warning(f"vision gagal {url.rsplit('/', 1)[-1]}: {e}")
        return None


def download_many(urls: list[str], workers: int = 12, cache: Path = CACHE) -> list[Path]:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = list(ex.map(lambda u: download(u, cache), urls))
    return [p for p in out if p is not None]


def aggtrades_url(symbol: str, month: str) -> str:
    """month 'YYYY-MM' (bulanan) atau 'YYYY-MM-DD' (harian)."""
    s = _safe_sym(symbol)
    kind = "monthly" if len(month) == 7 else "daily"
    return f"{BASE}/{kind}/aggTrades/{s}/{s}-aggTrades-{month}.zip"


def metrics_url(symbol: str, day: str) -> str:
    s = _safe_sym(symbol)
    return f"{BASE}/daily/metrics/{s}/{s}-metrics-{day}.zip"


def parse_aggtrades(zip_bytes: bytes) -> pd.DataFrame:
    """CSV aggTrades → DataFrame(ts, price, qty, is_buyer_maker) urut waktu.
    is_buyer_maker=True → taker MENJUAL ke bid (fill maker-BUY di bid);
    False → taker MEMBELI dari ask (fill maker-SELL di ask)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name))
    df.columns = [c.strip().lower() for c in df.columns]
    ts_col = "transact_time" if "transact_time" in df.columns else "timestamp"
    out = pd.DataFrame({"ts": df[ts_col].astype("int64"),
                        "price": df["price"].astype(float),
                        "qty": df["quantity"].astype(float),
                        "is_buyer_maker": df["is_buyer_maker"].astype(bool)})
    return out.sort_values("ts").reset_index(drop=True)


def parse_metrics(zip_bytes: bytes) -> pd.DataFrame:
    """CSV metrics → DataFrame(time index UTC, oi_value) 5-menit."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        name = z.namelist()[0]
        df = pd.read_csv(z.open(name))
    df.columns = [c.strip().lower() for c in df.columns]
    t = pd.to_datetime(df["create_time"], utc=True, format="mixed")
    val = df.get("sum_open_interest_value", df.get("sum_open_interest"))
    out = pd.DataFrame({"oi_value": pd.to_numeric(val, errors="coerce")})
    out.index = t
    return out.dropna().sort_index()


def load_aggtrades(symbol: str, months: list[str], cache: Path = CACHE) -> pd.DataFrame:
    frames = []
    for m in months:
        p = download(aggtrades_url(symbol, m), cache)
        if p:
            frames.append(parse_aggtrades(p.read_bytes()))
    if not frames:
        return pd.DataFrame(columns=["ts", "price", "qty", "is_buyer_maker"])
    return pd.concat(frames).sort_values("ts").reset_index(drop=True)


def load_metrics_oi(symbol: str, days: list[str], cache: Path = CACHE) -> pd.Series:
    """Gabung OI 5-menit banyak hari → Series oi_value (index UTC)."""
    paths = download_many([metrics_url(symbol, d) for d in days], cache=cache)
    frames = [parse_metrics(p.read_bytes()) for p in paths]
    if not frames:
        return pd.Series(dtype=float)
    s = pd.concat(frames)["oi_value"].sort_index()
    return s[~s.index.duplicated(keep="last")]
