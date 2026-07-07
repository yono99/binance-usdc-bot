"""Shadow keputusan FLAT Gemini — ukur biaya sinyal yang DILEWATKAN.

Filosofi identik VRP/MTF: alat ukur, bukan gerbang — tak pernah memblokir trading.
Bedanya: record dua-fase (pending → settled butuh UPDATE) → SQLite, bukan jsonl.

Kenapa ada: prompt trader sengaja bias flat ("mayoritas sinyal = NOISE"), tapi
keputusan flat tak pernah dicatat — kita menilai trade yang diambil, tak pernah
menilai yang dilewatkan. Modul ini mencatat tiap flat asli Gemini lalu, setelah
`horizon_bars` bar, mengukur: apakah ada gerakan tradeable ≥ k_atr×ATR (≈1R)
ke SALAH SATU arah yang terlewat (miss)?

Verdict PRA-REGISTRASI (jangan digeser setelah lihat hasil):
FLAT_BIAS_TOO_EXPENSIVE ⇔ n_settled ≥ sample DAN miss_rate > miss_threshold
DAN ≥1 bucket regime (n≥50) melewatinya. Hanya verdict ini yang membenarkan
menyentuh prompt — perubahan prompt = commit terpisah.

Config (config.yaml `flat_shadow:`): mode off|shadow, horizon_bars, k_atr,
sample, miss_threshold, retention_days.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from .logger import log
from .store import _conn, init_db

# mode default "off": aktif HANYA bila config punya blok flat_shadow (produksi) —
# supaya cfg minimal di tes/skrip lama tak diam-diam menulis ke bot.db nyata.
_DEF = {"mode": "off", "horizon_bars": 16, "k_atr": 1.0,
        "sample": 200, "miss_threshold": 0.35, "retention_days": 90}
_last_prune = 0.0


def _cfg(cfg: dict) -> dict:
    return {**_DEF, **(cfg.get("flat_shadow") or {})}


def record_flat(mode: str, symbol: str, price: float, atr: float, dec: dict,
                regime: str, bar_ts: str, cfg: dict) -> None:
    """Catat satu keputusan flat asli Gemini (pending). Boundary — tak boleh ganggu."""
    fc = _cfg(cfg)
    if fc["mode"] == "off" or not price or not atr or atr <= 0:
        return
    try:
        init_db()
        with _conn() as c:
            c.execute(
                "INSERT INTO flat_shadow (ts, mode, symbol, price, atr, conviction, "
                "rationale, regime, bar_ts) VALUES (?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), mode, symbol, float(price),
                 float(atr), dec.get("conviction"), (dec.get("rationale") or "")[:200],
                 regime, str(bar_ts)))
            c.commit()
    except Exception as e:  # boundary
        log.warning(f"flat_shadow record {symbol} gagal: {e}")


def _settle_one(row, buf: pd.DataFrame, horizon: int, k: float) -> dict | None:
    """Hitung MFE dua arah pada bar SETELAH bar_ts. None bila belum cukup bar
    (kecuali force). Partial (<horizon bar) hanya dipakai saat force-settle."""
    after = buf[buf.index > pd.Timestamp(row["bar_ts"])]
    if after.empty:
        return None
    after = after.iloc[:horizon]
    price, atr = row["price"], row["atr"]
    mfe_up = float(after["high"].max()) - price
    mfe_dn = price - float(after["low"].min())
    need = k * atr
    up_hit, dn_hit = mfe_up >= need, mfe_dn >= need
    return {"n_bars": len(after),
            "mfe_up_pct": round(mfe_up / price * 100, 3),
            "mfe_dn_pct": round(mfe_dn / price * 100, 3),
            "miss": 1 if (up_hit or dn_hit) else 0,
            "miss_dir": ("both" if (up_hit and dn_hit) else
                         "up" if up_hit else "down" if dn_hit else None)}


def settle_pending(mode: str, buffers: dict, cfg: dict) -> int:
    """Settle record pending yang horizonnya sudah lewat; prune settled tua ~per jam.
    Buffer kurang → tetap pending; force-settle parsial setelah 48 jam. Boundary."""
    global _last_prune
    fc = _cfg(cfg)
    if fc["mode"] == "off":
        return 0
    horizon, k = int(fc["horizon_bars"]), float(fc["k_atr"])
    n = 0
    try:
        init_db()
        now = datetime.now(timezone.utc)
        with _conn() as c:
            rows = c.execute("SELECT * FROM flat_shadow WHERE status='pending' AND mode=?",
                             (mode,)).fetchall()
            for row in rows:
                buf = buffers.get(row["symbol"])
                if buf is None or not len(buf):
                    continue
                res = _settle_one(row, buf, horizon, k)
                age_h = (now - datetime.fromisoformat(row["ts"])).total_seconds() / 3600
                if res is None or (res["n_bars"] < horizon and age_h < 48):
                    continue                      # belum matang; force parsial hanya >48j
                c.execute("UPDATE flat_shadow SET status='settled', mfe_up_pct=?, "
                          "mfe_dn_pct=?, miss=?, miss_dir=? WHERE id=?",
                          (res["mfe_up_pct"], res["mfe_dn_pct"], res["miss"],
                           res["miss_dir"], row["id"]))
                n += 1
            if time.time() - _last_prune > 3600:
                _last_prune = time.time()
                cutoff = (now - timedelta(days=int(fc["retention_days"]))).isoformat()
                c.execute("DELETE FROM flat_shadow WHERE status='settled' AND ts<?", (cutoff,))
            c.commit()
    except Exception as e:  # boundary
        log.warning(f"flat_shadow settle gagal: {e}")
    return n


def _rate(rows: list) -> dict:
    n = len(rows)
    return {"n": n, "miss_rate": round(sum(r["miss"] for r in rows) / n, 3) if n else None}


def report(mode: str | None = None, cfg: dict | None = None) -> dict:
    """Miss-rate keseluruhan / per-regime / per-bucket conviction + verdict pra-registrasi."""
    fc = _cfg(cfg or {})
    try:
        init_db()
        with _conn() as c:
            q = "SELECT symbol, conviction, regime, miss, miss_dir FROM flat_shadow WHERE status='settled'"
            args: list = []
            if mode:
                q += " AND mode=?"
                args.append(mode)
            rows = [dict(r) for r in c.execute(q, args).fetchall()]
            pending = c.execute("SELECT COUNT(*) FROM flat_shadow WHERE status='pending'"
                                + (" AND mode=?" if mode else ""), args).fetchone()[0]
    except Exception as e:  # boundary
        return {"verdict": "ERROR", "reason": str(e)}
    if not rows:
        return {"verdict": "NO_DATA", "reason": "belum ada flat settled", "pending": pending,
                "mode": mode}
    overall = _rate(rows)
    by_regime = {}
    for reg in sorted({r["regime"] or "unknown" for r in rows}):
        by_regime[reg] = _rate([r for r in rows if (r["regime"] or "unknown") == reg])
    by_conv = {}
    for lo, hi, tag in ((0.0, 0.3, "<0.3"), (0.3, 0.55, "0.3-0.55"), (0.55, 1.01, ">=0.55")):
        sub = [r for r in rows if lo <= (r["conviction"] or 0) < hi]
        if sub:
            by_conv[tag] = _rate(sub)
    dirs = {"up": sum(1 for r in rows if r["miss_dir"] in ("up", "both")),
            "down": sum(1 for r in rows if r["miss_dir"] in ("down", "both"))}
    res = {"mode": mode, **overall, "pending": pending, "per_regime": by_regime,
           "per_conviction": by_conv, "miss_dirs": dirs,
           "params": {k: fc[k] for k in ("horizon_bars", "k_atr", "sample", "miss_threshold")}}
    if overall["n"] < fc["sample"]:
        res["verdict"] = "INSUFFICIENT"
        res["reason"] = f"butuh ≥{fc['sample']} flat settled (ada {overall['n']})"
    else:
        regime_hit = any(v["n"] >= 50 and v["miss_rate"] > fc["miss_threshold"]
                         for v in by_regime.values())
        if overall["miss_rate"] > fc["miss_threshold"] and regime_hit:
            res["verdict"] = "FLAT_BIAS_TOO_EXPENSIVE"
            res["reason"] = (f"miss_rate {overall['miss_rate']} > {fc['miss_threshold']} "
                             "dan ada regime yang melewatinya — bias flat layak dilonggarkan DI regime itu")
        else:
            res["verdict"] = "NOT_PROVEN"
            res["reason"] = "flat Gemini mayoritas benar; bias flat dipertahankan"
    return res
