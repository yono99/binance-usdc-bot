"""Trade post-mortem — belajar DISIPLIN di bawah pondasi ilmu pemilik.

Hierarki (wajib):
  1. HARD: risk lock, circuit breaker, fail-open
  2. PONDASI: CE-STANCE, no auto-short dump/unlock, dump_short_boost OFF
  3. REVIEW: tulis SQLite trade_reviews (fakta + hipotesis proses)
  4. INJECT: hanya lesson status=injectable & !conflicts_foundation → prompt soft
  5. EDGE: jalur terpisah (CANDIDATE_EDGE) — review TIDAK auto-promote edge

Fail-soft: error review tidak pernah memblokir close/trading.
Spek: memory/TRADE_REVIEW.md
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from . import store
from .logger import log

# Patterns that contradict owner foundation / OOS hygiene (H-CYC, CE-STANCE, survival).
# If lesson text matches → conflicts_foundation=1, status stays hypothesis (not injectable).
_FOUNDATION_CONFLICT_RE = re.compile(
    r"(?i)("
    r"auto[- ]?short.*dump|short\s+(after|on|saat)\s+dump|"
    r"dump_short_boost|"
    r"short\s+unlock|unlock\s+short|"
    r"full\s+size\s+(on\s+)?(dump|markdown|unlock)|"
    r"ignore\s+(ce|cycle|stance)|"
    r"longgarkan\s+(risk|loss|daily)|"
    r"naikkan\s+(leverage|bet)|"
    r"disable\s+(stop|circuit|risk)|"
    r"catch\s+knife|"
    r"all[- ]?in|"
    r"promote[_ ]?paper|"
    r"edge\s+(baru|baru:|found|ditemukan)"
    r")"
)

ERROR_CLASSES = (
    "sl_hit",
    "tp_hit",
    "liq",
    "bad_regime_long",      # long saat dump/markdown (pondasi relevan)
    "size_not_reduced",     # CE would size-down tapi full size (shadow era / bug)
    "counter_trend",
    "low_confidence",
    "execution",
    "noise_or_ok",          # win / netral
    "unknown",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_error(
    *,
    outcome_r: float | None,
    exit_reason: str | None,
    side: str | None,
    dump_flag: bool = False,
    phase: str | None = None,
    unlock: bool = False,
    conviction: float | None = None,
    ce_reasons: list | None = None,
) -> str:
    """Deterministic process class — NOT a claim of market edge."""
    reason = (exit_reason or "").lower()
    if reason == "liq":
        return "liq"
    if reason == "tp" or (isinstance(outcome_r, (int, float)) and outcome_r > 0.15):
        return "tp_hit" if reason == "tp" else "noise_or_ok"
    if reason == "sl" or (isinstance(outcome_r, (int, float)) and outcome_r < 0):
        side_l = (side or "").lower()
        if side_l == "long" and (dump_flag or (phase or "").lower() in ("markdown", "bear")):
            return "bad_regime_long"
        if ce_reasons and isinstance(outcome_r, (int, float)) and outcome_r < 0:
            # CE had reasons but still lost — process note, not edge
            return "sl_hit"
        if conviction is not None and conviction < 0.45:
            return "low_confidence"
        return "sl_hit"
    return "unknown"


def foundation_conflict_check(lesson_text: str, *, side: str | None = None,
                              error_class: str | None = None) -> tuple[bool, str]:
    """Return (conflicts, notes). True = must NOT inject as soft rule."""
    text = lesson_text or ""
    if _FOUNDATION_CONFLICT_RE.search(text):
        return True, "lesson text matches foundation-conflict pattern (H-CYC/CE/risk)"
    # Explicit: never inject "short after dump" style even if re missed
    low = text.lower()
    if "short" in low and "dump" in low and "avoid short" not in low and "jangan short" not in low:
        if "long" not in low.split("then")[0] if "then" in low else True:
            # IF dump THEN short …
            if re.search(r"(?i)then\s+.*short", text):
                return True, "THEN short under dump/unlock-like framing blocked"
    if error_class == "bad_regime_long" and re.search(r"(?i)then\s+(enter\s+)?short", text):
        return True, "bad_regime_long must not flip to auto-short entry"
    return False, "ok"


def build_lesson_text(
    *,
    error_class: str,
    side: str | None,
    outcome_r: float | None,
    exit_reason: str | None,
    dump_flag: bool,
    phase: str | None,
    unlock: bool,
    setup: str | None,
    mae_pct: float | None = None,
    mfe_pct: float | None = None,
) -> str:
    """Deterministic IF…THEN…BECAUSE — process hygiene only.

    Kebijakan: belajar dari kekalahan (setup/konfluensi/SL-placement),
    BUKAN menghukum simbol/pair. Jangan tulis ban/blacklist pair di sini.
    """
    r_s = f"{outcome_r:.2f}R" if isinstance(outcome_r, (int, float)) else "?"
    ctx = []
    if dump_flag:
        ctx.append("dump_flag")
    if phase:
        ctx.append(f"phase={phase}")
    if unlock:
        ctx.append("unlock_window")
    if setup:
        ctx.append(f"setup={setup}")
    ctx_s = ",".join(ctx) if ctx else "normal"
    setup_s = setup or "unknown_setup"

    if error_class == "bad_regime_long":
        return (
            f"IF long AND ({ctx_s}) THEN reduce_size_or_skip_new_long "
            f"BECAUSE last long closed {exit_reason} ({r_s}) under defensive regime "
            f"(process — not a pair ban)"
        )
    if error_class == "low_confidence":
        return (
            f"IF conviction_low AND side={side or '?'} AND setup={setup_s} "
            f"THEN require_stronger_confluence_or_abstain "
            f"BECAUSE last trade {exit_reason} ({r_s}) — raise bar on process, not ban pair"
        )
    if error_class == "liq":
        return (
            f"IF leverage_or_sl_near_liq THEN tighten_risk "
            f"BECAUSE liquidation ({r_s}) — survival first"
        )
    if error_class in ("tp_hit", "noise_or_ok") and isinstance(outcome_r, (int, float)) and outcome_r > 0:
        return (
            f"IF side={side or '?'} AND context={ctx_s} THEN keep_process "
            f"BECAUSE last trade positive ({r_s}) — do not overfit win"
        )
    # SL / plain loss: learn process from path (MFE vs MAE), still no pair ban
    if error_class == "sl_hit" or (
        isinstance(outcome_r, (int, float)) and outcome_r < 0
    ):
        mfe = float(mfe_pct) if mfe_pct is not None else None
        mae = float(mae_pct) if mae_pct is not None else None
        if mfe is not None and mae is not None and mfe > 0 and mfe >= mae * 0.7:
            # sempat searah lalu disapu → SL/placement noise, bukan "pair cursed"
            return (
                f"IF setup={setup_s} AND side={side or '?'} AND context={ctx_s} "
                f"THEN recheck_sl_behind_structure_or_wait_cleaner_location "
                f"BECAUSE SL after MFE={mfe:.2f}% MAE={mae:.2f}% ({r_s}) — "
                f"path had edge of direction then noise; learn placement, do not ban pair"
            )
        if mfe is not None and mfe < 0.3:
            return (
                f"IF setup={setup_s} AND side={side or '?'} AND context={ctx_s} "
                f"THEN demand_stronger_alignment_before_same_setup "
                f"BECAUSE immediate adverse move MFE={mfe:.2f}% ({r_s}) — "
                f"timing/confluence weak; raise bar on setup, do not ban pair"
            )
        return (
            f"IF setup={setup_s} AND side={side or '?'} AND context={ctx_s} "
            f"THEN raise_confluence_bar_on_this_setup "
            f"BECAUSE closed {exit_reason} ({r_s}) — process lesson only, not pair blacklist"
        )
    return (
        f"IF side={side or '?'} AND context={ctx_s} THEN review_process_not_edge "
        f"BECAUSE closed {exit_reason} ({r_s}) — hypothesis only"
    )


def review_status(*, conflicts: bool, outcome_r: float | None, error_class: str) -> str:
    """hypothesis | injectable | retired — never 'promoted_edge'.

    Loss is risk, not guilt: sl_hit process lessons may inject as soft prompt
    (setup hygiene). Never means ban symbol. Wins stay hypothesis (anti-overfit).
    """
    if conflicts:
        return "hypothesis"  # stored for audit, not injected
    # Process-hygiene classes — soft inject into prompt (not hard gate)
    if error_class in ("bad_regime_long", "low_confidence", "liq", "sl_hit"):
        return "injectable"
    if error_class in ("tp_hit", "noise_or_ok"):
        return "hypothesis"  # wins: don't invent edge
    if isinstance(outcome_r, (int, float)) and outcome_r < 0:
        # unclassified loss still injectable as process note
        return "injectable"
    return "hypothesis"


def build_review(
    *,
    mode: str,
    symbol: str,
    side: str | None,
    outcome_r: float | None,
    exit_reason: str | None,
    pos: dict | None = None,
    decision_row: dict | None = None,
    dump_flag: bool | None = None,
    phase: str | None = None,
    unlock_in_window: bool | None = None,
) -> dict:
    """Assemble one review dict ready for store.insert_trade_review."""
    pos = pos or {}
    decision_row = decision_row or {}
    ms = decision_row.get("market_state") or {}

    if dump_flag is None:
        dump_flag = bool(
            pos.get("dump_flag")
            or (pos.get("cycle_candidate_tags") or {}).get("dump_flag")
            or ms.get("dump_flag")
            or (ms.get("btc_lead") or {}).get("dump_flag")
        )
    if phase is None:
        phase = (
            pos.get("phase")
            or (pos.get("cycle_candidate_tags") or {}).get("phase")
            or ms.get("phase")
            or (ms.get("cycle_context") or {}).get("phase")
        )
    if unlock_in_window is None:
        unlock_in_window = bool(
            pos.get("unlock_in_window")
            or (pos.get("cycle_candidate_tags") or {}).get("unlock_in_window")
            or (ms.get("unlock") or {}).get("in_window")
        )

    ce_reasons = pos.get("cycle_candidate_reasons") or decision_row.get("cycle_candidate_reasons") or []
    if isinstance(ce_reasons, str):
        try:
            ce_reasons = json.loads(ce_reasons)
        except Exception:
            ce_reasons = [ce_reasons] if ce_reasons else []

    conviction = pos.get("conviction")
    if conviction is None:
        conviction = decision_row.get("confidence")
    setup = pos.get("setup") or decision_row.get("setup")
    entry_reasoning = decision_row.get("reasoning") or pos.get("rationale") or ""

    error_class = classify_error(
        outcome_r=outcome_r,
        exit_reason=exit_reason,
        side=side or pos.get("side"),
        dump_flag=bool(dump_flag),
        phase=str(phase) if phase else None,
        unlock=bool(unlock_in_window),
        conviction=float(conviction) if conviction is not None else None,
        ce_reasons=list(ce_reasons) if ce_reasons else None,
    )

    lesson = build_lesson_text(
        error_class=error_class,
        side=side or pos.get("side"),
        outcome_r=outcome_r,
        exit_reason=exit_reason,
        dump_flag=bool(dump_flag),
        phase=str(phase) if phase else None,
        unlock=bool(unlock_in_window),
        setup=str(setup) if setup else None,
        mae_pct=pos.get("mae_pct"),
        mfe_pct=pos.get("mfe_pct"),
    )
    conflicts, notes = foundation_conflict_check(
        lesson, side=side or pos.get("side"), error_class=error_class
    )
    status = review_status(conflicts=conflicts, outcome_r=outcome_r, error_class=error_class)

    size_mult = pos.get("size_mult") or pos.get("cycle_candidate_size_mult")

    return {
        "ts": _utcnow(),
        "mode": mode or "dry",
        "symbol": symbol,
        "side": side or pos.get("side"),
        "outcome_r": float(outcome_r) if outcome_r is not None else None,
        "exit_reason": exit_reason,
        "error_class": error_class,
        "dump_flag": bool(dump_flag),
        "phase": str(phase) if phase else None,
        "unlock_in_window": bool(unlock_in_window),
        "conviction": float(conviction) if conviction is not None else None,
        "setup": setup,
        "size_mult": float(size_mult) if size_mult is not None else None,
        "cycle_candidate_reasons": list(ce_reasons) if ce_reasons else [],
        "entry_reasoning": entry_reasoning,
        "lesson_text": lesson,
        "conflicts_foundation": conflicts,
        "foundation_notes": notes,
        "status": status,
        "decision_id": decision_row.get("id"),
        "source": "deterministic",
        "meta": {
            "mae_pct": pos.get("mae_pct"),
            "mfe_pct": pos.get("mfe_pct"),
            "cycle_candidate_applied": pos.get("cycle_candidate_applied"),
        },
    }


def record_close_review(
    *,
    mode: str,
    symbol: str,
    side: str | None,
    outcome_r: float | None,
    exit_reason: str | None,
    pos: dict | None = None,
    decision_row: dict | None = None,
) -> dict | None:
    """Build + insert review. Returns review dict or None. Never raises to caller."""
    try:
        rev = build_review(
            mode=mode,
            symbol=symbol,
            side=side,
            outcome_r=outcome_r,
            exit_reason=exit_reason,
            pos=pos,
            decision_row=decision_row,
        )
        rid = store.insert_trade_review(rev)
        rev["id"] = rid
        if rid:
            log.info(
                f"trade_review #{rid} {symbol} class={rev['error_class']} "
                f"status={rev['status']} conflict={rev['conflicts_foundation']} "
                f"R={rev.get('outcome_r')}"
            )
        return rev
    except Exception as e:  # boundary
        log.warning(f"record_close_review {symbol}: {e}")
        return None


def injectable_lessons(mode: str | None = None, limit: int = 10) -> list[dict]:
    """For ReAct prompt: only injectable + !conflict. Shape like lessons.recent()."""
    rows = store.recent_trade_reviews(mode=mode, limit=max(limit * 3, 20), injectable_only=True)
    out = []
    seen = set()
    for r in rows:
        text = (r.get("lesson_text") or "").strip()
        if not text or text in seen:
            continue
        # double-check foundation at read time
        bad, _ = foundation_conflict_check(text, side=r.get("side"), error_class=r.get("error_class"))
        if bad:
            continue
        seen.add(text)
        out.append({
            "id": f"trev-{r.get('id')}",
            "lesson": text,
            "source": "trade_review",
            "error_class": r.get("error_class"),
        })
        if len(out) >= limit:
            break
    return out


def merge_lessons_for_prompt(
    base_lessons: list | None,
    mode: str | None = None,
    *,
    review_limit: int = 5,
    total_cap: int = 12,
) -> list:
    """Merge classic lessons + injectable trade_reviews (reviews first for hygiene)."""
    base = list(base_lessons or [])
    try:
        revs = injectable_lessons(mode=mode, limit=review_limit)
    except Exception:
        revs = []
    # Prefer process reviews, then classic — dedupe by lesson text
    seen = set()
    out = []
    for item in revs + base:
        if not isinstance(item, dict):
            continue
        t = (item.get("lesson") or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(item)
        if len(out) >= total_cap:
            break
    return out
