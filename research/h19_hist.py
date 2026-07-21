#!/usr/bin/env python3
"""H19 OI crowding-freshness — uji HISTORIS penuh via arsip metrics Binance Vision
(OI 5-menit sejak ~2021; batas 30 hari hanya di REST API, bukan di arsip).

  python h19_hist.py --days 450          # 30 small-cap teratas, grid pra-registrasi

Pipeline: unduh metrics harian (paralel, cache) -> panel OI 1h -> join close 1h +
funding -> score_oi_crowding (grid {24,72}h x holds {24,72} = 4 trial) ->
walk_forward_scores + verdict Bonferroni. Palang 4 standar.
"""
import argparse

import numpy as np
import pandas as pd
from rich.console import Console

from bot import carry, vision, xs_signals as xss, xsectional as xs
from bot.altdata import fetch_funding
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.exchange import Exchange
from bot.logger import log

console = Console()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols-file", default=None, help="file daftar simbol (spasi)")
    p.add_argument("--top", type=int, default=30)
    p.add_argument("--days", type=int, default=450)
    p.add_argument("--train", type=int, default=4000)
    p.add_argument("--test", type=int, default=1500)
    p.add_argument("--fee", type=float, default=0.02)
    p.add_argument("--slippage", type=float, default=0.05)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--funding-min", type=float, default=0.0003,
                   help="gerbang aktivasi |funding|/8h (dipilih dari coverage, bukan return)")
    p.add_argument("--fresh", action="store_true", help="abaikan cache panel")
    args = p.parse_args()

    import pickle
    from pathlib import Path as _P
    cache_p = _P("data/vision/h19_panel.pkl")
    if cache_p.exists() and not args.fresh:
        cols, index, close, oi, level = pickle.load(open(cache_p, "rb"))
        console.print(f"[cyan]Panel dari cache: {len(cols)} simbol × {len(index)} bar[/cyan]")
        _run(args, cols, close, oi, level)
        return

    if args.symbols_file:
        symbols = open(args.symbols_file).read().split()[:args.top]
    else:
        from pathlib import Path
        rows = []
        for pth in Path("data/snap").glob("*__1d.pkl"):
            df = pd.read_pickle(pth)
            if len(df) < args.days:
                continue
            t = df.iloc[-args.days:]
            parts = pth.name.replace("__1d.pkl", "").split("_")
            rows.append((f"{parts[0]}/{parts[1]}:{parts[2]}",
                         float((t.close * t.volume).median())))
        rows.sort(key=lambda r: -r[1])
        symbols = [s for s, _ in rows[20:20 + args.top]]   # buang 20 majors teratas

    end = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=2)
    days = [str((end - pd.Timedelta(days=i)).date()) for i in range(args.days)]
    log.info(f"H19 hist: {len(symbols)} simbol × {args.days} hari metrics …")

    ex = Exchange(load_settings())
    dfs, fundings, oi_map = {}, {}, {}
    for sym in symbols:
        try:
            oi = vision.load_metrics_oi(sym.replace("USDC", "USDT"), days)
            if len(oi) < args.days * 100:                  # ~288/hari; minimal ~35%
                log.warning(f"{sym}: OI tipis ({len(oi)}) — lewati")
                continue
            df = fetch_history(ex, sym, "1h", args.days * 24 + 100)
            fundings[sym] = fetch_funding(ex, sym, int(df.index[0].timestamp() * 1000),
                                          max_points=args.days * 3 + 100)
            dfs[sym], oi_map[sym] = df, oi.resample("1h").last()
            log.info(f"{sym}: OI {len(oi):,} titik, close {len(df)} bar")
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")

    panel = xs.align_close_panel(dfs)
    if panel.shape[1] < 10:
        console.print(f"[red]Simbol selaras cuma {panel.shape[1]} — kurang.[/red]")
        return
    cols = list(panel.columns)
    close = panel.to_numpy()
    oi = pd.DataFrame({s: oi_map[s].reindex(panel.index, method="ffill")
                       for s in cols})[cols].to_numpy()
    level, _ = carry.align_funding(fundings, panel.index, cols)
    log.info(f"Panel: {len(cols)} simbol × {len(panel)} bar 1h.")
    pickle.dump((cols, panel.index, close, oi, level), open(cache_p, "wb"))
    _run(args, cols, close, oi, level)


def _run(args, cols, close, oi, level) -> None:
    import numpy as np
    from bot import xs_signals as xss, xsectional as xs
    # Diagnostik AKTIVASI (dipilih dari coverage, BUKAN dari return):
    for x in (0.0001, 0.0002, 0.0003, 0.0005):
        frac = float(np.mean(np.abs(level) > x))
        active_rows = float(np.mean((np.abs(level) > x).sum(axis=1) >= 8))
        console.print(f"  |funding|>{x*100:.2f}%/8h: {frac:.1%} sel aktif; "
                      f"{active_rows:.1%} bar punya ≥8 simbol aktif")

    cost = 4 * (args.fee + args.slippage) / 100 * args.stress_mult
    panels = {f"oi{w}": xss.score_oi_crowding(level, oi, w, funding_min=args.funding_min)
              for w in (24, 72)}
    holds = [24, 72]
    n_trials = len(panels) * len(holds)
    windows, oos = xs.walk_forward_scores(close, panels, holds, 0.3, cost,
                                          args.train, args.test)
    for i, w in enumerate(windows, 1):
        console.print(f"  w{i}: {w.params['score']}/{w.params['hold']} "
                      f"IS {w.is_sharpe:+.3f} (n={w.is_n}) → OOS {w.oos_mean:+.4%} (n={w.oos_n})")
    v = xs.verdict(oos, n_trials)
    console.print(f"OOS: n={v['n']}, mean={v['mean']:+.4%}, win={v.get('win_rate', 0):.1%}, "
                  f"Sharpe={v.get('sharpe', '-')}, p_adj={v.get('p_adj', '-')}")
    color = "green" if v["ok"] else "red"
    console.print(f"[{color}]H19 verdict: {'LOLOS' if v['ok'] else 'DITOLAK'} — {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
