"""Chart data store — OHLCV ke SQLite (produk data: tarik sekali, baca selamanya.

- DB terpisah dari bot.db (data pasar bisa besar): `data/market.db`.
- Skema: candles(symbol, tf, ts_ms, open, high, low, close, volume) PK(symbol,tf,ts_ms)
  → idempotent (INSERT OR REPLACE); ingest ulang tak menduplikasi.
- `ingest` inkremental: lanjut dari ts terakhir (hemat API); backfill penuh bila kosong.
- Dibaca oleh: API dashboard /api/candles (chart), dan riset apa pun via `load`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "market.db"


def _conn(db: Path | None = None) -> sqlite3.Connection:
    path = db or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=10.0)
    c.execute("""CREATE TABLE IF NOT EXISTS candles(
        symbol TEXT NOT NULL, tf TEXT NOT NULL, ts_ms INTEGER NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        PRIMARY KEY(symbol, tf, ts_ms))""")
    return c


def upsert(symbol: str, tf: str, df: pd.DataFrame, db: Path | None = None) -> int:
    """Simpan DataFrame OHLCV (index datetime UTC). Idempotent. Return n baris."""
    if df is None or not len(df):
        return 0
    rows = [(symbol, tf, int(ts.timestamp() * 1000), float(r["open"]), float(r["high"]),
             float(r["low"]), float(r["close"]), float(r.get("volume", 0.0)))
            for ts, r in df.iterrows()]
    with _conn(db) as c:
        c.executemany("INSERT OR REPLACE INTO candles VALUES(?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def last_ts(symbol: str, tf: str, db: Path | None = None) -> int | None:
    with _conn(db) as c:
        row = c.execute("SELECT MAX(ts_ms) FROM candles WHERE symbol=? AND tf=?",
                        (symbol, tf)).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def load(symbol: str, tf: str, limit: int = 500, end_ms: int | None = None,
         db: Path | None = None) -> pd.DataFrame:
    """Baca candle TERBARU (≤limit), kembalikan kronologis dgn index datetime UTC."""
    q = "SELECT ts_ms, open, high, low, close, volume FROM candles WHERE symbol=? AND tf=?"
    args: list = [symbol, tf]
    if end_ms is not None:
        q += " AND ts_ms<=?"
        args.append(end_ms)
    q += " ORDER BY ts_ms DESC LIMIT ?"
    args.append(int(limit))
    with _conn(db) as c:
        rows = c.execute(q, args).fetchall()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows[::-1], columns=["ts_ms", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df.pop("ts_ms"), unit="ms", utc=True)
    df.index.name = "time"
    return df


def coverage(db: Path | None = None) -> list[dict]:
    """Ringkasan isi store per (symbol, tf) — untuk API/CLI status."""
    with _conn(db) as c:
        rows = c.execute("""SELECT symbol, tf, COUNT(*), MIN(ts_ms), MAX(ts_ms)
                            FROM candles GROUP BY symbol, tf ORDER BY symbol, tf""").fetchall()
    return [{"symbol": s, "tf": tf, "n": n,
             "first": pd.Timestamp(a, unit="ms", tz="UTC").isoformat(),
             "last": pd.Timestamp(b, unit="ms", tz="UTC").isoformat()}
            for s, tf, n, a, b in rows]


def ingest(ex, symbol: str, tf: str, bars: int = 1500, db: Path | None = None) -> int:
    """Tarik OHLCV dari exchange → SQLite. Inkremental bila sudah ada isi
    (fetch sejak ts terakhir); backfill `bars` bila kosong. Return n baris baru."""
    since = last_ts(symbol, tf, db)
    if since is None:
        from .backtest import fetch_history            # backfill penuh (paginated)
        df = fetch_history(ex, symbol, tf, bars)
        return upsert(symbol, tf, df, db)
    tf_ms = ex.client.parse_timeframe(tf) * 1000
    out: list = []
    cursor = since + 1                                  # setelah bar terakhir tersimpan
    while True:
        chunk = ex.client.fetch_ohlcv(symbol, tf, since=cursor, limit=1000)
        if not chunk:
            break
        out += chunk
        cursor = chunk[-1][0] + tf_ms
        if len(chunk) < 1000:
            break
    if not out:
        return 0
    df = pd.DataFrame(out, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return upsert(symbol, tf, df.set_index("time"), db)
