#!/usr/bin/env python3
"""Fase 2 — combiner multi-sinyal: cari beberapa sinyal weak-positif TAK berkorelasi,
gabungkan, uji signifikansi di LOCKBOX (segmen akhir yang tak disentuh saat seleksi).

  python combine.py --tf 1d --bars 2000 --hold 10 --window 60
  python combine.py --fee 0.02 --slippage 0.05   # cost-stress

Riset di USDT (histori panjang), eksekusi USDC. Kandidat: skew, BAB, reversal,
coskew, Amihud, turnover — semua rasional ekonomi, mekanisme berbeda.
"""
from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from bot import combiner as cb, xs_signals as xss
from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log
from bot.xsectional import align_close_panel, volume_panel

console = Console()

DEFAULT_UNIVERSE = [f"{s}/USDT:USDT" for s in (
    "BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "SOL", "DOT", "LTC", "LINK", "AVAX",
    "ATOM", "XLM", "TRX", "ETC", "FIL", "AAVE", "UNI", "NEAR", "ALGO", "SAND",
    "MANA", "AXS", "EGLD", "THETA")]


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--tf", default="1d")
    p.add_argument("--bars", type=int, default=2000)
    p.add_argument("--hold", type=int, default=10, help="hold/rebalance bersama (bar)")
    p.add_argument("--window", type=int, default=60, help="window skor (bar)")
    p.add_argument("--beta-win", type=int, default=60)
    p.add_argument("--quantile", type=float, default=0.3)
    p.add_argument("--lockbox-frac", type=float, default=0.25)
    p.add_argument("--corr-max", type=float, default=0.3)
    p.add_argument("--fee", type=float, default=0.0)
    p.add_argument("--slippage", type=float, default=0.05)
    p.add_argument("--stress-mult", type=float, default=1.0)
    p.add_argument("--weights", choices=["equal", "invvol"], default="equal")
    p.add_argument("--snapshot-dir", default=None)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    tf = args.tf
    symbols = args.symbols or DEFAULT_UNIVERSE
    ex = Exchange(settings)

    dfs = {}
    for sym in symbols:
        try:
            df = load_ohlcv(args.snapshot_dir, sym, tf) if args.snapshot_dir else None
            if df is None:
                df = fetch_history(ex, sym, tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(df, args.snapshot_dir, sym, tf)
            dfs[sym] = df
        except Exception as e:  # boundary
            log.warning(f"lewati {sym}: {e}")

    panel = align_close_panel(dfs)
    btc = [i for i, c in enumerate(panel.columns) if c.startswith("BTC")]
    if not btc or panel.shape[1] < 6:
        console.print("[red]Butuh BTC + >=6 simbol.[/red]")
        return
    bi = btc[0]
    close = panel.to_numpy()
    vol = volume_panel(dfs, panel.index, list(panel.columns))
    w, bw = args.window, args.beta_win
    log.info(f"Panel: {panel.shape[1]} simbol × {len(panel)} bar ({tf}), BTC@{bi}.")

    signals = {
        "skew": xss.score_skew(close, w),
        "bab": xss.score_bab(close, bi, bw),
        "reversal": xss.score_st_reversal(close, bi, max(2, w // 20), bw),
        "coskew": xss.score_coskew(close, bi, w),
        "amihud": xss.score_amihud(close, vol, w),
        "turnover": xss.score_turnover(close, vol, w),
    }
    cost = 4 * (args.fee + args.slippage) / 100 * args.stress_mult
    out = cb.run_combiner(close, signals, args.hold, args.quantile, cost,
                          args.lockbox_frac, warm=max(w, bw) + 2, weights=args.weights)

    # Rata-rata train per sinyal (skrining)
    if "train_means" in out:
        t0 = Table(title="Sinyal (rata-rata return TRAIN per rebalance)")
        for c in ["sinyal", "train mean", "terpilih?"]:
            t0.add_column(c, justify="right")
        for name in signals:
            tm = out["train_means"].get(name, 0.0)
            t0.add_row(name, f"{tm:+.4%}", "YA" if name in out.get("selected", []) else "-")
        console.print(t0)

    if not out.get("selected"):
        console.print(f"[red]DITOLAK — {out['reason']}[/red]")
        return

    console.print(f"\nTerpilih (tak-korelasi, weak-positif): [cyan]{out['selected']}[/cyan]")
    console.print(f"Korelasi train: {out['train_corr']}")
    t2 = Table(title="LOCKBOX (segmen tak tersentuh saat seleksi)")
    for c in ["gabungan mean", "gabungan Sharpe", "tunggal terbaik", "diversifikasi?", "p_adj", "n"]:
        t2.add_column(c, justify="right")
    t2.add_row(f"{out['combined_mean_lockbox']:+.4%}", str(out["combined_sharpe_lockbox"]),
               f"{out['best_single_mean_lockbox']:+.4%}", "YA" if out["diversifies"] else "TIDAK",
               str(out.get("p_adj", "-")), str(out["n_lockbox"]))
    console.print(t2)
    ok = out["ok"] and out["diversifies"]
    color = "green" if ok else "red"
    console.print(f"[{color}]Verdict: {'LOLOS' if ok else 'DITOLAK'} — {out['reason']}[/{color}]")
    if out["combined_mean_lockbox"] > 0 and not out["ok"]:
        console.print("[yellow]Gabungan positif tapi belum signifikan — butuh lebih banyak "
                      "data/sinyal, atau glimmer.[/yellow]")


if __name__ == "__main__":
    main()
