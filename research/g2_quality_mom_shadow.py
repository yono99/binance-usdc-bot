#!/usr/bin/env python3
"""G2 quality-momentum paper SHADOW — frozen params, log only, NO sizing / NO wire.

Frozen arm (EDGE_RISET_MULTIFAMILY_V2):
  id:     G2_qmom_h10_q0.3
  score:  rolling mean ret 20 / (rolling std ret 20 + eps)
  book:   long top 30%, short bottom 30% (cross-section)
  hold:   10 calendar days (1d bars)
  cost:   0.18% RT on LS day-book (same as discovery)
  universe: pure majors allowlist (no 1000*)

Modes:
  --backfill   rebuild full history counterfactual from data/snap → jsonl + report
  --once       append latest available signal day (if new) then settle due books
  --report     summarize existing shadow log only

  PYTHONPATH=. python research/g2_quality_mom_shadow.py --backfill
  PYTHONPATH=. python research/g2_quality_mom_shadow.py --once
  PYTHONPATH=. python research/g2_quality_mom_shadow.py --report

Outputs:
  logs/g2_qmom_shadow.jsonl   one JSON object per line (signals + settlements)
  logs/g2_qmom_shadow_report.json
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

from edge_hunt_multifamily_v2 import PURE_MAJORS, close_panel, load_named  # noqa: E402

# ── FROZEN — do not retune without new pre-registration ──────────────────────
ARM = {
    "id": "G2_qmom_h10_q0.3",
    "lookback": 20,
    "hold": 10,
    "top_q": 0.3,
    "cost_rt": 0.0018,
    "universe": "pure_majors_allowlist",
}
LOG_PATH = ROOT / "logs" / "g2_qmom_shadow.jsonl"
REPORT_PATH = ROOT / "logs" / "g2_qmom_shadow_report.json"
STATE_PATH = ROOT / "logs" / "g2_qmom_shadow_state.json"


def load_panel(snap: Path) -> pd.DataFrame:
    dfs = load_named(snap, PURE_MAJORS)
    if len(dfs) < 8:
        raise SystemExit(f"need >=8 pure majors, got {len(dfs)}")
    return close_panel(dfs)


def quality_score(panel: pd.DataFrame, lookback: int) -> pd.DataFrame:
    ret = panel.pct_change()
    mu = ret.rolling(lookback).mean()
    sd = ret.rolling(lookback).std()
    return mu / (sd + 1e-8)


def book_at(score_row: pd.Series, top_q: float) -> dict:
    s = score_row.dropna()
    if len(s) < 8:
        return {"longs": [], "shorts": [], "n": 0}
    k = max(1, int(len(s) * top_q))
    ordered = s.sort_values()
    shorts = list(ordered.iloc[:k].index)
    longs = list(ordered.iloc[-k:].index)
    return {
        "longs": longs,
        "shorts": shorts,
        "n": len(s),
        "k": k,
        "score_long_mean": float(ordered.iloc[-k:].mean()),
        "score_short_mean": float(ordered.iloc[:k].mean()),
    }


def settle_r(
    panel: pd.DataFrame,
    t0: pd.Timestamp,
    longs: list,
    shorts: list,
    hold: int,
    cost: float,
) -> dict | None:
    """Counterfactual LS return from close t0 to close t0+hold."""
    if t0 not in panel.index:
        return None
    i0 = panel.index.get_loc(t0)
    if not isinstance(i0, (int, np.integer)):
        return None
    i0 = int(i0)
    i1 = i0 + hold
    if i1 >= len(panel.index):
        return {"status": "pending", "exit_ts": None, "r_net": None, "r_gross": None}
    t1 = panel.index[i1]
    row0 = panel.loc[t0]
    row1 = panel.loc[t1]
    long_rets = []
    short_rets = []
    for s in longs:
        if s in row0.index and s in row1.index and np.isfinite(row0[s]) and np.isfinite(row1[s]) and row0[s] > 0:
            long_rets.append(float(row1[s] / row0[s] - 1.0))
    for s in shorts:
        if s in row0.index and s in row1.index and np.isfinite(row0[s]) and np.isfinite(row1[s]) and row0[s] > 0:
            short_rets.append(float(-(row1[s] / row0[s] - 1.0)))
    if not long_rets or not short_rets:
        return {"status": "void", "exit_ts": str(t1), "r_net": None, "r_gross": None}
    gross = float(np.mean(long_rets) + np.mean(short_rets))  # LS: already long-short parts
    # discovery used: long_mean - short_mean - cost  == mean(long_rets) - mean(short raw)
    # short_rets already negated, so gross = L + S_neg = L - raw_short = LS
    net = gross - cost
    return {
        "status": "settled",
        "exit_ts": str(t1.date()) if hasattr(t1, "date") else str(t1),
        "r_gross": gross,
        "r_net": net,
        "n_long": len(long_rets),
        "n_short": len(short_rets),
        "mean_long": float(np.mean(long_rets)),
        "mean_short_leg": float(np.mean(short_rets)),
    }


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def build_history(panel: pd.DataFrame) -> list[dict]:
    """Full causal backfill: signal at close t uses score up to t; hold from t."""
    lb = ARM["lookback"]
    hold = ARM["hold"]
    q = ARM["top_q"]
    cost = ARM["cost_rt"]
    score = quality_score(panel, lb)
    rows = []
    # need lookback + hold room
    for i in range(lb + 5, len(panel.index) - hold):
        t = panel.index[i]
        sc = score.loc[t]
        book = book_at(sc, q)
        if book["n"] < 8 or not book["longs"] or not book["shorts"]:
            continue
        st = settle_r(panel, t, book["longs"], book["shorts"], hold, cost)
        if st is None or st.get("status") == "void":
            continue
        rows.append(
            {
                "kind": "signal",
                "arm_id": ARM["id"],
                "signal_ts": str(t.date()) if hasattr(t, "date") else str(t),
                "hold": hold,
                "top_q": q,
                "cost_rt": cost,
                "longs": book["longs"],
                "shorts": book["shorts"],
                "universe_n": book["n"],
                "k": book["k"],
                "score_long_mean": book["score_long_mean"],
                "score_short_mean": book["score_short_mean"],
                "status": st["status"],
                "exit_ts": st.get("exit_ts"),
                "r_gross": st.get("r_gross"),
                "r_net": st.get("r_net"),
                "n_long": st.get("n_long"),
                "n_short": st.get("n_short"),
                "frozen": True,
                "wire": False,
                "source": "backfill_snap",
            }
        )
    return rows


def report(rows: list[dict]) -> dict:
    settled = [r for r in rows if r.get("status") == "settled" and r.get("r_net") is not None]
    pending = [r for r in rows if r.get("status") == "pending"]
    if not settled:
        return {
            "arm": ARM,
            "n_signals": len(rows),
            "n_settled": 0,
            "n_pending": len(pending),
            "note": "no settled trades yet",
        }
    a = np.array([float(r["r_net"]) for r in settled], dtype=float)
    # chronological 70/30 on settled
    k = int(len(a) * 0.70)
    tr, oos = a[:k], a[k:]
    # 50/30/20
    i1 = int(len(a) * 0.50)
    i2 = int(len(a) * 0.80)
    lock = a[i2:]

    def pack(x: np.ndarray) -> dict:
        if len(x) == 0:
            return {"n": 0, "mean": None, "win": None, "sum": None}
        return {
            "n": int(len(x)),
            "mean": float(x.mean()),
            "median": float(np.median(x)),
            "win": float((x > 0).mean()),
            "sum": float(x.sum()),
            "worst": float(x.min()),
            "best": float(x.max()),
        }

    # cost×2 proxy: subtract another cost_rt from each
    oos2 = oos - ARM["cost_rt"] if len(oos) else oos
    out = {
        "arm": ARM,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_signals": len(rows),
        "n_settled": len(settled),
        "n_pending": len(pending),
        "all": pack(a),
        "train_70": pack(tr),
        "oos_30": pack(oos),
        "lockbox_last20pct": pack(lock),
        "oos_cost_extra": pack(oos2),
        "last_signal_ts": settled[-1].get("signal_ts"),
        "last_r_net": settled[-1].get("r_net"),
        "wire": False,
        "status": "PAPER_SHADOW_ONLY",
        "note": (
            "Counterfactual LS book from snap closes. Not live fills. "
            "Do not wire to ForwardTester without human review + paper period."
        ),
    }
    # simple operational flag
    oos_ok = (
        out["oos_30"]["n"] >= 30
        and out["oos_30"]["mean"] is not None
        and out["oos_30"]["mean"] > 0
        and out["lockbox_last20pct"]["mean"] is not None
        and out["lockbox_last20pct"]["mean"] > 0
    )
    out["shadow_health"] = "OK_POSITIVE_OOS_LOCK" if oos_ok else "WATCH"
    return out


def once(panel: pd.DataFrame) -> dict:
    """Append latest signal if new; settle any pending that can close."""
    rows = read_jsonl(LOG_PATH)
    existing = {r.get("signal_ts") for r in rows if r.get("kind") == "signal"}
    lb = ARM["lookback"]
    hold = ARM["hold"]
    score = quality_score(panel, lb)
    # latest index with enough future? for once: open pending if no future yet
    t = panel.index[-1]
    # need score valid
    if len(panel) < lb + 5:
        return {"action": "skip", "reason": "panel too short"}
    # use last fully scored day (not NaN scores)
    sc = score.iloc[-1]
    if sc.notna().sum() < 8:
        t = panel.index[-2]
        sc = score.loc[t]
    ts = str(t.date()) if hasattr(t, "date") else str(t)
    actions = []
    if ts not in existing:
        book = book_at(sc, ARM["top_q"])
        st = settle_r(panel, t, book["longs"], book["shorts"], hold, ARM["cost_rt"])
        row = {
            "kind": "signal",
            "arm_id": ARM["id"],
            "signal_ts": ts,
            "hold": hold,
            "top_q": ARM["top_q"],
            "cost_rt": ARM["cost_rt"],
            "longs": book["longs"],
            "shorts": book["shorts"],
            "universe_n": book["n"],
            "k": book.get("k"),
            "score_long_mean": book.get("score_long_mean"),
            "score_short_mean": book.get("score_short_mean"),
            "status": (st or {}).get("status", "pending"),
            "exit_ts": (st or {}).get("exit_ts"),
            "r_gross": (st or {}).get("r_gross"),
            "r_net": (st or {}).get("r_net"),
            "frozen": True,
            "wire": False,
            "source": "once",
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }
        append_jsonl(LOG_PATH, row)
        rows.append(row)
        actions.append({"appended": ts, "status": row["status"]})
    else:
        actions.append({"skip": ts, "reason": "already logged"})

    # re-settle pendings
    changed = 0
    new_rows = []
    for r in rows:
        if r.get("status") == "pending" and r.get("kind") == "signal":
            t0 = pd.Timestamp(r["signal_ts"], tz="UTC")
            # align tz-naive panel
            if panel.index.tz is None and t0.tzinfo is not None:
                t0 = t0.tz_localize(None)
            elif panel.index.tz is not None and t0.tzinfo is None:
                t0 = t0.tz_localize("UTC")
            # match date
            matches = [ix for ix in panel.index if str(ix.date()) == r["signal_ts"]]
            if not matches:
                new_rows.append(r)
                continue
            t0 = matches[0]
            st = settle_r(panel, t0, r.get("longs") or [], r.get("shorts") or [], hold, ARM["cost_rt"])
            if st and st.get("status") == "settled":
                r = {**r, **{k: st[k] for k in ("status", "exit_ts", "r_gross", "r_net", "n_long", "n_short") if k in st}}
                changed += 1
        new_rows.append(r)
    if changed:
        write_jsonl(LOG_PATH, new_rows)
        rows = new_rows
        actions.append({"settled_updated": changed})
    return {"action": "once", "details": actions, "n_rows": len(rows)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=str(ROOT / "data" / "snap"))
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if not (args.backfill or args.once or args.report):
        args.backfill = True  # default: full shadow rebuild + report

    snap = Path(args.snap)
    panel = load_panel(snap)
    print(
        f"G2 SHADOW frozen {ARM['id']} | panel {panel.shape} "
        f"{panel.index.min().date()}→{panel.index.max().date()} | wire=False"
    )

    if args.backfill:
        rows = build_history(panel)
        write_jsonl(LOG_PATH, rows)
        print(f"backfill wrote {len(rows)} signals → {LOG_PATH}")
        STATE_PATH.write_text(
            json.dumps(
                {
                    "arm": ARM,
                    "backfilled_at": datetime.now(timezone.utc).isoformat(),
                    "n": len(rows),
                    "wire": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    if args.once:
        info = once(panel)
        print("once:", json.dumps(info, default=str))

    rows = read_jsonl(LOG_PATH)
    rep = report(rows)
    REPORT_PATH.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    print("=== SHADOW REPORT ===")
    print(json.dumps({k: rep[k] for k in rep if k != "arm"}, indent=2, default=str))
    print("arm", ARM["id"], "| health", rep.get("shadow_health"), "| wire", False)
    print("report →", REPORT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
