"""Candidate-edge fitness report — dry and/or live.

Measures CE-STANCE from decision_log + trades journal. Does NOT mutate config.
Verdicts are suggestions only (KEEP_SHADOW / DUAL_OK / RETIRE / …).

  python ce_report.py
  python ce_report.py --mode dry
  python ce_report.py --mode live
  python ce_report.py --both
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from . import decision_log


def _risk_stats(rs: list[float]) -> dict:
    if not rs:
        return {"max_drawdown_r": None, "std_r": None, "worst_r": None,
                "mean_r": None, "sum_r": None, "n": 0}
    arr = np.asarray(rs, dtype=float)
    cum = np.cumsum(arr)
    peak = np.maximum.accumulate(cum)
    dd = float(np.max(peak - cum)) if len(cum) else 0.0
    return {
        "max_drawdown_r": round(dd, 3),
        "std_r": round(float(arr.std()), 3),
        "worst_r": round(float(arr.min()), 3),
        "mean_r": round(float(arr.mean()), 4),
        "sum_r": round(float(arr.sum()), 3),
        "n": int(arr.size),
    }


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _decision_path(mode: str | None) -> Path:
    if mode:
        return Path(f"logs/decision_log_{mode}.jsonl")
    return Path("logs/decision_log.jsonl")


def _trades_path(mode: str | None) -> Path:
    if mode:
        return Path(f"logs/trades_{mode}.jsonl")
    return Path("logs/trades.jsonl")


def collect_shadow_events(mode: str | None = None) -> list[dict]:
    path = _decision_path(mode)
    rows = []
    for r in _read_jsonl(path):
        if r.get("action") == "CANDIDATE_EDGE_SHADOW":
            rows.append(r)
    return rows


def collect_closes(mode: str | None = None) -> list[dict]:
    """forward_close events with R if present."""
    rows = []
    for r in _read_jsonl(_trades_path(mode)):
        if r.get("event") not in ("forward_close", "close"):
            continue
        rr = r.get("r")
        if rr is None:
            rr = r.get("pnl_r") or r.get("outcome_r")
        if rr is None and r.get("pnl_usd") is not None and r.get("bet"):
            try:
                rr = float(r["pnl_usd"]) / float(r["bet"])
            except Exception:
                rr = None
        if rr is None:
            continue
        try:
            r = {**r, "r": float(rr)}
        except Exception:
            continue
        rows.append(r)
    return rows


def collect_enter_with_ce(mode: str | None = None) -> list[dict]:
    """ENTER rows that settled with outcome_r and any CE stamp (if present)."""
    path = _decision_path(mode)
    out = []
    for r in decision_log.read_all(path):
        if not str(r.get("action", "")).startswith("ENTER"):
            continue
        if r.get("outcome_r") is None:
            continue
        out.append(r)
    return out


def analyze_mode(mode: str | None = None, *, min_n: int = 20) -> dict[str, Any]:
    """Build fitness snapshot for one arena (dry/live/default)."""
    shadows = collect_shadow_events(mode)
    closes = collect_closes(mode)
    enters = collect_enter_with_ce(mode)

    n_shadow = len(shadows)
    n_downsize = sum(
        1 for s in shadows
        if (s.get("size_would") is not None
            and s.get("size_mult_before") is not None
            and float(s.get("size_would") or 1) < float(s.get("size_mult_before") or 1) * 0.999)
        or (s.get("cycle_candidate_size_mult") is not None
            and float(s.get("cycle_candidate_size_mult") or 1) < 0.999)
    )
    n_skip_would = sum(1 for s in shadows if s.get("skip_would") or s.get("cycle_candidate_skip"))

    # Reason histogram
    reasons: dict[str, int] = {}
    for s in shadows:
        for x in (s.get("cycle_candidate_reasons") or s.get("reasons") or []):
            reasons[str(x)] = reasons.get(str(x), 0) + 1

    all_r = [float(c["r"]) for c in closes]
    risk_all = _risk_stats(all_r)

    # ENTER with CE reasons stamped (if wire stamped open into decision — rare)
    ce_enter = [e for e in enters if e.get("cycle_candidate_reasons")]
    full_enter = [e for e in enters if not e.get("cycle_candidate_reasons")]
    risk_ce = _risk_stats([float(e["outcome_r"]) for e in ce_enter])
    risk_full = _risk_stats([float(e["outcome_r"]) for e in full_enter]) if full_enter else risk_all

    # Live state (only meaningful for live)
    live_state = {}
    try:
        from .cycle_candidate import load_live_state
        live_state = load_live_state()
    except Exception:
        pass

    # Verdict suggestion (non-mutating)
    if n_shadow < 10 and risk_all["n"] < min_n:
        verdict = "KEEP_SHADOW"
        reason = f"data kurang (shadow={n_shadow}, closes={risk_all['n']}; butuh n≥{min_n} atau shadow≥10)"
    elif risk_all["n"] >= min_n and risk_all.get("mean_r") is not None:
        # Stance is about risk; if we only have baseline, stay conservative
        if n_shadow >= 30:
            verdict = "PROMOTE_DRY_SIZE" if (mode in (None, "dry")) else "PROMOTE_LIVE_MICRO"
            reason = "shadow n cukup — lanjut size 1:1 dengan risk lock; bandingkan window berikutnya"
        else:
            verdict = "KEEP_SHADOW"
            reason = "kumpulkan shadow/closes lebih banyak"
    else:
        verdict = "KEEP_SHADOW"
        reason = "lanjut kumpulkan data"

    # RETIRE signal if we have CE-stamped enters that are clearly worse risk... hard; leave manual
    if risk_ce["n"] >= min_n and risk_full["n"] >= min_n:
        # if "kept full size" bucket worse than we hoped — informational only
        if (risk_ce.get("worst_r") is not None and risk_full.get("worst_r") is not None
                and risk_ce["worst_r"] > risk_full["worst_r"]
                and risk_ce.get("max_drawdown_r") is not None
                and risk_full.get("max_drawdown_r") is not None
                and risk_ce["max_drawdown_r"] < risk_full["max_drawdown_r"]):
            # CE-touched closes have better worst/dd than non-CE — stance may help
            if verdict.startswith("KEEP"):
                verdict = "DUAL_OK" if mode == "live" else "PROMOTE_DRY_SIZE"
                reason = "CE-touched closes look better on risk metrics (n cukup) — saran, bukan auto"

    return {
        "mode": mode or "default",
        "n_shadow_events": n_shadow,
        "n_shadow_downsize": n_downsize,
        "n_shadow_skip_would": n_skip_would,
        "reasons": reasons,
        "n_closes": risk_all["n"],
        "risk_all_closes": risk_all,
        "n_enter_settled": len(enters),
        "n_enter_ce_stamped": len(ce_enter),
        "risk_ce_enter": risk_ce,
        "risk_non_ce_enter": risk_full,
        "live_state": live_state if mode == "live" else None,
        "verdict": verdict,
        "reason": reason,
        "note": (
            "CE-STANCE = risk/stance candidate, NOT entry PROMOTE_PAPER. "
            "This report never changes config."
        ),
    }


def analyze_both(*, min_n: int = 20) -> dict:
    dry = analyze_mode("dry", min_n=min_n)
    live = analyze_mode("live", min_n=min_n)
    # Combined dual verdict
    dv, lv = dry["verdict"], live["verdict"]
    if dry["n_shadow_events"] < 10 and live["n_shadow_events"] < 5:
        dual = "KEEP_SHADOW"
        dual_reason = "kedua arena masih miskin data"
    elif "RETIRE" in (dv, lv):
        dual = "RETIRE"
        dual_reason = "satu arena menyarankan RETIRE — review manual"
    elif dv.startswith("PROMOTE") and lv.startswith("PROMOTE"):
        dual = "DUAL_OK"
        dual_reason = "dry & live keduanya arah lanjut — scale hati-hati, bukan all-in"
    elif dry["risk_all_closes"]["n"] >= min_n and live["risk_all_closes"]["n"] >= 10:
        # gap suspect: dry mean much better than live
        dm = dry["risk_all_closes"].get("mean_r")
        lm = live["risk_all_closes"].get("mean_r")
        if dm is not None and lm is not None and dm > 0 and lm < dm - 0.15:
            dual = "GAP_SUSPECT"
            dual_reason = "dry jauh lebih baik dari live — curiga gap fill paper, jangan scale"
        else:
            dual = "KEEP_SHADOW"
            dual_reason = "lanjut kumpulkan; bandingkan risk path"
    else:
        dual = "KEEP_SHADOW"
        dual_reason = "lanjut dual-track shadow/size mikro"

    return {
        "dry": dry,
        "live": live,
        "dual_verdict": dual,
        "dual_reason": dual_reason,
        "foundation": "owner cycle knowledge → CE-STANCE (size-down long)",
        "not": "PROMOTE_PAPER entry alpha",
    }


def report(mode: str | None = "both", *, min_n: int = 20) -> dict:
    if mode in (None, "both", "all"):
        return analyze_both(min_n=min_n)
    return analyze_mode(mode, min_n=min_n)
