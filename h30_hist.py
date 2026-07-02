#!/usr/bin/env python3
"""H30 langkah 1-2 pada FILL MAKER NYATA (aggTrades Binance Vision, >=28 hari).

  python h30_hist.py                       # 6 pair spread-lebar, 3 bulan terakhir
  python h30_hist.py --months 2026-03 2026-04 2026-05 2026-06

Verdict pra-registrasi (l2research.verdict): edge kotor terbaik <3bps -> H30 mati.
Catatan melekat: ini BATAS ATAS (fill orang lain, antrian tak terukur).
"""
import argparse

from rich.console import Console
from rich.table import Table

from bot import aggresearch as ar, l2research as lr, vision

console = Console()
PAIRS = ["CRV/USDC:USDC", "BOME/USDC:USDC", "FIL/USDC:USDC",
         "NEAR/USDC:USDC", "NEO/USDC:USDC", "PNUT/USDC:USDC"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*", default=PAIRS)
    p.add_argument("--months", nargs="*", default=["2026-04", "2026-05", "2026-06"])
    args = p.parse_args()

    per = {}
    for sym in args.symbols:
        tr = vision.load_aggtrades(sym, args.months)
        if len(tr) < 1000:
            console.print(f"[yellow]{sym}: data tipis ({len(tr)} trade) — lewati[/yellow]")
            continue
        per[sym] = ar.analyze_trades(tr)
        console.print(f"{sym}: {len(tr):,} trade dimuat")

    tbl = Table(title=f"H30 — fill maker NYATA ({', '.join(args.months)})")
    for c in ["symbol", "hari", "trades", "spread_eff med", "fills/jam", "adverse", "EDGE bps"]:
        tbl.add_column(c, justify="right")
    for s, v in sorted(per.items(), key=lambda kv: -(kv[1]["edge_gross_bps"] or -99)):
        tbl.add_row(s, str(v["days"]), f"{v['trades']:,}", str(v["spread_med_bps"]),
                    str(v["fill_rate_per_hour"]), str(v["adverse_bps"]), str(v["edge_gross_bps"]))
    console.print(tbl)
    v = lr.verdict(per)
    color = {"PREVIEW": "yellow", "PROCEED_TO_SIM": "green"}.get(v["verdict"], "red")
    console.print(f"[{color}]{v['verdict']}: {v['reason']}[/{color}]")


if __name__ == "__main__":
    main()
