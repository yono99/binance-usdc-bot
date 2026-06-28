"""Registry = sumber kebenaran tunggal + dedup deterministik (anti bug Gemini)."""
import pytest

from bot import registry


@pytest.fixture
def tmp_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY_PATH", tmp_path / "reg.json")
    return registry


def test_seed_when_missing(tmp_registry):
    recs = tmp_registry.load()
    ids = {r["id"] for r in recs}
    assert {"v5", "v6", "v7"} <= ids                 # seed memuat siklus yang sudah jalan
    assert "cross_exchange_basis" in tmp_registry.tested_sources(recs)


def test_record_upsert(tmp_registry):
    tmp_registry.record({"id": "v6", "source": "liquidation_cascade",
                         "name": "x", "oos_exp": -0.99, "verdict": "REJECTED"})
    recs = tmp_registry.load()
    v6 = [r for r in recs if r["id"] == "v6"]
    assert len(v6) == 1 and v6[0]["oos_exp"] == -0.99   # upsert, bukan duplikat
    assert "updated" in v6[0]


def test_is_duplicate_uses_tags(tmp_registry):
    # sumber yang ada di seed → duplikat; yang belum → bukan
    assert tmp_registry.is_duplicate("liquidation_cascade") is True
    assert tmp_registry.is_duplicate("options_flow") is False


def test_untested_excludes_tested(tmp_registry):
    unt = tmp_registry.untested_sources()
    assert "options_flow" in unt                      # belum diuji
    assert "cross_exchange_basis" not in unt          # sudah diuji (seed)
    assert "other" not in unt                          # 'other' bukan kandidat


def test_new_source_becomes_tested_after_record(tmp_registry):
    assert "options_flow" not in tmp_registry.tested_sources()
    tmp_registry.record({"id": "v8", "source": "options_flow", "name": "deribit",
                         "oos_exp": -0.2, "verdict": "REJECTED"})
    assert "options_flow" in tmp_registry.tested_sources()
    assert "options_flow" not in tmp_registry.untested_sources()


def test_copilot_fallback_next_is_untested(tmp_registry):
    from bot.copilot import StrategyCopilot
    # instance tanpa Gemini (enabled False) → _fallback_next pakai registry
    cop = StrategyCopilot.__new__(StrategyCopilot)
    tag, _desc = cop._fallback_next()
    assert tag in tmp_registry.untested_sources()      # selalu sumber belum-teruji
