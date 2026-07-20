"""Siklus BTC / dominance / unlock window — label TERUKUR untuk context agent.

Bukan prediktor entry. Default: inject ke prompt/audit saja; hard gate harus
opt-in terpisah dan lolos OOS dulu (lihat memory/CRYPTO_CYCLE_KNOWLEDGE.md).

Fase harga (bukan hanya tanggal halving):
  accumulation — jauh di bawah ATH, MA200 datar/turun, vol rendah relatif
  uptrend      — di atas MA200, MA200 naik, DD dari ATH sedang
  distribution — dekat ATH / euforia proxy, vol tinggi, MA200 naik tapi ret melambat
  markdown     — di bawah MA200, MA200 turun, DD dalam (bear)
  unknown      — data tipis
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Halving historis (UTC date)
HALVINGS = (
    datetime(2012, 11, 28, tzinfo=timezone.utc),
    datetime(2016, 7, 9, tzinfo=timezone.utc),
    datetime(2020, 5, 11, tzinfo=timezone.utc),
    datetime(2024, 4, 19, tzinfo=timezone.utc),
)


def calendar_halving_phase(now: datetime | None = None) -> str:
    """Label kasar dari waktu sejak halving terakhir (logika lama forward._halving_phase)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    past = [h for h in HALVINGS if h <= now]
    if not past:
        return "unknown"
    years_since = (now - max(past)).days / 365.25
    if years_since < 0.5:
        return "post-halving"
    if years_since < 1.0:
        return "bull"
    if years_since < 2.0:
        return "blow-off"
    if years_since < 3.0:
        return "bear"
    return "accumulation"


def years_since_halving(now: datetime | None = None) -> float | None:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    past = [h for h in HALVINGS if h <= now]
    if not past:
        return None
    return (now - max(past)).days / 365.25


