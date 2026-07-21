#!/usr/bin/env python3
"""Forward PAPER-test H28 (VRP-DVOL gate pada basket short-ivol/long-ivol).

  python h28_forward.py --once          # satu evaluasi (verifikasi)
  python h28_forward.py                 # daemon: evaluasi tiap hari ~00:05 UTC

ATURAN (dibekukan dari riset 2026-07-02 — TIDAK boleh di-tune selama test):
- Gate: gap = DVOL(BTC)/100 − RV30(BTC, tahunan) > 0.10 pada close harian terakhir.
- Basket: rank −ivol (ivol 60d, beta 60d vs BTC); LONG kuantil ivol-rendah 0.3,
  SHORT kuantil ivol-tinggi 0.3, dollar-neutral, equal-weight.
- Hold: 10 hari kalender; satu basket aktif pada satu waktu; tutup lalu boleh
  buka lagi bila gate aktif.
- PnL dicatat NET biaya deklarasi 4×(0.02%+0.05%) = 0.28%/siklus.
- PAPER ONLY: tak ada order; hanya state + trades.jsonl.

Konteks: H28 DITOLAK oleh palang (replikasi: mean −50%, p_adj=0.336) tapi
satu-satunya sinyal dgn OOS positif konsisten dua sampel. Test ini mengukur
TANDA efek secara forward dengan biaya nol. Butuh berbulan-bulan untuk n
berarti — jangan tarik kesimpulan dari <10 siklus.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from bot import xs_signals as xss, xsectional as xs
from bot.config import load_settings
from bot.exchange import Exchange
from bot.logger import log
from vrp_alpha import DVOL_URL  # endpoint publik yang sama dgn riset

ROOT = Path(__file__).resolve().parent.parent
OUTDIR = ROOT / "data" / "h28_forward"
STATE = OUTDIR / "state.json"
TRADES = OUTDIR / "trades.jsonl"
UNIVERSE_FILE = ROOT / "h28_universe.txt"

GATE_THR = 0.10
HOLD_DAYS = 10
QUANTILE = 0.3
IVOL_WIN, BETA_WIN = 60, 60
COST_FRAC = 4 * (0.02 + 0.05) / 100          # deklarasi riset: 0.28%/siklus
HIST_BARS = 160                               # warmup ivol+beta + buffer


def fetch_dvol_last() -> float | None:
    import urllib.request
    t1 = int(time.time() * 1000)
    t0 = t1 - 50 * 86400_000
    try:
        with urllib.request.urlopen(DVOL_URL.format(t0=t0, t1=t1), timeout=30) as resp:
            data = json.loads(resp.read())["result"]["data"]
        return float(data[-1][4]) if data else None
    except Exception as e:  # boundary
        log.warning(f"DVOL gagal: {e}")
        return None


def closed_daily_panel(ex: Exchange, symbols: list[str]) -> pd.DataFrame:
    """Panel close harian TERTUTUP (bar hari berjalan dibuang)."""
    dfs = {}
    today = pd.Timestamp.now(tz="UTC").normalize()
    for sym in symbols:
        try:
            df = ex.ohlcv(sym, "1d", limit=HIST_BARS)
            df = df[df.index < today]                     # hanya bar tertutup
            if len(df) >= IVOL_WIN + BETA_WIN + 5:
                dfs[sym] = df
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")
    return xs.align_close_panel(dfs)


def evaluate(ex: Exchange, state: dict) -> dict:
    panel = closed_daily_panel(ex, state["universe"])
    if panel.shape[1] < 20:
        log.warning(f"panel tipis ({panel.shape[1]} simbol) — lewati evaluasi")
        return state
    cols = list(panel.columns)
    close = panel.to_numpy()
    last_date = str(panel.index[-1].date())
    price_now = {s: float(close[-1, i]) for i, s in enumerate(cols)}

    # --- tutup basket bila jatuh tempo ---
    if state.get("open"):
        o = state["open"]
        age = (pd.Timestamp(last_date) - pd.Timestamp(o["entry_date"])).days
        if age >= HOLD_DAYS:
            longs = [price_now[s] / o["entry_prices"][s] - 1.0
                     for s in o["long"] if s in price_now]
            shorts = [price_now[s] / o["entry_prices"][s] - 1.0
                      for s in o["short"] if s in price_now]
            pnl = float(np.mean(longs) - np.mean(shorts) - COST_FRAC)
            rec = {"entry_date": o["entry_date"], "exit_date": last_date, "age_days": age,
                   "gap_at_entry": o["gap"], "pnl_net": round(pnl, 6),
                   "n_long": len(longs), "n_short": len(shorts)}
            with TRADES.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            hist = [json.loads(x) for x in TRADES.read_text(encoding="utf-8").splitlines()]
            pnls = [h["pnl_net"] for h in hist]
            log.info(f"H28 TUTUP {o['entry_date']}→{last_date}: pnl={pnl:+.4%} | "
                     f"kumulatif n={len(pnls)}, mean={np.mean(pnls):+.4%}, "
                     f"win={np.mean([p > 0 for p in pnls]):.0%}")
            state["open"] = None

    # --- gate & buka basket baru ---
    dvol = fetch_dvol_last()
    btc = [i for i, c in enumerate(cols) if c.startswith("BTC")]
    if dvol is None or not btc:
        return state
    r = xss.returns_panel(close)
    rv30 = float(np.nanstd(r[-30:, btc[0]]) * np.sqrt(365))
    gap = dvol / 100.0 - rv30
    log.info(f"H28 {last_date}: DVOL={dvol:.1f}, RV30={rv30 * 100:.1f}, gap={gap:+.3f}, "
             f"gate={'AKTIF' if gap > GATE_THR else 'off'}, "
             f"basket={'ADA' if state.get('open') else 'kosong'}")

    if state.get("open") is None and gap > GATE_THR:
        sc = xss.score_ivol(close, btc[0], IVOL_WIN, BETA_WIN)[-1]
        valid = [i for i in range(len(cols)) if np.isfinite(sc[i])]
        if len(valid) >= 20:
            order = sorted(valid, key=lambda i: sc[i])
            k = max(1, int(len(valid) * QUANTILE))
            short_syms = [cols[i] for i in order[:k]]     # skor rendah = ivol tinggi = SHORT
            long_syms = [cols[i] for i in order[-k:]]     # skor tinggi = ivol rendah = LONG
            state["open"] = {"entry_date": last_date, "gap": round(gap, 4),
                             "long": long_syms, "short": short_syms,
                             "entry_prices": {s: price_now[s] for s in long_syms + short_syms}}
            log.info(f"H28 BUKA {last_date}: gap={gap:+.3f}, {k} long / {k} short")
    state["last_eval"] = last_date
    return state


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=float, default=3600.0)
    args = p.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    ex = Exchange(load_settings())
    universe = [s.strip() for s in UNIVERSE_FILE.read_text().split() if s.strip()]
    state = json.loads(STATE.read_text()) if STATE.exists() else {"open": None, "last_eval": ""}
    state["universe"] = universe
    log.info(f"=== H28 FORWARD PAPER-TEST: {len(universe)} simbol, gate>{GATE_THR}, "
             f"hold {HOLD_DAYS}d, PAPER ONLY ===")

    while True:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday = str((pd.Timestamp(today) - pd.Timedelta(days=1)).date())
        if args.once or state.get("last_eval", "") < yesterday:
            state = evaluate(ex, state)
            STATE.write_text(json.dumps({k: v for k, v in state.items() if k != "universe"},
                                        indent=1))
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
