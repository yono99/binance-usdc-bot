"""_live_open: entry & SL/TP di try/except TERPISAH. Bila entry live terisi (uang
REAL) tapi SL/TP gagal ditempatkan, posisi TAK BOLEH hilang total dari pelacakan
tanpa upaya emergency-close — kalau tidak, bot buta thd eksposur telanjang nyata."""
import types

from bot.forward import ForwardTester


def _ft(create_order):
    ft = ForwardTester.__new__(ForwardTester)
    sent = []
    ft.notify = types.SimpleNamespace(send=lambda m: sent.append(m))
    ft.ex = types.SimpleNamespace(
        set_leverage=lambda sym, lev: None,
        client=types.SimpleNamespace(create_order=create_order, cancel_all_orders=lambda sym: None),
    )
    return ft, sent


def _rs():
    return types.SimpleNamespace(order_type="market", leverage=10)


def test_entry_itself_fails_reports_false_no_position_exists():
    calls = []
    def create_order(sym, typ, side, qty, price=None, params=None):
        calls.append(typ)
        raise RuntimeError("insufficient margin")
    ft, sent = _ft(create_order)
    ok, fill, pending = ft._live_open("BTC/USDC:USDC", True, 1.0, 100.0, 95.0, 110.0, _rs())
    assert ok is False and calls == ["market"]           # HANYA entry dicoba, tak ada SL/TP
    assert pending is None
    assert "GAGAL" in sent[0]


def test_sl_tp_fails_after_fill_emergency_close_succeeds():
    calls = []
    def create_order(sym, typ, side, qty, price=None, params=None):
        calls.append(typ)
        if typ == "market" and len(calls) == 1:
            return {"status": "closed", "average": 100.0}   # entry berhasil (terisi)
        if typ == "STOP_MARKET":
            raise RuntimeError("stop price invalid")      # SL gagal
        return {"average": 100.0}                        # emergency-close (market ke-2) berhasil
    ft, sent = _ft(create_order)
    ok, fill, pending = ft._live_open("BTC/USDC:USDC", True, 1.0, 100.0, 95.0, 110.0, _rs())
    assert ok is False and fill == 100.0                  # emergency-close sukses -> tak ada posisi tersisa
    assert pending is None
    assert calls == ["market", "STOP_MARKET", "market"]   # entry, SL(gagal), emergency-close
    assert "emergency-close berhasil" in sent[0]
    assert "DARURAT" not in sent[0]


def test_sl_tp_and_emergency_close_both_fail_must_stay_tracked():
    calls = []
    def create_order(sym, typ, side, qty, price=None, params=None):
        calls.append(typ)
        if typ == "market" and len(calls) == 1:
            return {"status": "closed", "average": 100.0}   # entry berhasil (terisi)
        raise RuntimeError("exchange down")                # SL gagal DAN emergency-close gagal
    ft, sent = _ft(create_order)
    ok, fill, pending = ft._live_open("BTC/USDC:USDC", True, 1.0, 100.0, 95.0, 110.0, _rs())
    assert ok is True and fill == 100.0                   # WAJIB tetap ok=True -> caller isi self.open
    assert pending is None
    assert "DARURAT" in sent[0] and "TELANJANG" in sent[0]
