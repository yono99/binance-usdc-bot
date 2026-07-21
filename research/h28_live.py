#!/usr/bin/env python3
"""H28 MIKRO-LIVE — eksekusi basket kecil dgn kill-switch permanen (addendum pemilik).

  python h28_live.py --once            # DRY: satu evaluasi, tanpa order (verifikasi)
  python h28_live.py                   # DRY daemon (tanpa order, catat "seandainya")
  python h28_live.py --live            # UANG NYATA (butuh saldo ≥ $25 di futures)

Sinyal identik paper-test (gate gap>0.10, hold 10 hari, ivol 60/beta 60, beku).
Beda: basket 5+5 kaki × $5 notional (total $50), skor dari panel USDT (riset),
EKSEKUSI di kembaran USDC (fee promo). KILL-SWITCH permanen: DD>15% notional
atau 6 siklus negatif — state 'dead' menolak hidup lagi walau di-restart.
Paper-test Tahap 1 tetap berjalan terpisah dan tak tersentuh.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from bot import h28live as hl, xs_signals as xss
from bot.config import load_settings
from bot.exchange import Exchange
from bot.logger import log
from h28_forward import (GATE_THR, HOLD_DAYS, IVOL_WIN, BETA_WIN, QUANTILE,  # noqa: F401
                         UNIVERSE_FILE, closed_daily_panel, fetch_dvol_last)

ROOT = Path(__file__).resolve().parent.parent
OUTDIR = ROOT / "data" / "h28_live"
STATE = OUTDIR / "state.json"
TRADES = OUTDIR / "trades.jsonl"


def usdc_twin(sym_usdt: str, markets: dict) -> str | None:
    twin = sym_usdt.replace("/USDT:USDT", "/USDC:USDC")
    return twin if twin in markets else None


def exec_price(ex, sym: str, side: str, per_leg: float, live: bool,
               reduce_only: bool = False) -> float | None:
    """Market order (live) atau harga ticker (dry). Return harga fill efektif."""
    try:
        last = float(ex.ticker(sym)["last"])
        if not live:
            return last                                   # DRY: asumsi fill di last
        qty = float(ex.client.amount_to_precision(sym, per_leg / last))
        params = {"reduceOnly": True} if reduce_only else {}
        o = ex.client.create_order(sym, "market", side, qty, params=params)
        return float(o.get("average") or o.get("price") or last)
    except Exception as e:  # boundary — satu kaki gagal dicatat, tak menghentikan basket
        log.error(f"H28-LIVE order {side} {sym} gagal: {e}")
        return None


def evaluate(ex, state: dict, live: bool) -> dict:
    tag = "LIVE" if live else "DRY"
    universe = [s.strip() for s in UNIVERSE_FILE.read_text().split() if s.strip()]
    panel = closed_daily_panel(ex, universe)
    if panel.shape[1] < 20:
        log.warning(f"H28-{tag}: panel tipis — lewati")
        return state
    cols = list(panel.columns)
    close = panel.to_numpy()
    last_date = str(panel.index[-1].date())
    per_leg = hl.leg_notional()

    # --- tutup basket jatuh tempo ---
    o = state.get("open")
    if o and (pd.Timestamp(last_date) - pd.Timestamp(o["entry_date"])).days >= HOLD_DAYS:
        exit_px = {}
        for s in o["longs"]:
            p = exec_price(ex, s, "sell", per_leg, live, reduce_only=True)
            if p:
                exit_px[s] = p
        for s in o["shorts"]:
            p = exec_price(ex, s, "buy", per_leg, live, reduce_only=True)
            if p:
                exit_px[s] = p
        pnl = hl.basket_pnl_usd(o["entry_px"], exit_px, o["longs"], o["shorts"], per_leg)
        rec = {"mode": tag, "entry_date": o["entry_date"], "exit_date": last_date,
               "pnl_usd": round(pnl, 4), "n_legs": len(o["longs"]) + len(o["shorts"])}
        OUTDIR.mkdir(parents=True, exist_ok=True)
        with TRADES.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        state["open"] = None
        trades = [json.loads(x) for x in TRADES.read_text(encoding="utf-8").splitlines()]
        dead, why = hl.kill_switch(trades)
        log.info(f"H28-{tag} TUTUP {o['entry_date']}→{last_date}: pnl=${pnl:+.2f} "
                 f"(total {len(trades)} siklus)")
        if dead:
            state["dead"], state["dead_reason"] = True, why
            log.error(f"H28-{tag} {why} — MATI PERMANEN.")
            return state

    # --- gate & buka ---
    dvol = fetch_dvol_last()
    btc = [i for i, c in enumerate(cols) if c.startswith("BTC")]
    if dvol is None or not btc:
        return state
    r = xss.returns_panel(close)
    rv30 = float(np.nanstd(r[-30:, btc[0]]) * np.sqrt(365))
    gap = dvol / 100.0 - rv30
    log.info(f"H28-{tag} {last_date}: gap={gap:+.3f}, gate={'AKTIF' if gap > GATE_THR else 'off'}, "
             f"basket={'ADA' if state.get('open') else 'kosong'}")

    if state.get("open") is None and gap > GATE_THR:
        if live and ex.equity_usdc(fallback=0.0) < 25.0:
            log.warning("H28-LIVE: saldo < $25 — basket TIDAK dibuka.")
            return state
        sc = xss.score_ivol(close, btc[0], IVOL_WIN, BETA_WIN)[-1]
        scores = {}
        for i, s in enumerate(cols):
            twin = usdc_twin(s, ex.markets)
            if twin and np.isfinite(sc[i]):
                scores[twin] = float(sc[i])
        longs, shorts = hl.select_legs(scores)
        if not longs:
            log.warning(f"H28-{tag}: kandidat USDC < 10 ({len(scores)}) — lewati.")
            return state
        entry_px = {}
        for s in longs:
            p = exec_price(ex, s, "buy", per_leg, live)
            if p:
                entry_px[s] = p
        for s in shorts:
            p = exec_price(ex, s, "sell", per_leg, live)
            if p:
                entry_px[s] = p
        state["open"] = {"entry_date": last_date, "gap": round(gap, 4), "longs": longs,
                         "shorts": shorts, "entry_px": entry_px, "per_leg": per_leg}
        log.info(f"H28-{tag} BUKA {last_date}: gap={gap:+.3f}, "
                 f"{len(longs)}L/{len(shorts)}S × ${per_leg:g}")
    state["last_eval"] = last_date
    return state


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="UANG NYATA (default: DRY)")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=float, default=3600.0)
    args = p.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {"open": None, "last_eval": ""}
    if state.get("dead"):
        log.error(f"H28-LIVE MATI PERMANEN ({state.get('dead_reason')}). "
                  "Sesuai pra-registrasi: tidak boleh dihidupkan lagi.")
        return
    ex = Exchange(load_settings())
    if args.live:
        log.warning("=== H28 MIKRO-LIVE: UANG NYATA. Kill-switch DD>15%/$50 atau "
                    "6 siklus negatif = mati permanen. ===")
    else:
        log.info("=== H28 mikro DRY (tanpa order) ===")

    while True:
        if args.once or hl.is_enabled():
            today = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
            yesterday = str((pd.Timestamp(today) - pd.Timedelta(days=1)).date())
            if args.once or state.get("last_eval", "") < yesterday:
                state = evaluate(ex, state, args.live)
                STATE.write_text(json.dumps(state, indent=1))
                if state.get("dead"):
                    return
        else:
            log.info("H28-LIVE nonaktif (toggle OFF) — menunggu, tidak evaluasi.")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
