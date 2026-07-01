#!/usr/bin/env python3
"""Fase 4 — H28 VRP timing via Deribit DVOL: harvest premi vol hanya saat premi ada.

  python vrp_alpha.py --symbols <...> --snapshot-dir data/snap_smallcap1400

Conditioner = gap DVOL(BTC) − RV30(BTC) tahunan (satu time-series, publik).
Sinyal = −ivol (SUDAH DITOLAK polos di Fase 2) yang hanya aktif saat gap di atas
ambang STRUKTURAL {0, 0.10} (dideklarasikan di muka, bukan di-tune). Beban
pembuktian sepenuhnya di conditioner.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
from rich.console import Console

from bot import xs_signals as xss, xsectional as xs
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log

console = Console()
DVOL_CACHE = Path("data/snap_dvol_btc_1d.pkl")
DVOL_URL = ("https://www.deribit.com/api/v2/public/get_volatility_index_data"
            "?currency=BTC&resolution=1D&start_timestamp={t0}&end_timestamp={t1}")


def fetch_dvol_daily(start_ms: int) -> pd.Series:
    """DVOL BTC harian (close) dari REST publik Deribit, paginated, cached."""
    if DVOL_CACHE.exists():
        return pd.read_pickle(DVOL_CACHE)
    rows, t0 = [], start_ms
    end = int(time.time() * 1000)
    while t0 < end:
        t1 = min(t0 + 900 * 86400_000, end)               # ≤900 hari per halaman
        with urllib.request.urlopen(DVOL_URL.format(t0=t0, t1=t1), timeout=30) as resp:
            data = json.loads(resp.read())["result"]["data"]
        rows += data
        if not data:
            break
        t0 = t1 + 86400_000
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r[0] for r in rows], unit="ms", utc=True)
    s = pd.Series([float(r[4]) for r in rows], index=idx).sort_index()
    s = s[~s.index.duplicated(keep="last")]
    DVOL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    s.to_pickle(DVOL_CACHE)
    return s


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", required=True)
    p.add_argument("--tf", default="1d")
    p.add_argument("--bars", type=int, default=1400)
    p.add_argument("--train", type=int, default=300)
    p.add_argument("--test", type=int, default=120)
    p.add_argument("--holds", nargs="*", type=int, default=[5, 10])
    p.add_argument("--gaps", nargs="*", type=float, default=[0.0, 0.10],
                   help="ambang struktural gap IV−RV (tahunan)")
    p.add_argument("--quantile", type=float, default=0.3)
    p.add_argument("--fee", type=float, default=0.02)
    p.add_argument("--slippage", type=float, default=0.05)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--snapshot-dir", default=None)
    args = p.parse_args()

    ex = Exchange(load_settings())
    dfs = {}
    for sym in args.symbols:
        try:
            df = load_ohlcv(args.snapshot_dir, sym, args.tf) if args.snapshot_dir else None
            if df is None:
                df = fetch_history(ex, sym, args.tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(df, args.snapshot_dir, sym, args.tf)
            dfs[sym] = df
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")

    panel = xs.align_close_panel(dfs)
    btc_cols = [i for i, c in enumerate(panel.columns) if c.startswith("BTC")]
    if not btc_cols or panel.shape[1] < 8:
        console.print("[red]Butuh BTC + >=8 simbol.[/red]")
        return
    btc_idx = btc_cols[0]
    close = panel.to_numpy()

    dvol = fetch_dvol_daily(int(panel.index[0].timestamp() * 1000))
    if not len(dvol):
        console.print("[red]DVOL Deribit tak terambil.[/red]")
        return
    iv = (dvol.reindex(panel.index, method="ffill") / 100.0).to_numpy()   # IV tahunan
    r = xss.returns_panel(close)
    rb = pd.Series(r[:, btc_idx], index=panel.index)
    rv = (rb.rolling(30).std() * np.sqrt(365)).to_numpy()                  # RV30 tahunan ≤t
    gap = iv - rv
    cover = float(np.isfinite(gap).mean())
    log.info(f"Panel {panel.shape[1]}×{len(panel)}; DVOL coverage {cover:.0%}; "
             f"gap med {np.nanmedian(gap):+.3f}")

    base = xss.score_ivol(close, btc_idx, ivol_window=60, beta_window=60)
    panels = {}
    for g in args.gaps:
        mask = gap > g
        panels[f"vrp>{g:g}"] = np.where(mask[:, None], base, np.nan)

    cost_frac = 4 * (args.fee + args.slippage) / 100 * args.stress_mult
    n_trials = len(panels) * len(args.holds)
    windows, oos = xs.walk_forward_scores(close, panels, args.holds, args.quantile,
                                          cost_frac, args.train, args.test)
    log.info(f"[H28 VRP] {len(windows)} window, grid={n_trials}, cost/rebal={cost_frac:.4%}")
    for i, w in enumerate(windows, 1):
        console.print(f"  w{i}: {w.params['score']}/{w.params['hold']} "
                      f"IS {w.is_sharpe:+.3f} (n={w.is_n}) → OOS {w.oos_mean:+.4%} (n={w.oos_n})")

    v = xs.verdict(oos, n_trials)
    console.print(f"OOS: n={v['n']}, mean={v['mean']:+.4%}, win={v.get('win_rate', 0):.1%}, "
                  f"Sharpe={v.get('sharpe', '-')}, p_adj={v.get('p_adj', '-')}")
    color = "green" if v["ok"] else "red"
    console.print(f"[{color}]Verdict: {'LOLOS' if v['ok'] else 'DITOLAK'} — {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
