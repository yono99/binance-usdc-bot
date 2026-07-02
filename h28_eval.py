#!/usr/bin/env python3
"""t-test pra-registrasi H28 — SATU PERINTAH, bisa dijalankan siapa pun kapan pun.

  python h28_eval.py

Sebelum 15 siklus: menampilkan progres (status PREVIEW, dilarang menyimpulkan).
Sesudahnya: verdict final LOLOS_TAHAP_1 / GAGAL sesuai aturan yang dikunci
2026-07-02 (penutup RESEARCH_HYPOTHESES_PHASE4.md).
"""
import json

from rich.console import Console

from bot import h28eval

console = Console()

s = h28eval.preview_status()
ev = s["evaluation"]
console.print(f"[bold]MESIN H28 — {s['mode']}[/bold]")
console.print(f"Gate: {s['gate']}")
console.print(f"Basket terbuka: {s['daemon_state']['open_basket'] or 'kosong'} | "
              f"eval terakhir: {s['daemon_state']['last_eval']}")
console.print(f"Siklus: {ev['progress']} | mean net: {ev['mean_net']} | "
              f"win: {ev['win_rate']} | total: {ev['sum_net']}")
if ev["status"] == "PREVIEW":
    console.print(f"[yellow]{ev['note']}[/yellow]")
else:
    color = "green" if ev["verdict"] == "LOLOS_TAHAP_1" else "red"
    console.print(f"[{color}]VERDICT: {ev['verdict']} (p={ev['p_value']}) — {ev['note']}[/{color}]")
