#!/usr/bin/env python3
"""H30 LANGKAH 3 — replay maker konservatif di aggTrades nyata (pra-registrasi).

  python h30_sim.py

Aturan verdict (dikunci SEBELUM run): pair lolos bila mean bps/round-trip > 0
pada STRESS PENUH (unwind = half-spread efektif + taker 2 bps) untuk KEDUA
offset {1.0x, 1.5x} half-spread efektif, dan rt/hari >= 20 (kapasitas nyata).
Lolos -> langkah 4: paper-quote mikro forward.
"""
from rich.console import Console
from rich.table import Table

from bot import aggresearch as ar, mmsim, vision

console = Console()
PAIRS = ["FIL/USDC:USDC", "NEAR/USDC:USDC", "CRV/USDC:USDC",
         "NEO/USDC:USDC", "BOME/USDC:USDC", "PNUT/USDC:USDC"]
MONTHS = ["2026-04", "2026-05", "2026-06"]
TAKER_STRESS = 2.0     # bps


def main() -> None:
    tbl = Table(title="H30 langkah 3 — replay konservatif (fill tembus, unwind bayar spread+taker)")
    for c in ["pair", "offset", "stress", "rt/hari", "unwind%", "win%", "mean bps", "bps/hari"]:
        tbl.add_column(c, justify="right")
    passed = {}
    for sym in PAIRS:
        tr = vision.load_aggtrades(sym, MONTHS)
        if len(tr) < 10_000:
            continue
        sp = ar.effective_spread_bps(tr)
        half = float(sp.median()) / 2
        results = []
        for mult in (1.0, 1.5):
            for taker in (0.0, TAKER_STRESS):
                r = mmsim.simulate(tr, offset_bps=half * mult,
                                   unwind_cost_bps=half + taker)
                tbl.add_row(sym.split("/")[0], f"{mult}x", f"+{taker:g}bp",
                            str(r["rt_per_day"]), f"{r['unwind_frac']:.0%}",
                            f"{(r['win_rate'] or 0):.0%}", str(r["mean_bps"]),
                            str(r["bps_per_day"]))
                if taker == TAKER_STRESS:
                    results.append(r)
        ok = all(x["mean_bps"] is not None and x["mean_bps"] > 0
                 and x["rt_per_day"] >= 20 for x in results)
        if ok:
            passed[sym] = results
    console.print(tbl)
    if passed:
        console.print(f"[green]PROCEED_TO_PAPER: {', '.join(passed)} — positif di stress "
                      f"penuh pada kedua offset. Langkah 4 = paper-quote mikro forward.[/green]")
    else:
        console.print("[red]REJECTED langkah 3: tak ada pair yang positif di stress penuh "
                      "pada kedua offset — batas atas langkah 2 tidak selamat dari "
                      "simulasi konservatif.[/red]")


if __name__ == "__main__":
    main()
