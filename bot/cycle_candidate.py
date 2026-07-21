"""Candidate-edge stance from owner cycle knowledge — NOT PROMOTE_PAPER.

Path (memory/CANDIDATE_EDGE.md):
  dry shadow → dry size → optional dry soft_block → live micro only if
  allow_live AND risk_ack (owner understands unproven risk).

Default actions are SIZE-DOWN / soft-skip NEW LONG only.
Never auto-short dump/unlock (H-CYC-01/02 OOS failed as entry alpha).
Fail-open: errors must not block trading.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .logger import log

_DEF = {
    "mode": "off",  # off|shadow|size|soft_block
    "allow_live": False,
    "risk_ack": False,
    "long_size_on_dump": 0.5,
    "long_size_on_markdown": 0.7,
    "long_size_on_unlock": 0.5,
    "soft_block_long_on_dump": True,
}


@dataclass
class CandidateVerdict:
    """Result for one prospective entry."""
    size_mult: float = 1.0
    skip: bool = False
    reasons: list[str] = field(default_factory=list)
    tags: dict[str, Any] = field(default_factory=dict)
    mode: str = "off"
    applied: bool = False  # True if size/skip actually enforced (not shadow-only)

    def as_dict(self) -> dict:
        return {
            "cycle_candidate_mode": self.mode,
            "cycle_candidate_size_mult": round(self.size_mult, 4),
            "cycle_candidate_skip": self.skip,
            "cycle_candidate_reasons": list(self.reasons),
            "cycle_candidate_applied": self.applied,
            "cycle_candidate_tags": dict(self.tags),
        }


def cfg_from(raw: dict | None) -> dict:
    ag = (raw or {}).get("agent") or {}
    cc = ag.get("cycle_candidate")
    if not isinstance(cc, dict):
        cc = {}
    out = {**_DEF, **{k: cc[k] for k in _DEF if k in cc}}
    out["mode"] = str(out.get("mode") or "off").lower().strip()
    if out["mode"] not in ("off", "shadow", "size", "soft_block"):
        out["mode"] = "off"
    out["allow_live"] = bool(out.get("allow_live", False))
    out["risk_ack"] = bool(out.get("risk_ack", False))
    for k in ("long_size_on_dump", "long_size_on_markdown", "long_size_on_unlock"):
        try:
            out[k] = float(out[k])
        except Exception:
            out[k] = _DEF[k]
        out[k] = min(1.0, max(0.05, out[k]))
    out["soft_block_long_on_dump"] = bool(out.get("soft_block_long_on_dump", True))
    return out


def _is_long(side: int | str) -> bool:
    if side == 1 or side == "long":
        return True
    return False


def evaluate(
    *,
    side: int | str,
    cfg: dict,
    live: bool = False,
    dump_flag: bool = False,
    cycle_context: dict | None = None,
) -> CandidateVerdict:
    """Compute stance mult / skip for a candidate entry.

    Shorts: never sized up here; no auto-short. Pass-through size_mult=1.
    Longs: may downsize or soft-skip under dump/markdown/unlock.
    """
    flags = cfg_from(cfg)
    mode = flags["mode"]
    v = CandidateVerdict(mode=mode)
    ctx = cycle_context or {}

    phase = str((ctx.get("phase") or ctx.get("measured_phase") or "")).lower()
    if not phase and isinstance(ctx.get("cycle"), dict):
        phase = str(ctx["cycle"].get("phase") or "").lower()
    # build_cycle_context shape
    if not phase:
        phase = str((ctx.get("price_phase") or "")).lower()
    cal = str((ctx.get("calendar_phase") or ctx.get("cal_phase") or "")).lower()
    unlock = ctx.get("unlock") if isinstance(ctx.get("unlock"), dict) else {}
    in_unlock = bool(unlock.get("in_window") or unlock.get("active"))

    v.tags = {
        "dump_flag": bool(dump_flag),
        "phase": phase or None,
        "calendar_phase": cal or None,
        "unlock_in_window": in_unlock,
        "live": bool(live),
    }

    if mode == "off":
        return v

    if not _is_long(side):
        # Candidate path is long-risk stance only (no short entry alpha).
        return v

    mult = 1.0
    reasons: list[str] = []

    if dump_flag:
        mult *= flags["long_size_on_dump"]
        reasons.append("dump_flag")
    if phase in ("markdown", "bear") or cal in ("bear",):
        mult *= flags["long_size_on_markdown"]
        reasons.append("phase_defensive")
    if in_unlock:
        mult *= flags["long_size_on_unlock"]
        reasons.append("unlock_window")

    mult = min(1.0, max(0.05, mult))
    skip = False
    if mode == "soft_block" and flags["soft_block_long_on_dump"] and dump_flag:
        skip = True
        reasons.append("soft_block_long_on_dump")

    v.size_mult = mult if reasons else 1.0
    v.skip = skip
    v.reasons = reasons

    # Enforce only when mode says so AND (dry OR live-ack).
    if mode in ("size", "soft_block"):
        if live and not (flags["allow_live"] and flags["risk_ack"]):
            # Live without ack → force shadow behavior (log only, no apply).
            v.applied = False
            v.tags["live_blocked_no_ack"] = True
        else:
            v.applied = True
    else:
        # shadow
        v.applied = False

    return v


def apply_size(base_size_mult: float, verdict: CandidateVerdict) -> float:
    """Combine conf size_mult with candidate stance. Only if applied."""
    try:
        base = float(base_size_mult)
    except Exception:
        base = 1.0
    if not verdict.applied or verdict.skip:
        return base
    if verdict.size_mult >= 0.999:
        return base
    return max(0.05, base * float(verdict.size_mult))


def stamp(verdict: CandidateVerdict | None) -> dict:
    if verdict is None:
        return {}
    return verdict.as_dict()


def should_log(verdict: CandidateVerdict) -> bool:
    """Log when any candidate reason fires (shadow or applied)."""
    return bool(verdict.reasons) and verdict.mode != "off"
