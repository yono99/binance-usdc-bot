"""Trade post-mortem under foundation hierarchy."""
from __future__ import annotations

from bot.trade_review import (
    build_lesson_text,
    build_review,
    classify_error,
    foundation_conflict_check,
    injectable_lessons,
    merge_lessons_for_prompt,
    review_status,
)


def test_classify_bad_regime_long():
    assert classify_error(
        outcome_r=-1.0, exit_reason="sl", side="long", dump_flag=True
    ) == "bad_regime_long"
    assert classify_error(
        outcome_r=-0.5, exit_reason="sl", side="long", phase="markdown"
    ) == "bad_regime_long"


def test_classify_tp_and_liq():
    assert classify_error(outcome_r=1.2, exit_reason="tp", side="long") == "tp_hit"
    assert classify_error(outcome_r=-1.0, exit_reason="liq", side="long") == "liq"


def test_foundation_blocks_short_after_dump():
    bad, notes = foundation_conflict_check(
        "IF dump THEN short alt BECAUSE beta", error_class="bad_regime_long"
    )
    assert bad is True
    assert "conflict" in notes.lower() or "short" in notes.lower() or "blocked" in notes.lower()


def test_foundation_allows_reduce_long():
    text = build_lesson_text(
        error_class="bad_regime_long", side="long", outcome_r=-1.0,
        exit_reason="sl", dump_flag=True, phase="markdown", unlock=False, setup=None,
    )
    bad, _ = foundation_conflict_check(text, side="long", error_class="bad_regime_long")
    assert bad is False
    assert "reduce_size" in text or "skip" in text


def test_review_status_injectable():
    assert review_status(conflicts=False, outcome_r=-1.0, error_class="bad_regime_long") == "injectable"
    assert review_status(conflicts=True, outcome_r=-1.0, error_class="bad_regime_long") == "hypothesis"
    assert review_status(conflicts=False, outcome_r=1.0, error_class="tp_hit") == "hypothesis"
    # Loss = learn process (soft inject), not pair ban
    assert review_status(conflicts=False, outcome_r=-1.0, error_class="sl_hit") == "injectable"


def test_sl_lesson_is_process_not_pair_ban():
    text = build_lesson_text(
        error_class="sl_hit", side="long", outcome_r=-1.02,
        exit_reason="sl", dump_flag=False, phase="uptrend", unlock=False,
        setup="scalp_range", mae_pct=5.8, mfe_pct=3.4,
    )
    assert "scalp_range" in text
    assert "do not ban pair" in text or "not pair" in text.lower()
    assert "blacklist" not in text.lower() or "not" in text.lower()
    bad, _ = foundation_conflict_check(text, side="long", error_class="sl_hit")
    assert bad is False


def test_build_review_and_store(tmp_path, monkeypatch):
    # Use real store if DB ok; just ensure build works
    rev = build_review(
        mode="dry",
        symbol="ETH/USDC:USDC",
        side="long",
        outcome_r=-0.8,
        exit_reason="sl",
        pos={
            "side": "long",
            "conviction": 0.55,
            "cycle_candidate_reasons": ["dump_flag"],
            "cycle_candidate_tags": {"dump_flag": True, "phase": "markdown"},
            "dump_flag": True,
        },
        decision_row={"id": "abc", "reasoning": "test entry", "confidence": 0.55},
    )
    assert rev["error_class"] == "bad_regime_long"
    assert rev["status"] == "injectable"
    assert rev["conflicts_foundation"] is False
    assert rev["lesson_text"]


def test_merge_lessons_prefers_reviews():
    base = [{"id": "1", "lesson": "IF x THEN y BECAUSE z"}]
    # without DB rows, merge still returns base
    out = merge_lessons_for_prompt(base, mode="dry", review_limit=2, total_cap=5)
    assert any(x.get("lesson") == "IF x THEN y BECAUSE z" for x in out)


def test_conflict_pattern_full_size_dump():
    bad, _ = foundation_conflict_check("IF dump THEN full size on dump BECAUSE FOMO")
    assert bad is True
