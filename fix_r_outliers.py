"""Koreksi satu-kali R-multiple yang meledak akibat bug SL ter-trail (denominator ~0).

Bug: R dihitung dari SL SAAT CLOSE (sudah digeser breakeven/tighten), bukan SL awal
→ contoh PLAY/USDT rugi $0.0119 tercatat R=-231.6 → verdict track-record rusak.
Kode sudah diperbaiki (risk0 beku saat open); skrip ini membetulkan DATA LAMA di
logs/bot.db: gemini_decisions.outcome_r + field r di event forward_close.

Jalankan DI MESIN BOT (Proxmox): python fix_r_outliers.py         (dry-run, lihat dulu)
                                 python fix_r_outliers.py --apply (tulis)
logs/trades.jsonl TIDAK disentuh (audit append-only).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent / "logs" / "bot.db"
CAP = 10.0          # |r| di atas ini = pasti bug (SL floor 1.75×ATR → R wajar < ~5)
MATCH_S = 120       # toleransi jarak ts keputusan vs ts open (detik)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def rebuild_trades(c: sqlite3.Connection) -> list[dict]:
    """Pasangkan forward_open↔forward_close per simbol (logika sama dgn dashboard)."""
    open_map: dict = {}
    trades = []
    rows = c.execute("SELECT id, ts, event, symbol, data FROM events "
                     "WHERE event IN ('forward_open','forward_close') ORDER BY id").fetchall()
    for r in rows:
        d = json.loads(r["data"])
        if r["event"] == "forward_open":
            open_map[r["symbol"]] = {"ts": r["ts"], **d}
        else:
            o = open_map.pop(r["symbol"], None)
            trades.append({"close_id": r["id"], "close_ts": r["ts"], "open": o, **d})
    return trades


def corrected_r(t: dict) -> float | None:
    """R dari pnl ÷ risk0 SL-awal. None bila data open tak lengkap."""
    o = t.get("open") or {}
    entry, sl, bet, lev = o.get("entry"), o.get("sl"), o.get("bet"), o.get("lev")
    pnl = t.get("pnl_usd")
    if None in (entry, sl, bet, lev, pnl) or not entry or sl == entry:
        return None
    qty = bet * lev / entry
    risk0 = abs(entry - sl) * qty
    return pnl / risk0 if risk0 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="tulis koreksi (default: dry-run)")
    ap.add_argument("--cap", type=float, default=CAP)
    args = ap.parse_args()

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    trades = rebuild_trades(c)
    fixes_ev, fixes_dec = [], []

    for t in trades:
        r_old = t.get("r")
        if r_old is None or abs(r_old) <= args.cap:
            continue
        r_new = corrected_r(t)
        if r_new is None:
            print(f"SKIP {t['symbol']} close_id={t['close_id']} r={r_old}: data open tak lengkap")
            continue
        fixes_ev.append((t, r_old, r_new))

    dec_rows = c.execute("SELECT id, ts, symbol, outcome_r FROM gemini_decisions "
                         "WHERE status='settled' AND outcome_r IS NOT NULL "
                         "AND ABS(outcome_r) > ?", (args.cap,)).fetchall()
    for d in dec_rows:
        # cocokkan ke trade: simbol sama + ts keputusan ≈ ts open
        cand = [t for t, _, _ in fixes_ev
                if t["symbol"] == d["symbol"] and t.get("open")
                and abs((_ts(t["open"]["ts"]) - _ts(d["ts"])).total_seconds()) < MATCH_S]
        if not cand:
            print(f"SKIP decision id={d['id']} {d['symbol']} r={d['outcome_r']}: "
                  f"tak ketemu pasangan open (koreksi manual)")
            continue
        r_new = corrected_r(cand[0])
        fixes_dec.append((d["id"], d["outcome_r"], r_new))

    for t, r_old, r_new in fixes_ev:
        print(f"event close_id={t['close_id']} {t['symbol']}: r {r_old:+.4f} -> {r_new:+.4f} "
              f"(pnl ${t.get('pnl_usd')})")
    for did, r_old, r_new in fixes_dec:
        print(f"gemini_decision id={did}: outcome_r {r_old:+.4f} -> {r_new:+.4f}")
    if not fixes_ev and not fixes_dec:
        print("Tidak ada outlier — bersih.")
        return
    if not args.apply:
        print("\nDRY-RUN — jalankan lagi dengan --apply untuk menulis.")
        return

    for t, _, r_new in fixes_ev:
        row = c.execute("SELECT data FROM events WHERE id=?", (t["close_id"],)).fetchone()
        d = json.loads(row["data"])
        d["r"] = round(r_new, 4)
        c.execute("UPDATE events SET data=? WHERE id=?", (json.dumps(d), t["close_id"]))
    for did, _, r_new in fixes_dec:
        c.execute("UPDATE gemini_decisions SET outcome_r=? WHERE id=?", (r_new, did))
    c.commit()
    print(f"\nDitulis: {len(fixes_ev)} event + {len(fixes_dec)} decision. "
          f"Cek ulang: curl localhost:8000/api/gemini-trader")


if __name__ == "__main__":
    main()
