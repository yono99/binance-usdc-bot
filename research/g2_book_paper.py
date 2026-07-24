#!/usr/bin/env python3
"""Path A — G2 FULL BOOK paper engine (setia research LS, frozen arm).

Arm: G2_qmom_h10_q0.3
  - Daily rebalance every `hold` days (default 10)
  - Long top 30% quality, short bottom 30% pure majors
  - Equal-weight each leg; cost_rt charged each rebalance (LS book)
  - Stress cost×2 reported

This is the FULL STRATEGY ENGINE path for G2 (not rules overlay).

  PYTHONPATH=. python research/g2_book_paper.py
  PYTHONPATH=. python research/g2_book_paper.py --snap data/snap --out logs/g2_book_paper.json

Does NOT place exchange orders. Counterfactual on snap closes only.
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

from g2_quality_mom_shadow import (  # noqa: E402
    ARM,
    book_at,
    load_panel,
    quality_score,
    settle_r,
)

REPORT = ROOT / "logs" / "g2_book_paper.json"
SERIES = ROOT / "logs" / "g2_book_paper_series.jsonl"


def pack(xs: list[float]) -> dict:
    a = np.asarray(xs, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return {"n": 0, "mean": None, "median": None, "win": None, "sum": None, "sharpe": None}
    sd = float(a.std(ddof=1)) if len(a) > 1 else 0.0
    mean = float(a.mean())
    return {
        "n": int(len(a)),
        "mean": mean,
        "median": float(np.median(a)),
        "win": float((a > 0).mean()),
        "sum": float(a.sum()),
        "worst": float(a.min()),
        "best": float(a.max()),
        "sharpe": float(mean / sd) if sd > 0 else None,
    }


def run_book(panel: pd.DataFrame, hold: int, top_q: float, cost: float) -> list[dict]:
    """Non-overlapping rebalance every `hold` bars (full engine cadence)."""
    lb = ARM["lookback"]
    score = quality_score(panel, lb)
    rows = []
    i = lb + 5
    n = len(panel.index)
    while i + hold < n:
        t = panel.index[i]
        sc = score.loc[t]
        book = book_at(sc, top_q)
        if book["n"] < 8 or not book["longs"]:
            i += 1
            continue
        st = settle_r(panel, t, book["longs"], book["shorts"], hold, cost)
        if not st or st.get("status") != "settled":
            i += hold
            continue
        rows.append(
            {
                "signal_ts": str(t.date()) if hasattr(t, "date") else str(t),
                "exit_ts": st.get("exit_ts"),
                "r_net": st.get("r_net"),
                "r_gross": st.get("r_gross"),
                "longs": book["longs"],
                "shorts": book["shorts"],
                "k": book.get("k"),
                "universe_n": book.get("n"),
                "cost_rt": cost,
            }
        )
        i += hold  # non-overlapping periods
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=str(ROOT / "data" / "snap"))
    ap.add_argument("--out", default=str(REPORT))
    ap.add_argument("--hold", type=int, default=ARM["hold"])
    ap.add_argument("--top-q", type=float, default=ARM["top_q"])
    args = ap.parse_args()

    panel = load_panel(Path(args.snap))
    print(f"Path A G2 BOOK | {ARM['id']} | panel {panel.shape} | hold={args.hold} q={args.top_q}")

    rows = run_book(panel, args.hold, args.top_q, ARM["cost_rt"])
    rows2 = run_book(panel, args.hold, args.top_q, ARM["cost_rt"] * 2)
    r_net = [float(r["r_net"]) for r in rows if r.get("r_net") is not None]
    r2 = [float(r["r_net"]) for r in rows2 if r.get("r_net") is not None]

    # chronological splits
    def split70(a):
        k = int(len(a) * 0.70)
        return a[:k], a[k:]

    def split502030(a):
        i1 = int(len(a) * 0.50)
        i2 = int(len(a) * 0.80)
        return a[:i1], a[i1:i2], a[i2:]

    tr, oos = split70(r_net)
    tr5, oos5, lock = split502030(r_net)
    tr2, oos2 = split70(r2)

    # cumulative equity of 1 unit risk per period (sum R)
    equity = np.cumsum(r_net) if r_net else np.array([])
    maxdd = None
    if len(equity):
        peak = np.maximum.accumulate(equity)
        dd = equity - peak
        maxdd = float(dd.min())

    out = {
        "meta": {
            "path": "A_full_book_engine",
            "arm": dict(ARM),
            "hold": args.hold,
            "top_q": args.top_q,
            "rebalance": "non_overlapping_every_hold_days",
            "panel": list(panel.shape),
            "range": [str(panel.index.min().date()), str(panel.index.max().date())],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "wire": False,
        },
        "all": pack(r_net),
        "train_70": pack(tr),
        "oos_30": pack(oos),
        "train_50": pack(tr5),
        "oos_30_of_50_30_20": pack(oos5),
        "lockbox_20": pack(lock),
        "all_cost2x": pack(r2),
        "oos_cost2x_70_30": pack(oos2),
        "max_drawdown_sumR": maxdd,
        "n_rebalances": len(rows),
        "last": rows[-3:] if rows else [],
        "verdict": None,
        "note": (
            "Full G2 strategy engine on snap (not live). "
            "PROMOTE operational only after human review + live paper process."
        ),
    }

    # simple verdict
    oos_m = out["oos_30"].get("mean")
    lock_m = out["lockbox_20"].get("mean")
    tr_m = out["train_70"].get("mean")
    c2_m = out["oos_cost2x_70_30"].get("mean")
    if (
        out["oos_30"]["n"] >= 20
        and tr_m is not None and tr_m > 0
        and oos_m is not None and oos_m > 0
        and lock_m is not None and lock_m > 0
        and c2_m is not None and c2_m > 0
    ):
        out["verdict"] = "PATH_A_PASS_PAPER_BOOK"
    elif oos_m is not None and oos_m > 0 and tr_m is not None and tr_m > 0:
        out["verdict"] = "PATH_A_LEAN_LOCK_OR_N"
    else:
        out["verdict"] = "PATH_A_FAIL"

    Path(args.out).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    SERIES.parent.mkdir(parents=True, exist_ok=True)
    with SERIES.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")

    print("=== PATH A G2 FULL BOOK ===")
    print("n_rebalances", out["n_rebalances"])
    print("train_70", out["train_70"])
    print("oos_30", out["oos_30"])
    print("lockbox_20", out["lockbox_20"])
    print("oos_cost2x", out["oos_cost2x_70_30"])
    print("maxDD_sumR", maxdd)
    print("VERDICT", out["verdict"])
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
