#!/usr/bin/env python3
"""Fase 4 — H27 dislokasi basis lintas-venue (Binance vs Bybit) cross-sectional.

  python basis_alpha.py --symbols <...> --snapshot-dir data/snap_smallcap1400

Beda dari v5 (DITOLAK): v5 = time-series per-simbol @15m; ini RELATIF antar-pair
@1d — fade pair yang premium-nya melebar vs baseline sendiri (z-score 30d).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from rich.console import Console

from bot import xs_signals as xss, xsectional as xs
from bot.altdata import fetch_bybit_close
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log

console = Console()
BYBIT_CACHE = Path("data/snap_bybit")


def bybit_close_cached(symbol: str, tf: str, since_ms: int, bars: int) -> pd.Series:
    BYBIT_CACHE.mkdir(parents=True, exist_ok=True)
    p = BYBIT_CACHE / (symbol.replace("/", "_").replace(":", "_") + f"__{tf}.pkl")
    if p.exists():
        return pd.read_pickle(p)
    s = fetch_bybit_close(symbol, tf, since_ms, bars)
    if len(s):
        s.to_pickle(p)
    return s


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", required=True)
    p.add_argument("--tf", default="1d")
    p.add_argument("--bars", type=int, default=1400)
    p.add_argument("--train", type=int, default=300)
    p.add_argument("--test", type=int, default=120)
    p.add_argument("--z-window", type=int, default=30)
    p.add_argument("--holds", nargs="*", type=int, default=[2, 5])
    p.add_argument("--quantile", type=float, default=0.3)
    p.add_argument("--fee", type=float, default=0.02)
    p.add_argument("--slippage", type=float, default=0.05)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--snapshot-dir", default=None)
    args = p.parse_args()

    ex = Exchange(load_settings())
    dfs, bybit = {}, {}
    for sym in args.symbols:
        try:
            df = load_ohlcv(args.snapshot_dir, sym, args.tf) if args.snapshot_dir else None
            if df is None:
                df = fetch_history(ex, sym, args.tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(df, args.snapshot_dir, sym, args.tf)
            bc = bybit_close_cached(sym, args.tf, int(df.index[0].timestamp() * 1000), args.bars)
            if len(bc) < args.bars * 0.7:
                log.warning(f"lewati {sym}: histori Bybit tipis ({len(bc)})")
                continue
            dfs[sym], bybit[sym] = df, bc
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")

    panel = xs.align_close_panel(dfs)
    if panel.shape[1] < 8:
        console.print("[red]Butuh >=8 simbol dgn data dua venue.[/red]")
        return
    close = panel.to_numpy()
    byb = pd.DataFrame({s: bybit[s].reindex(panel.index, method="ffill")
                        for s in panel.columns})[list(panel.columns)].to_numpy()
    basis = close / np.where(byb > 0, byb, np.nan) - 1.0
    log.info(f"Panel dua-venue: {panel.shape[1]} simbol × {len(panel)} bar ({args.tf}).")

    cost_frac = 4 * (args.fee + args.slippage) / 100 * args.stress_mult
    panels = {f"vb{args.z_window}": xss.score_venue_basis(basis, args.z_window)}
    n_trials = len(panels) * len(args.holds)
    windows, oos = xs.walk_forward_scores(close, panels, args.holds, args.quantile,
                                          cost_frac, args.train, args.test)
    log.info(f"[H27 basis] {len(windows)} window, grid={n_trials}, cost/rebal={cost_frac:.4%}")
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
