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

CREATE TABLE IF NOT EXISTS news_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    active INTEGER NOT NULL,
    note   TEXT
);

CREATE TABLE IF NOT EXISTS screen_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    symbol  TEXT NOT NULL,
    signal  TEXT,
    price   REAL,
    atr_pct REAL,
    blocked TEXT
);
CREATE INDEX IF NOT EXISTS idx_screen_symbol ON screen_log(symbol);
CREATE INDEX IF NOT EXISTS idx_news_ts       ON news_log(ts);

CREATE TABLE IF NOT EXISTS gemini_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    model         TEXT,
    purpose       TEXT,
    key_idx       INTEGER,
    prompt_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    total_tokens  INTEGER DEFAULT 0,
    ok            INTEGER DEFAULT 1,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_gemini_ts ON gemini_usage(ts);

-- ===== Gemini Trader: keputusan, pelajaran (playbook), refleksi =====
CREATE TABLE IF NOT EXISTS gemini_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    setup       TEXT,                -- tag setup (kunci evidence-gate)
    side        TEXT,                -- long | short | flat
    conviction  REAL DEFAULT 0,      -- 0..1
    rationale   TEXT,
    context     TEXT,                -- JSON data yang dilihat (audit/replay)
    model       TEXT,
    status      TEXT DEFAULT 'open', -- open | settled
    outcome_r   REAL                 -- diisi saat settle
);
CREATE INDEX IF NOT EXISTS idx_gdec_symbol ON gemini_decisions(symbol);
CREATE INDEX IF NOT EXISTS idx_gdec_setup  ON gemini_decisions(setup);
CREATE INDEX IF NOT EXISTS idx_gdec_status ON gemini_decisions(status);

CREATE TABLE IF NOT EXISTS gemini_lessons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    scope         TEXT,              -- mis. simbol/regime ('*' = umum)
    setup         TEXT,              -- setup yang dirujuk (untuk evidence-gate)
    text          TEXT NOT NULL,
    n_support     INTEGER DEFAULT 0, -- bukti dari rekam jejak (dihitung KODE)
    exp_r_support REAL DEFAULT 0,
    confidence    TEXT DEFAULT 'low',
    active        INTEGER DEFAULT 0  -- 1 HANYA bila lolos evidence-gate
);
CREATE INDEX IF NOT EXISTS idx_glesson_active ON gemini_lessons(active);

