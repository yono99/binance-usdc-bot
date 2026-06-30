"""Phase 2 — decision log: append + update outcome saat close (traceable entry→R)."""
import json

from bot import decision_log as dl


def _enter(symbol="BTC/USDC:USDC", action="ENTER_LONG", _id="a1"):
    return {"ts": "t", "id": _id, "symbol": symbol, "action": action,
            "reasoning": "x", "confidence": 0.6, "key_risks": [], "lesson_triggered": "",
            "source": "LLM", "signal_scores": {"long": 0.6, "short": 0.1},
            "market_state": {"price": 100}, "outcome": None, "outcome_r": None,
            "filled_at_close": False}


def test_append_and_read(tmp_path):
    p = tmp_path / "d.jsonl"
    dl.append(_enter(), path=p)
    dl.append(_enter(_id="a2"), path=p)
    assert len(dl.read_all(p)) == 2
    assert dl.recent(1, path=p)[0]["id"] == "a2"      # terbaru dulu


def test_record_outcome_updates_matching_entry(tmp_path):
    p = tmp_path / "d.jsonl"
    dl.append(_enter(_id="a1"), path=p)
    mid = dl.record_outcome("BTC/USDC:USDC", "TP_HIT", 1.85, path=p)
    assert mid == "a1"
    row = dl.read_all(p)[0]
    assert row["outcome"] == "TP_HIT" and row["outcome_r"] == 1.85 and row["filled_at_close"] is True


def test_record_outcome_targets_last_open_only(tmp_path):
    p = tmp_path / "d.jsonl"
    dl.append(_enter(_id="a1"), path=p)
    dl.record_outcome("BTC/USDC:USDC", "SL_HIT", -1.0, path=p)   # tutup a1
    dl.append(_enter(_id="a2"), path=p)                          # entry baru
    mid = dl.record_outcome("BTC/USDC:USDC", "TP_HIT", 2.0, path=p)
    rows = {r["id"]: r for r in dl.read_all(p)}
    assert mid == "a2"
    assert rows["a1"]["outcome"] == "SL_HIT"      # a1 tak tersentuh lagi
    assert rows["a2"]["outcome"] == "TP_HIT"


def test_record_outcome_ignores_skip_rows(tmp_path):
    p = tmp_path / "d.jsonl"
    skip = _enter(_id="s1"); skip["action"] = "SKIP"
    dl.append(skip, path=p)
    assert dl.record_outcome("BTC/USDC:USDC", "TP_HIT", 1.0, path=p) is None  # SKIP tak ditautkan


def test_record_outcome_no_match_returns_none(tmp_path):
    p = tmp_path / "d.jsonl"
    assert dl.record_outcome("ETH/USDC:USDC", "TP_HIT", 1.0, path=p) is None  # file kosong


def test_corrupt_line_skipped(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text(json.dumps(_enter()) + "\n{ broken json\n", encoding="utf-8")
    assert len(dl.read_all(p)) == 1     # baris korup dilewati, tak meledak
