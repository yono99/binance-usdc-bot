#!/usr/bin/env python3
"""H19 OI crowding-freshness — pipeline SIAP-JALAN (pra-registrasi Fase 5).

  python h19_report.py                 # gerbang kecukupan: butuh >=180 hari rekaman OI

Loader merakit panel OI per jam dari data/oi/*.jsonl.gz (rekaman oicollect.py),
join funding + close, lalu uji lewat engine standar (walk_forward_scores, grid
maks 4 trial, Bonferroni). Sebelum data cukup: hanya laporan status rekaman.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import pandas as pd
from rich.console import Console

console = Console()
OI_DIR = Path("data/oi")
MIN_DAYS = 180


def oi_panel(oi_dir: Path = OI_DIR) -> pd.DataFrame:
    """Panel OI [jam × simbol] dari rekaman sweep (oi_value; fallback amount)."""
    rows = []
    for p in sorted(oi_dir.glob("oi_*.jsonl.gz")):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                for line in fh:
                    r = json.loads(line)
                    v = r.get("oi_value") or r.get("oi_amount")
                    if v:
                        rows.append((r["ts"], r["symbol"], float(v)))
        except Exception:  # boundary — file hari berjalan bisa terpotong
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "symbol", "oi"])
    df["hour"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.floor("h")
    return df.pivot_table(index="hour", columns="symbol", values="oi", aggfunc="last")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--min-days", type=int, default=MIN_DAYS)
    p.add_argument("--delta-window", nargs="*", type=int, default=[24, 72],
                   help="window ΔOI (jam) — grid 2 × holds 2 = 4 trial (pra-registrasi)")
    p.add_argument("--holds", nargs="*", type=int, default=[24, 72])
    p.add_argument("--fee", type=float, default=0.02)
    p.add_argument("--slippage", type=float, default=0.05)
    args = p.parse_args()

    panel = oi_panel()
    if panel.empty:
        console.print("[red]Belum ada rekaman OI (data/oi kosong?). Cek oicollect.[/red]")
        return
    days = (panel.index[-1] - panel.index[0]).total_seconds() / 86400
    console.print(f"Rekaman OI: {panel.shape[1]} simbol × {len(panel)} jam "
                  f"({days:.1f} hari; mulai {panel.index[0].date()}).")
    if days < args.min_days:
        console.print(f"[yellow]DATA BELUM CUKUP: {days:.1f} < {args.min_days} hari. "
                      f"Perkiraan siap: {(panel.index[0] + pd.Timedelta(days=args.min_days)).date()}. "
                      f"Pipeline siap — jalankan ulang saat matang.[/yellow]")
        return

    # ---- jalur uji penuh (aktif otomatis saat data cukup) ----
    from bot import carry, xs_signals as xss, xsectional as xs
    from bot.altdata import fetch_funding
    from bot.backtest import fetch_history
    from bot.config import load_settings
    from bot.exchange import Exchange

    ex = Exchange(load_settings())
    syms = [s for s in panel.columns][:150]
    dfs, fundings = {}, {}
    for sym in syms:
        try:
            df = fetch_history(ex, sym, "1h", len(panel) + 200)
            fundings[sym] = fetch_funding(ex, sym, int(df.index[0].timestamp() * 1000),
                                          max_points=len(panel) // 2 + 200)
            dfs[sym] = df
        except Exception:
            continue
    px = xs.align_close_panel(dfs)
    oi = panel.reindex(px.index, method="ffill")[list(px.columns)].to_numpy()
    level, _ = carry.align_funding(fundings, px.index, list(px.columns))
    close = px.to_numpy()
    panels = {f"oi{w}": xss.score_oi_crowding(level, oi, w) for w in args.delta_window}
    cost = 4 * (args.fee + args.slippage) / 100
    n_trials = len(panels) * len(args.holds)
    _, oos = xs.walk_forward_scores(close, panels, args.holds, 0.3, cost,
                                    train_len=len(px) // 2, test_len=len(px) // 5)
    v = xs.verdict(oos, n_trials)
    color = "green" if v["ok"] else "red"
    console.print(f"[{color}]H19 verdict: {'LOLOS' if v['ok'] else 'DITOLAK'} — {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