def measured_cycle_phase(
    close: pd.Series,
    *,
    ma_len: int = 200,
    ath_lookback: int | None = None,
    asof: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Fase dari harga BTC (causal s/d `asof`).

    Returns dict: phase, ma200, ma200_slope, dd_from_ath, ret_60, vol_ratio, years_since_halving.
    """
    s = close.dropna().astype(float).sort_index()
    if asof is not None:
        s = s.loc[:asof]
    if len(s) < ma_len + 20:
        return {
            "phase": "unknown",
            "reason": f"need>={ma_len + 20} bars, got {len(s)}",
            "calendar_phase": calendar_halving_phase(
                s.index[-1].to_pydatetime() if len(s) else None
            ),
        }

    px = float(s.iloc[-1])
    ma = s.rolling(ma_len, min_periods=ma_len).mean()
    ma_now = float(ma.iloc[-1])
    ma_prev = float(ma.iloc[-21])  # ~1 bulan slope
    slope = (ma_now / ma_prev - 1.0) if ma_prev > 0 else 0.0

    lb = ath_lookback or len(s)
    ath = float(s.iloc[-lb:].max())
    dd = (px / ath - 1.0) if ath > 0 else 0.0  # 0 at ATH, negative in drawdown

    ret_60 = float(s.iloc[-1] / s.iloc[-61] - 1.0) if len(s) > 61 else 0.0
    r = s.pct_change()
    vol_20 = float(r.iloc[-20:].std()) if len(r) > 20 else 0.0
    vol_100 = float(r.iloc[-100:].std()) if len(r) > 100 else vol_20
    vol_ratio = (vol_20 / vol_100) if vol_100 > 1e-12 else 1.0

    ts = s.index[-1]
    if hasattr(ts, "to_pydatetime"):
        now = ts.to_pydatetime()
    else:
        now = datetime.now(timezone.utc)
    if getattr(now, "tzinfo", None) is None:
        now = now.replace(tzinfo=timezone.utc)
    ysh = years_since_halving(now)
    cal = calendar_halving_phase(now)

    # Rules (sederhana, terdokumentasi — bukan optimized DOF)
    above = px >= ma_now
    ma_up = slope > 0.0
    deep_dd = dd <= -0.35          # ≥35% off ATH
    mild_dd = dd <= -0.15
    near_ath = dd >= -0.08         # within 8% of ATH
    high_vol = vol_ratio >= 1.25

    if not above and (not ma_up) and deep_dd:
        phase = "markdown"
    elif not above and mild_dd and not ma_up:
        phase = "accumulation"
    elif above and ma_up and near_ath and (high_vol or ret_60 > 0.25):
        phase = "distribution"
    elif above and ma_up:
        phase = "uptrend"
    elif above and not ma_up:
        phase = "distribution" if near_ath else "uptrend"
    elif not above and ma_up:
        phase = "accumulation"  # early recover under MA
    else:
        phase = "markdown" if deep_dd else "accumulation"

    return {
        "phase": phase,
        "calendar_phase": cal,
        "years_since_halving": round(ysh, 3) if ysh is not None else None,
        "price": px,
        "ma200": ma_now,
        "ma200_slope_20d": round(slope, 5),
        "dd_from_ath": round(dd, 4),
        "ret_60d": round(ret_60, 4),
        "vol_ratio_20_100": round(vol_ratio, 3),
        "asof": str(ts),
    }


def dominance_regime(
    btcdom_close: pd.Series,
    btc_close: pd.Series | None = None,
    *,
    lookback: int = 20,
    asof: pd.Timestamp | None = None,
    flat_btc_pct: float = 0.05,
) -> dict[str, Any]:
    """Proxy alt-season / risk-off dari BTC.D (BTCDOM perpetual index).

    - risk_off: BTCDOM naik kuat (alt underperform)
    - alt_season: BTCDOM turun + BTC relatif flat (rotasi ke alt)
    - btc_lead: BTC naik kuat, dominance apa saja
    - neutral: selain itu
    """
    d = btcdom_close.dropna().astype(float).sort_index()
    if asof is not None:
        d = d.loc[:asof]
    if len(d) < lookback + 2:
        return {"regime": "unknown", "reason": "thin BTCDOM"}

    dom_ret = float(d.iloc[-1] / d.iloc[-1 - lookback] - 1.0)
    btc_ret = None
    if btc_close is not None and len(btc_close.dropna()) > lookback + 2:
        b = btc_close.dropna().astype(float).sort_index()
        if asof is not None:
            b = b.loc[:asof]
        if len(b) > lookback + 1:
            btc_ret = float(b.iloc[-1] / b.iloc[-1 - lookback] - 1.0)

    if btc_ret is not None and btc_ret >= flat_btc_pct and dom_ret > 0:
        reg = "btc_lead"
    elif dom_ret >= 0.03:
        reg = "risk_off"
    elif dom_ret <= -0.03 and (btc_ret is None or abs(btc_ret) <= flat_btc_pct):
        reg = "alt_season"
    elif dom_ret <= -0.03:
        reg = "alt_bid"  # alt outperform even if BTC moving
    else:
        reg = "neutral"

    return {
        "regime": reg,
        "btcdom_ret": round(dom_ret, 4),
        "btc_ret": round(btc_ret, 4) if btc_ret is not None else None,
        "lookback": lookback,
        "asof": str(d.index[-1]),
    }


def load_unlock_calendar(path: str | Path) -> pd.DataFrame:
    """CSV: symbol,unlock_date,pct_supply,note (symbol base e.g. APT or APT/USDT:USDT)."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=["symbol", "unlock_date", "pct_supply", "note"])
    df = pd.read_csv(p)
    need = {"symbol", "unlock_date"}
    if not need.issubset(set(c.lower() for c in df.columns)):
        # try exact
        cols = {c.lower(): c for c in df.columns}
        if "symbol" not in cols or "unlock_date" not in cols:
            raise ValueError(f"unlock CSV needs symbol,unlock_date columns: {p}")
    df = df.rename(columns={c: c.lower() for c in df.columns})
    df["unlock_date"] = pd.to_datetime(df["unlock_date"], utc=True, errors="coerce")
    df = df.dropna(subset=["symbol", "unlock_date"])
    if "pct_supply" not in df.columns:
        df["pct_supply"] = np.nan
    if "note" not in df.columns:
        df["note"] = ""
    df["symbol"] = df["symbol"].astype(str).str.strip()
    return df.reset_index(drop=True)


def unlock_window_for(
    symbol: str,
    when: pd.Timestamp | datetime,
    calendar: pd.DataFrame,
    *,
    pre_days: int = 3,
    post_days: int = 7,
) -> dict[str, Any]:
    """Apakah `symbol` dalam window unlock di sekitar `when`?"""
    if calendar is None or len(calendar) == 0:
        return {"in_window": False, "reason": "no_calendar"}
    when = pd.Timestamp(when)
    if when.tzinfo is None:
        when = when.tz_localize("UTC")
    base = symbol.split("/")[0].upper().replace("1000", "")
    rows = calendar[calendar["symbol"].str.upper().str.contains(base, regex=False)]
    if rows.empty:
        # exact base match
        rows = calendar[calendar["symbol"].str.upper() == base]
    if rows.empty:
        return {"in_window": False, "reason": "symbol_not_in_calendar", "base": base}

    hits = []
    for _, r in rows.iterrows():
        u = r["unlock_date"]
        if pd.isna(u):
            continue
        delta = (when.normalize() - pd.Timestamp(u).tz_convert("UTC").normalize()).days
        if -pre_days <= delta <= post_days:
            hits.append({
                "unlock_date": str(u.date()) if hasattr(u, "date") else str(u),
                "days_from_unlock": int(delta),
                "pct_supply": r.get("pct_supply"),
                "note": r.get("note", ""),
            })
    if not hits:
        return {"in_window": False, "reason": "outside_window", "base": base}
    return {"in_window": True, "base": base, "events": hits}


def build_cycle_context(
    btc_close: pd.Series | None,
    btcdom_close: pd.Series | None = None,
    *,
    symbol: str | None = None,
    unlock_calendar: pd.DataFrame | None = None,
    asof: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Satu dict untuk inject agent (fail-soft)."""
    out: dict[str, Any] = {
        "phase": "unknown",
        "calendar_phase": calendar_halving_phase(),
        "dominance": {"regime": "unknown"},
        "unlock": {"in_window": False, "reason": "not_checked"},
    }
    try:
        if btc_close is not None and len(btc_close) > 0:
            m = measured_cycle_phase(btc_close, asof=asof)
            out.update({k: m[k] for k in m if k != "reason" or m.get("phase") == "unknown"})
            out["phase"] = m.get("phase", "unknown")
            if "calendar_phase" in m:
                out["calendar_phase"] = m["calendar_phase"]
            out["metrics"] = {
                k: m[k] for k in (
                    "ma200_slope_20d", "dd_from_ath", "ret_60d",
                    "vol_ratio_20_100", "years_since_halving",
                ) if k in m
            }
    except Exception as e:
        out["phase_error"] = str(e)[:120]

    try:
        if btcdom_close is not None and len(btcdom_close) > 0:
            out["dominance"] = dominance_regime(btcdom_close, btc_close, asof=asof)
    except Exception as e:
        out["dominance"] = {"regime": "unknown", "error": str(e)[:80]}

    if symbol and unlock_calendar is not None and len(unlock_calendar):
        when = asof or (btc_close.index[-1] if btc_close is not None and len(btc_close) else datetime.now(timezone.utc))
        try:
            out["unlock"] = unlock_window_for(symbol, when, unlock_calendar)
        except Exception as e:
            out["unlock"] = {"in_window": False, "error": str(e)[:80]}
    elif unlock_calendar is None or (unlock_calendar is not None and len(unlock_calendar) == 0):
        out["unlock"] = {"in_window": False, "reason": "no_calendar"}

    return out
