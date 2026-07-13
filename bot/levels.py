"""Level Validity Detection — True S/R via time-at-price binning (not naive range position).

Masalah: signals.py hanya pakai 20-bar high/low + pos_in_range → tidak bedakan level
"teruji" (banyak disentuh) vs level "kebetulan lewat".

Solusi: Deteksi level S/R dari timeframe lebih tinggi (default 1h) dengan lookback panjang
(200-300 candle), binning berbasis ATR, hitung touch count + recency weighting.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional
from functools import lru_cache
import time

from . import chartstore
from . import indicators as ind
from .config import load_settings


@dataclass
class Level:
    """Valid Support/Resistance level."""
    price: float
    level_type: str          # "support" or "resistance"
    strength: float          # touch count with recency weighting
    raw_touches: int         # raw touch count (unweighted)
    high_touches: int        # times price approached from below (resistance tests)
    low_touches: int         # times price approached from above (support tests)
    bin_low: float           # bin boundaries
    bin_high: float
    last_touch_idx: int      # index of most recent touch
    dist_atr: float          # distance from current price in ATR units
    
    def __repr__(self):
        return f"Level({self.level_type} @ {self.price:.4f}, strength={self.strength:.1f}, touches={self.raw_touches}, H/L={self.high_touches}/{self.low_touches}, dist={self.dist_atr:.1f}ATR)"


# Cache per symbol: {symbol: (last_update_ts, levels_list)}
_level_cache: dict = {}
# Config defaults
_DEFAULT_LOOKBACK = 250      # candles at detection timeframe
_DEFAULT_TIMEFRAME = "1h"
_DEFAULT_MIN_TOUCHES = 15
_DEFAULT_BIN_WIDTH_ATR_MULT = 0.15
_DEFAULT_RECENCY_HALFLIFE = 50  # bars for exponential decay


def _get_config():
    """Get level detection config from settings."""
    s = load_settings()
    lvl_cfg = s.raw.get("level_detection", {})
    return {
        "timeframe": lvl_cfg.get("timeframe", _DEFAULT_TIMEFRAME),
        "lookback": lvl_cfg.get("lookback", _DEFAULT_LOOKBACK),
        "min_touches": lvl_cfg.get("min_touches", _DEFAULT_MIN_TOUCHES),
        "bin_width_atr_mult": lvl_cfg.get("bin_width_atr_mult", _DEFAULT_BIN_WIDTH_ATR_MULT),
        "recency_halflife": lvl_cfg.get("recency_halflife", _DEFAULT_RECENCY_HALFLIFE),
    }


def _compute_bin_width(atr: float, cfg: dict) -> float:
    """Bin width = ATR * multiplier (default 0.15×ATR)."""
    return max(atr * cfg["bin_width_atr_mult"], 1e-8)


def _detect_levels_from_df(df: pd.DataFrame, cfg: dict) -> List[Level]:
    """Detect valid S/R levels from OHLCV DataFrame.
    
    Uses time-at-price binning with ATR-based bin width.
    """
    if df.empty or len(df) < 20:
        return []
    
    close = df["close"]
    high = df["high"]
    low = df["low"]
    
    # Use last `lookback` candles
    lookback = min(cfg["lookback"], len(df))
    recent = df.iloc[-lookback:]
    
    atr_val = float(ind.atr(recent, 14).iloc[-1])
    if atr_val <= 0:
        return []
    
    bin_width = _compute_bin_width(atr_val, cfg)
    
    # Price range for binning
    price_min = float(recent["low"].min())
    price_max = float(recent["high"].max())
    
    # Create bins
    n_bins = int((price_max - price_min) / bin_width) + 1
    if n_bins > 5000:  # sanity limit
        n_bins = 5000
        bin_width = (price_max - price_min) / n_bins
    
    bin_edges = np.linspace(price_min, price_max, n_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    
    # Count touches per bin (high/low touching bin)
    touch_counts = np.zeros(n_bins)
    high_touches = np.zeros(n_bins)
    low_touches = np.zeros(n_bins)
    last_touch_idx = np.full(n_bins, -1, dtype=int)
    
    for i in range(len(recent)):
        row = recent.iloc[i]
        # Find bins touched by high/low
        h_bin = int((row["high"] - price_min) / bin_width)
        l_bin = int((row["low"] - price_min) / bin_width)
        h_bin = max(0, min(h_bin, n_bins - 1))
        l_bin = max(0, min(l_bin, n_bins - 1))
        
        # All bins between low and high are "touched"
        for b in range(l_bin, h_bin + 1):
            touch_counts[b] += 1
            last_touch_idx[b] = i  # most recent = higher index
        # Count high/low separately for type determination
        high_touches[h_bin] += 1
        low_touches[l_bin] += 1
    
    # Apply recency weighting: exponential decay
    halflife = cfg["recency_halflife"]
    if halflife > 0:
        # weight = 0.5^(age / halflife)
        max_idx = len(recent) - 1
        recency_weights = 0.5 ** ((max_idx - np.arange(len(recent))) / halflife)
        # Recompute weighted touches
        weighted_counts = np.zeros(n_bins)
        for i in range(len(recent)):
            row = recent.iloc[i]
            h_bin = int((row["high"] - price_min) / bin_width)
            l_bin = int((row["low"] - price_min) / bin_width)
            h_bin = max(0, min(h_bin, n_bins - 1))
            l_bin = max(0, min(l_bin, n_bins - 1))
            w = recency_weights[i]
            for b in range(l_bin, h_bin + 1):
                weighted_counts[b] += w
        strength = weighted_counts
    else:
        strength = touch_counts
    
    # Filter valid levels
    min_touches = cfg["min_touches"]
    levels = []
    
    for b in range(n_bins):
        if strength[b] >= min_touches:
            center = bin_centers[b]
            bin_low = bin_edges[b]
            bin_high = bin_edges[b + 1]
            
            # Determine if support or resistance based on HOW it was tested
            # high_touches = price approached from below (tested as resistance)
            # low_touches = price approached from above (tested as support)
            if high_touches[b] >= low_touches[b]:
                level_type = "resistance"
            else:
                level_type = "support"
            
            # Also compute distance from current price for convenience
            recent_price = float(close.iloc[-1])
            dist_atr = abs(recent_price - center) / atr_val if atr_val > 0 else float('inf')
            
            levels.append(Level(
                price=center,
                level_type=level_type,
                strength=float(strength[b]),
                raw_touches=int(touch_counts[b]),
                high_touches=int(high_touches[b]),
                low_touches=int(low_touches[b]),
                bin_low=bin_low,
                bin_high=bin_high,
                last_touch_idx=int(last_touch_idx[b]) if last_touch_idx[b] >= 0 else -1,
                dist_atr=dist_atr
            ))
    
    # Sort by strength (descending)
    levels.sort(key=lambda x: x.strength, reverse=True)
    return levels


def get_valid_levels(symbol: str, timeframe: str = None, force_refresh: bool = False) -> List[Level]:
    """Get valid S/R levels for a symbol.
    
    Args:
        symbol: Trading pair symbol (e.g., "BTC/USDC:USDC")
        timeframe: Detection timeframe (default from config, usually "1h")
        force_refresh: Bypass cache
        
    Returns:
        List of Level objects (support/resistance), sorted by strength
    """
    cfg = _get_config()
    tf = timeframe or cfg["timeframe"]
    
    # Cache key
    cache_key = f"{symbol}:{tf}"
    now = time.time()
    
    if not force_refresh and cache_key in _level_cache:
        cached_ts, cached_levels = _level_cache[cache_key]
        # Refresh only on new candle close (check if last bar timestamp changed)
        try:
            df = chartstore.load(symbol, tf, limit=2)
            if not df.empty:
                last_bar_ts = int(df.index[-1].timestamp())
                # If cache is less than 1 candle old, use it
                if now - cached_ts < 3600:  # 1h max cache
                    return cached_levels
        except Exception:
            pass
    
    # Load data
    try:
        df = chartstore.load(symbol, tf, limit=cfg["lookback"] + 50)
    except Exception:
        return []
    
    if df.empty:
        return []
    
    levels = _detect_levels_from_df(df, cfg)
    
    # Update cache
    _level_cache[cache_key] = (now, levels)
    return levels


def find_nearest_level(symbol: str, price: float, level_type: str, 
                       max_distance_atr_mult: float = 0.5,
                       timeframe: str = None) -> Optional[Level]:
    """Find nearest valid level of given type within distance threshold.
    
    Args:
        symbol: Trading pair
        price: Current price
        level_type: "support" or "resistance"
        max_distance_atr_mult: Max distance in ATR multiples (default 0.5×ATR)
        timeframe: Detection timeframe
        
    Returns:
        Nearest Level if found within threshold, else None
    """
    levels = get_valid_levels(symbol, timeframe)
    if not levels:
        return None
    
    # Get ATR for distance calculation
    cfg = _get_config()
    try:
        df = chartstore.load(symbol, cfg["timeframe"], limit=20)
        atr_val = float(ind.atr(df, 14).iloc[-1])
    except Exception:
        return None
    
    if atr_val <= 0:
        return None
    
    max_dist = atr_val * max_distance_atr_mult
    
    # Filter by type and distance
    candidates = [
        lvl for lvl in levels 
        if lvl.level_type == level_type and abs(lvl.price - price) <= max_dist
    ]
    
    if not candidates:
        return None
    
    # Return nearest
    return min(candidates, key=lambda x: abs(x.price - price))


def clear_cache(symbol: str = None):
    """Clear level cache for a symbol or all."""
    if symbol:
        keys_to_del = [k for k in _level_cache if k.startswith(f"{symbol}:")]
        for k in keys_to_del:
            del _level_cache[k]
    else:
        _level_cache.clear()


# Convenience function for pre-gate in signals.py / forward.py
def is_price_at_valid_level(symbol: str, price: float, side: str, 
                            max_dist_atr_mult: float = 0.5) -> tuple[bool, Optional[Level]]:
    """Pre-gate check: is price near a valid S/R level for fade entry?
    
    Args:
        symbol: Trading pair
        price: Current price
        side: "long" (needs support) or "short" (needs resistance)
        max_dist_atr_mult: Max distance in ATR multiples
        
    Returns:
        (has_valid_level, Level or None)
    """
    level_type = "support" if side == "long" else "resistance"
    lvl = find_nearest_level(symbol, price, level_type, max_dist_atr_mult)
    return (lvl is not None, lvl)


# Offline validation helper
def validate_levels_manual(symbol: str, timeframe: str = "1h", lookback: int = 300) -> dict:
    """Run level detection and return detailed info for manual validation.
    
    Use this to verify the module works correctly (e.g., BNB case study).
    """
    cfg = _get_config()
    cfg["timeframe"] = timeframe
    cfg["lookback"] = lookback
    
    df = chartstore.load(symbol, timeframe, limit=lookback + 50)
    if df.empty:
        return {"error": "No data"}
    
    levels = _detect_levels_from_df(df, cfg)
    
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "lookback": lookback,
        "n_levels": len(levels),
        "levels": [
            {
                "price": l.price,
                "type": l.level_type,
                "strength": l.strength,
                "raw_touches": l.raw_touches,
                "bin_range": f"{l.bin_low:.4f}-{l.bin_high:.4f}",
                "last_touch_bars_ago": len(df) - 1 - l.last_touch_idx if l.last_touch_idx >= 0 else None
            }
            for l in levels
        ]
    }


if __name__ == "__main__":
    # Quick test
    import sys
    if len(sys.argv) > 1:
        sym = sys.argv[1]
        print(f"Testing levels for {sym}...")
        result = validate_levels_manual(sym)
        import json
        print(json.dumps(result, indent=2, default=str))
    else:
        print("Usage: python -m bot.levels <SYMBOL>")