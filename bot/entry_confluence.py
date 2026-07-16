"""Entry Confluence Gate — 3-factor alignment (SHADOW: catat, jangan blokir).

Filosofi identik VRP/MTF/flat_shadow: lahir sebagai SHADOW — catat hasil gate,
JANGAN memblokir entry aktual — sampai sampel cukup (N>=30) membuktikan gate
menaikkan exp_R. Naik kelas ke enforce = commit terpisah.

Faktor:
1. btc_macro_tier — BTC alignment tiered (full/reduced/blocked)
2. pair_structure_confluence — floor per-component trend + momentum
3. nearest_level_quality — S/R location quality tiering (strong/secondary/null)

Config (config.yaml `entry_confluence:`):
- mode         : off | shadow (default). enforce dibangun terpisah.
- trend_floor  : ambang minimal trend_score (default 0.1, butuh kalibrasi)
- momentum_floor : ambang minimal momentum_score (default 0.1, butuh kalibrasi)
- proximity_atr_mult : jarak maks ke level dalam ATR (default 0.5)
- touch_count_min  : syarat minimum touch (default 12, hipotesis BNB)
- touch_count_strong : level kuat (default 25, hipotesis BNB)
- btc_reduced_mult  : size multiplier saat BTC tier=reduced (default 0.7)
- location_secondary_mult : size multiplier saat location=secondary (default 0.8)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .logger import log


@dataclass
class GateResult:
    """Hasil gate untuk satu kandidat entry — dicatat ke shadow table."""
    ts: str
    symbol: str
    side: str                          # "short" | "long"
    setup: str                         # trend_pullback, range_fade, dll.
    btc_tier: str                      # full | reduced | blocked
    structure_pass: bool               # pair structure confluence lulus?
    location_quality: str | None       # strong | secondary | null
    would_enter: bool                  # gate: enter?
    actually_entered: bool             # apakah rules lama jadi entry?
    conviction: float = 0.0
    price: float = 0.0
    reason: str = ""
    outcome_r: float | None = None     # diisi belakangan saat trade settle


_DEF_CFG = {
    "mode": "shadow",
    "trend_floor": 0.1,
    "momentum_floor": 0.1,
    "proximity_atr_mult": 0.5,
    "touch_count_min": 12,
    "touch_count_strong": 25,
    "btc_reduced_mult": 0.7,
    "location_secondary_mult": 0.8,
}


def _cfg(cfg: dict) -> dict:
    return {**_DEF_CFG, **(cfg.get("entry_confluence") or {})}


def entry_confluence_gate(symbol: str, side: str, setup: str,
                           price: float, atr: float,
                           trend_score: float, momentum_score: float,
                           btc_lead_score: float | None,
                           levels_module, signals_module, altdata_module,
                           full_config: dict) -> dict:
    """Evaluate 3-factor gate for one entry candidate.

    Args:
        symbol: trading pair
        side: "long" or "short"
        setup: Gemini setup name
        price: current price
        atr: ATR value
        trend_score: dari signals.py evaluate()
        momentum_score: dari signals.py evaluate()
        btc_lead_score: BTC return % from altdata
        levels_module: bot.levels module (injected for testability)
        signals_module: bot.signals module (injected)
        altdata_module: bot.altdata module (injected)
        full_config: full config dict

    Returns:
        dict with keys: decision (enter/skip), size_mult, reason, details...
    """
    ec = _cfg(full_config)
    dump_pct = float(full_config.get("btc", {}).get("dump_pct", 0.5))

    # Factor 1: BTC Macro Alignment
    btc_tier = altdata_module.btc_macro_tier(btc_lead_score, side, dump_pct)
    if btc_tier == "blocked":
        return {
            "decision": "skip",
            "reason": f"btc_opposing (tier=blocked, btc_lead={btc_lead_score:.2f}%)",
            "btc_tier": "blocked",
            "structure_pass": False,
            "location_quality": None,
            "size_mult": 0.0,
        }

    # Factor 2: Pair Structure Confluence
    trend_floor = ec["trend_floor"]
    momentum_floor = ec["momentum_floor"]
    structure_pass = signals_module.pair_structure_confluence_ok(
        trend_score, momentum_score, side, trend_floor, momentum_floor)

    # Factor 3: S/R Location Quality
    location_quality = None
    nearest_level = None
    if setup in ("range_fade", "scalp_range", "trend_pullback",
                 "range_fade_v2", "scalp_range_v2"):
        try:
            quality, lvl = levels_module.nearest_level_quality(
                symbol, price, side,
                proximity_atr_mult=ec["proximity_atr_mult"],
                touch_count_min=ec["touch_count_min"],
                touch_count_strong=ec["touch_count_strong"])
            location_quality = quality
            nearest_level = lvl
        except Exception as e:
            log.debug(f"entry_confluence: levels check failed for {symbol}: {e}")
            location_quality = None

    # Combine gate decision
    if not structure_pass:
        return {
            "decision": "skip",
            "reason": f"pair_not_independently_aligned (trend={trend_score:.3f}, mom={momentum_score:.3f}, floor={trend_floor}/{momentum_floor})",
            "btc_tier": btc_tier,
            "structure_pass": False,
            "location_quality": location_quality,
            "size_mult": 0.0,
        }

    if location_quality is None and setup in ("range_fade", "scalp_range", "trend_pullback",
                                               "range_fade_v2", "scalp_range_v2"):
        return {
            "decision": "skip",
            "reason": f"no_valid_level_nearby ({ec['proximity_atr_mult']}×ATR, min_touches={ec['touch_count_min']})",
            "btc_tier": btc_tier,
            "structure_pass": True,
            "location_quality": None,
            "size_mult": 0.0,
        }

    # Calculate size multiplier
    size_mult = 1.0
    if btc_tier == "reduced":
        size_mult *= ec["btc_reduced_mult"]
    if location_quality == "secondary":
        size_mult *= ec["location_secondary_mult"]

    return {
        "decision": "enter",
        "reason": f"gate PASSED (btc={btc_tier}, struct={structure_pass}, loc={location_quality}, mult={size_mult:.2f})",
        "btc_tier": btc_tier,
        "structure_pass": structure_pass,
        "location_quality": location_quality,
        "size_mult": size_mult,
        "nearest_level": nearest_level,
    }


# ── Shadow logging ──────────────────────────────────────────────────────────

def log_shadow(result: GateResult) -> None:
    """Catat hasil gate ke SQLite shadow table."""
    try:
        from . import store
        store.log_entry_confluence_shadow(result)
    except Exception as e:
        log.debug(f"entry_confluence: log shadow gagal: {e}")


# ── Config reader ───────────────────────────────────────────────────────────

def get_config(cfg: dict) -> dict:
    """Ambil konfig entry confluence dari full config."""
    return _cfg(cfg)
