#!/usr/bin/env python3
"""Path B — G2 ENTRY A/B: does quality rank improve real paper entries?

Two sources:
  1) Live stamps: decision_log G2_ENTRY_SHADOW + opens with g2_* fields (going forward)
  2) Historical reconstruction: join trades journal opens/closes with G2 rank
     at open date from snap (immediate measurement on past dry trades)

  PYTHONPATH=. python research/g2_entry_ab_report.py
  PYTHONPATH=. python research/g2_entry_ab_report.py --mode dry --snap data/snap

Output: logs/g2_entry_ab_report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "research")]

from g2_quality_mom_shadow import ARM, load_panel, quality_score  # noqa: E402

OUT = ROOT / "logs" / "g2_entry_ab_report.json"


def pack(xs: list[float]) -> dict:
    a = np.asarray([float(x) for x in xs if x is not None and np.isfinite(float(x))], dtype=float)
    if len(a) == 0:
        return {"n": 0, "mean": None, "win": None, "sum": None, "worst": None}
    return {
        "n": int(len(a)),
        "mean": float(a.mean()),
        "median": float(np.median(a)),
        "win": float((a > 0).mean()),
        "sum": float(a.sum()),
        "worst": float(a.min()),
        "best": float(a.max()),
        "std": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
    }


def base_of(sym: str) -> str:
    return sym.split("/")[0].upper().replace("1000", "").replace("1M", "")


def col_for_base(panel: pd.DataFrame, base: str) -> str | None:
    """Map coin base → panel column (full CCXT symbol)."""
    b = base.upper()
    for c in panel.columns:
        if str(c).split("/")[0].upper() == b:
            return c
    return None


def load_trades(mode: str) -> list[dict]:
    paths = [
        ROOT / f"logs/trades_{mode}.jsonl",
        ROOT / "logs/trades.jsonl",
    ]
    rows = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        if rows:
            break
    return rows


def pair_opens_closes(rows: list[dict]) -> list[dict]:
    """Match forward_open → next forward_close per symbol (FIFO)."""
    opens: dict[str, list] = {}
    paired = []
    for r in rows:
        ev = r.get("event")
        sym = r.get("symbol")
        if not sym:
            continue
        if ev == "forward_open":
            opens.setdefault(sym, []).append(r)
        elif ev == "forward_close":
            if not opens.get(sym):
                continue
            o = opens[sym].pop(0)
            paired.append({"open": o, "close": r})
    return paired


def rank_panel_history(panel: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """DataFrame of rank_pct per day per base (0..1)."""
    score = quality_score(panel, lookback)
    # rank across columns each day
    return score.rank(axis=1, pct=True)


def label_trade(rank_pct: float | None, side: str, top_q: float) -> str:
    if rank_pct is None or not np.isfinite(rank_pct):
        return "unknown"
    if rank_pct >= 1.0 - top_q:
        bucket = "top"
    elif rank_pct <= top_q:
        bucket = "bottom"
    else:
        bucket = "mid"
    side = (side or "").lower()
    if bucket == "mid":
        return "neutral"
    if side == "long":
        return "aligned" if bucket == "top" else "misaligned"
    if side == "short":
        return "aligned" if bucket == "bottom" else "misaligned"
    return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="dry")
    ap.add_argument("--snap", default=str(ROOT / "data" / "snap"))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--top-q", type=float, default=ARM["top_q"])
    args = ap.parse_args()

    trades = load_trades(args.mode)
    pairs = pair_opens_closes(trades)
    print(f"Path B | mode={args.mode} | open-close pairs={len(pairs)}")

    panel = load_panel(Path(args.snap))
    ranks = rank_panel_history(panel, ARM["lookback"])
    # normalize index to date for join
    rank_by_date = ranks.copy()
    rank_by_date.index = pd.to_datetime(rank_by_date.index).tz_localize(None)

    labeled = []
    for p in pairs:
        o, c = p["open"], p["close"]
        r = c.get("r")
        if r is None:
            continue
        side = (o.get("side") or c.get("side") or "").lower()
        sym = o.get("symbol") or c.get("symbol")
        base = base_of(sym)
        # open ts → date
        ts = o.get("ts") or o.get("opened_ts")
        try:
            od = pd.Timestamp(ts).tz_localize(None) if ts else None
            if od is not None and od.tzinfo:
                od = od.tz_localize(None)
            day = od.normalize() if od is not None else None
        except Exception:
            day = None
        rp = None
        col = col_for_base(rank_by_date, base)
        if day is not None and col is not None:
            # last rank on or before open day
            sub = rank_by_date.loc[:day, col].dropna()
            if len(sub):
                rp = float(sub.iloc[-1])
        # prefer stamp on open if present
        if o.get("g2_aligned") is True:
            lab = "aligned"
            rp = o.get("g2_rank_pct", rp)
        elif o.get("g2_aligned") is False:
            lab = "misaligned"
            rp = o.get("g2_rank_pct", rp)
        else:
            lab = label_trade(rp, side, args.top_q)
        labeled.append(
            {
                "symbol": sym,
                "base": base,
                "in_g2_universe": col is not None,
                "side": side,
                "r": float(r),
                "label": lab,
                "rank_pct": rp,
                "open_ts": ts,
                "close_ts": c.get("ts"),
                "reason": c.get("reason"),
            }
        )

    # also count G2_ENTRY_SHADOW rows
    shadow_n = 0
    dlog = ROOT / f"logs/decision_log_{args.mode}.jsonl"
    if not dlog.exists():
        dlog = ROOT / "logs/decision_log.jsonl"
    if dlog.exists():
        for line in dlog.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("action") == "G2_ENTRY_SHADOW":
                shadow_n += 1

    # Primary A/B = trades that are IN G2 pure-majors universe only
    in_univ = [x for x in labeled if x.get("in_g2_universe")]
    aligned = [x["r"] for x in in_univ if x["label"] == "aligned"]
    mis = [x["r"] for x in in_univ if x["label"] == "misaligned"]
    neu = [x["r"] for x in in_univ if x["label"] == "neutral"]
    unk = [x["r"] for x in labeled if x["label"] == "unknown"]
    out_univ = [x["r"] for x in labeled if not x.get("in_g2_universe")]

    pa, pm = pack(aligned), pack(mis)
    delta = None
    if pa["mean"] is not None and pm["mean"] is not None:
        delta = pa["mean"] - pm["mean"]

    # verdict Path B (only on pure-majors subset)
    verdict = "PATH_B_INCONCLUSIVE"
    reason = ""
    if pa["n"] < 15 or pm["n"] < 10:
        verdict = "PATH_B_INCONCLUSIVE"
        reason = (
            f"n_aligned={pa['n']} n_misaligned={pm['n']} in pure-majors "
            f"(need ≥15 / ≥10); n_outside_universe={len(out_univ)}"
        )
    elif delta is not None and delta > 0 and (pa["mean"] or 0) > (pm["mean"] or 0):
        verdict = "PATH_B_LEAN_POSITIVE"
        reason = f"aligned mean {pa['mean']:+.4f} > misaligned {pm['mean']:+.4f} (delta {delta:+.4f})"
        if pa["n"] >= 40 and pm["n"] >= 25 and delta > 0.05 and (pa["mean"] or 0) > 0:
            verdict = "PATH_B_PASS_ENTRY_FILTER"
            reason += " — consider block=true after human review"
    elif delta is not None and delta <= 0:
        verdict = "PATH_B_FAIL"
        reason = f"aligned not better (delta {delta:+.4f})"

    out = {
        "meta": {
            "path": "B_entry_overlay_ab",
            "arm": dict(ARM),
            "mode": args.mode,
            "top_q": args.top_q,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "method": "historical_rank_at_open_from_snap + optional open stamps",
            "g2_entry_shadow_log_lines": shadow_n,
            "universe": "pure_majors_only_for_ab",
        },
        "n_pairs": len(pairs),
        "n_labeled": len(labeled),
        "n_in_g2_universe": len(in_univ),
        "n_outside_g2_universe": len(out_univ),
        "aligned": pa,
        "misaligned": pm,
        "neutral": pack(neu),
        "unknown": pack(unk),
        "outside_universe": pack(out_univ),
        "delta_mean_aligned_minus_misaligned": delta,
        "verdict": verdict,
        "reason": reason,
        "samples_tail": labeled[-8:],
        "note": (
            "A/B only among pure-majors opens. Outside universe = fail-open (G2 N/A). "
            "block=true only after PATH_B_PASS + human review."
        ),
    }
    Path(args.out).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print("=== PATH B G2 ENTRY A/B ===")
    print("aligned", pa)
    print("misaligned", pm)
    print("neutral", pack(neu))
    print("unknown", pack(unk))
    print("delta", delta)
    print("VERDICT", verdict, "—", reason)
    print("G2_ENTRY_SHADOW lines", shadow_n)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
