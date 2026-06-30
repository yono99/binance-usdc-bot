"""journal — dual-write JSONL + SQLite; kegagalan store tak menjatuhkan bot."""
import json

from bot import logger, store


def test_journal_writes_jsonl_and_calls_store(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "LOG_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(store, "insert_event", lambda e, p, ts=None: calls.append((e, p)))
    logger.journal("open", {"symbol": "BTC/USDC:USDC", "qty": 2})
    rec = json.loads((tmp_path / "trades.jsonl").read_text(encoding="utf-8").strip())
    assert rec["event"] == "open" and rec["symbol"] == "BTC/USDC:USDC" and "ts" in rec
    assert calls and calls[0][0] == "open"


def test_journal_survives_store_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "LOG_DIR", tmp_path)

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(store, "insert_event", boom)
    logger.journal("close", {"symbol": "ETH/USDC:USDC"})   # tak boleh meledak
    assert (tmp_path / "trades.jsonl").exists()             # JSONL tetap aman
