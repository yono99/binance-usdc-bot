"""SQLite store: sumber kebenaran durable untuk event trade (pengganti trades.jsonl).

Kenapa SQLite: file JSONL append-only tak punya DELETE/UPDATE/query/transaksi.
SQLite memberi semua itu dalam SATU file (logs/bot.db), nol ops, ideal single-user.
journal() dual-write: JSONL (audit/post-mortem) + SQLite (query & hapus dari UI).
Dashboard membaca dari sini. WAL mode → aman baca-tulis konkuren (bot tulis, UI baca).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "logs" / "bot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    event   TEXT NOT NULL,
    symbol  TEXT,
    data    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_event  ON events(event);
CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol);
CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(ts);

CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=5.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")   # baca-tulis konkuren aman
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)


def insert_event(event: str, payload: dict, ts: str | None = None) -> int:
    """Catat satu event (open/close/dll). Kembalikan id baris."""
    init_db()
    ts = ts or payload.get("ts") or datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO events (ts, event, symbol, data) VALUES (?,?,?,?)",
            (ts, event, payload.get("symbol"), json.dumps(payload, default=str)),
        )
        return cur.lastrowid


def all_events() -> list[dict]:
    """Event berurut waktu; bentuk dict identik baris JSONL lama + field 'id'."""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT id, ts, event, data FROM events ORDER BY id").fetchall()
    out = []
    for r in rows:
        rec = json.loads(r["data"])
        rec.update(id=r["id"], ts=r["ts"], event=r["event"])
        out.append(rec)
    return out


def delete_event(event_id: int) -> bool:
    """Hapus satu event. Kembalikan True bila ada yang terhapus."""
    init_db()
    with _conn() as c:
        return c.execute("DELETE FROM events WHERE id=?", (event_id,)).rowcount > 0


def delete_trade(close_id: int) -> int:
    """Hapus satu trade: event close (id ini) + event open pasangannya (open terakhir
    untuk simbol yang sama sebelum close ini). Kembalikan jumlah baris terhapus."""
    init_db()
    with _conn() as c:
        row = c.execute("SELECT symbol FROM events WHERE id=? AND event='forward_close'",
                        (close_id,)).fetchone()
        if not row:
            return c.execute("DELETE FROM events WHERE id=?", (close_id,)).rowcount
        opn = c.execute(
            "SELECT id FROM events WHERE event='forward_open' AND symbol=? AND id<? "
            "ORDER BY id DESC LIMIT 1", (row["symbol"], close_id)).fetchone()
        ids = [close_id] + ([opn["id"]] if opn else [])
        q = f"DELETE FROM events WHERE id IN ({','.join('?' * len(ids))})"
        return c.execute(q, ids).rowcount


def clear_events() -> int:
    """Kosongkan seluruh riwayat. Kembalikan jumlah baris terhapus."""
    init_db()
    with _conn() as c:
        return c.execute("DELETE FROM events").rowcount


# ---------- key-value: blob JSON singleton (runtime settings, status bot) ----------

def set_kv(key: str, payload: dict) -> None:
    """Simpan/timpa satu blob JSON (mis. 'runtime', 'status')."""
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT INTO kv (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(payload, default=str), datetime.now(timezone.utc).isoformat()),
        )


def get_kv(key: str) -> dict | None:
    """Ambil blob JSON; None bila belum ada."""
    init_db()
    with _conn() as c:
        row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def migrate_jsonl(path: Path) -> int:
    """Impor baris JSONL lama ke SQLite (idempoten jika tabel kosong). Kembalikan jumlah terimpor."""
    if not path.exists():
        return 0
    init_db()
    n = 0
    with _conn() as c, open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            c.execute("INSERT INTO events (ts, event, symbol, data) VALUES (?,?,?,?)",
                      (rec.get("ts") or datetime.now(timezone.utc).isoformat(),
                       rec.get("event", ""), rec.get("symbol"),
                       json.dumps(rec, default=str)))
            n += 1
    return n
