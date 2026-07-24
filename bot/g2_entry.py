"""G2 quality-momentum as ENTRY SIGNAL overlay (adapted from research LS book).

Research arm (frozen): G2_qmom_h10_q0.3
  score = mean_ret_20 / std_ret_20
  long top 30%, short bottom 30% of pure majors (daily)

Entry adaptation (single-symbol bot):
  Rules already propose LONG or SHORT. G2 answers: is this name in the
  quality bucket that matches that side?
    LONG  + top_q    → aligned (allow)
    LONG  + bottom_q → misaligned (would deny)
    SHORT + bottom_q → aligned
    SHORT + top_q    → misaligned
    mid band         → neutral (allow; not in LS book)

Default: SHADOW only (log would-deny). Hard block only if g2_entry.block=true.
Fail-open: no panel / symbol missing / error → allow.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .logger import log

# Frozen research params — do not retune without new pre-registration
LOOKBACK = 20
TOP_Q = 0.3
HOLD_DAYS = 10  # research hold; overlay is rank-at-entry only

PURE_MAJORS = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "ADA", "DOT",
    "LTC", "ATOM", "NEAR", "UNI", "AAVE", "APT", "ARB", "OP", "SUI", "FIL",
    "INJ", "TIA", "SEI", "TRX", "ETC", "XLM", "ALGO", "ICP",
}

_cache: dict[str, Any] = {"ts": 0.0, "ranks": None, "asof": None, "n": 0}
_TTL_S = 1800.0  # 30m — daily ranks


@dataclass
class G2Verdict:
    allow: bool = True
    aligned: bool | None = None  # True aligned, False misaligned, None neutral/unknown
    bucket: str | None = None  # top|bottom|mid|unknown
    rank_pct: float | None = None
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "allow": self.allow,
            "aligned": self.aligned,
            "bucket": self.bucket,
            "rank_pct": self.rank_pct,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


def from_config(cfg: dict | None) -> dict:
    ag = (cfg or {}).get("agent") or {}
    g = ag.get("g2_entry") if isinstance(ag.get("g2_entry"), dict) else {}
    return {
        "shadow": bool(g.get("shadow", False)),
        "block": bool(g.get("block", False)),
        "top_q": float(g.get("top_q", TOP_Q)),
        "lookback": int(g.get("lookback", LOOKBACK)),
        "snap_dir": str(g.get("snap_dir") or "data/snap"),
    }


def _base(sym: str) -> str:
    return sym.split("/")[0].upper().replace("1000", "").replace("1M", "")


def _load_panel(snap_dir: str | Path) -> pd.DataFrame | None:
    root = Path(snap_dir)
    if not root.is_dir():
        return None
    series: dict[str, pd.Series] = {}
    for p in sorted(root.glob("*__1d.pkl")):
        if "BTCDOM" in p.name.upper():
            continue
        stem = p.stem.replace("__1d", "")
        parts = stem.split("_")
        if len(parts) >= 3 and parts[-1] == parts[-2]:
            sym = f"{'_'.join(parts[:-2])}/{parts[-2]}:{parts[-2]}"
        else:
            sym = stem
        base = _base(sym)
        if base not in PURE_MAJORS:
            continue
        if base.startswith("1000") or "1000" in p.name:
            continue
        # prefer USDT
        if base in {_base(s) for s in series} and "USDC" in sym:
            continue
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if "close" not in df.columns or len(df) < LOOKBACK + 30:
            continue
        s = df["close"].astype(float).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        # key by base for stable identity across USDT/USDC
        series[base] = s
    if len(series) < 8:
        return None
    panel = pd.DataFrame(series).sort_index().ffill()
    return panel


def _compute_ranks(panel: pd.DataFrame, lookback: int) -> tuple[pd.Series, pd.Timestamp]:
    """Latest cross-sectional rank_pct (0=worst quality, 1=best)."""
    ret = panel.pct_change()
    mu = ret.rolling(lookback).mean()
    sd = ret.rolling(lookback).std()
    score = mu / (sd + 1e-8)
    last = score.iloc[-1].dropna()
    # rank pct: high score → high rank_pct
    rank_pct = last.rank(pct=True)
    asof = panel.index[-1]
    return rank_pct, asof


def refresh(cfg: dict | None = None, *, force: bool = False) -> dict[str, Any]:
    """Refresh rank cache. Returns metrics dict."""
    flags = from_config(cfg)
    now = time.time()
    if (
        not force
        and _cache["ranks"] is not None
        and now - float(_cache["ts"]) < _TTL_S
    ):
        return {
            "cached": True,
            "n": _cache["n"],
            "asof": str(_cache["asof"]),
        }
    try:
        panel = _load_panel(flags["snap_dir"])
        if panel is None or panel.empty:
            _cache["ranks"] = None
            _cache["ts"] = now
            return {"ok": False, "note": "no_panel"}
        ranks, asof = _compute_ranks(panel, flags["lookback"])
        _cache["ranks"] = ranks
        _cache["asof"] = asof
        _cache["n"] = int(len(ranks))
        _cache["ts"] = now
        log.info(f"g2_entry ranks refreshed n={len(ranks)} asof={asof.date() if hasattr(asof, 'date') else asof}")
        return {"ok": True, "n": len(ranks), "asof": str(asof)}
    except Exception as e:
        log.warning(f"g2_entry refresh fail-open: {e}")
        _cache["ranks"] = None
        _cache["ts"] = now
        return {"ok": False, "error": str(e)}


def evaluate(
    symbol: str,
    side: int | str,
    cfg: dict | None = None,
) -> G2Verdict:
    """side: 1/long or -1/short. Fail-open allow=True."""
    flags = from_config(cfg)
    if not flags["shadow"] and not flags["block"]:
        return G2Verdict(True, None, None, None, [], {"note": "disabled"})

    if _cache["ranks"] is None:
        refresh(cfg)

    ranks: pd.Series | None = _cache.get("ranks")
    if ranks is None or len(ranks) < 3:
        return G2Verdict(True, None, "unknown", None, [], {"note": "no_ranks_fail_open"})

    base = _base(symbol)
    # map 1000PEPE → PEPE if present
    if base not in ranks.index:
        # try raw
        if base not in ranks.index:
            return G2Verdict(
                True, None, "unknown", None, ["not_in_universe"],
                {"base": base, "note": "outside_pure_majors_fail_open"},
            )

    rp = float(ranks.loc[base])
    top_q = flags["top_q"]
    bot_q = top_q
    if rp >= 1.0 - top_q:
        bucket = "top"
    elif rp <= bot_q:
        bucket = "bottom"
    else:
        bucket = "mid"

    want_long = side in (1, "long", "LONG")
    want_short = side in (-1, "short", "SHORT")

    if bucket == "mid":
        aligned = None
        reasons: list[str] = ["mid_band"]
        allow = True
    elif want_long:
        aligned = bucket == "top"
        reasons = ["long_needs_top"] if not aligned else ["long_aligned_top"]
        allow = aligned if flags["block"] else True
        if flags["block"] and not aligned:
            allow = False
            reasons = ["g2_deny_long_not_top"]
    elif want_short:
        aligned = bucket == "bottom"
        reasons = ["short_needs_bottom"] if not aligned else ["short_aligned_bottom"]
        allow = aligned if flags["block"] else True
        if flags["block"] and not aligned:
            allow = False
            reasons = ["g2_deny_short_not_bottom"]
    else:
        aligned = None
        reasons = ["no_side"]
        allow = True

    # shadow never hard-blocks
    if flags["shadow"] and not flags["block"]:
        allow = True

    return G2Verdict(
        allow=allow,
        aligned=aligned,
        bucket=bucket,
        rank_pct=round(rp, 4),
        reasons=reasons,
        metrics={
            "base": base,
            "asof": str(_cache.get("asof")),
            "universe_n": _cache.get("n"),
            "top_q": top_q,
            "lookback": flags["lookback"],
            "arm_id": "G2_qmom_h10_q0.3",
        },
    )


def stamp(v: G2Verdict | None) -> dict:
    if v is None:
        return {}
    d = v.as_dict()
    return {
        "g2_allow": d["allow"],
        "g2_aligned": d["aligned"],
        "g2_bucket": d["bucket"],
        "g2_rank_pct": d["rank_pct"],
        "g2_reasons": d["reasons"],
        "g2_metrics": {
            k: v
            for k, v in (d.get("metrics") or {}).items()
            if isinstance(v, (int, float, str, bool)) or v is None
        },
    }
