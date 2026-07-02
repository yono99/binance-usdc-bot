#!/usr/bin/env python3
"""Kalibrasi lantai SL dari 1 TAHUN data 15m (bahan bakar utk Fix A).

  python sl_calibrate.py                # 15 pair lolos screener, 35040 bar 15m

Output: kuantil MAE pemenang (×ATR) per pair + agregat, total & subset
'setelah candle besar'. Rekomendasi lantai = q80 agregat. Data via chartstore
(sekalian mengisi data/market.db 1 tahun — produk chart ikut kenyang).
"""
import json

from rich.console import Console
from rich.table import Table

from bot import chartstore, slcalib
from bot.config import load_settings
from bot.exchange import Exchange

console = Console()
PAIRS = ["1000PEPE/USDC:USDC", "AAVE/USDC:USDC", "AVAX/USDC:USDC", "BCH/USDC:USDC",
         "BNB/USDC:USDC", "BTC/USDC:USDC", "DOGE/USDC:USDC", "ENA/USDC:USDC",
         "ETH/USDC:USDC", "LINK/USDC:USDC", "LTC/USDC:USDC", "SOL/USDC:USDC",
         "SUI/USDC:USDC", "TRUMP/USDC:USDC", "XRP/USDC:USDC"]


def main() -> None:
    ex = Exchange(load_settings())
    tbl = Table(title="Kalibrasi lantai SL — MAE pemenang (×ATR14), horizon 16 bar 15m, TP 2.5×ATR")
    for c in ["pair", "n win", "q50", "q75", "q80", "q90", "| candle-besar q80"]:
        tbl.add_column(c, justify="right")
    agg = {"semua": [], "setelah_candle_besar": []}
    for sym in PAIRS:
        try:
            chartstore.ingest(ex, sym, "15m", bars=35040)
            df = chartstore.load(sym, "15m", limit=35040)
            if len(df) < 5000:
                console.print(f"[yellow]{sym}: data {len(df)} bar — lewati[/yellow]")
                continue
            r = slcalib.mae_of_winners(df)
            s, b = r["semua"], r["setelah_candle_besar"]
            tbl.add_row(sym.split("/")[0], str(s["n_winners"]), str(s.get("mae_q50", "-")),
                        str(s.get("mae_q75", "-")), str(s.get("mae_q80", "-")),
                        str(s.get("mae_q90", "-")), str(b.get("mae_q80", "-")))
            if "mae_q80" in s:
                agg["semua"].append(s["mae_q80"])
            if "mae_q80" in b:
                agg["setelah_candle_besar"].append(b["mae_q80"])
        except Exception as e:
            console.print(f"[yellow]{sym}: gagal ({e})[/yellow]")
    console.print(tbl)
    if agg["semua"]:
        import statistics
        rec = round(statistics.median(agg["semua"]), 2)
        rec_big = round(statistics.median(agg["setelah_candle_besar"]), 2) if agg["setelah_candle_besar"] else None
        console.print(f"[bold]REKOMENDASI lantai k_atr (median q80): {rec}×ATR "
                      f"| khusus setelah candle besar: {rec_big}×ATR[/bold]")
        json.dump({"k_atr_q80_median": rec, "big_candle_q80_median": rec_big,
                   "per_pair_q80": agg}, open("data/sl_calibration.json", "w"), indent=1)
        console.print("Tersimpan: data/sl_calibration.json — bandingkan dgn default "
                      "_sl_floor (k_atr=1.0, k_range=0.5); sesuaikan HANYA berdasar angka ini.")


if __name__ == "__main__":
    main()
