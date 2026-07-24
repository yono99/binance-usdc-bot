#!/usr/bin/env python3
"""Master loop: download screened universe → multi-round hunt → compact status.

Implements spirit of riset_edge.txt with project bar (PROMOTE_PAPER):
  1) Download 1d OHLCV for COIN perps passing volume screen
  2) Run discovery rounds that are still novel / not fully done on all-time
  3) Write logs + update memory/EDGE_HUNT_STATE.json summary snippet

  PYTHONPATH=. python research/edge_hunt_riset_loop.py --download
  PYTHONPATH=. python research/edge_hunt_riset_loop.py --hunt-only
  PYTHONPATH=. python research/edge_hunt_riset_loop.py --download --force-download

Does NOT wire runtime. Surrender = document 0 PROMOTE after queued rounds.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PY = sys.executable
STATE = ROOT / "memory" / "EDGE_HUNT_STATE.json"
STATUS = ROOT / "research" / "EDGE_RISET_STATUS.md"


def run(cmd: list[str], log_path: Path | None = None) -> int:
    print(">>", " ".join(cmd), flush=True)
    env = {**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": str(ROOT)}
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as f:
            p = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=f, stderr=subprocess.STDOUT)
            return p.returncode
    p = subprocess.run(cmd, cwd=str(ROOT), env=env)
    return p.returncode


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def save_state(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    d["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    STATE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def summarize_json(path: Path) -> dict:
    if not path.exists():
        return {"error": "missing", "path": str(path)}
    d = json.loads(path.read_text(encoding="utf-8"))
    cands = d.get("candidates") or [a for a in (d.get("arms") or []) if a.get("verdict") == "CANDIDATE"]
    verdicts = d.get("verdicts")
    if not verdicts and d.get("arms"):
        from collections import Counter

        verdicts = dict(Counter(a.get("verdict") for a in d["arms"]))
    return {
        "path": str(path),
        "meta": d.get("meta"),
        "verdicts": verdicts,
        "n_candidates": len(cands) if isinstance(cands, list) else 0,
        "candidate_ids": [c.get("id") for c in (cands or [])][:20]
        if isinstance(cands, list)
        else [],
        "promoted": d.get("promoted") or d.get("promoted_filters"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true", help="run volume-screened all-time download")
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--hunt-only", action="store_true")
    ap.add_argument("--skip-r12", action="store_true")
    ap.add_argument("--min-qv", type=float, default=5_000_000)
    ap.add_argument("--snap", default=str(ROOT / "data" / "snap"))
    args = ap.parse_args()

    state = load_state()
    state.setdefault("campaign", "riset_edge_loop")
    state["owner_authority"] = "full_edge_search_data_first"
    state.setdefault("promote_paper", 0)
    results = []

    if args.download and not args.hunt_only:
        dl_cmd = [
            PY,
            str(ROOT / "research" / "download_snap_alltime.py"),
            "--tf",
            "1d",
            "--bars",
            "3500",
            "--settle",
            "USDT",
            "--screen-volume",
            "--min-qv",
            str(args.min_qv),
            "--snapshot-dir",
            args.snap,
        ]
        if args.force_download:
            dl_cmd.append("--force")
        code = run(dl_cmd, ROOT / "logs" / "_download_riset_edge.log")
        results.append({"step": "download", "exit": code})
        # coverage
        run(
            [PY, str(ROOT / "research" / "_snap_coverage.py"), "--snap", args.snap],
            ROOT / "logs" / "_snap_coverage_riset.txt",
        )

    # R12 discovery
    if not args.skip_r12:
        out12 = ROOT / "logs" / "edge_hunt_round12.json"
        code = run(
            [
                PY,
                str(ROOT / "research" / "edge_hunt_round12.py"),
                "--snap",
                args.snap,
                "--out",
                str(out12),
            ],
            ROOT / "logs" / "_edge_hunt_round12.out",
        )
        summ = summarize_json(out12)
        results.append({"step": "R12", "exit": code, "summary": summ})
        completed = state.setdefault("completed_rounds", [])
        completed.append(
            {
                "id": "R12_volume_regime_riset_cats",
                "script": "research/edge_hunt_round12.py",
                "out": str(out12),
                "verdicts": summ.get("verdicts"),
                "n_candidates": summ.get("n_candidates"),
                "promote_paper": 0 if not summ.get("n_candidates") else "CHECK_STRICT",
            }
        )

    # Optional: re-run A-F if download refreshed (sanity, not retread claim)
    # skipped by default — already done all-time

    state["loop_results"] = results
    # queue update
    n_cand = 0
    for r in results:
        s = r.get("summary") or {}
        n_cand += int(s.get("n_candidates") or 0)

    if n_cand == 0:
        state["queue"] = [
            {
                "id": "R14_1h_liquid_top20",
                "status": "next",
                "novelty": "subday liquid only; cost harsh",
            },
            {
                "id": "funding_carry_hist",
                "status": "queued",
                "novelty": "non-OHLCV from riset_edge.txt",
            },
            {
                "id": "SURRENDER_OHLCV_ENTRY",
                "status": "armed",
                "note": "If R14+funding also 0 CANDIDATE → honest stop entry hunt on public OHLCV",
            },
        ]
        state["surrender_ohlcv_entry"] = False  # not yet until R14+funding attempted or explicit
    else:
        state["queue"] = [
            {"id": "STRICT_VALIDATE_CANDIDATES", "status": "next", "ids": "see R12 candidates"}
        ]

    save_state(state)
    print(json.dumps({"n_candidates_total": n_cand, "results": results}, indent=2, default=str))
    print("state →", STATE)
    return 0 if n_cand >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
