#!/usr/bin/env python3
"""G2 Path A — paper BOOK runner (full strategy engine, separate from rules bot).

Frozen arm G2_qmom_h10_q0.3:
  rebalance every 10 daily bars · long top 30% · short bottom 30% · cost 0.18% RT

This process does NOT trade on Binance and does NOT touch forwardtest positions.
It maintains a paper equity curve in R-units from snap closes.

  PYTHONPATH=. python research/g2_book_runner.py --backfill   # rebuild history
  PYTHONPATH=. python research/g2_book_runner.py --once       # settle + maybe open (cron daily)
  PYTHONPATH=. python research/g2_book_runner.py --report

State:  logs/g2_book_state.json
Ledger: logs/g2_book_ledger.jsonl
Report: logs/g2_book_live_report.json

Optional PM2 (daily): see ecosystem.config.cjs app `g2-book` (commented).
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

from g2_book_paper import pack, run_book  # noqa: E402
from g2_quality_mom_shadow import (  # noqa: E402
    ARM,
    book_at,
    load_panel,
    quality_score,
    settle_r,
)

STATE = ROOT / "logs" / "g2_book_state.json"
LEDGER = ROOT / "logs" / "g2_book_ledger.jsonl"
REPORT = ROOT / "logs" / "g2_book_live_report.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    if not STATE.exists():
        return {
            "arm": dict(ARM),
            "open": None,
            "equity_sum_r": 0.0,
            "n_closed": 0,
            "last_once": None,
            "wire": False,
        }
    return json.loads(STATE.read_text(encoding="utf-8"))


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    st["updated_at"] = _now()
    STATE.write_text(json.dumps(st, indent=2, default=str), encoding="utf-8")


def append_ledger(row: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


def read_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def backfill(panel: pd.DataFrame) -> dict:
    """Rebuild non-overlapping book history into ledger + state."""
    rows = run_book(panel, ARM["hold"], ARM["top_q"], ARM["cost_rt"])
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("w", encoding="utf-8") as f:
        eq = 0.0
        for r in rows:
            eq += float(r["r_net"] or 0)
            rec = {
                **r,
                "kind": "close",
                "equity_sum_r": round(eq, 6),
                "source": "backfill",
            }
            f.write(json.dumps(rec, default=str) + "\n")
    st = {
        "arm": dict(ARM),
        "open": None,
        "equity_sum_r": round(sum(float(r["r_net"]) for r in rows), 6),
        "n_closed": len(rows),
        "last_signal_ts": rows[-1]["signal_ts"] if rows else None,
        "last_exit_ts": rows[-1]["exit_ts"] if rows else None,
        "last_once": _now(),
        "wire": False,
        "mode": "paper_book",
        "backfilled_at": _now(),
    }
    # if last period ended, ready for new open on --once
    save_state(st)
    return {"n": len(rows), "equity_sum_r": st["equity_sum_r"]}


def _panel_day(panel: pd.DataFrame, day_str: str) -> pd.Timestamp | None:
    for ix in panel.index:
        d = str(ix.date()) if hasattr(ix, "date") else str(ix)[:10]
        if d == day_str[:10]:
            return ix
    return None


def once(panel: pd.DataFrame) -> dict:
    """Settle open book if due; open new book if flat and data ready."""
    st = load_state()
    hold = int(ARM["hold"])
    cost = float(ARM["cost_rt"])
    actions = []
    last_ix = panel.index[-1]
    last_day = str(last_ix.date()) if hasattr(last_ix, "date") else str(last_ix)[:10]

    # 1) settle
    op = st.get("open")
    if op:
        t0 = _panel_day(panel, op["signal_ts"])
        if t0 is None:
            actions.append({"settle": "skip", "reason": "signal day not in panel"})
        else:
            stt = settle_r(panel, t0, op["longs"], op["shorts"], hold, cost)
            if stt and stt.get("status") == "settled":
                r_net = float(stt["r_net"])
                st["equity_sum_r"] = round(float(st.get("equity_sum_r") or 0) + r_net, 6)
                st["n_closed"] = int(st.get("n_closed") or 0) + 1
                rec = {
                    "kind": "close",
                    "signal_ts": op["signal_ts"],
                    "exit_ts": stt.get("exit_ts"),
                    "r_net": r_net,
                    "r_gross": stt.get("r_gross"),
                    "longs": op["longs"],
                    "shorts": op["shorts"],
                    "equity_sum_r": st["equity_sum_r"],
                    "source": "once",
                    "logged_at": _now(),
                }
                append_ledger(rec)
                st["open"] = None
                st["last_exit_ts"] = stt.get("exit_ts")
                actions.append({"settled": op["signal_ts"], "r_net": r_net})
            else:
                actions.append({"settle": "pending", "signal_ts": op["signal_ts"]})

    # 2) open new if flat
    if st.get("open") is None:
        # need enough history
        lb = int(ARM["lookback"])
        if len(panel) < lb + hold + 5:
            actions.append({"open": "skip", "reason": "panel short"})
        else:
            # signal on last bar that still allows hold in future OR mark pending
            # For live paper: open on latest full day; settle later when hold elapses
            t = panel.index[-1]
            # avoid re-open same day as last closed signal
            last_sig = st.get("last_signal_ts")
            t_day = str(t.date()) if hasattr(t, "date") else str(t)[:10]
            can_open = True
            if last_sig:
                # next open only after previous period would have ended
                try:
                    prev = pd.Timestamp(last_sig)
                    # find index
                    t_prev = _panel_day(panel, last_sig)
                    if t_prev is not None:
                        i_prev = panel.index.get_loc(t_prev)
                        if isinstance(i_prev, (int, np.integer)):
                            # need at least hold bars after previous signal
                            if int(i_prev) + hold > panel.index.get_loc(t):
                                can_open = False
                                actions.append({"open": "skip", "reason": "within_hold_of_last"})
                except Exception:
                    pass
            # also skip if already have ledger entry for this day
            if can_open:
                for r in read_ledger()[-5:]:
                    if r.get("signal_ts") == t_day:
                        can_open = False
                        actions.append({"open": "skip", "reason": "already_have_signal_day"})
                        break
            if can_open:
                score = quality_score(panel, lb)
                sc = score.loc[t]
                book = book_at(sc, ARM["top_q"])
                if book["n"] >= 8 and book["longs"] and book["shorts"]:
                    st["open"] = {
                        "signal_ts": t_day,
                        "longs": book["longs"],
                        "shorts": book["shorts"],
                        "k": book.get("k"),
                        "universe_n": book.get("n"),
                        "opened_at": _now(),
                        "hold": hold,
                        "cost_rt": cost,
                        "arm_id": ARM["id"],
                    }
                    st["last_signal_ts"] = t_day
                    append_ledger(
                        {
                            "kind": "open",
                            "signal_ts": t_day,
                            "longs": book["longs"],
                            "shorts": book["shorts"],
                            "k": book.get("k"),
                            "source": "once",
                            "logged_at": _now(),
                        }
                    )
                    actions.append({"opened": t_day, "k": book.get("k")})
                else:
                    actions.append({"open": "skip", "reason": "book empty"})

    st["last_once"] = _now()
    st["panel_last"] = last_day
    save_state(st)
    return {"actions": actions, "equity_sum_r": st.get("equity_sum_r"), "open": st.get("open")}


def report() -> dict:
    ledger = read_ledger()
    closes = [r for r in ledger if r.get("kind") == "close" and r.get("r_net") is not None]
    r_net = [float(r["r_net"]) for r in closes]
    st = load_state()

    def split70(a):
        k = int(len(a) * 0.70)
        return a[:k], a[k:]

    tr, oos = split70(r_net)
    out = {
        "meta": {
            "arm": dict(ARM),
            "wire": False,
            "mode": "paper_book_runner",
            "generated_at": _now(),
        },
        "state": {
            "open": st.get("open"),
            "equity_sum_r": st.get("equity_sum_r"),
            "n_closed": st.get("n_closed"),
            "last_signal_ts": st.get("last_signal_ts"),
            "panel_last": st.get("panel_last"),
        },
        "all": pack(r_net),
        "train_70": pack(tr),
        "oos_30": pack(oos),
        "last_closes": closes[-5:],
        "path_a_reference": "See logs/g2_book_paper.json for full non-overlap audit",
    }
    # maxDD on equity
    if r_net:
        eq = np.cumsum(r_net)
        peak = np.maximum.accumulate(eq)
        out["max_drawdown_sumR"] = float((eq - peak).min())
    REPORT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=str(ROOT / "data" / "snap"))
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    if not (args.backfill or args.once or args.report):
        args.once = True

    panel = load_panel(Path(args.snap))
    print(
        f"G2 BOOK RUNNER {ARM['id']} | panel {panel.shape} "
        f"{panel.index.min().date()}→{panel.index.max().date()} | wire=False"
    )

    if args.backfill:
        info = backfill(panel)
        print("backfill", info)

    if args.once:
        info = once(panel)
        print("once", json.dumps(info, default=str)[:500])

    rep = report()
    print("=== BOOK REPORT ===")
    print(json.dumps({k: rep[k] for k in ("state", "all", "train_70", "oos_30", "max_drawdown_sumR") if k in rep}, indent=2))
    print("report →", REPORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
