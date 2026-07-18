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
    mode    TEXT,
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

-- ===== Flat shadow: keputusan FLAT Gemini + evaluasi forward (miss = ada gerakan
-- tradeable ≥ k×ATR dalam horizon yang dilewatkan). Dua fase pending→settled. =====
CREATE TABLE IF NOT EXISTS flat_shadow (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    mode       TEXT NOT NULL,          -- dry | test | live (isolasi per-mode)
    symbol     TEXT NOT NULL,
    price      REAL NOT NULL,          -- harga saat decide
    atr        REAL NOT NULL,          -- ATR saat decide (basis 1R hipotetis)
    conviction REAL,
    rationale  TEXT,
    regime     TEXT,                   -- json _regime_stamp
    bar_ts     TEXT NOT NULL,          -- index bar terakhir saat decide
    status     TEXT DEFAULT 'pending', -- pending | settled
    mfe_up_pct REAL, mfe_dn_pct REAL,
    miss       INTEGER, miss_dir TEXT  -- diisi saat settle
);
CREATE INDEX IF NOT EXISTS idx_flat_status ON flat_shadow(status, ts);

-- ===== Kalibrasi confidence: Brier score per trade (per mode, diisi saat close) =====
CREATE TABLE IF NOT EXISTS calibration_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    trade_id         INTEGER,           -- id keputusan gemini (gdecision) bila ada
    symbol           TEXT,
    predicted_prob   REAL NOT NULL,     -- probabilitas yang diberikan pada arah yang di-bet
    realized_outcome INTEGER NOT NULL,  -- 1 = profit, 0 = rugi
    brier            REAL NOT NULL,     -- (predicted_prob - realized_outcome)^2
    mode             TEXT NOT NULL      -- dry | test | live (isolasi per-mode)
);
CREATE INDEX IF NOT EXISTS idx_cal_mode_ts ON calibration_log(mode, ts);

CREATE TABLE IF NOT EXISTS entry_confluence_shadow (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    side             TEXT NOT NULL,
    setup            TEXT,
    btc_tier         TEXT,              -- full | reduced | blocked
    structure_pass   INTEGER,           -- 0/1
    location_quality TEXT,              -- strong | secondary | null
    would_enter      INTEGER,           -- 0/1 — hasil gate baru
    actually_entered INTEGER,           -- 0/1 — apakah rules lama entry
    conviction       REAL DEFAULT 0,
    price            REAL DEFAULT 0,
    reason           TEXT,
    outcome_r        REAL               -- diisi belakangan saat trade settle
);
CREATE INDEX IF NOT EXISTS idx_ec_shadow_ts ON entry_confluence_shadow(ts);
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


def _migrate() -> None:
    """Kolom evaluasi exit (Fix B 2026-07-02): MAE/MFE + alasan exit per keputusan
    Gemini — bahan menjawab 'SL terlalu mepet?' dgn data, bukan perasaan.

    Tahap 0 (plan-sess): kolom `mode` di Gemini tables untuk isolasi per-mode.
    Data lama di-backfill mode='dry' (DEFAULT). Idempotent — ALTER aman dipanggil
    tiap kali. Index gabungan (mode, status)/(mode, active) mempercepat query
    per-mode & menggantikan index lama untuk lookup spesifik."""
    with _conn() as c:
        for col, typ in (("mae_pct", "REAL"), ("mfe_pct", "REAL"), ("exit_reason", "TEXT")):
            try:
                c.execute(f"ALTER TABLE gemini_decisions ADD COLUMN {col} {typ}")
            except Exception:  # kolom sudah ada
                pass
        # ---- Isolasi per-mode (plan-sess Tahap 0a) ----
        for col, default in (("gemini_decisions", "'dry'"), ("gemini_lessons", "'dry'"),
                             ("gemini_reflections", "'dry'")):
            try:
                c.execute(f"ALTER TABLE {col} ADD COLUMN mode TEXT DEFAULT {default}")
            except Exception:  # kolom sudah ada (ALTER idempotent)
                pass
        # ---- Kolom `mode` di events table (untuk close_exists mode-isolation) ----
        try:
            c.execute("ALTER TABLE events ADD COLUMN mode TEXT")
        except Exception:  # kolom sudah ada
            pass
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_events_mode ON events(mode)")
        except Exception:  # index sudah ada
            pass
        for name, cols in (("idx_gdec_mode_status", "gemini_decisions(mode, status)"),
                           ("idx_glesson_mode_active", "gemini_lessons(mode, active)"),
                           ("idx_gref_mode", "gemini_reflections(mode)")):
            try:
                c.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {cols}")
            except Exception:  # index sudah ada
                pass


