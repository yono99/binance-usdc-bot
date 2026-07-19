import json

from bot.dashboard import build_trades, compute_stats, filter_trades


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
        {"event": "forward_open", "symbol": "SOLUSDC", "side": "long", "entry": 10, "sl": 9, "tp": 12},
        {"event": "forward_close", "symbol": "SOLUSDC", "r": -1.0, "reason": "liq", "equity": 1028,
         "ts": "2026-01-01T02:00:00+00:00"},
        {"event": "forward_open", "symbol": "BTCUSDC", "side": "long", "entry": 101, "sl": 99, "tp": 106},
    ])
    s = compute_stats(p, start_equity=1000)
    assert s["trades"] == 3
    assert s["liquidations"] == 1
    assert round(s["win_rate"], 1) == 33.3
    assert abs(s["expectancy_r"] - (-0.5 / 3)) < 1e-3   # (1.5-1-1)/3, dibulatkan 4 desimal
    assert s["profit_factor"] == 0.75                   # 1.5 / 2.0
    assert s["equity"] == 1028
    assert s["liq_points"] == [3]          # close ke-3 (SOL liq) -> titik 3 di kurva
    assert len(s["open_positions"]) == 1 and s["open_positions"][0]["symbol"] == "BTCUSDC"
    assert len(s["per_symbol"]) == 3
    assert s["equity_curve"][0] == 1000


def test_build_and_filter_trades():
    events = [
        {"event": "forward_open", "symbol": "BTCUSDC", "side": "long", "entry": 100, "sl": 98,
         "tp": 105, "ts": "2026-01-01T00:00:00+00:00"},
        {"event": "forward_close", "symbol": "BTCUSDC", "exit": 105, "reason": "tp", "r": 1.5,
         "pnl_usd": 18, "equity": 1018, "ts": "2026-01-01T01:00:00+00:00"},
        {"event": "forward_open", "symbol": "ETHUSDC", "side": "short", "entry": 50, "sl": 51,
         "tp": 47, "ts": "2026-01-02T00:00:00+00:00"},
        {"event": "forward_close", "symbol": "ETHUSDC", "exit": 51, "reason": "liq", "r": -1,
         "pnl_usd": -12, "equity": 1006, "ts": "2026-01-02T03:00:00+00:00"},
    ]
    tr = build_trades(events)
    assert len(tr) == 2
    assert tr[0]["symbol"] == "BTCUSDC" and tr[0]["entry"] == 100 and tr[0]["exit"] == 105
    assert len(filter_trades(tr, reason="liq")) == 1
    assert len(filter_trades(tr, symbol="eth")) == 1
    assert len(filter_trades(tr, dfrom="2026-01-02")) == 1
    assert len(filter_trades(tr, dto="2026-01-01")) == 1


def test_build_trades_hides_orphan_close_without_side_entry():
    """Close yatim tanpa side/entry = baris hantu di UI — disembunyikan."""
    events = [
        {"event": "forward_open", "symbol": "AAA", "side": "long", "entry": 1.0, "ts": "t0"},
        {"event": "forward_close", "symbol": "AAA", "side": "long", "entry": 1.0,
         "exit": 1.1, "reason": "sl", "r": -1, "ts": "t1"},
        # double-close yatim, payload rusak (tanpa side/entry)
        {"event": "forward_close", "symbol": "AAA", "exit": 1.05, "reason": "sl",
         "r": -0.5, "ts": "t2"},
    ]
    tr = build_trades(events)
    assert len(tr) == 1
    assert tr[0]["entry"] == 1.0 and tr[0]["side"] == "long"


def test_build_trades_orphan_uses_close_side_entry_fallback():
    """Open hilang tapi close punya side/entry → tetap tampil (bukan baris kosong)."""
    events = [
        {"event": "forward_close", "symbol": "BBB", "side": "short", "entry": 50.0,
         "exit": 49.0, "reason": "tp", "r": 1.0, "ts": "t1"},
    ]
    tr = build_trades(events)
    assert len(tr) == 1
    assert tr[0]["side"] == "short" and tr[0]["entry"] == 50.0


def test_build_trades_partial_does_not_consume_open():
    """Partial TP tidak memakan open_map → close final tetap dapat entry dari open."""
    events = [
        {"event": "forward_open", "symbol": "CCC", "side": "long", "entry": 10.0,
         "sl": 9.0, "tp": 12.0, "ts": "t0"},
        {"event": "forward_close", "symbol": "CCC", "side": "long", "entry": 10.0,
         "exit": 11.5, "reason": "tp_partial", "r": 0.5, "ts": "t1"},
        {"event": "forward_close", "symbol": "CCC", "side": "long", "entry": 10.0,
         "exit": 12.0, "reason": "tp", "r": 0.8, "ts": "t2"},
    ]
    tr = build_trades(events)
    assert len(tr) == 2
    assert tr[0].get("partial") is True and tr[0]["entry"] == 10.0
    assert tr[1]["reason"] == "tp" and tr[1]["entry"] == 10.0 and tr[1]["side"] == "long"


def test_build_trades_partial_event_name():
    events = [
        {"event": "forward_open", "symbol": "DDD", "side": "long", "entry": 2.0, "ts": "t0"},
        {"event": "forward_close_partial", "symbol": "DDD", "side": "long", "entry": 2.0,
         "exit": 2.1, "reason": "tp_partial", "partial": True, "ts": "t1"},
        {"event": "forward_close", "symbol": "DDD", "side": "long", "entry": 2.0,
         "exit": 2.2, "reason": "tp", "ts": "t2"},
    ]
    tr = build_trades(events)
    assert len(tr) == 2
    assert tr[1]["entry"] == 2.0


def test_compute_stats_empty(tmp_path):
    s = compute_stats(tmp_path / "kosong.jsonl")
    assert s["trades"] == 0
    assert s["open_positions"] == []
    assert s["expectancy_r"] == 0.0


def test_json_safe_sanitizes_nan_inf():
    """NaN/inf dari statistik (profit_factor inf saat win tanpa loss) tak boleh
    meledakkan endpoint JSON (insiden /api/gemini-trader 2026-07-02)."""
    from bot.dashboard import _json_safe
    dirty = {"pf": float("inf"), "x": float("nan"),
             "nested": [{"y": float("-inf"), "ok": 1.5}], "s": "a",
             "tup": (float("inf"), 2.0)}   # tuple → list oleh json; inf di dlmnya hrs bersih
    clean = _json_safe(dirty)
    import json
    json.dumps(clean)                                   # tak boleh raise
    assert clean["pf"] is None and clean["x"] is None
    assert clean["nested"][0]["y"] is None and clean["nested"][0]["ok"] == 1.5
    assert clean["tup"] == [None, 2.0]
