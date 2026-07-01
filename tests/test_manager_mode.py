"""Jalan A — Manager mode: resolusi posture agent (override manajer disiplin)."""
import types

from bot.forward import ForwardTester
from bot.settings_store import RuntimeSettings

posture = ForwardTester._agent_posture


def _rs(**kw):
    base = dict(agent_full_auto=False, agent_tool_loop=False, agent_autonomous=False,
                agent_planner=False, agent_ab_shadow=False, agent_manager_mode=False,
                technique="auto")
    return types.SimpleNamespace(**{**base, **kw})


def test_default_all_off():
    p = posture({}, _rs())
    assert p == {"tool_loop": False, "autonomous": False, "use_planner": False,
                 "ab_shadow": False, "use_gemini_trader": False}


def test_full_auto_enables_stack():
    p = posture({}, _rs(agent_full_auto=True))
    assert p["tool_loop"] and p["autonomous"] and p["use_planner"]


def test_gemini_technique_direction():
    assert posture({}, _rs(technique="gemini"))["use_gemini_trader"] is True


def test_manager_mode_forces_discipline_posture():
    # Manager-mode meng-override: tool_loop OFF, autonomous+planner ON, arah RULES (bukan gemini)
    p = posture({}, _rs(agent_manager_mode=True, technique="gemini"))
    assert p["tool_loop"] is False
    assert p["autonomous"] is True and p["use_planner"] is True
    assert p["use_gemini_trader"] is False       # arah dari rules walau teknik=gemini


def test_manager_mode_beats_full_auto():
    # full_auto menyalakan tool_loop, TAPI manager_mode mematikannya (frugal menang)
    p = posture({}, _rs(agent_full_auto=True, agent_manager_mode=True))
    assert p["tool_loop"] is False and p["autonomous"] is True


def test_config_yaml_also_resolves():
    p = posture({"tool_loop": True}, _rs())      # dari config.yaml
    assert p["tool_loop"] is True


def test_settings_field_default_and_clamp():
    assert RuntimeSettings().agent_manager_mode is False
    from bot.settings_store import _from_dict
    assert _from_dict({"agent_manager_mode": 1}).agent_manager_mode is True