CREATE TABLE IF NOT EXISTS gemini_reflections (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    period  TEXT,
    summary TEXT,
    metrics TEXT
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


# ---------- histori news veto & screening (append, sudah di-dedup pemanggil) ----------

def log_news(active: bool, note: str) -> None:
    init_db()
    with _conn() as c:
        c.execute("INSERT INTO news_log (ts, active, note) VALUES (?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), 1 if active else 0, note))


def log_screen(symbol: str, signal: str | None, price: float | None,
               atr_pct: float | None, blocked: str | None) -> None:
    init_db()
    with _conn() as c:
        c.execute("INSERT INTO screen_log (ts, symbol, signal, price, atr_pct, blocked) "
                  "VALUES (?,?,?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), symbol, signal, price, atr_pct, blocked))


def news_log(limit: int = 200) -> list[dict]:
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT id, ts, active, note FROM news_log ORDER BY ts DESC, id DESC LIMIT ?",
                         (limit,)).fetchall()
    return [{"id": r["id"], "ts": r["ts"], "active": bool(r["active"]), "note": r["note"]} for r in rows]


def screen_log(symbol: str | None = None, limit: int = 500) -> list[dict]:
    init_db()
    q = "SELECT id, ts, symbol, signal, price, atr_pct, blocked FROM screen_log"
    args: list = []
    if symbol:
        q += " WHERE symbol=?"
        args.append(symbol)
    q += " ORDER BY ts DESC, id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]


# ---------- pemantauan token Gemini ----------

def log_gemini_usage(model: str, purpose: str, key_idx: int, prompt_tokens: int,
                     output_tokens: int, total_tokens: int, ok: bool = True,
                     error: str = "") -> None:
    init_db()
    with _conn() as c:
        c.execute(
            "INSERT INTO gemini_usage (ts, model, purpose, key_idx, prompt_tokens, "
            "output_tokens, total_tokens, ok, error) VALUES (?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), model, purpose, key_idx,
             prompt_tokens, output_tokens, total_tokens, 1 if ok else 0, error))


def gemini_usage_stats(recent: int = 30) -> dict:
    init_db()
    today = datetime.now(timezone.utc).date().isoformat()
    with _conn() as c:
        tot = c.execute("SELECT COUNT(*) calls, COALESCE(SUM(total_tokens),0) tok, "
                        "COALESCE(SUM(ok=0),0) errs FROM gemini_usage").fetchone()
        td = c.execute("SELECT COUNT(*) calls, COALESCE(SUM(total_tokens),0) tok "
                       "FROM gemini_usage WHERE ts LIKE ?", (today + "%",)).fetchone()
        per_model = c.execute(
            "SELECT model, COUNT(*) calls, COALESCE(SUM(total_tokens),0) tok "
            "FROM gemini_usage GROUP BY model ORDER BY tok DESC").fetchall()
        per_key = c.execute(
            "SELECT key_idx, COUNT(*) calls, COALESCE(SUM(total_tokens),0) tok, "
            "COALESCE(SUM(ok=0),0) errs FROM gemini_usage GROUP BY key_idx ORDER BY key_idx").fetchall()
        per_purpose = c.execute(
            "SELECT purpose, COUNT(*) calls, COALESCE(SUM(total_tokens),0) tok "
            "FROM gemini_usage GROUP BY purpose ORDER BY tok DESC").fetchall()
        rows = c.execute("SELECT id, ts, model, purpose, key_idx, prompt_tokens, "
                         "output_tokens, total_tokens, ok, error FROM gemini_usage "
                         "ORDER BY id DESC LIMIT ?", (recent,)).fetchall()
    return {
        "total": {"calls": tot["calls"], "tokens": tot["tok"], "errors": tot["errs"]},
        "today": {"calls": td["calls"], "tokens": td["tok"]},
        "per_model": [dict(r) for r in per_model],
        "per_key": [dict(r) for r in per_key],
        "per_purpose": [dict(r) for r in per_purpose],
        "recent": [dict(r) for r in rows],
    }


# ---------- Gemini Trader: keputusan ----------

def record_decision(symbol: str, setup: str, side: str, conviction: float,
                    rationale: str, context: dict, model: str = "") -> int:
    init_db()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO gemini_decisions (ts, symbol, setup, side, conviction, rationale, "
            "context, model, status) VALUES (?,?,?,?,?,?,?,?, 'open')",
            (datetime.now(timezone.utc).isoformat(), symbol, setup, side, float(conviction),
             rationale, json.dumps(context, default=str), model))
        return cur.lastrowid


def settle_decision(decision_id: int, outcome_r: float) -> bool:
    init_db()
    with _conn() as c:
        return c.execute(
            "UPDATE gemini_decisions SET status='settled', outcome_r=? WHERE id=?",
            (float(outcome_r), decision_id)).rowcount > 0


def recent_decisions(symbol: str | None = None, limit: int = 20) -> list[dict]:
    init_db()
    q = "SELECT id, ts, symbol, setup, side, conviction, rationale, status, outcome_r FROM gemini_decisions"
    args: list = []
    if symbol:
        q += " WHERE symbol=?"
        args.append(symbol)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def settled_decisions() -> list[dict]:
    """Semua keputusan Gemini yang sudah ada hasilnya (untuk track record/signifikansi)."""
    init_db()
    with _conn() as c:
        rows = c.execute("SELECT symbol, setup, side, conviction, outcome_r FROM gemini_decisions "
                         "WHERE status='settled' AND outcome_r IS NOT NULL ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def setup_stats(setup: str, scope: str | None = None) -> dict:
    """Statistik settled per setup — DASAR evidence-gate (dihitung KODE, bukan AI)."""
    init_db()
    q = ("SELECT COUNT(*) n, COALESCE(AVG(outcome_r),0) exp_r, "
         "COALESCE(SUM(outcome_r>0),0) wins FROM gemini_decisions "
         "WHERE status='settled' AND setup=?")
    args: list = [setup]
    if scope and scope != "*":
        q += " AND symbol=?"
        args.append(scope)
    with _conn() as c:
        r = c.execute(q, args).fetchone()
    n = r["n"]
    return {"setup": setup, "n": n, "exp_r": float(r["exp_r"]),
            "win_rate": (r["wins"] / n * 100) if n else 0.0}


# ---------- Gemini Trader: pelajaran (playbook) + evidence-gate ----------

def add_lesson(scope: str, setup: str, text: str) -> int:
    """Usulan pelajaran (status awal: belum aktif sampai lolos evidence-gate)."""
    init_db()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO gemini_lessons (ts, scope, setup, text, active) VALUES (?,?,?,?,0)",
            (datetime.now(timezone.utc).isoformat(), scope, setup, text))
        return cur.lastrowid


def promote_lessons(min_n: int = 20) -> int:
    """EVIDENCE-GATE (anti-takhayul): aktifkan pelajaran HANYA bila setup rujukannya punya
    cukup sampel settled (n ≥ min_n). Update bukti & confidence dari statistik nyata.
    Yang tak cukup bukti dinonaktifkan. Kembalikan jumlah yang kini aktif."""
    init_db()
    active = 0
    with _conn() as c:
        lessons = c.execute("SELECT id, scope, setup FROM gemini_lessons").fetchall()
        for les in lessons:
            st = setup_stats(les["setup"], les["scope"]) if les["setup"] else {"n": 0, "exp_r": 0.0}
            n, exp_r = st["n"], st["exp_r"]
            ok = n >= min_n
            conf = "high" if n >= 3 * min_n else ("med" if ok else "low")
            c.execute("UPDATE gemini_lessons SET n_support=?, exp_r_support=?, confidence=?, active=? "
                      "WHERE id=?", (n, exp_r, conf, 1 if ok else 0, les["id"]))
            active += 1 if ok else 0
    return active


def active_lessons(limit: int = 20) -> list[dict]:
    """Pelajaran yang LOLOS evidence-gate — aman disuntik ke prompt keputusan."""
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT id, scope, setup, text, n_support, exp_r_support, confidence "
            "FROM gemini_lessons WHERE active=1 ORDER BY n_support DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def add_reflection(period: str, summary: str, metrics: dict) -> int:
    init_db()
    with _conn() as c:
        cur = c.execute("INSERT INTO gemini_reflections (ts, period, summary, metrics) VALUES (?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(), period, summary,
                         json.dumps(metrics, default=str)))
        return cur.lastrowid


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
