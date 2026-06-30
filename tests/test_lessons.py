"""Phase 3 — LessonsEngine: derivasi (fallback deterministik), akurasi, pensiun."""
import pytest

from bot.config import Settings
from bot.lessons import LessonsEngine


@pytest.fixture
def lessons(cfg, tmp_path):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    return LessonsEngine(s, cfg, path=tmp_path / "lessons.json")


def _row(_id="d1", action="ENTER_LONG", outcome="TP_HIT", r=1.5, regime="trend"):
    return {"id": _id, "action": action, "reasoning": "x",
            "market_state": {"regime": regime}, "signal_scores": {"long": 0.6},
            "outcome": outcome, "outcome_r": r}


# ---------- derivasi ----------

def test_derive_creates_lesson_when_llm_off(lessons):
    l = lessons.derive_from_trade(_row())
    assert l is not None and "IF" in l["lesson"] and l["source"] == "deterministic"
    assert lessons.active()[0]["id"] == l["id"]


def test_derive_skips_unsettled_trade(lessons):
    assert lessons.derive_from_trade(_row(outcome=None)) is None


def test_five_trades_yield_lessons(lessons):
    # Success criterion Phase 3: ≥1 pelajaran setelah 5 trade tertutup
    for i in range(5):
        lessons.derive_from_trade(_row(_id=f"d{i}", r=(1.0 if i % 2 else -1.0)))
    assert len(lessons.active()) == 5


# ---------- akurasi & recent ----------

def test_record_trigger_updates_accuracy(lessons):
    l = lessons.derive_from_trade(_row())
    lid = l["id"]
    assert lessons.record_trigger(lid, correct=True) is True
    assert lessons.record_trigger(lid, correct=False) is True
    cur = next(x for x in lessons.all() if x["id"] == lid)
    assert cur["times_triggered"] == 2 and cur["times_correct"] == 1 and cur["confidence"] == 0.5


def test_record_trigger_unknown_id(lessons):
    assert lessons.record_trigger("nope", correct=True) is False
    assert lessons.record_trigger("", correct=True) is False


def test_recent_returns_id_and_text(lessons):
    lessons.derive_from_trade(_row(_id="d1"))
    r = lessons.recent(10)
    assert r and set(r[0]) == {"id", "lesson"}


# ---------- pensiun ----------

def test_retire_low_accuracy_after_enough_triggers(lessons):
    l = lessons.derive_from_trade(_row())
    lid = l["id"]
    for _ in range(12):
        lessons.record_trigger(lid, correct=False)     # akurasi 0.0, 12 pemicu
    assert lessons.score_and_retire(min_triggers=10, min_acc=0.4) == 1
    assert lessons.active() == []                       # dipensiunkan → tak aktif


def test_no_retire_before_min_triggers(lessons):
    l = lessons.derive_from_trade(_row())
    for _ in range(5):
        lessons.record_trigger(l["id"], correct=False)  # akurasi buruk tapi < 10 pemicu
    assert lessons.score_and_retire(min_triggers=10, min_acc=0.4) == 0
    assert len(lessons.active()) == 1
