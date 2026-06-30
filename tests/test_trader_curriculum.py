"""Kurikulum trader — kontrak output (sl/tp wajib), SETUPS, prompt kelola posisi."""
from bot.trader_curriculum import (SETUPS, curriculum_prompt, manage_prompt,
                                   KNOWLEDGE)


def test_setups_taxonomy():
    assert "no_trade" in SETUPS
    for k in ("trend_pullback", "breakout_continuation", "range_fade", "exhaustion_reversal"):
        assert k in SETUPS


def test_curriculum_contract_requires_sl_tp():
    p = curriculum_prompt()
    assert '"sl"' in p and '"tp"' in p          # kontrak level baru
    assert "WAJIB" in p                          # SL wajib bila side≠flat
    assert "JSON" in p and "flat" in p


def test_curriculum_includes_setups_and_core():
    p = curriculum_prompt()
    assert "EXPECTANCY" in p
    for k in ("trend_pullback", "no_trade"):
        assert k in p


def test_manage_prompt_is_exit_only():
    m = manage_prompt()
    assert "tighten_stop" in m and "exit" in m
    assert "DILARANG melonggarkan" in m         # guardrail exit-only


def test_module_selection_subset():
    only = curriculum_prompt(modules=["risk"])
    assert "average down" in only               # modul risk ada
    assert "POLA CANDLE" not in only            # modul lain tak ikut
    assert set(["risk"]).issubset(KNOWLEDGE)
