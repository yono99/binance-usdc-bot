"""Phase 6 — endpoint panel Agent (decisions/lessons/agent-health/evolution).
Verifikasi wiring & bentuk JSON; tak boleh meledak walau log belum ada."""
import json

from bot import dashboard


def _body(resp):
    return json.loads(resp.body)


def test_api_decisions_shape():
    b = _body(dashboard.api_decisions(page=1, page_size=5))
    assert "decisions" in b and isinstance(b["decisions"], list)


def test_api_lessons_shape():
    b = _body(dashboard.api_lessons(page=1, page_size=5))
    assert "lessons" in b and "page" in b and "page_size" in b and "total" in b and isinstance(b["lessons"], list)


def test_api_agent_health_shape():
    b = _body(dashboard.api_agent_health())
    for k in ("total", "llm", "fallbacks", "fallback_rate", "llm_available_rate", "by_source"):
        assert k in b
    assert isinstance(b["by_source"], dict)


def test_api_evolution_shape():
    b = _body(dashboard.api_evolution(page=1, page_size=10))
    assert "events" in b and isinstance(b["events"], list)


def test_api_ab_shape():
    b = _body(dashboard.api_ab())
    assert "verdict" in b and "n_total" in b


def test_agent_page_served():
    html = dashboard.agent_page()
    assert "Agent Monitor" in html and "/api/decisions" in html and "/api/ab" in html
