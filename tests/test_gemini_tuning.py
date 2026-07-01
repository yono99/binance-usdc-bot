"""Penyetelan Gemini di UI: field RuntimeSettings + clamp."""
from bot.settings_store import RuntimeSettings, _from_dict


def test_defaults():
    s = RuntimeSettings()
    assert s.gemini_decide_seconds == 180 and s.gemini_manage_seconds == 60
    assert s.gemini_portfolio_seconds == 300 and s.gemini_plan_hours == 6
    assert s.gemini_tool_iters == 4


def test_clamp_bounds():
    s = _from_dict({"gemini_decide_seconds": 5, "gemini_manage_seconds": 99999,
                    "gemini_portfolio_seconds": 10, "gemini_plan_hours": 99,
                    "gemini_tool_iters": 50})
    assert s.gemini_decide_seconds == 30        # min 30
    assert s.gemini_manage_seconds == 3600      # max 3600
    assert s.gemini_portfolio_seconds == 60     # min 60
    assert s.gemini_plan_hours == 24            # max 24
    assert s.gemini_tool_iters == 8             # max 8
