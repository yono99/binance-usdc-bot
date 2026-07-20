#!/usr/bin/env python3
"""Bangun data/unlock_calendar.csv historis (kurasi publik) untuk H-CYC-02.

Bukan scrape live TokenUnlocks/DefiLlama (API 402/berbayar; seed open-source
hanya event *upcoming*). Sumber: jadwal vesting TGE yang terdokumentasi publik
(ARB/OP/APT/SUI/STRK/dll.) + linear monthly setelah cliff.

Jujur: ini **aproksimasi kurasi**, bukan feed on-chain 100% akurat.
Cukup untuk spek OOS: "apakah window unlock cenderung bearish?"
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

# (symbol, first_unlock_or_cliff, months, pct_per_event, note)
# pct = % circulating / total supply approx di event (publik, dibulatkan)
SCHEDULES: list[tuple[str, date, int, float, str]] = [
    # Arbitrum — large team/investor unlock Mar 2024 then ~monthly
    ("ARB", date(2024, 3, 16), 1, 11.5, "ARB large team/investor unlock (public)"),
    ("ARB", date(2024, 4, 16), 24, 1.1, "ARB residual monthly vesting approx"),
    # Optimism — core contributors / investors monthly post-cliff
    ("OP", date(2024, 5, 31), 1, 3.5, "OP large unlock window (public reports)"),
    ("OP", date(2024, 6, 30), 20, 1.2, "OP monthly vesting approx"),
    # Aptos — investor quarterly-ish then denser
    ("APT", date(2023, 10, 12), 1, 2.5, "APT investor unlock (public)"),
    ("APT", date(2024, 1, 12), 1, 2.5, "APT investor unlock"),
    ("APT", date(2024, 4, 12), 1, 2.5, "APT investor unlock"),
    ("APT", date(2024, 7, 12), 1, 2.5, "APT investor unlock"),
    ("APT", date(2024, 10, 12), 1, 2.5, "APT investor unlock"),
    ("APT", date(2025, 1, 12), 1, 2.5, "APT investor unlock"),
    ("APT", date(2025, 4, 12), 1, 2.5, "APT investor unlock"),
    ("APT", date(2025, 7, 12), 1, 2.0, "APT investor unlock"),
    ("APT", date(2025, 10, 12), 1, 2.0, "APT investor unlock"),
    # Sui — cliff ~May 2024 then monthly
    ("SUI", date(2024, 5, 1), 1, 1.5, "SUI post-cliff unlock start"),
    ("SUI", date(2024, 6, 1), 20, 1.0, "SUI monthly vesting approx"),
    # Starknet
    ("STRK", date(2024, 4, 15), 1, 2.0, "STRK early unlock window"),
    ("STRK", date(2024, 5, 15), 20, 1.0, "STRK monthly vesting approx"),
    # Celestia
    ("TIA", date(2024, 4, 29), 1, 3.0, "TIA early unlock cluster"),
    ("TIA", date(2024, 10, 29), 12, 1.5, "TIA ongoing unlocks approx"),
    # Sei
    ("SEI", date(2024, 6, 15), 1, 2.0, "SEI unlock window"),
    ("SEI", date(2024, 8, 15), 16, 1.0, "SEI monthly approx"),
    # Worldcoin
    ("WLD", date(2024, 7, 24), 1, 2.5, "WLD unlock window (public)"),
    ("WLD", date(2024, 10, 24), 12, 1.2, "WLD ongoing approx"),
    # Immutable
    ("IMX", date(2024, 3, 1), 1, 2.0, "IMX unlock"),
    ("IMX", date(2024, 6, 1), 18, 1.0, "IMX monthly approx"),
    # dYdX
    ("DYDX", date(2024, 1, 1), 1, 2.0, "DYDX unlock"),
    ("DYDX", date(2024, 4, 1), 18, 1.0, "DYDX monthly approx"),
    # ApeCoin — quarterly-ish
    ("APE", date(2023, 3, 17), 1, 2.0, "APE unlock"),
    ("APE", date(2023, 6, 17), 12, 1.5, "APE quarterly-ish approx"),
    # Blur
    ("BLUR", date(2024, 2, 14), 1, 2.0, "BLUR unlock"),
    ("BLUR", date(2024, 5, 14), 18, 1.0, "BLUR monthly approx"),
    # Ethena
    ("ENA", date(2024, 10, 2), 1, 2.5, "ENA early unlock"),
    ("ENA", date(2025, 1, 2), 12, 1.5, "ENA ongoing approx"),
    # Wormhole
    ("W", date(2024, 10, 3), 1, 2.0, "W unlock"),
    ("W", date(2025, 1, 3), 12, 1.0, "W monthly approx"),
    # Jito / Jupiter / Pyth (Solana ecosystem — often cliff then unlock)
    ("JTO", date(2024, 6, 7), 1, 3.0, "JTO unlock window"),
    ("JTO", date(2024, 12, 7), 8, 1.5, "JTO ongoing"),
    ("JUP", date(2024, 6, 30), 1, 2.0, "JUP unlock"),
    ("JUP", date(2025, 1, 30), 8, 1.2, "JUP ongoing"),
    ("PYTH", date(2024, 5, 20), 1, 2.0, "PYTH unlock"),
    ("PYTH", date(2024, 11, 20), 10, 1.0, "PYTH ongoing"),
    # AltLayer / Dymension / Zeta (L2/new L1 unlocks often large early)
    ("ALT", date(2024, 7, 25), 1, 3.0, "ALT unlock"),
    ("ALT", date(2025, 1, 25), 8, 1.5, "ALT ongoing"),
    ("DYM", date(2024, 8, 6), 1, 2.5, "DYM unlock"),
    ("DYM", date(2025, 2, 6), 8, 1.2, "DYM ongoing"),
    ("ZETA", date(2024, 6, 1), 1, 2.0, "ZETA unlock"),
    ("ZETA", date(2024, 12, 1), 10, 1.0, "ZETA ongoing"),
    # Sandbox / Gala / Axie style older vesting (still supply events)
    ("SAND", date(2023, 2, 14), 1, 1.5, "SAND unlock"),
    ("SAND", date(2023, 8, 14), 18, 1.0, "SAND ongoing"),
    ("MANA", date(2023, 1, 15), 12, 1.0, "MANA vesting approx"),
    ("AXS", date(2023, 5, 1), 1, 2.0, "AXS unlock"),
    ("AXS", date(2023, 11, 1), 18, 1.0, "AXS ongoing"),
    # LDO / CRV residual (smaller continuous — keep few larger)
    ("LDO", date(2023, 12, 15), 1, 1.5, "LDO unlock cluster"),
    ("LDO", date(2024, 6, 15), 12, 1.0, "LDO ongoing"),
    # 1INCH
    ("1INCH", date(2023, 9, 24), 1, 2.0, "1INCH unlock"),
    ("1INCH", date(2024, 3, 24), 12, 1.0, "1INCH ongoing"),
    # FIL miner/vesting style — large continuous; sample quarterly
    ("FIL", date(2023, 1, 15), 1, 1.2, "FIL unlock sample"),
    ("FIL", date(2023, 4, 15), 24, 1.0, "FIL quarterly-ish approx"),
    # DOT / ATOM staking unlocks are different (unbond) — skip pure staking
    # AEVO / ETHFI / PENDLE newer
    ("AEVO", date(2024, 5, 13), 1, 2.5, "AEVO unlock"),
    ("AEVO", date(2024, 11, 13), 10, 1.2, "AEVO ongoing"),
    ("ETHFI", date(2024, 6, 1), 1, 2.0, "ETHFI unlock"),
    ("ETHFI", date(2024, 12, 1), 10, 1.0, "ETHFI ongoing"),
    ("PENDLE", date(2024, 4, 1), 1, 1.5, "PENDLE unlock"),
    ("PENDLE", date(2024, 10, 1), 12, 1.0, "PENDLE ongoing"),
    # ID (SPACE ID), BIGTIME
    ("ID", date(2024, 3, 20), 1, 2.0, "ID unlock"),
    ("ID", date(2024, 9, 20), 12, 1.0, "ID ongoing"),
    ("BIGTIME", date(2024, 4, 10), 1, 2.5, "BIGTIME unlock"),
    ("BIGTIME", date(2024, 10, 10), 12, 1.2, "BIGTIME ongoing"),
    # MANTA
    ("MANTA", date(2024, 5, 18), 1, 3.0, "MANTA unlock"),
    ("MANTA", date(2024, 11, 18), 10, 1.5, "MANTA ongoing"),
    # AVAX foundation unlocks (sparser)
    ("AVAX", date(2023, 9, 22), 1, 1.5, "AVAX unlock sample"),
    ("AVAX", date(2024, 3, 22), 1, 1.5, "AVAX unlock sample"),
    ("AVAX", date(2024, 9, 22), 1, 1.5, "AVAX unlock sample"),
    ("AVAX", date(2025, 3, 22), 1, 1.5, "AVAX unlock sample"),
]


def expand(schedules: list[tuple[str, date, int, float, str]]) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for sym, start, months, pct, note in schedules:
        for i in range(months):
            # approx monthly step 30d
            d = start + timedelta(days=30 * i) if months > 1 and i > 0 else start + timedelta(days=0)
            if months > 1 and i > 0:
                # keep calendar-month-ish: add months
                m = start.month - 1 + i
                y = start.year + m // 12
                mo = m % 12 + 1
                day = min(start.day, 28)
                d = date(y, mo, day)
            key = (sym, d.isoformat())
            if key in seen:
                continue
            seen.add(key)
            # only keep through mid-2026 for paper horizon; study uses snap to ~2026-07
            if d > date(2026, 7, 1):
                continue
            if d < date(2022, 1, 1):
                continue
            rows.append({
                "symbol": sym,
                "unlock_date": d.isoformat(),
                "pct_supply": round(pct, 2),
                "note": note if i == 0 else f"{note} (m+{i})",
            })
    rows.sort(key=lambda r: (r["unlock_date"], r["symbol"]))
    return rows


def main() -> None:
    rows = expand(SCHEDULES)
    out = Path("data/unlock_calendar.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "unlock_date", "pct_supply", "note"])
        w.writeheader()
        w.writerows(rows)
    # also copy to example-friendly path note
    syms = sorted({r["symbol"] for r in rows})
    print(f"Wrote {out}: {len(rows)} events, {len(syms)} symbols")
    print("symbols:", ",".join(syms))
    print("date range:", rows[0]["unlock_date"], "→", rows[-1]["unlock_date"])
    large = [r for r in rows if r["pct_supply"] >= 2.0]
    print(f"events pct>=2%: {len(large)}")


if __name__ == "__main__":
    main()
