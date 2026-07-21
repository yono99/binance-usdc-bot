#!/usr/bin/env python
"""Expectancy report dari logs/trades.jsonl.

Menjawab pertanyaan sebenarnya: "ada dimensi yang +EV SETELAH fee?" — bukan
"strategi cukup pintar?". Mengelompokkan hasil trade per: reason, symbol, side,
conviction, dan (bila sudah dilog) regime.

Asumsi fee: pnl_usd di forward_close SUDAH net fee dari simulator (commit fee
per-settle+per-leg). Pakai --haircut $ untuk uji-sensitivitas fee tambahan.

Regime: field 'regime' BELUM ditulis ke log (dicek 2026-07 = 0 baris). Sampai
patch logging masuk, bucket regime akan kosong. Lihat catatan di akhir output.

Pakai:  python regime_ev.py [--haircut 0.02] [path=logs/trades.jsonl]
"""
from __future__ import annotations
import sys, json, collections, statistics as st

FEE_NOTE = ("pnl_usd diasumsikan SUDAH net fee (simulator). "
            "--haircut mengurangi $ tetap per-trade untuk stress-test.")


def load(path):
    """FIFO-match forward_open -> forward_close per symbol. Kembalikan list record
    close yang diperkaya field open (side, conviction). PURE atas isi file."""
    opens = collections.defaultdict(collections.deque)  # symbol -> deque(open dict)
    recs = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = d.get("event", "")
        sym = d.get("symbol", "?")
        if ev == "forward_open":
            opens[sym].append(d)
        elif ev == "forward_close":
            o = opens[sym].popleft() if opens[sym] else {}
            recs.append({
                "symbol": sym,
                "pnl": float(d.get("pnl_usd", 0) or 0),
                "r": float(d.get("r", 0) or 0),
                "reason": d.get("reason", "?"),
                "side": o.get("side", "?"),
                "conviction": o.get("conviction"),
                # regime bisa datang dari open ATAU close begitu patch logging masuk
                "regime": d.get("regime") or o.get("regime") or "(belum dilog)",
            })
    return recs


def conv_bucket(c):
    if c is None:
        return "?"
    try:
        c = float(c)
    except (TypeError, ValueError):
        return "?"
    return "hi>=0.7" if c >= 0.7 else ("mid>=0.4" if c >= 0.4 else "lo<0.4")


def summarize(rows, haircut=0.0):
    """(n, win%, avg_win, avg_loss, EV/trade, total, expectancy_R). PURE."""
    pnls = [r["pnl"] - haircut for r in rows]
    rs = [r["r"] for r in rows]
    n = len(pnls)
    if n == 0:
        return None
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr = len(wins) / n
    aw = st.mean(wins) if wins else 0.0
    al = st.mean(losses) if losses else 0.0
    ev = st.mean(pnls)              # <-- inilah expectancy per trade setelah fee
    exp_r = st.mean(rs) if rs else 0.0
    return dict(n=n, wr=wr, aw=aw, al=al, ev=ev, total=sum(pnls), exp_r=exp_r)


def _row(label, s):
    flag = "+" if s["ev"] > 0 else ("=" if s["ev"] == 0 else "-")
    return (f"  {label:<16} n={s['n']:>4}  win={s['wr']*100:5.1f}%  "
            f"avgW={s['aw']:+7.3f}  avgL={s['al']:+7.3f}  "
            f"EV/trade={s['ev']:+7.4f} {flag}  total={s['total']:+8.3f}  "
            f"expR={s['exp_r']:+.3f}")


def group(rows, keyfn, haircut):
    g = collections.defaultdict(list)
    for r in rows:
        g[keyfn(r)].append(r)
    out = {}
    for k, v in g.items():
        s = summarize(v, haircut)
        if s:
            out[k] = s
    return out


def report(rows, haircut=0.0):
    lines = []
    ov = summarize(rows, haircut)
    if not ov:
        return "tidak ada forward_close di log."
    lines.append(f"Fee: {FEE_NOTE}  (haircut=${haircut:g}/trade)")
    lines.append(f"TOTAL: {_row('semua', ov)[2:]}")
    for title, keyfn in [
        ("per REGIME", lambda r: r["regime"]),
        ("per REASON", lambda r: r["reason"]),
        ("per SIDE", lambda r: r["side"]),
        ("per CONVICTION", lambda r: conv_bucket(r["conviction"])),
        ("per SYMBOL", lambda r: r["symbol"]),
    ]:
        g = group(rows, keyfn, haircut)
        lines.append(f"\n{title}:")
        for k in sorted(g, key=lambda x: g[x]["ev"], reverse=True):
            lines.append(_row(str(k), g[k]))
    if all(r["regime"] == "(belum dilog)" for r in rows):
        lines.append(
            "\nCATATAN: bucket REGIME kosong karena field 'regime' belum ditulis "
            "ke log. Tambahkan regime ke event forward_close (regime sudah dihitung "
            "di forward.py:289) — sesudah itu skrip ini otomatis mengisinya.")
    return "\n".join(lines)


def _selfcheck():
    # 2 menang (+1 each) 1 kalah (-2): EV=(1+1-2)/3=0 ; win%=66.7
    rows = [
        {"pnl": 1.0, "r": 1, "reason": "tp", "side": "long", "conviction": 0.8, "regime": "trend"},
        {"pnl": 1.0, "r": 1, "reason": "tp", "side": "long", "conviction": 0.8, "regime": "trend"},
        {"pnl": -2.0, "r": -2, "reason": "sl", "side": "short", "conviction": 0.3, "regime": "chaos"},
    ]
    s = summarize(rows)
    assert s["n"] == 3 and abs(s["ev"]) < 1e-9, s
    assert abs(s["wr"] - 2 / 3) < 1e-9, s
    g = group(rows, lambda r: r["regime"], 0.0)
    assert abs(g["trend"]["ev"] - 1.0) < 1e-9 and abs(g["chaos"]["ev"] + 2.0) < 1e-9, g
    # haircut menggeser EV turun sebesar haircut
    assert abs(summarize(rows, 0.1)["ev"] + 0.1) < 1e-9
    print("selfcheck OK")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--selfcheck":
        _selfcheck(); sys.exit(0)
    haircut = 0.0
    if args and args[0] == "--haircut":
        haircut = float(args[1]); args = args[2:]
    path = args[0] if args else "logs/trades.jsonl"
    print(report(load(path), haircut))
