"""return_pct dulu palsu (-98.94%) karena start_equity di-hardcode 1000 padahal saldo
paper ~$12. Fix: start_equity default = ekuitas close PERTAMA (return sejak trade pertama
tercatat), bisa dioverride caller. Diuji lewat param path (tanpa store/jaringan)."""
import json
from bot.dashboard import compute_stats


def _log(tmp_path, closes):
    p = tmp_path / "t.jsonl"
    rows = [{"event": "forward_open", "symbol": "X"}] + [
        {"event": "forward_close", "symbol": "X", **c} for c in closes]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


def test_return_pct_tidak_lagi_98_persen(tmp_path):
    # saldo bergerak 11.7 -> 10.57 (rugi kecil), BUKAN dari 1000
    p = _log(tmp_path, [{"r": -0.1, "equity": 11.7, "reason": "sl"},
                        {"r": 0.05, "equity": 10.57, "reason": "tp"}])
    s = compute_stats(path=p)
    assert s["equity_curve"][0] == 11.7            # start dari data, bukan 1000
    assert s["equity"] == 10.57
    assert -20 < s["return_pct"] < 5               # ~ -9.7%, jauh dari -98.94
    assert s["return_pct"] > -50


def test_start_equity_eksplisit_dihormati(tmp_path):
    p = _log(tmp_path, [{"r": -0.1, "equity": 11.7, "reason": "sl"},
                        {"r": 0.05, "equity": 10.57, "reason": "tp"}])
    s = compute_stats(path=p, start_equity=12.0)
    assert s["equity_curve"][0] == 12.0
    assert abs(s["return_pct"] - (10.57 / 12 - 1) * 100) < 0.1


def test_tanpa_close_tidak_error(tmp_path):
    p = tmp_path / "e.jsonl"
    p.write_text('{"event":"forward_open","symbol":"X"}', encoding="utf-8")
    s = compute_stats(path=p)
    assert s["trades"] == 0 and s["return_pct"] == 0.0
