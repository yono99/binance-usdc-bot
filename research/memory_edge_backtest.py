#!/usr/bin/env python3
"""Backtest: AI-style entry memory loop (riset_edge.txt 2026-07-24 revision).

Implements *deterministically* what the prompt describes:
  regime classify → retrieve technique stats from SQLite → playbook signals
  → confidence gate → entry → exit SL/TP → update rolling stats.

This is NOT LLM calls. It is a reproducible case-based memory system so we can
measure whether "learn from past technique×regime" has OOS edge after costs.

  PYTHONPATH=. python research/memory_edge_backtest.py
  PYTHONPATH=. python research/memory_edge_backtest.py --snap data/snap --symbols 40

Verdict uses chronological split: first 70% builds memory + trades (train),
last 30% is OOS with continuing memory (realistic online learning).
Also reports pure OOS-only stats and baseline (no memory, conf from signal only).
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.indicators import adx, atr, bollinger, ema, rsi  # noqa: E402

COST_RT = 0.0018  # fee+slip round-turn (same project bar)
MIN_N_MEMORY = 30
CONF_MIN = 0.5
RR_TARGET = 2.0  # TP = RR * risk
LOOKBACK_STATS_DAYS = 90
TECHNIQUES = ("swing", "breakout", "mean_reversion", "momentum_pullback")
# scalping excluded on 1d (prompt: 1-5m only)


# ── SQLite schema (from riset_edge.txt) ──────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_log (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    technique TEXT NOT NULL,
    direction TEXT NOT NULL,
    market_regime TEXT NOT NULL,
    entry_timestamp TEXT NOT NULL,
    entry_price REAL,
    stop_loss_price REAL,
    take_profit_price REAL,
    exit_timestamp TEXT,
    exit_price REAL,
    exit_reason TEXT,
    position_size_pct REAL,
    pnl_r_multiple REAL,
    indicators_json TEXT,
    confidence_score REAL,
    historical_stats_used_json TEXT,
    reasoning_text TEXT,
    was_entry_taken INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS technique_performance_rolling (
    technique TEXT NOT NULL,
    market_regime TEXT NOT NULL,
    symbol_tier TEXT NOT NULL,
    n_trades INTEGER DEFAULT 0,
    win_rate REAL,
    avg_r_multiple REAL,
    expectancy REAL,
    profit_factor REAL,
    last_updated TEXT,
    PRIMARY KEY (technique, market_regime, symbol_tier)
);
CREATE INDEX IF NOT EXISTS idx_trade_log_regime
  ON trade_log (market_regime, technique, was_entry_taken);
"""


def symbol_tier(sym: str) -> str:
    base = sym.split("/")[0].upper() if "/" in sym else sym.split("_")[0].upper()
    if base in ("BTC", "ETH"):
        return "major"
    if base in (
        "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "ADA", "DOT", "LTC", "ATOM",
        "NEAR", "UNI", "AAVE", "TRX", "APT", "ARB", "OP", "SUI",
    ):
        return "large_cap"
    return "mid_small_cap"


# ── Features + regime ────────────────────────────────────────────────────────

