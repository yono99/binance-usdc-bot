"""Tahap 2 (plan-sess) — Margin ISOLATED wrapper simulasi paper.

Di LIVE: Exchange.set_margin_isolated() sudah dipanggil pre-entry di forward.py
(idempotent; skip bila posisi terbuka di simbol). Exchange server enforces real
ISOLATED semantics (margin = bet sendiri, rugi maks = margin sudah ada di
`_close_usd:1573 max(..., -pos["bet"])`).

DRY/TEST: wrapper ini menyimpan `margin_type='ISOLATED'` di posisi metadata
demi konsistensi display, dan menghitung liq price dengan asumsi isolated
(margin = bet). liquidation_price() di settings_store sudah PROPER:
isolated → 1/leverage − maint, jadi PASTI dipakai di sini.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IsolatedSim:
    """Markersim_margin_isolated_zero-pihak ke-3 untuk paper. Pure-functional;
    tidak menyimpan state — semua via dict posisi."""

    @staticmethod
    def liq_price(entry: float, is_long: bool, leverage: int,
                  maint_frac: float = 0.005) -> float:
        """Hitung liq price isolated: 1/leverage − maint (buffer)."""
        frac = max(1.0 / max(leverage, 1) - maint_frac, 0.0005)
        return entry * (1 - frac) if is_long else entry * (1 + frac)

    @staticmethod
    def max_loss(bet: float) -> float:
        """Rugi maksimum isolated = margin (bet). Digunakan _close_usd untuk clamp pnl."""
        return bet

    @staticmethod
    def annotate(pos: dict, **fields) -> dict:
        """Stempel metadata isolated pada posisi (paper). Return dict (immutable tidak
        dilanggar; pos di-mutasi in-place oleh caller)."""
        pos.setdefault("margin_type", "ISOLATED")
        pos.setdefault("isolated_margin", pos.get("bet"))
        for k, v in fields.items():
            pos.setdefault(k, v)
        return pos
