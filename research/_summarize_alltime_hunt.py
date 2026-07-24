#!/usr/bin/env python3
"""Summarize logs/edge_hunt_alltime_*.json — compact status for edge loop."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "logs" / "edge_hunt_alltime_20260724.json"


def main() -> int:
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    d = json.loads(p.read_text(encoding="utf-8"))
    print("file", p)
    print("meta", d.get("meta"))
    rounds = d.get("rounds") or {}
    print("rounds", list(rounds.keys()))
    all_arms: list[dict] = []
    for rk, rv in rounds.items():
        arms = rv.get("arms") or []
        if isinstance(arms, dict):
            for aid, a in arms.items():
                a = dict(a)
                a["id"] = a.get("id", aid)
                a["round"] = rk
                all_arms.append(a)
        else:
            for a in arms:
                a = dict(a)
                a["round"] = rk
                all_arms.append(a)
    print("n_arms", len(all_arms))
    print("verdicts", dict(Counter(a.get("verdict") for a in all_arms)))

    def oosm(a: dict) -> float:
        o = a.get("oos") or {}
        m = o.get("mean")
        return float(m) if m is not None else -999.0

    pos = sorted(
        [a for a in all_arms if (a.get("oos") or {}).get("mean") is not None],
        key=oosm,
        reverse=True,
    )
    print("TOP 15 OOS:")
    for a in pos[:15]:
        o = a["oos"]
        t = a.get("train") or {}
        tm = t.get("mean")
        tm_s = f"{tm:+.4%}" if tm is not None else "n/a"
        print(
            f"  {a.get('round')} {a.get('id')}: oos={o['mean']:+.4%} n={o['n']} "
            f"train={tm_s} v={a.get('verdict')} p_adj={a.get('p_adj')}"
        )

    cands = [a for a in all_arms if a.get("verdict") == "CANDIDATE"]
    print("CANDIDATES", len(cands))
    for a in cands:
        print(" ", a.get("id"), a.get("reason"))

    both = [
        a
        for a in all_arms
        if (a.get("train") or {}).get("mean", 0) is not None
        and (a.get("oos") or {}).get("mean", 0) is not None
        and float((a.get("train") or {}).get("mean") or 0) > 0
        and float((a.get("oos") or {}).get("mean") or 0) > 0
    ]
    print("train+OOS+", len(both))
    for a in sorted(both, key=oosm, reverse=True)[:25]:
        o = a["oos"]
        t = a["train"]
        print(
            f"  {a.get('round')} {a.get('id')}: oos={o['mean']:+.4%} n={o['n']} "
            f"train={t['mean']:+.4%} v={a.get('verdict')} p_adj={a.get('p_adj')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