def enrich(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c = d["close"].astype(float)
    d["ema20"] = ema(c, 20)
    d["ema50"] = ema(c, 50)
    d["rsi"] = rsi(c, 14)
    d["atr"] = atr(d, 14)
    adx_v, plus_di, minus_di = adx(d, 14)
    d["adx"] = adx_v
    d["plus_di"] = plus_di
    d["minus_di"] = minus_di
    mid, up, lo, width = bollinger(c, 20, 2.0)
    d["bb_mid"], d["bb_up"], d["bb_lo"], d["bb_width"] = mid, up, lo, width
    # percentile rank of width vs trailing 90 (vectorized approx via rolling mean/std)
    w_mu = width.rolling(90, min_periods=30).mean()
    w_sd = width.rolling(90, min_periods=30).std()
    d["bb_width_pct"] = ((width - w_mu) / (w_sd + 1e-12)).clip(-3, 3)
    # map z to ~0-1 for thresholds: z<=-1 → contraction-ish, z>=1 → expansion
    d["bb_width_pct"] = (d["bb_width_pct"] + 3) / 6.0
    d["vol_ma"] = d["volume"].astype(float).rolling(20).mean()
    d["vol_ratio"] = d["volume"].astype(float) / d["vol_ma"].replace(0, np.nan)
    # swing high/low 10
    d["swing_hi"] = d["high"].rolling(10).max().shift(1)
    d["swing_lo"] = d["low"].rolling(10).min().shift(1)
    # fib-ish pullback from 20d high/low leg
    d["hi20"] = d["high"].rolling(20).max()
    d["lo20"] = d["low"].rolling(20).min()
    return d


def classify_regime(row: pd.Series) -> str:
    adx_v = float(row.get("adx") or 0)
    c = float(row["close"])
    e50 = float(row.get("ema50") or c)
    bb_pct = row.get("bb_width_pct")
    bb_pct = float(bb_pct) if bb_pct == bb_pct else 0.5
    # expansion: width rank high
    if bb_pct >= 0.70 and adx_v >= 20:
        return "high_vol_expansion"
    if bb_pct <= 0.30:
        return "low_vol_contraction"
    if adx_v > 25:
        return "trending_up" if c >= e50 else "trending_down"
    if adx_v < 20:
        return "ranging"
    # mid ADX
    return "trending_up" if c >= e50 else "trending_down"


# ── Playbook signals (causal: use row i closed bar → enter next open ≈ close[i]) ─

@dataclass
class Signal:
    technique: str
    direction: str  # long|short
    sl: float
    tp: float
    score: float  # 0-0.4 technical strength
    reason: str


def playbook_signals(row: pd.Series, regime: str) -> list[Signal]:
    """Return candidate signals that match regime playbooks (may be empty)."""
    out: list[Signal] = []
    c = float(row["close"])
    atr_v = float(row.get("atr") or 0)
    if atr_v <= 0 or not math.isfinite(atr_v):
        return out
    e20, e50 = float(row["ema20"]), float(row["ema50"])
    adx_v = float(row["adx"])
    r = float(row["rsi"])
    vol_r = float(row.get("vol_ratio") or 1.0)
    if not math.isfinite(vol_r):
        vol_r = 1.0
    bb_up, bb_lo = float(row["bb_up"]), float(row["bb_lo"])
    swing_lo = float(row.get("swing_lo") or c - 2 * atr_v)
    swing_hi = float(row.get("swing_hi") or c + 2 * atr_v)

    # SWING — trending
    if regime in ("trending_up", "trending_down") and adx_v > 25:
        if regime == "trending_up" and c > e50:
            # pullback near ema20
            dist = abs(c - e20) / atr_v
            if dist < 1.2 and r < 60:
                sl = min(swing_lo, c - 1.5 * atr_v)
                risk = c - sl
                if risk > 0:
                    tp = c + RR_TARGET * risk
                    sc = min(0.4, 0.15 + 0.1 * min(adx_v / 40, 1) + (0.1 if vol_r > 1 else 0))
                    out.append(Signal("swing", "long", sl, tp, sc, f"swing long pullback ema adx={adx_v:.0f}"))
        if regime == "trending_down" and c < e50:
            dist = abs(c - e20) / atr_v
            if dist < 1.2 and r > 40:
                sl = max(swing_hi, c + 1.5 * atr_v)
                risk = sl - c
                if risk > 0:
                    tp = c - RR_TARGET * risk
                    sc = min(0.4, 0.15 + 0.1 * min(adx_v / 40, 1))
                    out.append(Signal("swing", "short", sl, tp, sc, f"swing short pullback adx={adx_v:.0f}"))

    # BREAKOUT — contraction → expansion or close beyond BB with volume
    if regime in ("low_vol_contraction", "high_vol_expansion", "ranging"):
        if c > bb_up and vol_r >= 1.5:
            sl = float(row["bb_mid"])
            risk = c - sl
            if risk > 0:
                tp = c + RR_TARGET * risk
                sc = min(0.4, 0.2 + 0.1 * min(vol_r / 2, 1))
                out.append(Signal("breakout", "long", sl, tp, sc, f"breakup bb vol={vol_r:.1f}"))
        if c < bb_lo and vol_r >= 1.5:
            sl = float(row["bb_mid"])
            risk = sl - c
            if risk > 0:
                tp = c - RR_TARGET * risk
                sc = min(0.4, 0.2 + 0.1 * min(vol_r / 2, 1))
                out.append(Signal("breakout", "short", sl, tp, sc, f"breakdown bb vol={vol_r:.1f}"))

    # MEAN REVERSION — ranging
    if regime == "ranging":
        if r < 30 and c <= bb_lo * 1.002:
            sl = c - 1.2 * atr_v
            risk = c - sl
            if risk > 0:
                tp = min(float(row["bb_mid"]), c + RR_TARGET * risk)
                sc = min(0.4, 0.15 + 0.15 * (30 - r) / 30)
                out.append(Signal("mean_reversion", "long", sl, tp, sc, f"MR long rsi={r:.0f}"))
        if r > 70 and c >= bb_up * 0.998:
            sl = c + 1.2 * atr_v
            risk = sl - c
            if risk > 0:
                tp = max(float(row["bb_mid"]), c - RR_TARGET * risk)
                sc = min(0.4, 0.15 + 0.15 * (r - 70) / 30)
                out.append(Signal("mean_reversion", "short", sl, tp, sc, f"MR short rsi={r:.0f}"))

    # MOMENTUM PULLBACK — trend after extension
    if regime in ("trending_up", "trending_down") and adx_v > 20:
        hi20, lo20 = float(row["hi20"]), float(row["lo20"])
        span = hi20 - lo20
        if span > 0:
            retr = (hi20 - c) / span if regime == "trending_up" else (c - lo20) / span
            if regime == "trending_up" and 0.38 <= retr <= 0.61 and c > e50:
                sl = lo20
                risk = c - sl
                if risk > 0:
                    tp = c + RR_TARGET * risk
                    sc = min(0.4, 0.2)
                    out.append(Signal("momentum_pullback", "long", sl, tp, sc, f"mp long fib~{retr:.2f}"))
            if regime == "trending_down" and 0.38 <= retr <= 0.61 and c < e50:
                sl = hi20
                risk = sl - c
                if risk > 0:
                    tp = c - RR_TARGET * risk
                    sc = min(0.4, 0.2)
                    out.append(Signal("momentum_pullback", "short", sl, tp, sc, f"mp short fib~{retr:.2f}"))

    return out


# ── Memory store ─────────────────────────────────────────────────────────────

class MemoryStore:
    """In-memory rolling stats (fast) + optional SQLite audit of taken trades only."""

    def __init__(self, path: Path, persist: bool = True):
        self.path = path
        self.persist = persist
        self._rs: dict[tuple[str, str, str], list[float]] = {}
        self._tid = 0
        self.n_skips = 0
        self.conn = None
        if persist:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                path.unlink()
            self.conn = sqlite3.connect(str(path))
            self.conn.execute("PRAGMA journal_mode=OFF")
            self.conn.execute("PRAGMA synchronous=OFF")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def _key(self, technique: str, regime: str, tier: str) -> tuple[str, str, str]:
        return (technique, regime, tier)

    def retrieve(self, technique: str, regime: str, tier: str) -> dict:
        rs = self._rs.get(self._key(technique, regime, tier), [])
        n = len(rs)
        if n < MIN_N_MEMORY:
            return {
                "status": "insufficient_data",
                "n_trades": n,
                "win_rate": None,
                "expectancy": None,
                "profit_factor": None,
                "hist_score": 0.0,
            }
        a = np.asarray(rs, dtype=float)
        wr = float((a > 0).mean())
        exp = float(a.mean())
        wins = a[a > 0]
        losses = a[a <= 0]
        gp = float(wins.sum()) if len(wins) else 0.0
        gl = float(-losses.sum()) if len(losses) else 0.0
        pf = (gp / gl) if gl > 1e-12 else (10.0 if gp > 0 else 0.0)
        hist = 0.0
        if exp > 0:
            hist += min(0.25, exp * 0.5)
        if pf > 1.0:
            hist += min(0.15, (pf - 1.0) * 0.1)
        return {
            "status": "sufficient",
            "n_trades": n,
            "win_rate": wr,
            "expectancy": exp,
            "profit_factor": pf,
            "hist_score": min(0.4, hist),
        }

    def log_skip(self, **kw):
        # skip audit rows in backtest (too many); count only
        self.n_skips += 1

    def open_trade(self, **kw) -> int:
        self._tid += 1
        tid = self._tid
        if self.conn is not None:
            self.conn.execute(
                """INSERT INTO trade_log(
                    trade_id,symbol,timeframe,technique,direction,market_regime,
                    entry_timestamp,entry_price,stop_loss_price,take_profit_price,
                    position_size_pct,indicators_json,confidence_score,
                    historical_stats_used_json,reasoning_text,was_entry_taken)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    tid,
                    kw["symbol"],
                    kw.get("timeframe", "1d"),
                    kw["technique"],
                    kw["direction"],
                    kw["regime"],
                    kw["entry_ts"],
                    kw.get("entry_price"),
                    kw.get("sl"),
                    kw.get("tp"),
                    kw.get("size_pct", 1.0),
                    json.dumps(kw.get("indicators") or {}),
                    kw.get("confidence"),
                    json.dumps(kw.get("hist") or {}),
                    kw.get("reason", ""),
                ),
            )
        return tid

    def close_trade(
        self,
        trade_id: int,
        exit_ts: str,
        exit_price: float,
        reason: str,
        r_mult: float,
        technique: str,
        regime: str,
        tier: str,
    ):
        k = self._key(technique, regime, tier)
        self._rs.setdefault(k, []).append(float(r_mult))
        if self.conn is not None:
            self.conn.execute(
                """UPDATE trade_log SET exit_timestamp=?, exit_price=?, exit_reason=?,
                   pnl_r_multiple=? WHERE trade_id=?""",
                (exit_ts, exit_price, reason, r_mult, trade_id),
            )

    def flush(self):
        if self.conn is None:
            return
        # write rolling table once at end
        for (tech, reg, tier), rs in self._rs.items():
            if not rs:
                continue
            a = np.asarray(rs, dtype=float)
            n = len(a)
            wr = float((a > 0).mean())
            avg_r = float(a.mean())
            wins = a[a > 0]
            losses = a[a <= 0]
            gp = float(wins.sum()) if len(wins) else 0.0
            gl = float(-losses.sum()) if len(losses) else 0.0
            pf = (gp / gl) if gl > 1e-12 else (10.0 if gp > 0 else 0.0)
            self.conn.execute(
                """INSERT INTO technique_performance_rolling
                   (technique, market_regime, symbol_tier, n_trades, win_rate,
                    avg_r_multiple, expectancy, profit_factor, last_updated)
                   VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
                (tech, reg, tier, n, wr, avg_r, avg_r, pf),
            )
        self.conn.commit()


def confidence(tech_score: float, hist: dict, regime_fit: float) -> float:
    return float(min(1.0, tech_score + hist.get("hist_score", 0.0) + regime_fit))


def regime_fit_bonus(technique: str, regime: str) -> float:
    """0-0.2 playbook fit."""
    good = {
        "swing": ("trending_up", "trending_down"),
        "breakout": ("low_vol_contraction", "high_vol_expansion"),
        "mean_reversion": ("ranging",),
        "momentum_pullback": ("trending_up", "trending_down"),
    }
    if regime in good.get(technique, ()):
        return 0.2
    if technique == "breakout" and regime in ("ranging", "trending_up", "trending_down"):
        return 0.05
    return 0.0


# ── Backtest engine ──────────────────────────────────────────────────────────

@dataclass
class ClosedTrade:
    symbol: str
    technique: str
    regime: str
    direction: str
    r: float
    exit_reason: str
    entry_ts: str
    conf: float
    memory_status: str
    phase: str  # train|oos


def simulate_symbol(
    sym: str,
    df: pd.DataFrame,
    mem: MemoryStore,
    cut: pd.Timestamp,
    max_hold: int = 15,
) -> list[ClosedTrade]:
    d = enrich(df)
    closed: list[ClosedTrade] = []
    pos = None  # dict
    tier = symbol_tier(sym)
    idx = d.index

    for i in range(60, len(d) - 1):
        row = d.iloc[i]
        t = idx[i]
        phase = "train" if t < cut else "oos"
        nxt = d.iloc[i + 1]
        # manage open
        if pos is not None:
            hi, lo = float(nxt["high"]), float(nxt["low"])
            exit_px = reason = None
            if pos["direction"] == "long":
                if lo <= pos["sl"]:
                    exit_px, reason = pos["sl"], "cut_loss"
                elif hi >= pos["tp"]:
                    exit_px, reason = pos["tp"], "take_profit"
            else:
                if hi >= pos["sl"]:
                    exit_px, reason = pos["sl"], "cut_loss"
                elif lo <= pos["tp"]:
                    exit_px, reason = pos["tp"], "take_profit"
            pos["bars"] += 1
            if exit_px is None and pos["bars"] >= max_hold:
                exit_px, reason = float(nxt["close"]), "timeout"
            if exit_px is not None:
                # R multiple
                if pos["direction"] == "long":
                    raw = (exit_px - pos["entry"]) / pos["risk"]
                else:
                    raw = (pos["entry"] - exit_px) / pos["risk"]
                # cost in R units
                cost_r = COST_RT * pos["entry"] / pos["risk"]
                r_mult = raw - cost_r
                mem.close_trade(
                    pos["trade_id"],
                    str(idx[i + 1]),
                    exit_px,
                    reason,
                    r_mult,
                    pos["technique"],
                    pos["regime"],
                    tier,
                )
                closed.append(
                    ClosedTrade(
                        sym,
                        pos["technique"],
                        pos["regime"],
                        pos["direction"],
                        r_mult,
                        reason,
                        pos["entry_ts"],
                        pos["conf"],
                        pos["mem_status"],
                        pos["phase"],
                    )
                )
                pos = None
            continue  # one position per symbol

        regime = classify_regime(row)
        sigs = playbook_signals(row, regime)
        if not sigs:
            continue

        # pick best by confidence with memory
        best = None
        best_conf = -1.0
        best_hist = None
        for s in sigs:
            hist = mem.retrieve(s.technique, regime, tier)
            # prefer techniques with positive memory when available
            rf = regime_fit_bonus(s.technique, regime)
            conf = confidence(s.score, hist, rf)
            # slight boost if memory ranks this technique high
            if hist["status"] == "sufficient" and (hist.get("expectancy") or 0) > 0:
                conf = min(1.0, conf + 0.05)
            if conf > best_conf:
                best_conf = conf
                best = s
                best_hist = hist

        if best is None or best_conf < CONF_MIN:
            mem.log_skip(
                symbol=sym,
                technique=best.technique if best else "none",
                direction=best.direction if best else "long",
                regime=regime,
                entry_ts=str(t),
                entry_price=float(row["close"]),
                sl=best.sl if best else None,
                tp=best.tp if best else None,
                confidence=best_conf if best else 0.0,
                hist=best_hist or {"status": "no_signal"},
                reason=f"skip conf={best_conf:.2f} regime={regime}",
                indicators={"tier": tier, "adx": float(row["adx"]), "rsi": float(row["rsi"])},
            )
            continue

        entry = float(row["close"])  # enter on close of signal bar (conservative vs next open)
        risk = abs(entry - best.sl)
        if risk <= 0 or risk / entry < 0.002:
            continue
        tid = mem.open_trade(
            symbol=sym,
            technique=best.technique,
            direction=best.direction,
            regime=regime,
            entry_ts=str(t),
            entry_price=entry,
            sl=best.sl,
            tp=best.tp,
            confidence=best_conf,
            hist=best_hist,
            reason=best.reason,
            indicators={
                "tier": tier,
                "adx": float(row["adx"]),
                "rsi": float(row["rsi"]),
                "atr": float(row["atr"]),
                "regime": regime,
            },
        )
        pos = {
            "trade_id": tid,
            "direction": best.direction,
            "entry": entry,
            "sl": best.sl,
            "tp": best.tp,
            "risk": risk,
            "technique": best.technique,
            "regime": regime,
            "entry_ts": str(t),
            "conf": best_conf,
            "mem_status": best_hist.get("status"),
            "phase": phase,
            "bars": 0,
        }

    return closed


def pack_r(trades: list[ClosedTrade]) -> dict:
    if not trades:
        return {"n": 0, "mean_r": None, "win": None, "pf": None, "exp_r": None}
    a = np.array([t.r for t in trades], dtype=float)
    wins = a[a > 0]
    losses = a[a <= 0]
    gp = float(wins.sum()) if len(wins) else 0.0
    gl = float(-losses.sum()) if len(losses) else 0.0
    pf = (gp / gl) if gl > 1e-12 else None
    return {
        "n": int(len(a)),
        "mean_r": float(a.mean()),
        "median_r": float(np.median(a)),
        "win": float((a > 0).mean()),
        "pf": pf,
        "exp_r": float(a.mean()),
        "worst_r": float(a.min()),
        "best_r": float(a.max()),
        "sum_r": float(a.sum()),
    }


def load_symbols(snap: Path, max_symbols: int, min_bars: int) -> dict[str, pd.DataFrame]:
    files = sorted(snap.glob("*__1d.pkl"))
    scored = []
    for p in files:
        if "BTCDOM" in p.name.upper():
            continue
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if len(df) < min_bars or "close" not in df.columns:
            continue
        vol = float(df["volume"].tail(90).mean()) if "volume" in df.columns else 0
        stem = p.stem.replace("__1d", "")
        parts = stem.split("_")
        if len(parts) >= 3 and parts[-1] == parts[-2]:
            sym = f"{'_'.join(parts[:-2])}/{parts[-2]}:{parts[-2]}"
        else:
            sym = stem
        scored.append((vol, sym, df))
    scored.sort(reverse=True, key=lambda x: x[0])
    out = {}
    for _, sym, df in scored[:max_symbols]:
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]
        out[sym] = df
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=str(ROOT / "data" / "snap"))
    ap.add_argument("--symbols", type=int, default=40)
    ap.add_argument("--min-bars", type=int, default=400)
    ap.add_argument("--db", default=str(ROOT / "logs" / "memory_edge_bt.db"))
    ap.add_argument("--out", default=str(ROOT / "logs" / "memory_edge_backtest.json"))
    args = ap.parse_args()

    snap = Path(args.snap)
    dfs = load_symbols(snap, args.symbols, args.min_bars)
    if len(dfs) < 5:
        print("not enough symbols", len(dfs))
        return 2
    # common cut: 70% of median length timeline using BTC if present
    btc = next((df for s, df in dfs.items() if s.upper().startswith("BTC")), next(iter(dfs.values())))
    cut = btc.index[int(len(btc) * 0.70)]
    print(f"symbols={len(dfs)} cut={cut.date()} cost_rt={COST_RT} conf_min={CONF_MIN} min_n={MIN_N_MEMORY}")

    mem = MemoryStore(Path(args.db), persist=True)
    all_closed: list[ClosedTrade] = []
    for i, (sym, df) in enumerate(dfs.items(), 1):
        tr = simulate_symbol(sym, df, mem, cut)
        all_closed.extend(tr)
        if i <= 3 or i % 10 == 0 or i == len(dfs):
            print(f"  [{i}/{len(dfs)}] {sym}: closed+={len(tr)} total={len(all_closed)}", flush=True)
    mem.flush()
    print(f"skips_logged_as_count_only={mem.n_skips}", flush=True)

    train = [t for t in all_closed if t.phase == "train"]
    oos = [t for t in all_closed if t.phase == "oos"]
    # memory-sufficient vs insufficient at entry
    oos_mem = [t for t in oos if t.memory_status == "sufficient"]
    oos_cold = [t for t in oos if t.memory_status != "sufficient"]

    by_tech: dict[str, list] = {}
    by_reg: dict[str, list] = {}
    for t in oos:
        by_tech.setdefault(t.technique, []).append(t)
        by_reg.setdefault(t.regime, []).append(t)

    # verdict
    oos_stats = pack_r(oos)
    verdict = "INCONCLUSIVE"
    reason = ""
    if oos_stats["n"] < 50:
        verdict = "INCONCLUSIVE"
        reason = f"OOS n={oos_stats['n']} < 50"
    elif oos_stats["mean_r"] is not None and oos_stats["mean_r"] <= 0:
        verdict = "REJECTED"
        reason = f"OOS mean_R={oos_stats['mean_r']:+.4f} ≤ 0 n={oos_stats['n']}"
    elif oos_stats.get("pf") is not None and oos_stats["pf"] < 1.0:
        verdict = "REJECTED"
        reason = f"OOS PF={oos_stats['pf']:.2f} < 1"
    else:
        # still need train not wildly opposite + modest bar
        tr_s = pack_r(train)
        if tr_s["mean_r"] is not None and tr_s["mean_r"] <= 0:
            verdict = "NOT_PROVEN"
            reason = f"OOS mean_R>0 but train mean_R={tr_s['mean_r']:+.4f}≤0 (inconsistent)"
        else:
            verdict = "NOT_PROVEN"
            reason = (
                f"OOS mean_R={oos_stats['mean_r']:+.4f} n={oos_stats['n']} PF={oos_stats.get('pf')} "
                "— needs lockbox + paper; memory loop alone not auto-PROMOTE"
            )

    out = {
        "meta": {
            "spec": "riset_edge.txt memory-loop (deterministic backtest)",
            "snap": str(snap.resolve()),
            "n_symbols": len(dfs),
            "cut": str(cut),
            "cost_rt": COST_RT,
            "conf_min": CONF_MIN,
            "min_n_memory": MIN_N_MEMORY,
            "rr_target": RR_TARGET,
            "techniques": list(TECHNIQUES),
            "db": str(args.db),
        },
        "train": pack_r(train),
        "oos": oos_stats,
        "oos_memory_sufficient": pack_r(oos_mem),
        "oos_memory_cold_start": pack_r(oos_cold),
        "oos_by_technique": {k: pack_r(v) for k, v in by_tech.items()},
        "oos_by_regime": {k: pack_r(v) for k, v in by_reg.items()},
        "verdict": verdict,
        "reason": reason,
        "promote_paper": False,
        "note": (
            "PROMOTE_PAPER requires separate project bar (lockbox, cost×2, multi-trial). "
            "This run measures whether memory-gated playbooks beat zero after cost."
        ),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("\n=== MEMORY EDGE BACKTEST ===")
    print("TRAIN", out["train"])
    print("OOS  ", out["oos"])
    print("OOS mem-sufficient", out["oos_memory_sufficient"])
    print("OOS cold-start    ", out["oos_memory_cold_start"])
    print("by technique", json.dumps(out["oos_by_technique"], indent=2))
    print("by regime", json.dumps(out["oos_by_regime"], indent=2))
    print("VERDICT:", verdict, "—", reason)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
