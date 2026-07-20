#!/usr/bin/env python3
"""Tutup ghost paper dry: forward_open tanpa close di events, botstate open kosong.

Paper-only. Menulis forward_close reason=reconcile_state_flat (exit=entry, pnl=0, r=0)
agar journal/stats konsisten, lalu pastikan botstate_dry.open={} dan screen sticky
di-refresh lewat baris screen_log netral.

Jalankan di server (cwd=repo), bot boleh online — restart bot setelahnya agar
status/screen ikut segar.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "logs" / "bot.db"
MODE = "dry"


def main() -> int:
    if not DB.exists():
        print("DB missing:", DB)
        return 1
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat()

    opens: dict[str, dict] = {}
    for row in con.execute(
        "SELECT id, ts, event, symbol, data, mode FROM events WHERE COALESCE(mode,'dry')=? ORDER BY id",
        (MODE,),
    ):
        d = json.loads(row["data"]) if row["data"] else {}
        sym = row["symbol"]
        if not sym:
            continue
        if row["event"] == "forward_open":
            opens[sym] = {**d, "ts": row["ts"], "symbol": sym}
        elif row["event"] == "forward_close":
            opens.pop(sym, None)

    print(f"ghost opens ({MODE}): {len(opens)}")
    for s, d in sorted(opens.items()):
        print(f"  {s} {d.get('side')} ts={d.get('ts')} bet={d.get('bet')} entry={d.get('entry')}")

    closed = 0
    for sym, d in opens.items():
        entry = float(d.get("entry") or 0)
        side = d.get("side") or "long"
        payload = {
            "symbol": sym,
            "side": side,
            "entry": round(entry, 6) if entry else 0,
            "exit": round(entry, 6) if entry else 0,
            "reason": "reconcile_state_flat",
            "pnl_usd": 0.0,
            "r": 0.0,
            "regime": "unknown",
            "mae_pct": 0.0,
            "mfe_pct": 0.0,
            "funding_usd": 0.0,
            "equity": None,  # diisi di bawah dari botstate
            "note": "auto-close ghost: open di journal tanpa botstate/close",
        }
        # equity snapshot
        row = con.execute("SELECT value FROM kv WHERE key=?", (f"botstate_{MODE}",)).fetchone()
        bal_u = bal_c = 0.0
        if row:
            st = json.loads(row["value"])
            bal_u = float(st.get("balance_usdt") or 0)
            bal_c = float(st.get("balance_usdc") or 0)
        payload["equity"] = round(bal_u + bal_c, 2)
        data_s = json.dumps(payload)
        con.execute(
            "INSERT INTO events (ts, event, symbol, data, mode) VALUES (?,?,?,?,?)",
            (now, "forward_close", sym, data_s, MODE),
        )
        # mirror journal jsonl
        jpath = ROOT / "logs" / f"trades_{MODE}.jsonl"
        with open(jpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now, "event": "forward_close", **payload, "mode": MODE}) + "\n")
        closed += 1
        print(f"  CLOSED ghost {sym}")

    # botstate: force open empty (sudah empty, pastikan)
    row = con.execute("SELECT value FROM kv WHERE key=?", (f"botstate_{MODE}",)).fetchone()
    if row:
        st = json.loads(row["value"])
        st["open"] = {}
        st["pending"] = {}
        con.execute(
            "INSERT INTO kv (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (f"botstate_{MODE}", json.dumps(st), now),
        )
        print("botstate open cleared; bal", st.get("balance_usdt"), st.get("balance_usdc"))

    # status open_count 0
    row = con.execute("SELECT value FROM kv WHERE key=?", (f"status:{MODE}",)).fetchone()
    if row:
        st = json.loads(row["value"])
        st["open_count"] = 0
        st["day_trades"] = int(st.get("day_trades") or 0)
        # clear in_position on symbols
        syms = st.get("symbols") or []
        if isinstance(syms, list):
            for s2 in syms:
                if isinstance(s2, dict):
                    s2["in_position"] = False
                    s2["position"] = None
                    if s2.get("blocked") in (
                        "sudah ada posisi",
                        "✓ posisi dibuka",
                    ) or (isinstance(s2.get("blocked"), str) and "posisi" in s2.get("blocked", "")):
                        s2["blocked"] = "reconcile: flat"
        con.execute(
            "INSERT INTO kv (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (f"status:{MODE}", json.dumps(st), now),
        )
        print("status:dry open_count forced 0")

    # sticky screen: tulis baris netral utk simbol ghost agar UI tak menampilkan "sudah ada posisi"
    for sym in list(opens.keys()):
        con.execute(
            "INSERT INTO screen_log (ts, symbol, signal, price, atr_pct, blocked) "
            "VALUES (?,?,?,?,?,?)",
            (now, sym, "skip", None, None, "reconcile: flat (state cleaned)"),
        )
    print(f"screen_log refresh rows: {len(opens)}")

    con.commit()
    con.close()
    print(f"DONE closed={closed} at {now}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
