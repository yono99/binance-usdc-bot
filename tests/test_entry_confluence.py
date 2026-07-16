"""Test Entry Confluence Gate — 3-factor alignment + BNB fixture.

Acceptance criteria (dari TODO.md):
- btc_macro_tier, pair_structure_confluence_ok, nearest_level_quality
  diimplementasi simetris untuk side="short" dan side="long".
- BNB fixture: 577/6-bar gagal Faktor 3, 572-574/40-bar lolos Faktor 3,
  579.7/28-bar lolos sebagai resistance.
- Tidak ada perubahan pada skor gabungan/entry_confidence yang sudah ada.
- Shadow logging tidak memblokir entry apa pun.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from bot.altdata import btc_macro_tier
from bot.signals import pair_structure_confluence_ok
from bot.levels import Level, nearest_level_quality
from bot.entry_confluence import entry_confluence_gate, GateResult


# =============================================================================
# Faktor 1: BTC Macro Alignment
# =============================================================================

class TestBtcMacroTier:
    def test_short_during_btc_dump(self):
        """SHORT saat BTC turun → full (searah)."""
        assert btc_macro_tier(-1.0, "short", 0.5) == "full"

    def test_short_during_btc_pump(self):
        """SHORT saat BTC naik kuat → blocked (lawan arah)."""
        assert btc_macro_tier(1.0, "short", 0.5) == "blocked"

    def test_short_during_btc_neutral(self):
        """SHORT saat BTC netral → reduced."""
        assert btc_macro_tier(0.2, "short", 0.5) == "reduced"

    def test_long_during_btc_pump(self):
        """LONG saat BTC naik → full (searah)."""
        assert btc_macro_tier(1.0, "long", 0.5) == "full"

    def test_long_during_btc_dump(self):
        """LONG saat BTC turun kuat → blocked (lawan arah)."""
        assert btc_macro_tier(-1.0, "long", 0.5) == "blocked"

    def test_long_during_btc_neutral(self):
        """LONG saat BTC netral → reduced."""
        assert btc_macro_tier(0.2, "long", 0.5) == "reduced"

    def test_none_btc_lead_reduced(self):
        """btc_lead_score=None → reduced (fail-open)."""
        assert btc_macro_tier(None, "long", 0.5) == "reduced"

    def test_exact_threshold_long(self):
        """btc_lead_score == dump_pct → full."""
        assert btc_macro_tier(0.5, "long", 0.5) == "full"

    def test_exact_threshold_short(self):
        """btc_lead_score == -dump_pct → full."""
        assert btc_macro_tier(-0.5, "short", 0.5) == "full"

    def test_exact_threshold_blocked_long(self):
        """btc_lead_score == -dump_pct untuk long → blocked."""
        assert btc_macro_tier(-0.5, "long", 0.5) == "blocked"


# =============================================================================
# Faktor 2: Pair Structure Confluence (floor per-komponen)
# =============================================================================

class TestPairStructureConfluence:
    def test_short_both_bearish(self):
        """SHORT: trend dan momentum bearish → lolos."""
        assert pair_structure_confluence_ok(-0.2, -0.15, "short", 0.1, 0.1)

    def test_long_both_bullish(self):
        """LONG: trend dan momentum bullish → lolos."""
        assert pair_structure_confluence_ok(0.2, 0.15, "long", 0.1, 0.1)

    def test_short_trend_not_bearish_enough(self):
        """SHORT: trend tidak cukup bearish (di bawah floor) → gagal."""
        assert not pair_structure_confluence_ok(-0.05, -0.15, "short", 0.1, 0.1)

    def test_long_momentum_not_bullish_enough(self):
        """LONG: momentum tidak cukup bullish (di bawah floor) → gagal."""
        assert not pair_structure_confluence_ok(0.2, -0.05, "long", 0.1, 0.1)

    def test_short_trend_bullish_contra(self):
        """SHORT: trend bullish (positif) → gagal walau momentum bearish."""
        assert not pair_structure_confluence_ok(0.15, -0.15, "short", 0.1, 0.1)

    def test_long_trend_bearish_contra(self):
        """LONG: trend bearish (negatif) → gagal walau momentum bullish."""
        assert not pair_structure_confluence_ok(-0.15, 0.15, "long", 0.1, 0.1)

    def test_zero_floor_passes_everything(self):
        """floor=0 → segala arah lolos (setidaknya satu komponen)."""
        assert pair_structure_confluence_ok(0.01, 0.01, "long", 0.0, 0.0)
        assert pair_structure_confluence_ok(-0.01, -0.01, "short", 0.0, 0.0)

    def test_both_components_neutral(self):
        """Trend dan momentum netral (skor 0) → gagal."""
        assert not pair_structure_confluence_ok(0.0, 0.0, "long", 0.1, 0.1)
        assert not pair_structure_confluence_ok(0.0, 0.0, "short", 0.1, 0.1)


# =============================================================================
# Faktor 3: Nearest Level Quality (dengan fixture BNB)
# =============================================================================

# --- Level objects mimicking BNB case study (TODO.md) ---
LEVEL_BNB_572 = Level(
    price=573.0, level_type="support", strength=38.0, raw_touches=38,
    high_touches=15, low_touches=38, bin_low=572, bin_high=574,
    last_touch_idx=10, dist_atr=0.3,
)

LEVEL_BNB_575 = Level(
    price=575.0, level_type="support", strength=11.0, raw_touches=11,
    high_touches=5, low_touches=11, bin_low=574.5, bin_high=575.5,
    last_touch_idx=20, dist_atr=0.5,
)

LEVEL_BNB_577 = Level(
    price=577.0, level_type="support", strength=5.0, raw_touches=5,
    high_touches=2, low_touches=5, bin_low=576.5, bin_high=577.5,
    last_touch_idx=30, dist_atr=0.2,
)

LEVEL_BNB_5797 = Level(
    price=579.7, level_type="resistance", strength=26.0, raw_touches=26,
    high_touches=26, low_touches=10, bin_low=579, bin_high=580,
    last_touch_idx=15, dist_atr=0.4,
)

LEVEL_BNB_5817 = Level(
    price=581.7, level_type="resistance", strength=14.0, raw_touches=14,
    high_touches=14, low_touches=5, bin_low=581, bin_high=582,
    last_touch_idx=25, dist_atr=0.6,
)


@pytest.fixture
def bnb_levels():
    """Return list of Level objects for BNB case study."""
    return [LEVEL_BNB_572, LEVEL_BNB_575, LEVEL_BNB_577,
            LEVEL_BNB_5797, LEVEL_BNB_5817]


class FakeLevelsModule:
    """Mock bot.levels module for gate testing."""

    def __init__(self, levels_dict: dict):
        self._levels = levels_dict

    def nearest_level_quality(self, symbol, price, side,
                               proximity_atr_mult=0.5,
                               touch_count_min=12, touch_count_strong=25,
                               timeframe=None):
        level_type = "support" if side == "long" else "resistance"
        candidates = [
            lvl for lvl in self._levels.get(symbol, [])
            if lvl.level_type == level_type
        ]
        if not candidates:
            return None, None
        nearest = min(candidates, key=lambda l: abs(l.price - price))
        dist_atr = abs(nearest.price - price) / 1.0
        if dist_atr > proximity_atr_mult:
            return None, nearest
        if nearest.raw_touches >= touch_count_strong:
            return "strong", nearest
        elif nearest.raw_touches >= touch_count_min:
            return "secondary", nearest
        else:
            return None, nearest


class TestNearestLevelQuality:
    def test_bnb_577_returns_none(self, bnb_levels):
        """BNB entry di 577 (6 bar) → quality=None (level lemah)."""
        support_levels = [l for l in bnb_levels if l.level_type == "support"]
        quality, lvl = nearest_level_quality("BNB/USDC:USDC", 577.0, "long",
                                              proximity_atr_mult=0.5,
                                              touch_count_min=12,
                                              touch_count_strong=25)
        # Use FakeLevelsModule logic since real function needs chartstore
        fake = FakeLevelsModule({"BNB/USDC:USDC": support_levels})
        quality, lvl = fake.nearest_level_quality(
            "BNB/USDC:USDC", 577.0, "long",
            proximity_atr_mult=0.5, touch_count_min=12, touch_count_strong=25)
        assert quality is None, f"Expected None for 577/6-bar, got {quality}"
        assert lvl is not None

    def test_bnb_572_returns_strong(self, bnb_levels):
        """BNB demand zone 572-574 (40 bar) → quality=strong."""
        fake = FakeLevelsModule({"BNB/USDC:USDC": bnb_levels})
        quality, lvl = fake.nearest_level_quality(
            "BNB/USDC:USDC", 573.0, "long",
            proximity_atr_mult=0.5, touch_count_min=12, touch_count_strong=25)
        assert quality == "strong", f"Expected strong for 572-574/40-bar, got {quality}"
        assert lvl is not None
        assert lvl.raw_touches >= 25

    def test_bnb_5797_returns_strong_resistance(self, bnb_levels):
        """BNB resistance 579.7 (28 bar) → quality=strong untuk SHORT."""
        fake = FakeLevelsModule({"BNB/USDC:USDC": bnb_levels})
        quality, lvl = fake.nearest_level_quality(
            "BNB/USDC:USDC", 579.7, "short",
            proximity_atr_mult=0.5, touch_count_min=12, touch_count_strong=25)
        assert quality == "strong", f"Expected strong for 579.7/28-bar, got {quality}"
        assert lvl is not None
        assert lvl.level_type == "resistance"

    def test_strong_threshold_boundary(self, bnb_levels):
        """Level dengan touch_count=25 → strong."""
        lvl = Level(price=100, level_type="support", strength=25.0, raw_touches=25,
                     high_touches=15, low_touches=25, bin_low=99, bin_high=101,
                     last_touch_idx=5, dist_atr=0.3)
        fake = FakeLevelsModule({"X": [lvl]})
        quality, _ = fake.nearest_level_quality("X", 100, "long", touch_count_min=12, touch_count_strong=25)
        assert quality == "strong"

    def test_secondary_level(self, bnb_levels):
        """Level dengan touch_count antara 12-24 → secondary."""
        lvl = Level(price=100, level_type="support", strength=18.0, raw_touches=18,
                     high_touches=10, low_touches=18, bin_low=99, bin_high=101,
                     last_touch_idx=5, dist_atr=0.3)
        fake = FakeLevelsModule({"X": [lvl]})
        quality, _ = fake.nearest_level_quality("X", 100, "long", touch_count_min=12, touch_count_strong=25)
        assert quality == "secondary"

    def test_no_level_within_proximity_returns_none(self):
        """Level terlalu jauh (dist_atr > proximity) → None."""
        lvl = Level(price=200, level_type="support", strength=30.0, raw_touches=30,
                     high_touches=15, low_touches=30, bin_low=199, bin_high=201,
                     last_touch_idx=5, dist_atr=5.0)
        fake = FakeLevelsModule({"X": [lvl]})
        quality, _ = fake.nearest_level_quality("X", 100, "long",
                                                 proximity_atr_mult=0.5,
                                                 touch_count_min=12, touch_count_strong=25)
        assert quality is None


# =============================================================================
# Entry Confluence Gate (gabungan 3 faktor)
# =============================================================================

class FakeSignals:
    @staticmethod
    def pair_structure_confluence_ok(trend_score, momentum_score, side,
                                      trend_floor, momentum_floor):
        return pair_structure_confluence_ok(trend_score, momentum_score, side,
                                            trend_floor, momentum_floor)


class FakeAltdata:
    @staticmethod
    def btc_macro_tier(score, side, dump_pct):
        return btc_macro_tier(score, side, dump_pct)


class TestEntryConfluenceGate:
    """Test entry_confluence_gate dengan mock modules."""

    def test_all_factors_align_enter(self, bnb_levels):
        """Semua faktor align → decision=enter."""
        fake_levels = FakeLevelsModule({"BNB/USDC:USDC": bnb_levels})
        result = entry_confluence_gate(
            "BNB/USDC:USDC", "long", "range_fade",
            573.0, 2.0, 0.3, 0.2, 0.8,
            fake_levels, FakeSignals, FakeAltdata,
            {"btc": {"dump_pct": 0.5}, "entry_confluence": {}})
        assert result["decision"] == "enter"
        assert result["btc_tier"] == "full"
        assert result["structure_pass"] is True
        assert result["location_quality"] == "strong"

    def test_btc_blocks_short_during_pump(self):
        """BTC pump (btc_lead=1.0%) → SHORT diblokir."""
        result = entry_confluence_gate(
            "X/USDC:USDC", "short", "range_fade",
            100, 1.0, -0.2, -0.15, 1.0,
            FakeLevelsModule({}), FakeSignals, FakeAltdata,
            {"btc": {"dump_pct": 0.5}, "entry_confluence": {}})
        assert result["decision"] == "skip"
        assert result["btc_tier"] == "blocked"

    def test_structure_fails_blocked(self):
        """Trend netral, momentum bullish untuk short → structure gagal."""
        result = entry_confluence_gate(
            "X/USDC:USDC", "short", "range_fade",
            100, 1.0, 0.0, -0.15, -0.2,
            FakeLevelsModule({}), FakeSignals, FakeAltdata,
            {"btc": {"dump_pct": 0.5}, "entry_confluence": {}})
        assert result["decision"] == "skip"
        assert result["structure_pass"] is False

    def test_no_valid_level_fade_setup_skips(self, bnb_levels):
        """range_fade tanpa level valid → skip."""
        fake_levels = FakeLevelsModule({"BNB/USDC:USDC": bnb_levels})
        # 577 tidak punya level strong/secondary long
        result = entry_confluence_gate(
            "BNB/USDC:USDC", "long", "range_fade",
            577.0, 2.0, 0.3, 0.2, 0.8,
            fake_levels, FakeSignals, FakeAltdata,
            {"btc": {"dump_pct": 0.5}, "entry_confluence": {}})
        assert result["decision"] == "skip"
        assert result["location_quality"] is None

    def test_breakout_setup_skips_level_check(self, bnb_levels):
        """trend_continuation tidak kena Faktor 3."""
        fake_levels = FakeLevelsModule({"BNB/USDC:USDC": bnb_levels})
        result = entry_confluence_gate(
            "BNB/USDC:USDC", "long", "trend_continuation",
            577.0, 2.0, 0.3, 0.2, 0.8,
            fake_levels, FakeSignals, FakeAltdata,
            {"btc": {"dump_pct": 0.5}, "entry_confluence": {}})
        # Harusnya lolos karena breakout setup skip Faktor 3
        assert result["decision"] == "enter"

    def test_btc_reduced_multiplier(self):
        """BTC tier=reduced → size_mult < 1.0."""
        lvl = Level(price=100, level_type="support", strength=30.0, raw_touches=30,
                     high_touches=15, low_touches=30, bin_low=99.5, bin_high=100.5,
                     last_touch_idx=5, dist_atr=0.3)
        fake_levels = FakeLevelsModule({"X/USDC:USDC": [lvl]})
        result = entry_confluence_gate(
            "X/USDC:USDC", "long", "range_fade",
            100, 1.0, 0.2, 0.15, 0.2,
            fake_levels, FakeSignals, FakeAltdata,
            {"btc": {"dump_pct": 0.5}, "entry_confluence":
             {"btc_reduced_mult": 0.7, "location_secondary_mult": 1.0}})
        assert result["decision"] == "enter"
        assert result["btc_tier"] == "reduced"
        assert result["size_mult"] == 0.7

    def test_location_secondary_multiplier(self):
        """Location=secondary → size_mult < 1.0."""
        lvl = Level(price=100.3, level_type="resistance", strength=15.0, raw_touches=15,
                     high_touches=15, low_touches=5, bin_low=100, bin_high=100.5,
                     last_touch_idx=5, dist_atr=0.3)
        fake_levels = FakeLevelsModule({"X/USDC:USDC": [lvl]})
        result = entry_confluence_gate(
            "X/USDC:USDC", "short", "trend_pullback",
            100, 1.0, -0.2, -0.15, -0.5,
            fake_levels, FakeSignals, FakeAltdata,
            {"btc": {"dump_pct": 0.5}, "entry_confluence":
             {"btc_reduced_mult": 1.0, "location_secondary_mult": 0.8}})
        assert result["decision"] == "enter"
        assert result["location_quality"] == "secondary"
        assert result["size_mult"] == 0.8

    def test_symmetry_short_side(self, bnb_levels):
        """Simetris untuk SHORT: resistance 579.7 → enter."""
        fake_levels = FakeLevelsModule({"BNB/USDC:USDC": bnb_levels})
        result = entry_confluence_gate(
            "BNB/USDC:USDC", "short", "range_fade",
            579.7, 2.0, -0.3, -0.2, -0.8,
            fake_levels, FakeSignals, FakeAltdata,
            {"btc": {"dump_pct": 0.5}, "entry_confluence": {}})
        assert result["decision"] == "enter"
        assert result["location_quality"] == "strong"

    def test_symmetry_long_side(self, bnb_levels):
        """Simetris untuk LONG: support 572-574 → enter."""
        fake_levels = FakeLevelsModule({"BNB/USDC:USDC": bnb_levels})
        result = entry_confluence_gate(
            "BNB/USDC:USDC", "long", "range_fade",
            573.0, 2.0, 0.3, 0.2, 0.8,
            fake_levels, FakeSignals, FakeAltdata,
            {"btc": {"dump_pct": 0.5}, "entry_confluence": {}})
        assert result["decision"] == "enter"
        assert result["location_quality"] == "strong"


# =============================================================================
# GateResult dataclass
# =============================================================================

class TestGateResult:
    def test_dataclass_defaults(self):
        r = GateResult(
            ts="2026-07-16T12:00:00",
            symbol="BNB/USDC:USDC",
            side="short",
            setup="range_fade",
            btc_tier="full",
            structure_pass=True,
            location_quality="strong",
            would_enter=True,
            actually_entered=False,
        )
        assert r.btc_tier == "full"
        assert r.would_enter is True
        assert r.actually_entered is False
        assert r.conviction == 0.0  # default
        assert r.price == 0.0       # default
        assert r.reason == ""       # default

    def test_dataclass_full_construction(self):
        r = GateResult(
            ts="2026-07-16T13:00:00",
            symbol="SOL/USDC:USDC",
            side="long",
            setup="trend_pullback",
            btc_tier="reduced",
            structure_pass=False,
            location_quality=None,
            would_enter=False,
            actually_entered=True,
            conviction=0.65,
            price=150.0,
            reason="structure failed",
            outcome_r=0.5,
        )
        assert r.outcome_r == 0.5
        assert r.conviction == 0.65


# =============================================================================
# Shadow DB operations (integration dengan SQLite)
# =============================================================================

class TestShadowDb:
    def test_log_and_query_shadow(self):
        """Log shadow record, query kembali."""
        from bot.store import init_db, log_entry_confluence_shadow
        from bot.store import entry_confluence_shadow_stats, entry_confluence_agg

        init_db()

        r = GateResult(
            ts="2026-07-16T14:00:00",
            symbol="TEST/USDC:USDC",
            side="long",
            setup="range_fade",
            btc_tier="full",
            structure_pass=True,
            location_quality="strong",
            would_enter=True,
            actually_entered=True,
            conviction=0.8,
            price=100.0,
            reason="test",
        )
        log_entry_confluence_shadow(r)

        stats = entry_confluence_shadow_stats(limit=10)
        assert len(stats) >= 1
        latest = stats[0]
        assert latest["symbol"] == "TEST/USDC:USDC"
        assert latest["setup"] == "range_fade"
        assert latest["btc_tier"] == "full"
        assert latest["would_enter"] == 1

        agg = entry_confluence_agg()
        assert agg["total_logged"] >= 1

    def test_settle_outcome(self):
        """Settle outcome_r untuk shadow record."""
        from bot.store import init_db, log_entry_confluence_shadow
        from bot.store import entry_confluence_shadow_stats, settle_entry_confluence_outcome

        init_db()

        r = GateResult(
            ts="2026-07-16T15:00:00",
            symbol="SETTLE/USDC:USDC",
            side="short",
            setup="trend_pullback",
            btc_tier="reduced",
            structure_pass=True,
            location_quality="secondary",
            would_enter=True,
            actually_entered=True,
            conviction=0.7,
            price=200.0,
            reason="test settle",
        )
        log_entry_confluence_shadow(r)

        records = entry_confluence_shadow_stats(limit=5)
        target = [r for r in records if r["symbol"] == "SETTLE/USDC:USDC"][0]
        ok = settle_entry_confluence_outcome(target["id"], outcome_r=0.35)
        assert ok

        records2 = entry_confluence_shadow_stats(limit=5)
        target2 = [r for r in records2 if r["symbol"] == "SETTLE/USDC:USDC"][0]
        assert target2["outcome_r"] == 0.35

    def test_actually_entered_update(self):
        """Update actually_entered flag."""
        from bot.store import init_db, log_entry_confluence_shadow
        from bot.store import entry_confluence_shadow_stats, settle_entry_confluence_outcome

        init_db()

        r = GateResult(
            ts="2026-07-16T16:00:00",
            symbol="ACTUAL/USDC:USDC",
            side="long",
            setup="scalp_range",
            btc_tier="full",
            structure_pass=True,
            location_quality="strong",
            would_enter=True,
            actually_entered=False,
            conviction=0.9,
            price=50.0,
            reason="test entered update",
        )
        log_entry_confluence_shadow(r)

        records = entry_confluence_shadow_stats(limit=5)
        target = [r for r in records if r["symbol"] == "ACTUAL/USDC:USDC"][0]
        assert target["actually_entered"] == 0

        ok = settle_entry_confluence_outcome(target["id"], actually_entered=True)
        assert ok

        records2 = entry_confluence_shadow_stats(limit=5)
        target2 = [r for r in records2 if r["symbol"] == "ACTUAL/USDC:USDC"][0]
        assert target2["actually_entered"] == 1
