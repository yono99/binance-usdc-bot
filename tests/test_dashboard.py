import json

from bot.dashboard import compute_stats


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def test_compute_stats_from_journal(tmp_path):
    p = tmp_path / "trades.jsonl"
    _write(p, [
        {"event": "forward_open", "symbol": "BTCUSDC", "side": "long", "entry": 100, "sl": 98, "tp": 105},
        {"event": "forward_close", "symbol": "BTCUSDC", "r": 1.5, "reason": "tp", "equity": 1075,
         "ts": "2026-01-01T00:00:00+00:00"},
        {"event": "forward_open", "symbol": "ETHUSDC", "side": "short", "entry": 50, "sl": 51, "tp": 47},
        {"event": "forward_close", "symbol": "ETHUSDC", "r": -1.0, "reason": "sl", "equity": 1040,
         "ts": "2026-01-01T01:00:00+00:00"},
        {"event": "forward_open", "symbol": "BTCUSDC", "side": "long", "entry": 101, "sl": 99, "tp": 106},
    ])
    s = compute_stats(p, start_equity=1000)
    assert s["trades"] == 2
    assert s["win_rate"] == 50.0
    assert abs(s["expectancy_r"] - 0.25) < 1e-9
    assert s["profit_factor"] == 1.5            # 1.5 / 1.0
    assert s["equity"] == 1040
    assert len(s["open_positions"]) == 1 and s["open_positions"][0]["symbol"] == "BTCUSDC"
    assert len(s["per_symbol"]) == 2
    assert s["equity_curve"][0] == 1000


def test_compute_stats_empty(tmp_path):
    s = compute_stats(tmp_path / "kosong.jsonl")
    assert s["trades"] == 0
    assert s["open_positions"] == []
    assert s["expectancy_r"] == 0.0
