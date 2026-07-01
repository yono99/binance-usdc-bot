"""Toggle agent dari UI: field RuntimeSettings + endpoint non-destruktif + hot-reload."""
import json

from bot import dashboard
from bot.settings_store import RuntimeSettings, _from_dict


# ---------- field & persistensi ----------

def test_agent_flags_default_false():
    s = RuntimeSettings()
    for f in ("agent_full_auto", "agent_tool_loop", "agent_autonomous",
              "agent_planner", "agent_ab_shadow"):
        assert getattr(s, f) is False


def test_agent_flags_loaded_and_coerced():
    s = _from_dict({"agent_planner": 1, "agent_full_auto": "yes", "agent_tool_loop": 0})
    assert s.agent_planner is True and s.agent_full_auto is True and s.agent_tool_loop is False


# ---------- endpoint ----------

def test_get_agent_settings(monkeypatch):
    monkeypatch.setattr(dashboard, "load_settings", lambda mode=None: RuntimeSettings(agent_planner=True))
    b = json.loads(dashboard.api_get_agent_settings().body)
    assert b["agent_planner"] is True and b["agent_full_auto"] is False
    assert b["news_veto"] is True                      # default ON, tersedia di UI


def test_news_veto_toggle_off(monkeypatch):
    saved = {}
    cur = RuntimeSettings()
    monkeypatch.setattr(dashboard, "load_settings", lambda mode=None: cur)
    monkeypatch.setattr(dashboard, "save_settings", lambda s: saved.update(s=s))
    b = json.loads(dashboard.api_set_agent_settings({"news_veto": False}).body)
    assert b["news_veto"] is False and saved["s"].news_veto is False


def test_post_agent_settings_is_non_destructive(monkeypatch):
    saved = {}
    cur = RuntimeSettings(technique="auto", leverage=50, bet_usd=7.0)
    monkeypatch.setattr(dashboard, "load_settings", lambda mode=None: cur)
    monkeypatch.setattr(dashboard, "save_settings", lambda s: saved.update(s=s))
    b = json.loads(dashboard.api_set_agent_settings({"agent_full_auto": True}).body)
    assert b["agent_full_auto"] is True
    # setting lain TAK tersentuh (bukan reset ke default seperti POST /api/settings penuh)
    assert saved["s"].leverage == 50 and saved["s"].bet_usd == 7.0
    assert saved["s"].agent_full_auto is True