def insert_event(event: str, payload: dict, ts: str | None = None) -> int:
    """Catat satu event (open/close/dll). Kembalikan id baris."""
    init_db()
    ts = ts or payload.get("ts") or datetime.now(timezone.utc).isoformat()
    mode = payload.get("mode")   # di-set oleh logger.journal() dari _JOURNAL_MODE
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO events (ts, event, symbol, mode, data) VALUES (?,?,?,?,?)",
            (ts, event, payload.get("symbol"), mode, json.dumps(payload, default=str)),
        )
        return cur.lastrowid


def close_exists(mode: str, symbol: str, since: float | None = None) -> bool:
    """Cek apakah sudah ada forward_close untuk symbol ini dalam rentang waktu.
    Mode isolation: HANYA event dengan mode=ybs atau data lama (mode IS NULL)
    yang dianggap blocking — mencegah false positive lintas mode."""
    init_db()
    with _conn() as c:
        q = "SELECT COUNT(*) FROM events WHERE event='forward_close' AND symbol=? AND (mode=? OR mode IS NULL) "
        params: list = [symbol, mode]
        if since is not None:
            since_iso = datetime.fromtimestamp(since, tz=timezone.utc).isoformat()
            q += "AND ts >= ?"
            params.append(since_iso)
        return c.execute(q, params).fetchone()[0] > 0


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
    untuk simbol yang sama sebelum close ini). Kembalikan jumlah baris terhapus.
    Hanya bekerja untuk event forward_close — ID jenis lain ditolak."""
    init_db()
    with _conn() as c:
        row = c.execute("SELECT symbol FROM events WHERE id=? AND event='forward_close'",
                        (close_id,)).fetchone()
        if not row:
            return 0    # bukan forward_close → jangan hapus sembarangan
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


def reset_gemini_usage() -> int:
    """Kosongkan tabel pemantauan token Gemini (reset counter dari UI). Kembalikan jumlah
    baris terhapus. Tabel lain (keputusan/kalibrasi/pelajaran) TAK tersentuh."""
    init_db()
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM gemini_usage").fetchone()[0]
        c.execute("DELETE FROM gemini_usage")
        c.execute("DELETE FROM sqlite_sequence WHERE name='gemini_usage'")
    return int(n)


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
                    rationale: str, context: dict, model: str = "",
                    mode: str = "dry") -> int:
    """Catat keputusan Gemini. Arg `mode` mengisolasi per-mode (default 'dry' = back-compat
    pemanggil lama). DEFAULT bukan hal ideal — pemanggil produk (forward.py) WAJIB lempar
    mode eksplisit; default hanya ekuivalen sebelum Tahap 0."""
    init_db()
    _migrate()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO gemini_decisions (ts, symbol, setup, side, conviction, rationale, "
            "context, model, status, mode) VALUES (?,?,?,?,?,?,?,?, 'open', ?)",
            (datetime.now(timezone.utc).isoformat(), symbol, setup, side, float(conviction),
             rationale, json.dumps(context, default=str), model, mode))
        return cur.lastrowid


_R_SANE = 20.0  # ponytail: 1 trade |R|>20 non-fisik (sizing risk-first ~1R) → bug hitung,
                # bukan hasil nyata. Clamp di chokepoint settle agar tak meracuni AVG(outcome_r)
                # yang disuntik ke prompt (biang zero-entry PLAY R=-231). Naikkan bila sizing berubah.


def settle_decision(decision_id: int, outcome_r: float, mae_pct: float | None = None,
                    mfe_pct: float | None = None, exit_reason: str | None = None) -> bool:
    init_db()
    _migrate()
    r = float(outcome_r)
    if abs(r) > _R_SANE:
        import logging
        logging.getLogger("bot").warning(
            f"outcome_r={r:.2f} di luar batas waras (±{_R_SANE}) untuk decision {decision_id} "
            f"— di-clamp; cek risk0/entry (kemungkinan SL ter-trail ke ~breakeven).")
        r = max(-_R_SANE, min(_R_SANE, r))
    with _conn() as c:
        return c.execute(
            "UPDATE gemini_decisions SET status='settled', outcome_r=?, mae_pct=?, "
            "mfe_pct=?, exit_reason=? WHERE id=?",
            (r, mae_pct, mfe_pct, exit_reason, decision_id)).rowcount > 0


def recent_decisions(symbol: str | None = None, limit: int = 20,
                     mode: str | None = None) -> list[dict]:
    """Keputusan terbaru. mode=None = lintas-mode (mirror perilaku lama); pass mode='dry'
    untuk filter ke satu mode saja (Tahap 0: cegah track record bercampur)."""
    init_db()
    _migrate()
    q = ("SELECT id, ts, symbol, setup, side, conviction, rationale, status, outcome_r, "
         "mae_pct, mfe_pct, exit_reason FROM gemini_decisions")
    args: list = []
    where: list[str] = []
    if symbol:
        where.append("symbol=?")
        args.append(symbol)
    if mode is not None:
        where.append("mode=?")
        args.append(mode)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def exit_stats(mode: str | None = None) -> list[dict]:
    """Scorecard AGREGAT per exit_reason (sl/tp/cut-loss/gemini_exit/liq) — DIHITUNG KODE,
    bukan klaim AI. Agar Gemini BELAJAR cara-keluar mana yang sistematis merugikan (mis.
    gemini_exit -EV → berhenti cut prematur, biarkan SL/TP jalan).

    Tahap 0 (plan-sess): mode=None (default) = lintas-mode, back-compat. mode='dry'/
    'test'/'live' = filter agregat ke mode itu saja."""
    init_db()
    _migrate()
    q = ("SELECT COALESCE(exit_reason,'?') reason, COUNT(*) n, "
         "COALESCE(AVG(outcome_r),0) exp_r, COALESCE(SUM(outcome_r>0),0) wins, "
         "COALESCE(SUM(outcome_r),0) sum_r FROM gemini_decisions "
         "WHERE status='settled' AND outcome_r IS NOT NULL")
    args: list = []
    if mode is not None:
        q += " AND mode=?"
        args.append(mode)
    q += " GROUP BY COALESCE(exit_reason,'?') ORDER BY exp_r ASC"
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    out = []
    for r in rows:
        n = r["n"] or 0
        out.append({"reason": r["reason"], "n": n,
                    "win_rate": round((r["wins"] / n * 100) if n else 0.0, 1),
                    "exp_r": round(float(r["exp_r"]), 3),
                    "sum_r": round(float(r["sum_r"]), 3)})
    return out


def loss_postmortems(symbol: str | None = None, limit: int = 5) -> list[dict]:
    """POST-MORTEM SL/cut-loss sebagai TANYA-JAWAB (dari data yang SUDAH tersimpan).

    Menyandingkan alasan Gemini SAAT MASUK (rationale + regime/adx/rsi dari context
    entry) dengan HASILNYA (exit_reason, R, MAE/MFE) agar Gemini bisa MENGOREKSI
    penalarannya sendiri — bukan mengulang entry yang gagal. Tak butuh kolom baru:
    context entry sudah disimpan saat commit, tinggal dibaca ulang."""
    init_db()
    _migrate()
    q = ("SELECT symbol, setup, side, conviction, rationale, context, outcome_r, "
         "mae_pct, mfe_pct, exit_reason FROM gemini_decisions "
         "WHERE status='settled' AND (exit_reason IN ('sl','liq') OR outcome_r < 0)")
    args: list = []
    if symbol:
        q += " AND symbol=?"
        args.append(symbol)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    out = []
    for r in rows:
        try:
            mkt = json.loads(r["context"]).get("market", {}) if r["context"] else {}
        except Exception:  # context lama/rusak → tetap pakai sisanya
            mkt = {}
        mae, mfe = r["mae_pct"], r["mfe_pct"]
        if mfe is not None and mae is not None and mfe >= max(mae, 0.5) * 1.5:
            hint = "SL kemepetan — sempat untung besar (MFE) lalu tersapu; longgarkan/trailing"
        elif mae is not None and (mfe or 0) < (mae or 0) * 0.5:
            hint = "arah/timing salah — langsung merugi (MAE) tanpa MFE berarti"
        else:
            hint = "kekalahan wajar — periksa apakah setup ini memang beredge"
        out.append({
            "tanya": f"kenapa {r['side']} {r['symbol']} (setup={r['setup']}, "
                     f"conv={r['conviction']}) di regime={mkt.get('regime')}?",
            "jawab_saat_masuk": r["rationale"],
            "pasar_saat_masuk": {k: mkt.get(k) for k in ("regime", "adx", "rsi", "atr_pct")},
            "hasil": r["exit_reason"] or ("cut-loss" if (r["outcome_r"] or 0) < 0 else "?"),
            "R": round(float(r["outcome_r"]), 3) if r["outcome_r"] is not None else None,
            "mae_pct": mae, "mfe_pct": mfe,
            "koreksi": hint,
        })
    return out


def settled_decisions(mode: str | None = None) -> list[dict]:
    """Semua keputusan Gemini yang sudah ada hasilnya (untuk track record/signifikansi).
    Tahap 0: mode=None = lintas-mode (back-compat); mode='dry'/'test'/'live' = filter."""
    init_db()
    _migrate()
    q = ("SELECT symbol, setup, side, conviction, outcome_r, mae_pct, "
         "mfe_pct, exit_reason, mode FROM gemini_decisions "
         "WHERE status='settled' AND outcome_r IS NOT NULL")
    args: list = []
    if mode is not None:
        q += " AND mode=?"
        args.append(mode)
    q += " ORDER BY id"
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def setup_stats(setup: str, scope: str | None = None,
                mode: str | None = None) -> dict:
    """Statistik settled per setup — DASAR evidence-gate (dihitung KODE, bukan AI).

    Tahap 0: arg `mode` opsional filter per-mode (default None = lintas-mode, back-compat)."""
    init_db()
    _migrate()
    q = ("SELECT COUNT(*) n, COALESCE(AVG(outcome_r),0) exp_r, "
         "COALESCE(SUM(outcome_r>0),0) wins FROM gemini_decisions "
         "WHERE status='settled' AND setup=?")
    args: list = [setup]
    if scope and scope != "*":
        q += " AND symbol=?"
        args.append(scope)
    if mode is not None:
        q += " AND mode=?"
        args.append(mode)
    with _conn() as c:
        r = c.execute(q, args).fetchone()
    n = r["n"]
    # Analisis exit (Fix B): berapa sering SL tersambar, dan dari SL-hit itu berapa
    # yang MFE-nya sempat besar (= SL terlalu mepet: sempat untung lalu tersapu).
    q2 = ("SELECT COALESCE(SUM(exit_reason='sl'),0) sl_hits, "
          "COALESCE(AVG(CASE WHEN exit_reason='sl' THEN mae_pct END),0) avg_mae_sl, "
          "COALESCE(AVG(CASE WHEN exit_reason='sl' THEN mfe_pct END),0) avg_mfe_sl "
          "FROM gemini_decisions WHERE status='settled' AND setup=?")
    args2: list = [setup]
    if scope and scope != "*":
        q2 += " AND symbol=?"
        args2.append(scope)
    if mode is not None:
        q2 += " AND mode=?"
        args2.append(mode)
    with _conn() as c:
        e = c.execute(q2, args2).fetchone()
    return {"setup": setup, "n": n, "exp_r": float(r["exp_r"]),
            "win_rate": (r["wins"] / n * 100) if n else 0.0,
            "sl_hit_rate": (e["sl_hits"] / n * 100) if n else 0.0,
            "avg_mae_sl_pct": round(float(e["avg_mae_sl"]), 3),
            "avg_mfe_before_sl_pct": round(float(e["avg_mfe_sl"]), 3)}


# ---------- Gemini Trader: pelajaran (playbook) + evidence-gate ----------

def add_lesson(scope: str, setup: str, text: str, mode: str = "dry") -> int:
    """Usulan pelajaran (status awal: belum aktif sampai lolos evidence-gate). mode='dry'
    default = back-compat pemanggil lama. Pemanggil produksi (reflect/propose_lesson)
    WAJIB pass mode eksplisit."""
    init_db()
    _migrate()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO gemini_lessons (ts, scope, setup, text, active, mode) "
            "VALUES (?,?,?,?,0, ?)",
            (datetime.now(timezone.utc).isoformat(), scope, setup, text, mode))
        return cur.lastrowid


def promote_lessons(min_n: int = 20, mode: str | None = None,
                    share_across_modes: bool = False) -> int:
    """EVIDENCE-GATE (anti-takhayul): aktifkan pelajaran HANYA bila setup rujukannya punya
    cukup sampel settled (n ≥ min_n). Update bukti & confidence dari statistik nyata.
    Yang tak cukup bukti dinonaktifkan. Kembalikan jumlah yang kini aktif.

    Tahap 0 (plan-sess) isolasi per-mode:
      - mode=None & not share → treat SEMUA record (lintas-mode) PERMODE-nya sendiri:
        aggregat bukti digabung, tapi aktivasi per-baris mengikuti mode baris asalnya.
      - mode=None & share=True (opt-in config) → sama dengan mode=None & not share,
        bedanya filter pemilihan baris untuk hitung bukti TIDAK membatasi mode.
      - mode='dry'/'test'/'live' → filter evidence BUKTI sesuai mode itu saja
        (default config: pemisahan bukti per-mode = satu track-record per mode).
    """
    init_db()
    _migrate()
    active = 0
    with _conn() as c:
        lessons = c.execute("SELECT id, scope, setup, mode FROM gemini_lessons").fetchall()
        for les in lessons:
            les_mode = les["mode"] if les["mode"] is not None else "dry"
            # bukti untuk aktivasi: spesifik per-les.mode agar tak kontaminasi
            st = setup_stats(les["setup"], les["scope"], mode=les_mode)
            n, exp_r = st["n"], st["exp_r"]
            ok = n >= min_n
            conf = "high" if n >= 3 * min_n else ("med" if ok else "low")
            c.execute("UPDATE gemini_lessons SET n_support=?, exp_r_support=?, confidence=?, active=? "
                      "WHERE id=?", (n, exp_r, conf, 1 if ok else 0, les["id"]))
            active += 1 if ok else 0
    return active


def active_lessons(limit: int = 20, mode: str | None = None,
                   share_across_modes: bool = False) -> list[dict]:
    """Pelajaran yang LOLOS evidence-gate — aman disuntik ke prompt keputusan.

    Tahap 0 (plan-sess): default mode=None = back-compat (lintas-mode, BERISIKO
    kontaminasi bukti saat banyak mode aktif). Pemanggil produk HARUS pass mode
    eksplisit; share_across_modes=True khusus opt-in admin (config flag)."""
    init_db()
    _migrate()
    where = "WHERE active=1"
    args: list = []
    if mode is not None and not share_across_modes:
        where += " AND mode=?"
        args.append(mode)
    with _conn() as c:
        rows = c.execute(
            f"SELECT id, scope, setup, text, n_support, exp_r_support, confidence "
            f"FROM gemini_lessons {where} ORDER BY n_support DESC LIMIT ?",
            (*args, limit)).fetchall()
    return [dict(r) for r in rows]


def is_setup_retired(setup: str, mode: str | None = None,
                     share_across_modes: bool = False) -> bool:
    """HARD GATE: cek apakah setup sudah dipensiunkan (retired) via evidence-gate.
    
    Returns True jika setup BLOKIR:
    - active=0 (gagal evidence-gate) 
    - n_support >= 10 (cukup sampel untuk evaluasi)
    - exp_r_support < 0 (expectancy negatif = setup gagal)
    
    Dipanggil SEBELUM entry untuk hard-block setup yang sudah terbukti gagal.
    
    Tahap 0: default mode=None = lintas-mode (back-compat). Produksi HARUS pass mode.
    """
    if not setup:
        return False
    init_db()
    _migrate()
    where = "WHERE active=0 AND n_support >= 10 AND exp_r_support < 0"
    args: list = []
    if mode is not None and not share_across_modes:
        where += " AND mode=?"
        args.append(mode)
    with _conn() as c:
        row = c.execute(
            f"SELECT 1 FROM gemini_lessons {where} AND setup=? LIMIT 1",
            (*args, setup)).fetchone()
    return row is not None


def add_reflection(period: str, summary: str, metrics: dict,
                   mode: str = "dry") -> int:
    """Catat refleksi berkala. Tahap 0: label `mode` agar histori refleksi
    tak bercampur antar-mode (sama kebijakan dgn decisions/lessons)."""
    init_db()
    _migrate()
    with _conn() as c:
        cur = c.execute("INSERT INTO gemini_reflections (ts, period, summary, metrics, mode) "
                        "VALUES (?,?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(), period, summary,
                         json.dumps(metrics, default=str), mode))
        return cur.lastrowid


# ---------- kalibrasi confidence (Brier) ----------

def log_calibration(trade_id: int | None, symbol: str, predicted_prob: float,
                    realized_outcome: int, mode: str) -> None:
    """Skor satu trade yang tutup: brier = (p - outcome)^2. Instrumentasi murni."""
    init_db()
    p = max(0.0, min(1.0, float(predicted_prob)))
    o = 1 if realized_outcome else 0
    with _conn() as c:
        c.execute("INSERT INTO calibration_log (ts, trade_id, symbol, predicted_prob, "
                  "realized_outcome, brier, mode) VALUES (?,?,?,?,?,?,?)",
                  (datetime.now(timezone.utc).isoformat(), trade_id, symbol, p, o,
                   (p - o) ** 2, mode))


def calibration_report(mode: str, last_n: int = 50, days: int = 14) -> dict:
    """Rolling Brier per mode: N trade terakhir + X hari terakhir.
    Brier 0.25 = tak lebih baik dari koin; makin kecil makin terkalibrasi."""
    init_db()
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def _agg(rows) -> dict:
        n = len(rows)
        if not n:
            return {"n": 0, "brier": None, "hit_rate": None, "avg_prob": None}
        return {"n": n,
                "brier": round(sum(r["brier"] for r in rows) / n, 4),
                "hit_rate": round(sum(r["realized_outcome"] for r in rows) / n * 100, 1),
                "avg_prob": round(sum(r["predicted_prob"] for r in rows) / n, 3)}

    with _conn() as c:
        recent = c.execute("SELECT predicted_prob, realized_outcome, brier FROM calibration_log "
                           "WHERE mode=? ORDER BY id DESC LIMIT ?", (mode, last_n)).fetchall()
        window = c.execute("SELECT predicted_prob, realized_outcome, brier FROM calibration_log "
                           "WHERE mode=? AND ts>=?", (mode, since)).fetchall()
    return {"mode": mode, f"last_{last_n}_trades": _agg(recent),
            f"last_{days}_days": _agg(window)}


# ---------- Entry Confluence Gate shadow ----------

def log_entry_confluence_shadow(rec) -> None:
    """Catat hasil gate ke shadow table. `rec` bisa dataclass atau dict."""
    init_db()
    _migrate()
    if hasattr(rec, "__dataclass_fields__"):
        d = {f: getattr(rec, f) for f in rec.__dataclass_fields__}
    else:
        d = dict(rec)
    with _conn() as c:
        c.execute(
            "INSERT INTO entry_confluence_shadow "
            "(ts, symbol, side, setup, btc_tier, structure_pass, location_quality, "
            "would_enter, actually_entered, conviction, price, reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.get("ts", ""), d.get("symbol", ""), d.get("side", ""),
             d.get("setup", ""), d.get("btc_tier", ""),
             1 if d.get("structure_pass") else 0,
             d.get("location_quality"),
             1 if d.get("would_enter") else 0,
             1 if d.get("actually_entered") else 0,
             float(d.get("conviction", 0)),
             float(d.get("price", 0)),
             str(d.get("reason", ""))[:300]))


def settle_entry_confluence_shadow(shadow_id: int, outcome_r: float) -> bool:
    """Isi outcome_r setelah trade settle."""
    init_db()
    with _conn() as c:
        return c.execute(
            "UPDATE entry_confluence_shadow SET outcome_r=? WHERE id=?",
            (float(outcome_r), shadow_id)).rowcount > 0


def settle_entry_confluence_outcome(shadow_id: int, actually_entered: bool = False,
                                     outcome_r: float | None = None) -> bool:
    """Update actually_entered dan/atau outcome_r untuk shadow record."""
    init_db()
    sets = []
    params = []
    if outcome_r is not None:
        sets.append("outcome_r=?")
        params.append(float(outcome_r))
    if actually_entered:
        sets.append("actually_entered=1")
    if not sets:
        return False
    params.append(shadow_id)
    with _conn() as c:
        return c.execute(
            f"UPDATE entry_confluence_shadow SET {', '.join(sets)} WHERE id=?",
            params).rowcount > 0


def entry_confluence_shadow_stats(limit: int = 200) -> list[dict]:
    """Ambil N record shadow terbaru."""
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT id, ts, symbol, side, setup, btc_tier, structure_pass, "
            "location_quality, would_enter, actually_entered, conviction, price, "
            "reason, outcome_r FROM entry_confluence_shadow "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def entry_confluence_agg() -> dict:
    """Agregasi shadow: perbandingan would_enter vs actually_entered."""
    init_db()
    out = {"total_logged": 0, "would_enter": 0, "would_skip": 0,
           "actually_entered": 0, "would_enter_and_entered": 0,
           "would_skip_but_entered": 0, "by_setup": {}, "by_btc_tier": {},
           "by_location": {}}
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) n FROM entry_confluence_shadow").fetchone()
        out["total_logged"] = total["n"] if total else 0

        we = c.execute("SELECT COUNT(*) n FROM entry_confluence_shadow WHERE would_enter=1").fetchone()
        out["would_enter"] = we["n"] if we else 0

        ae = c.execute("SELECT COUNT(*) n FROM entry_confluence_shadow WHERE actually_entered=1").fetchone()
        out["actually_entered"] = ae["n"] if ae else 0

        weae = c.execute("SELECT COUNT(*) n FROM entry_confluence_shadow WHERE would_enter=1 AND actually_entered=1").fetchone()
        out["would_enter_and_entered"] = weae["n"] if weae else 0

        wsbe = c.execute("SELECT COUNT(*) n FROM entry_confluence_shadow WHERE would_enter=0 AND actually_entered=1").fetchone()
        out["would_skip_but_entered"] = wsbe["n"] if wsbe else 0

        setups = c.execute(
            "SELECT setup, COUNT(*) n, COALESCE(SUM(would_enter),0) would_enter, "
            "COALESCE(SUM(actually_entered),0) actually_entered, "
            "COALESCE(AVG(outcome_r),0) avg_outcome_r "
            "FROM entry_confluence_shadow GROUP BY setup").fetchall()
        for s in setups:
            out["by_setup"][s["setup"]] = dict(s)

        tiers = c.execute(
            "SELECT btc_tier, COUNT(*) n, COALESCE(SUM(would_enter),0) would_enter, "
            "COALESCE(AVG(outcome_r),0) avg_outcome_r "
            "FROM entry_confluence_shadow GROUP BY btc_tier").fetchall()
        for t in tiers:
            out["by_btc_tier"][t["btc_tier"]] = dict(t)

        locs = c.execute(
            "SELECT location_quality, COUNT(*) n, COALESCE(SUM(would_enter),0) would_enter, "
            "COALESCE(AVG(outcome_r),0) avg_outcome_r "
            "FROM entry_confluence_shadow GROUP BY location_quality").fetchall()
        for l in locs:
            out["by_location"][l["location_quality"]] = dict(l)

    return out


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
