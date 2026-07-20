#!/usr/bin/env python3
"""P2 — H-CYC-02/03: fase siklus terukur, BTC.D/alt-season, kerangka unlock.

Ukur (bukan deploy entry):
  A) Fase harga BTC (accumulation/uptrend/distribution/markdown) vs forward ret BTC & alt EW
  B) Dominance regime (BTCDOM) vs alt EW forward
  C) Unlock calendar: bila CSV ada → short/long di window; bila tidak → gap jujur

Pakai:
  python cyc02_cycle_unlock_altseason.py
  python cyc02_cycle_unlock_altseason.py --unlock-csv data/unlock_calendar.csv
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from bot.cycle_regime import (
    build_cycle_context,
    dominance_regime,
    load_unlock_calendar,
    measured_cycle_phase,
    unlock_window_for,
)


def _load_close(path: Path) -> pd.Series | None:
    if not path.exists():
        return None
    df = pd.read_pickle(path)
    if "close" not in df.columns:
        return None
    s = df["close"].astype(float).sort_index()
    return s[~s.index.duplicated(keep="last")]


def _sym_from_stem(stem: str, tf: str) -> str:
    suf = f"__{tf}"
    if stem.endswith(suf):
        stem = stem[: -len(suf)]
    parts = stem.split("_")
    if len(parts) >= 3 and parts[-1] == parts[-2]:
        return f"{'_'.join(parts[:-2])}/{parts[-2]}:{parts[-2]}"
    return stem


def load_universe(snap: Path, tf: str, min_bars: int) -> tuple[pd.Series, pd.Series | None, dict[str, pd.Series]]:
    btc = None
    btcdom = None
    alts: dict[str, pd.Series] = {}
    for p in sorted(snap.glob(f"*__{tf}.pkl")):
        s = _load_close(p)
        if s is None or len(s) < min_bars:
            continue
        stem = p.stem
        if stem.upper().startswith("BTCDOM"):
            btcdom = s
            continue
        if stem.upper().startswith("BTC_") and "DOM" not in stem.upper():
            if btc is None or len(s) > len(btc):
                btc = s
            continue
        alts[_sym_from_stem(stem, tf)] = s
    if btc is None:
        raise SystemExit("BTC not found")
    return btc, btcdom, alts


def t_p_pos(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return 1.0
    sd = x.std(ddof=1)
    if sd <= 0:
        return 0.0 if x.mean() > 0 else 1.0
    t = x.mean() / (sd / math.sqrt(len(x)))
    return 0.5 * math.erfc(t / math.sqrt(2.0))


def t_p_neg(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 3:
        return 1.0
    sd = x.std(ddof=1)
    if sd <= 0:
        return 0.0 if x.mean() < 0 else 1.0
    t = x.mean() / (sd / math.sqrt(len(x)))
    return 0.5 * math.erfc((-t) / math.sqrt(2.0)) if t < 0 else 1.0 - 0.5 * math.erfc(t / math.sqrt(2.0))


def pack(xs: list[float]) -> dict:
    a = np.asarray(xs, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return {"n": 0}
    return {
        "n": int(len(a)),
        "mean": float(a.mean()),
        "median": float(np.median(a)),
        "win": float((a > 0).mean()),
        "p_pos": t_p_pos(a),
        "p_neg": t_p_neg(a),
    }


def alt_ew_forward(alts: dict[str, pd.Series], t: pd.Timestamp, hold: int, min_n: int = 20) -> float | None:
    fwds = []
    for s in alts.values():
        if t not in s.index:
            continue
        loc = s.index.get_loc(t)
        if not isinstance(loc, (int, np.integer)):
            continue
        loc = int(loc)
        if loc + hold >= len(s):
            continue
        px0, px1 = float(s.iloc[loc]), float(s.iloc[loc + hold])
        if px0 > 0 and math.isfinite(px0) and math.isfinite(px1):
            fwds.append(px1 / px0 - 1.0)
    if len(fwds) < min_n:
        return None
    return float(np.mean(fwds))


def btc_forward(btc: pd.Series, t: pd.Timestamp, hold: int) -> float | None:
    if t not in btc.index:
        return None
    loc = btc.index.get_loc(t)
    if not isinstance(loc, (int, np.integer)):
        return None
    loc = int(loc)
    if loc + hold >= len(btc):
        return None
    px0, px1 = float(btc.iloc[loc]), float(btc.iloc[loc + hold])
    if px0 <= 0:
        return None
    return px1 / px0 - 1.0


def study_phases(btc: pd.Series, alts: dict[str, pd.Series], holds: list[int], step: int, oos_frac: float) -> dict:
    idx = btc.index
    # need MA200 history
    start = 220
    times = list(idx[start::step])
    cut = idx[int(len(idx) * (1.0 - oos_frac))]
    by_phase: dict[str, dict[str, list]] = {}

    for t in times:
        m = measured_cycle_phase(btc, asof=t)
        ph = m.get("phase", "unknown")
        if ph == "unknown":
            continue
        split = "oos" if t >= cut else "train"
        for hold in holds:
            br = btc_forward(btc, t, hold)
            ar = alt_ew_forward(alts, t, hold)
            key = f"{ph}|{hold}|{split}"
            by_phase.setdefault(key, {"btc": [], "alt": []})
            if br is not None:
                by_phase[key]["btc"].append(br)
            if ar is not None:
                by_phase[key]["alt"].append(ar)

    rows = []
    for key, v in sorted(by_phase.items()):
        ph, hold, split = key.split("|")
        rows.append({
            "phase": ph,
            "hold": int(hold),
            "split": split,
            "btc": pack(v["btc"]),
            "alt_ew": pack(v["alt"]),
        })
    return {"cut": str(cut), "rows": rows}


def study_dominance(btc: pd.Series, btcdom: pd.Series | None, alts: dict[str, pd.Series],
                    holds: list[int], step: int, oos_frac: float) -> dict:
    if btcdom is None:
        return {"error": "no BTCDOM series"}
    # align common index
    common = btc.index.intersection(btcdom.index)
    btc_a = btc.reindex(common).ffill()
    dom_a = btcdom.reindex(common).ffill()
    start = 40
    times = list(common[start::step])
    cut = common[int(len(common) * (1.0 - oos_frac))]
    by: dict[str, dict[str, list]] = {}
    for t in times:
        d = dominance_regime(dom_a, btc_a, asof=t)
        reg = d.get("regime", "unknown")
        if reg == "unknown":
            continue
        split = "oos" if t >= cut else "train"
        for hold in holds:
            ar = alt_ew_forward(alts, t, hold)
            br = btc_forward(btc_a, t, hold)
            key = f"{reg}|{hold}|{split}"
            by.setdefault(key, {"alt": [], "btc": []})
            if ar is not None:
                by[key]["alt"].append(ar)
            if br is not None:
                by[key]["btc"].append(br)
    rows = []
    for key, v in sorted(by.items()):
        reg, hold, split = key.split("|")
        rows.append({
            "dominance_regime": reg,
            "hold": int(hold),
            "split": split,
            "alt_ew": pack(v["alt"]),
            "btc": pack(v["btc"]),
        })
    return {"cut": str(cut), "rows": rows}


def study_unlock(btc: pd.Series, alts: dict[str, pd.Series], cal: pd.DataFrame,
                 holds: list[int], pre: int, post: int, cost: float) -> dict:
    if cal is None or len(cal) == 0:
        return {
            "status": "NO_DATA",
            "reason": (
                "No unlock calendar loaded. Public unlock APIs often paywalled. "
                "Copy data/unlock_calendar.example.csv → data/unlock_calendar.csv and fill rows."
            ),
            "events": 0,
        }
    # map base → series
    base_map: dict[str, pd.Series] = {}
    for sym, s in alts.items():
        base = sym.split("/")[0].upper()
        base_map[base] = s
        if base.startswith("1000") and len(base) > 4:
            base_map[base[4:]] = s

    events = []
    long_by_h = {h: [] for h in holds}
    short_by_h = {h: [] for h in holds}
    for _, row in cal.iterrows():
        base = str(row["symbol"]).split("/")[0].upper()
        s = base_map.get(base)
        if s is None:
            events.append({"base": base, "status": "no_price_series"})
            continue
        u = pd.Timestamp(row["unlock_date"])
        if u.tzinfo is None:
            u = u.tz_localize("UTC")
        # entry at unlock day close if available else next
        if u not in s.index:
            # nearest forward bar
            fut = s.index[s.index >= u]
            if len(fut) == 0:
                events.append({"base": base, "status": "no_bars_after"})
                continue
            t = fut[0]
        else:
            t = u
        for hold in holds:
            loc = s.index.get_loc(t)
            if not isinstance(loc, (int, np.integer)):
                continue
            loc = int(loc)
            if loc + hold >= len(s):
                continue
            fwd = float(s.iloc[loc + hold] / s.iloc[loc] - 1.0)
            long_by_h[hold].append(fwd - cost)
            short_by_h[hold].append(-fwd - cost)
        events.append({
            "base": base,
            "status": "ok",
            "entry": str(t.date()),
            "unlock": str(u.date()),
            "pct_supply": row.get("pct_supply"),
        })

    out = {
        "status": "MEASURED",
        "n_calendar_rows": int(len(cal)),
        "n_matched_price": sum(1 for e in events if e.get("status") == "ok"),
        "events": events[:50],
        "long_after_unlock": {str(h): pack(long_by_h[h]) for h in holds},
        "short_after_unlock": {str(h): pack(short_by_h[h]) for h in holds},
    }
    # verdict short
    s7 = out["short_after_unlock"].get("7") or out["short_after_unlock"].get(str(holds[-1]), {})
    if s7.get("n", 0) >= 15 and s7.get("mean", 0) > 0 and s7.get("p_pos", 1) < 0.05:
        out["verdict"] = "CANDIDATE"
        out["reason"] = f"short after unlock mean={s7['mean']:+.4%} n={s7['n']} p={s7['p_pos']:.4f}"
    elif s7.get("n", 0) < 15:
        out["verdict"] = "INCONCLUSIVE"
        out["reason"] = f"n too small for short hold (n={s7.get('n', 0)}) — expand calendar"
    else:
        out["verdict"] = "NOT_PROVEN"
        out["reason"] = f"short mean={s7.get('mean')} n={s7.get('n')} p={s7.get('p_pos')}"
    return out


def verdict_phase(rows: list[dict]) -> dict:
    """Stance guidance: is markdown/distribution bad for long alt OOS?"""
    oos = [r for r in rows if r["split"] == "oos" and r["hold"] == 7]
    notes = []
    for r in oos:
        a = r["alt_ew"]
        if a.get("n", 0) < 10:
            continue
        notes.append(f"{r['phase']}: alt7d mean={a['mean']:+.2%} n={a['n']} p_pos={a['p_pos']:.3f}")
    # simple: markdown OOS alt negative → stance risk_off useful as context
    md = next((r for r in oos if r["phase"] == "markdown"), None)
    up = next((r for r in oos if r["phase"] == "uptrend"), None)
    if md and md["alt_ew"].get("n", 0) >= 10 and md["alt_ew"]["mean"] < 0:
        if up and up["alt_ew"].get("mean", -1) > md["alt_ew"]["mean"]:
            return {
                "verdict": "USEFUL_AS_STANCE_CONTEXT",
                "reason": "OOS markdown alt weaker than uptrend — inject phase to agent stance/size only",
                "notes": notes,
            }
    return {
        "verdict": "CONTEXT_ONLY",
        "reason": "phase labels differ but not strong enough for hard gate — keep prompt inject only",
        "notes": notes,
    }


def verdict_dom(rows: list[dict]) -> dict:
    oos = [r for r in rows if r["split"] == "oos" and r["hold"] == 7]
    alt_s = next((r for r in oos if r["dominance_regime"] == "alt_season"), None)
    risk = next((r for r in oos if r["dominance_regime"] == "risk_off"), None)
    notes = []
    for r in oos:
        a = r["alt_ew"]
        if a.get("n", 0):
            notes.append(f"{r['dominance_regime']}: alt7d={a['mean']:+.2%} n={a['n']}")
    if alt_s and risk and alt_s["alt_ew"].get("n", 0) >= 8 and risk["alt_ew"].get("n", 0) >= 8:
        if alt_s["alt_ew"]["mean"] > risk["alt_ew"]["mean"]:
            return {
                "verdict": "USEFUL_AS_STANCE_CONTEXT",
                "reason": (
                    f"OOS alt_season alt7d {alt_s['alt_ew']['mean']:+.2%} > "
                    f"risk_off {risk['alt_ew']['mean']:+.2%} — dominance inject OK as context"
                ),
                "notes": notes,
            }
    return {"verdict": "CONTEXT_ONLY", "reason": "dominance split weak/unstable", "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot-dir", default="data/snap")
    ap.add_argument("--tf", default="1d")
    ap.add_argument("--min-bars", type=int, default=200)
    ap.add_argument("--holds", nargs="*", type=int, default=[1, 7, 20])
    ap.add_argument("--step", type=int, default=5, help="sample every N days (speed)")
    ap.add_argument("--oos-frac", type=float, default=0.30)
    ap.add_argument("--unlock-csv", default="data/unlock_calendar.csv")
    ap.add_argument("--fee", type=float, default=0.04)
    ap.add_argument("--slippage", type=float, default=0.05)
    ap.add_argument("--out", default="logs/cyc02_cycle_unlock.json")
    args = ap.parse_args()

    cost = 2 * (args.fee + args.slippage) / 100
    snap = Path(args.snapshot_dir)
    print(f"Loading {snap} …")
    btc, btcdom, alts = load_universe(snap, args.tf, args.min_bars)
    print(f"BTC bars={len(btc)} alts={len(alts)} btcdom={'yes' if btcdom is not None else 'NO'}")

    # snapshot current context
    ctx = build_cycle_context(btc, btcdom)
    print("\n=== Current cycle context ===")
    print(json.dumps(ctx, indent=2, default=str)[:1200])

    print("\n=== Phase study (sample step=%d) ===" % args.step)
    ph = study_phases(btc, alts, args.holds, args.step, args.oos_frac)
    print("cut", ph["cut"])
    for r in ph["rows"]:
        if r["hold"] not in (1, 7):
            continue
        a, b = r["alt_ew"], r["btc"]
        if a.get("n", 0) == 0:
            continue
        print(
            f"  {r['phase']:14s} hold={r['hold']:<3} {r['split']:5s} "
            f"alt n={a['n']:<4} mean={a['mean']:+.3%}  "
            f"btc n={b.get('n', 0):<4} mean={b.get('mean', float('nan')):+.3%}"
            if b.get("n") else
            f"  {r['phase']:14s} hold={r['hold']:<3} {r['split']:5s} alt n={a['n']} mean={a['mean']:+.3%}"
        )
    v_ph = verdict_phase(ph["rows"])
    print("PHASE VERDICT:", v_ph["verdict"], "—", v_ph["reason"])
    for n in v_ph.get("notes") or []:
        print("  ", n)

    print("\n=== Dominance / alt-season study ===")
    dom = study_dominance(btc, btcdom, alts, args.holds, args.step, args.oos_frac)
    if "error" in dom:
        print(dom["error"])
        v_dom = {"verdict": "NO_DATA", "reason": dom["error"]}
    else:
        print("cut", dom["cut"])
        for r in dom["rows"]:
            if r["hold"] != 7:
                continue
            a = r["alt_ew"]
            if a.get("n", 0) == 0:
                continue
            print(
                f"  {r['dominance_regime']:12s} {r['split']:5s} "
                f"alt7d n={a['n']:<4} mean={a['mean']:+.3%} win={a['win']:.1%}"
            )
        v_dom = verdict_dom(dom["rows"])
        print("DOM VERDICT:", v_dom["verdict"], "—", v_dom["reason"])

    print("\n=== Unlock study ===")
    cal_path = Path(args.unlock_csv)
    cal = load_unlock_calendar(cal_path) if cal_path.exists() else pd.DataFrame()
    if not cal_path.exists():
        print(f"missing {cal_path} — trying example (usually empty of real events)")
        ex = Path("data/unlock_calendar.example.csv")
        if ex.exists():
            # example has comment lines — load may fail; strip
            try:
                cal = load_unlock_calendar(ex)
            except Exception:
                cal = pd.DataFrame()
    un = study_unlock(btc, alts, cal, args.holds, 3, 7, cost)
    print("UNLOCK:", un.get("status"), un.get("verdict"), un.get("reason") or un.get("reason", ""))
    if un.get("status") == "MEASURED":
        for h in args.holds:
            s = un["short_after_unlock"].get(str(h), {})
            if s.get("n"):
                print(f"  short hold={h}: n={s['n']} mean={s['mean']:+.3%} p_pos={s['p_pos']:.3f}")

    out = {
        "meta": {
            "snapshot": str(snap),
            "n_alts": len(alts),
            "btc_bars": len(btc),
            "holds": args.holds,
            "step": args.step,
            "cost_rt": cost,
        },
        "current_context": ctx,
        "phase_study": ph,
        "phase_verdict": v_ph,
        "dominance_study": dom,
        "dominance_verdict": v_dom,
        "unlock_study": un,
        "p3_recommendation": {
            "inject_context": True,
            "hard_gate": False,
            "fields": ["phase", "calendar_phase", "dominance.regime", "unlock.in_window"],
            "note": (
                "Wire build_cycle_context into ReAct/Gemini prompt only. "
                "No FLAT/manage. No auto short unlock until CANDIDATE + n large."
            ),
        },
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
