#!/usr/bin/env python3
"""Fase 2 — uji hipotesis cross-sectional berbasis SKOR (H2/H3/H6/H18/H15).

  python xs_alpha.py --hypothesis resid_mom --tf 1h --bars 8000
  python xs_alpha.py --hypothesis leadlag --tf 1h
  python xs_alpha.py --hypothesis ivol|skew|funding_accel

Semua lewat engine skor generik: skor tinggi=long, rendah=short, dollar-neutral,
walk-forward OOS + koreksi multiple-testing.
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from bot import carry, sector, xs_signals as xss, xsectional as xs
from bot.altdata import fetch_funding
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log

console = Console()

DEFAULT_UNIVERSE = [
    "BTC/USDC:USDC", "ETH/USDC:USDC", "SOL/USDC:USDC", "BNB/USDC:USDC", "XRP/USDC:USDC",
    "DOGE/USDC:USDC", "ADA/USDC:USDC", "AVAX/USDC:USDC", "LINK/USDC:USDC", "LTC/USDC:USDC",
    "TRX/USDC:USDC", "DOT/USDC:USDC", "NEAR/USDC:USDC", "ATOM/USDC:USDC", "UNI/USDC:USDC",
]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--hypothesis", required=True,
                   choices=["resid_mom", "leadlag", "ivol", "skew", "funding_accel", "sector",
                            "illiq_shock", "dbeta"])
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--tf", default="1h")
    p.add_argument("--bars", type=int, default=8000)
    p.add_argument("--train", type=int, default=1500)
    p.add_argument("--test", type=int, default=400)
    p.add_argument("--quantile", type=float, default=0.3)
    p.add_argument("--fee", type=float, default=0.0)
    p.add_argument("--slippage", type=float, default=0.03)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--reverse", action="store_true")
    p.add_argument("--holds", nargs="*", type=int, default=None, help="override grid hold (bar)")
    p.add_argument("--windows", nargs="*", type=int, default=None,
                   help="override window skor (bar) — untuk daily pakai satuan hari mis. 30 60")
    p.add_argument("--beta-win", type=int, default=240, help="window beta (bar)")
    p.add_argument("--snapshot-dir", default=None)
    return p.parse_args()


def build_panels(hyp, close, btc_idx, level, windows=None, beta_win=240, vol=None):
    """Kembalikan (score_panels: dict, holds: list) untuk hipotesis terpilih.
    windows/beta_win dalam BAR — sesuaikan dgn timeframe (mis. daily: window 30-60)."""
    if hyp == "resid_mom":       # H3
        w = windows or (120, 240, 480)
        return ({f"rm{lb}": xss.score_residual_momentum(close, btc_idx, lb, beta_win) for lb in w},
                [24, 48])
    if hyp == "leadlag":         # H2
        return ({f"ll_b{bw}": xss.score_btc_leadlag(close, btc_idx, bw)
                 for bw in (60, 120, 240)}, [1, 4, 12])
    if hyp == "ivol":            # H6
        w = windows or (120, 240)
        return ({f"iv{x}": xss.score_ivol(close, btc_idx, x, beta_win) for x in w}, [24, 120])
    if hyp == "skew":            # H18
        w = windows or (720, 1440)
        return ({f"sk{x}": xss.score_skew(close, x) for x in w}, [120, 240])
    if hyp == "funding_accel":   # H15
        return ({f"fa{i}": xss.score_funding_accel(level, i) for i in (8, 24)}, [8, 24])
    if hyp == "sector":          # H13 — klaster naratif, leader→follower (daily: window 60/90)
        w = windows or (60, 90)
        return ({f"sec{cw}": sector.score_sector_leadlag(close, vol, cw, lead_lookback=3)
                 for cw in w}, [5, 10])
    if hyp == "illiq_shock":     # H26 — reversal syok likuiditas (daily: syok 3/5 hari)
        w = windows or (3, 5)
        return ({f"is{sw}": xss.score_illiq_shock(close, vol, sw) for sw in w}, [3, 5])
    if hyp == "dbeta":           # H31 — premi asimetri downside-beta (daily)
        w = windows or (60, 120)
        return ({f"db{x}": xss.score_downside_beta(close, btc_idx, x) for x in w}, [5, 10])
    raise ValueError(hyp)


def main() -> None:
    args = parse_args()
    settings = load_settings()
    cfg = settings.raw
    tf = args.tf
    symbols = args.symbols or DEFAULT_UNIVERSE
    ex = Exchange(settings)
    need_funding = args.hypothesis == "funding_accel"

    dfs, fundings = {}, {}
    for sym in symbols:
        try:
            df = load_ohlcv(args.snapshot_dir, sym, tf) if args.snapshot_dir else None
            if df is None:
                df = fetch_history(ex, sym, tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(df, args.snapshot_dir, sym, tf)
            if need_funding:
                fundings[sym] = fetch_funding(ex, sym, int(df.index[0].timestamp() * 1000))
            dfs[sym] = df
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")

    panel = xs.align_close_panel(dfs)
    btc_cols = [i for i, c in enumerate(panel.columns) if c.startswith("BTC")]
    if not btc_cols or panel.shape[1] < 5:
        console.print("[red]Butuh BTC di panel + >=5 simbol.[/red]")
        return
    btc_idx = btc_cols[0]
    close = panel.to_numpy()
    level = carry.align_funding(fundings, panel.index, list(panel.columns))[0] if need_funding else None
    vol = (xs.volume_panel(dfs, panel.index, list(panel.columns))
           if args.hypothesis in ("sector", "illiq_shock") else None)
    log.info(f"Panel: {panel.shape[1]} simbol × {len(panel)} bar ({tf}), BTC@{btc_idx}.")

    cost_frac = 4 * (args.fee + args.slippage) / 100 * args.stress_mult
    panels, holds = build_panels(args.hypothesis, close, btc_idx, level, args.windows,
                                 args.beta_win, vol)
    if args.holds:
        holds = args.holds
    n_trials = len(panels) * len(holds)
    windows, oos = xs.walk_forward_scores(close, panels, holds, args.quantile, cost_frac,
                                          args.train, args.test, reverse=args.reverse)
    log.info(f"[{args.hypothesis}{' REVERSE' if args.reverse else ''}] "
             f"{len(windows)} window, grid={n_trials}, cost/rebal={cost_frac:.4%}")

    tbl = Table(title=f"H:{args.hypothesis} — {tf}, q={args.quantile}")
    for c in ["window", "score/hold", "IS Sharpe", "IS n", "OOS mean", "OOS n"]:
        tbl.add_column(c, justify="right")
    for i, w in enumerate(windows, 1):
        tbl.add_row(str(i), f"{w.params['score']}/{w.params['hold']}",
                    f"{w.is_sharpe:+.3f}", str(w.is_n), f"{w.oos_mean:+.4%}", str(w.oos_n))
    console.print(tbl)

    v = xs.verdict(oos, n_trials)
    t2 = Table(title="GABUNGAN OOS")
    for c in ["rebalances", "win%", "mean/rebal", "Sharpe", "p_adj"]:
        t2.add_column(c, justify="right")
    t2.add_row(str(v["n"]), f"{v.get('win_rate', 0) * 100:.1f}", f"{v['mean']:+.4%}",
               str(v.get("sharpe", "-")), str(v.get("p_adj", "-")))
    console.print(t2)
    color = "green" if v["ok"] else "red"
    console.print(f"[{color}]Verdict: {'LOLOS' if v['ok'] else 'DITOLAK'} — {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
