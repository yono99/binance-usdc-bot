"""Candidate-edge stance from owner cycle knowledge — NOT PROMOTE_PAPER.

Path (memory/CANDIDATE_EDGE.md):
  Owner knowledge foundation → dry ⇄ live 1:1 rules → measure risk+realism.
  Live enforce only if allow_live AND risk_ack (owner understands unproven risk).
  Optional stop_loss_r_live: auto-disable live enforce when cum R hits floor.

Default actions are SIZE-DOWN / soft-skip NEW LONG only.
Never auto-short dump/unlock (H-CYC-01/02 OOS failed as entry alpha).
Fail-open: errors must not block trading.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
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
    # Live micro stop: when cum R of CE-touched closes ≤ this → force no-enforce.
    # None / omit = no auto stop. Typical: -5.0 (five R of considered loss).
    "stop_loss_r_live": -5.0,
}

# Persist live CE R path (survives restart). Dry does not write here.
LIVE_STATE_PATH = Path("logs/ce_live_state.json")


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
    # stop_loss_r_live: None disables auto-stop; else float (typically negative)
    if "stop_loss_r_live" in cc and cc["stop_loss_r_live"] is None:
        out["stop_loss_r_live"] = None
    else:
        try:
            out["stop_loss_r_live"] = float(out["stop_loss_r_live"])
        except Exception:
            out["stop_loss_r_live"] = _DEF["stop_loss_r_live"]
    return out


def _is_long(side: int | str) -> bool:
    if side == 1 or side == "long":
        return True
    return False


def load_live_state(path: Path | str | None = None) -> dict:
    p = Path(path or LIVE_STATE_PATH)
    if not p.exists():
        return {"cum_r": 0.0, "n_closes": 0, "stopped": False, "history": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {"cum_r": 0.0, "n_closes": 0, "stopped": False, "history": []}
        d.setdefault("cum_r", 0.0)
        d.setdefault("n_closes", 0)
        d.setdefault("stopped", False)
        d.setdefault("history", [])
        return d
    except Exception as e:
        log.warning(f"ce_live_state read fail: {e}")
        return {"cum_r": 0.0, "n_closes": 0, "stopped": False, "history": []}


def save_live_state(state: dict, path: Path | str | None = None) -> None:
    p = Path(path or LIVE_STATE_PATH)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # keep history short
        hist = list(state.get("history") or [])[-50:]
        out = {
            "cum_r": round(float(state.get("cum_r") or 0.0), 4),
            "n_closes": int(state.get("n_closes") or 0),
            "stopped": bool(state.get("stopped")),
            "history": hist,
            "stop_reason": state.get("stop_reason"),
        }
        p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"ce_live_state write fail: {e}")


def live_enforce_ok(cfg: dict, *, state: dict | None = None) -> tuple[bool, str]:
    """Whether live may APPLY size/soft_block (not just log).

    Requires allow_live + risk_ack, and not past stop_loss_r_live.
    """
    flags = cfg_from(cfg)
    if not flags["allow_live"] or not flags["risk_ack"]:
        return False, "no_ack"
    st = state if state is not None else load_live_state()
    if st.get("stopped"):
        return False, "stop_rule_latched"
    stop = flags.get("stop_loss_r_live")
    if stop is not None:
        try:
            cum = float(st.get("cum_r") or 0.0)
            if cum <= float(stop):
                return False, "stop_rule"
        except Exception:
            pass
    return True, "ok"


def record_live_close_r(
    outcome_r: float,
    cfg: dict,
    *,
    symbol: str = "",
    path: Path | str | None = None,
) -> dict:
    """Update live cum R after a close that was CE-touched. May latch stop."""
    st = load_live_state(path)
    try:
        r = float(outcome_r)
    except Exception:
        return st
    st["cum_r"] = float(st.get("cum_r") or 0.0) + r
    st["n_closes"] = int(st.get("n_closes") or 0) + 1
    hist = list(st.get("history") or [])
    hist.append({"symbol": symbol, "r": round(r, 4), "cum_r": round(st["cum_r"], 4)})
    st["history"] = hist[-50:]
    flags = cfg_from(cfg)
    stop = flags.get("stop_loss_r_live")
    if stop is not None and st["cum_r"] <= float(stop):
        st["stopped"] = True
        st["stop_reason"] = (
            f"cum_r {st['cum_r']:.3f} ≤ stop_loss_r_live {float(stop):.3f}"
        )
        log.warning(f"CE LIVE STOP RULE: {st['stop_reason']} — enforce OFF until reset")
    save_live_state(st, path)
    return st


def reset_live_stop(path: Path | str | None = None, *, keep_history: bool = True) -> dict:
    """Manual clear of stop latch (owner decision after review)."""
    st = load_live_state(path)
    st["stopped"] = False
    st["stop_reason"] = None
    if not keep_history:
        st["cum_r"] = 0.0
        st["n_closes"] = 0
        st["history"] = []
    save_live_state(st, path)
    return st


def evaluate(
    *,
    side: int | str,
    cfg: dict,
    live: bool = False,
    dump_flag: bool = False,
    cycle_context: dict | None = None,
    live_state: dict | None = None,
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

    # Enforce only when mode says so AND (dry OR live-ack + stop ok).
    if mode in ("size", "soft_block"):
        if live:
            ok, why = live_enforce_ok(cfg, state=live_state)
            if not ok:
                v.applied = False
                v.tags["live_blocked_no_ack"] = why in ("no_ack",)
                v.tags["live_blocked_stop"] = why.startswith("stop")
                v.tags["live_block_reason"] = why
            else:
                v.applied = True
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
