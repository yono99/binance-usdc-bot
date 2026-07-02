#!/usr/bin/env python3
"""H30 spread capture — laporan langkah 1-2 pra-registrasi (ukur + bunuh-cepat).

  python h30_report.py                # semua simbol di data/l2
Verdict hanya keluar bila data >= 28 hari; sebelum itu PREVIEW (tooling check).
"""
from rich.console import Console
from rich.table import Table

from bot import l2research as lr

console = Console()

def main() -> None:
    per = {}
    for s in lr.symbols_available():
        df = lr.load_symbol(s)
        if len(df):
            per[s] = lr.analyze_symbol(df)
    tbl = Table(title="H30 — spread capture (langkah 1-2)")
    for c in ["symbol", "hari", "spread med", "half-life", "fills/jam", "adverse", "EDGE bps"]:
        tbl.add_column(c, justify="right")
    for s, v in sorted(per.items(), key=lambda kv: -(kv[1]["edge_gross_bps"] or -99)):
        tbl.add_row(s, str(v["days"]), str(v["spread_med_bps"]),
                    str(round(v["half_life_snaps"], 1)) if v["half_life_snaps"] else "-",
                    str(v["fill_rate_per_hour"]), str(v["adverse_bps"]), str(v["edge_gross_bps"]))
    console.print(tbl)
    v = lr.verdict(per)
    color = {"PREVIEW": "yellow", "PROCEED_TO_SIM": "green"}.get(v["verdict"], "red")
    console.print(f"[{color}]{v['verdict']}: {v['reason']}[/{color}]")

if __name__ == "__main__":
    main()
