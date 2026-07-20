"""Risk overlay filters (Jalan A) — skip-gate candidates from edge hunt.

Research (2026-07-21, edge_hunt_risk_filter + validate):
  PROMOTE_FILTER_PAPER (synthetic streams, 50/30/20 + lockbox ↓DD):
    - skip_breadth_lo   : skip when % alts above SMA50 is in bottom 30% (100d)
    - skip_corr_or_volhi: skip when avg corr high OR BTC vol20 high (top quartile)

These are NOT entry alpha. They reduce max drawdown on baseline streams.
Default use: SHADOW log only. Hard block only if risk_filter_block=true.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .logger import log

SNAP_DIR = Path("data/snap")
# Cache panel in-process (reload at most every hour — daily bars).
_panel_cache: dict[str, Any] = {"ts": 0.0, "panel": None, "btc": None}
_PANEL_TTL_S = 3600.0
_MAX_ALTS = 80
_MIN_BARS = 120


@dataclass
class FilterVerdict:
    allow: bool
    reasons: list[str]
    metrics: dict[str, Any]

    def as_dict(self) -> dict:
        return {
            "allow": self.allow,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


def _sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=max(5, n // 2)).mean()


def breadth_fraction(panel: pd.DataFrame, sma_n: int = 50) -> pd.Series:
    """Fraction of columns with close > SMA(sma_n)."""
    if panel is None or panel.empty:
        return pd.Series(dtype=float)
    sma = panel.rolling(sma_n, min_periods=max(10, sma_n // 2)).mean()
    return (panel > sma).astype(float).mean(axis=1)


def avg_corr_vs_ew(rets: pd.DataFrame, win: int = 20) -> float:
    """Mean correlation of each column vs equal-weight return over last `win` bars."""
    if rets is None or len(rets) < win:
        return float("nan")
    block = rets.iloc[-win:]
    ew = block.mean(axis=1)
    cors = []
    for c in block.columns:
        x = block[c]
        m = x.notna() & ew.notna()
        if m.sum() < win // 2:
            continue
        if float(x[m].std()) < 1e-12 or float(ew[m].std()) < 1e-12:
            continue
        cors.append(float(x[m].corr(ew[m])))
    return float(np.nanmean(cors)) if cors else float("nan")


def evaluate_risk_filters(
    *,
    panel_daily: pd.DataFrame | None,
    btc_close: pd.Series | None,
    asof=None,
    skip_breadth_lo: bool = True,
    skip_corr_or_volhi: bool = True,
    breadth_q: float = 0.30,
    corr_q: float = 0.70,
    vol_q: float = 0.75,
    lookback_q: int = 100,
) -> FilterVerdict:
    """Causal filter at last closed bar (or asof). allow=False → would skip new entries."""
    reasons: list[str] = []
    metrics: dict[str, Any] = {}

    if panel_daily is None or panel_daily.empty:
        return FilterVerdict(True, [], {"note": "no_panel"})

    df = panel_daily.sort_index()
    if asof is not None:
        df = df.loc[df.index <= asof]
    if len(df) < 60:
        return FilterVerdict(True, [], {"note": "short_history", "n": len(df)})

    # --- breadth ---
    if skip_breadth_lo:
        b = breadth_fraction(df, 50)
        b_now = float(b.iloc[-1]) if len(b) else float("nan")
        b_hist = b.iloc[-lookback_q:] if len(b) >= 20 else b
        thr = float(b_hist.quantile(breadth_q)) if b_hist.notna().sum() >= 10 else float("nan")
        metrics["breadth"] = b_now
        metrics["breadth_lo_thr"] = thr
        if np.isfinite(b_now) and np.isfinite(thr) and b_now <= thr:
            reasons.append("breadth_lo")

    # --- corr / btc vol ---
    if skip_corr_or_volhi:
        rets = df.pct_change()
        ac = avg_corr_vs_ew(rets, 20)
        # rolling history of avg corr (bounded last lookback_q bars)
        corr_series = []
        start = max(20, len(rets) - lookback_q)
        for i in range(start, len(rets)):
            corr_series.append(avg_corr_vs_ew(rets.iloc[: i + 1].iloc[-20:], 20))
        corr_s = pd.Series(corr_series)
        corr_thr = float(corr_s.quantile(corr_q)) if corr_s.notna().sum() >= 10 else float("nan")
        metrics["avg_corr"] = ac
        metrics["corr_hi_thr"] = corr_thr
        if np.isfinite(ac) and np.isfinite(corr_thr) and ac >= corr_thr:
            reasons.append("corr_hi")

        if btc_close is not None and len(btc_close) >= 40:
            bc = btc_close.reindex(df.index).ffill()
            br = bc.pct_change()
            vol20 = br.rolling(20).std()
            v_now = float(vol20.iloc[-1]) if len(vol20) else float("nan")
            v_hist = vol20.iloc[-lookback_q:]
            v_thr = float(v_hist.quantile(vol_q)) if v_hist.notna().sum() >= 10 else float("nan")
            metrics["btc_vol20"] = v_now
            metrics["btc_vol_hi_thr"] = v_thr
            if np.isfinite(v_now) and np.isfinite(v_thr) and v_now >= v_thr:
                reasons.append("btc_vol_hi")

    # BOTH families default ON: skip if EITHER family triggers
    if skip_breadth_lo and skip_corr_or_volhi:
        allow = not (
            ("breadth_lo" in reasons)
            or ("corr_hi" in reasons)
            or ("btc_vol_hi" in reasons)
        )
    elif skip_breadth_lo and not skip_corr_or_volhi:
        allow = "breadth_lo" not in reasons
    elif skip_corr_or_volhi and not skip_breadth_lo:
        allow = not (("corr_hi" in reasons) or ("btc_vol_hi" in reasons))
    else:
        allow = True

    return FilterVerdict(allow=allow, reasons=reasons, metrics=metrics)


def from_config(cfg: dict) -> dict:
    """Read agent.risk_filter_* flags. Defaults: shadow off, block off, both families on."""
    ag = (cfg or {}).get("agent") or {}
    return {
        "shadow": bool(ag.get("risk_filter_shadow", False)),
        "block": bool(ag.get("risk_filter_block", False)),
        "skip_breadth_lo": bool(ag.get("risk_filter_breadth", True)),
        "skip_corr_or_volhi": bool(ag.get("risk_filter_corr_vol", True)),
    }


def load_daily_panel(
    snap_dir: Path | str = SNAP_DIR,
    *,
    max_alts: int = _MAX_ALTS,
    min_bars: int = _MIN_BARS,
    force: bool = False,
) -> tuple[pd.DataFrame | None, pd.Series | None]:
    """Load daily close panel + BTC series from snap pkls (cached, TTL 1h).

    Returns (panel_without_btc, btc_close). Fail-soft → (None, None).
    """
    now = time.time()
    if (not force and _panel_cache["panel"] is not None
            and now - float(_panel_cache["ts"]) < _PANEL_TTL_S):
        return _panel_cache["panel"], _panel_cache["btc"]

    root = Path(snap_dir)
    if not root.is_dir():
        return None, None
    try:
        series: dict[str, pd.Series] = {}
        btc: pd.Series | None = None
        # Prefer longest files first so panel has history
        files = sorted(root.glob("*__1d.pkl"), key=lambda p: p.stat().st_size, reverse=True)
        for p in files:
            try:
                df = pd.read_pickle(p)
                if df is None or "close" not in getattr(df, "columns", []):
                    continue
                c = df["close"].dropna()
                if len(c) < min_bars:
                    continue
                name = p.stem.replace("__1d", "")
                # Normalize: BTC* → btc series; others → alts
                up = name.upper()
                if up.startswith("BTC_") or up.startswith("BTCUSDT") or "BTC_USDC" in up or up.startswith("BTCDOM"):
                    if btc is None and not up.startswith("BTCDOM"):
                        btc = c
                    continue
                if len(series) >= max_alts:
                    continue
                series[name] = c
            except Exception:
                continue
        if not series:
            return None, None
        panel = pd.DataFrame(series).sort_index().ffill()
        # Drop rows where too few alts present
        thr = max(10, int(0.5 * len(series)))
        panel = panel.dropna(thresh=thr)
        if btc is not None:
            btc = btc.reindex(panel.index).ffill()
        _panel_cache["ts"] = now
        _panel_cache["panel"] = panel
        _panel_cache["btc"] = btc
        return panel, btc
    except Exception as e:  # boundary
        log.warning(f"risk_filter load_daily_panel gagal: {e}")
        return None, None


def check(cfg: dict, *, asof=None, force_reload: bool = False) -> FilterVerdict:
    """One-shot evaluate from config + snap cache. Fail-open (allow=True)."""
    flags = from_config(cfg)
    if not flags["shadow"] and not flags["block"]:
        return FilterVerdict(True, [], {"note": "disabled"})
    panel, btc = load_daily_panel(force=force_reload)
    return evaluate_risk_filters(
        panel_daily=panel,
        btc_close=btc,
        asof=asof,
        skip_breadth_lo=flags["skip_breadth_lo"],
        skip_corr_or_volhi=flags["skip_corr_or_volhi"],
    )


def stamp(verdict: FilterVerdict | None) -> dict:
    """Fields to attach on open / decision log (empty if no verdict)."""
    if verdict is None:
        return {}
    d = verdict.as_dict()
    return {
        "risk_filter_allow": d["allow"],
        "risk_filter_reasons": d["reasons"],
        "risk_filter_metrics": {
            k: (round(v, 6) if isinstance(v, float) else v)
            for k, v in (d.get("metrics") or {}).items()
            if isinstance(v, (int, float, str, bool)) or v is None
        },
    }
