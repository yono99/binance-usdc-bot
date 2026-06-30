"""Point 1 — perkakas (tools) untuk ReAct tool-loop sejati.

Agen boleh MEMANGGIL tool ini on-demand (reason → call tool → observe → reason lagi),
bukan menerima semua data sekaligus. Semua tool READ-ONLY (observasi), aman, dan
fail-soft (error → {"error": ...}, tak pernah melempar ke loop agen).

ToolContext memegang kolaborator (exchange, posisi, buffer, lessons) supaya tool mudah
diuji dengan stub. build_tools(ctx) -> {name: {"desc","fn"}} dipakai ReactAgent.
"""
from __future__ import annotations

import numpy as np

from .logger import log


class ToolContext:
    def __init__(self, ex=None, open_positions=None, buffers=None, cfg=None, lessons=None):
        self.ex = ex
        self.open = open_positions or {}
        self.buffers = buffers or {}
        self.cfg = cfg or {}
        self.lessons = lessons

    # ---- snapshot portofolio (murah, tanpa jaringan) ----
    def portfolio(self) -> dict:
        pos = [{"symbol": s, "side": p.get("side"), "entry": p.get("entry"),
                "bet": p.get("bet")} for s, p in self.open.items()]
        return {"positions": pos, "count": len(pos),
                "exposure_usd": round(sum((p.get("bet") or 0) for p in self.open.values()), 2)}


def _safe(fn):
    """Bungkus tool agar error → {"error": ...} (tak pernah melempar ke loop agen)."""
    def wrapped(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:  # boundary
            log.warning(f"tool error: {e}")
            return {"error": str(e)[:160]}
    return wrapped


def build_tools(ctx: ToolContext) -> dict[str, dict]:
    def get_orderbook(args):
        sym = args.get("symbol")
        ob = ctx.ex.client.fetch_order_book(sym, limit=10)
        bids, asks = ob.get("bids") or [], ob.get("asks") or []
        if not bids or not asks:
            return {"error": "order book kosong"}
        bid, ask = bids[0][0], asks[0][0]
        bv = float(sum(b[1] for b in bids))
        av = float(sum(a[1] for a in asks))
        spread = (ask - bid) / ((ask + bid) / 2) * 100
        imb = (bv - av) / (bv + av) if (bv + av) else 0.0
        return {"spread_pct": round(spread, 4), "bid_vol": round(bv, 2),
                "ask_vol": round(av, 2), "imbalance": round(imb, 3)}

    def get_ticker(args):
        t = ctx.ex.ticker(args.get("symbol"))
        return {"last": t.get("last"), "quote_volume_24h": t.get("quoteVolume"),
                "pct_change_24h": t.get("percentage")}

    def get_portfolio(args):
        return ctx.portfolio()

    def check_correlation(args):
        sym = args.get("symbol")
        base = ctx.buffers.get(sym)
        if base is None or len(base) < 20:
            return {"error": "buffer simbol kurang"}
        br = base["close"].pct_change().tail(50).reset_index(drop=True)
        worst_sym, worst = None, 0.0
        for osym in ctx.open:
            ob = ctx.buffers.get(osym)
            if ob is None or len(ob) < 20:
                continue
            orr = ob["close"].pct_change().tail(50).reset_index(drop=True)
            c = br.corr(orr)
            if c == c and abs(c) > abs(worst):     # c==c → bukan NaN
                worst, worst_sym = float(c), osym
        return {"max_abs_corr": round(worst, 3), "with": worst_sym,
                "n_open": len(ctx.open)}

    def get_funding(args):
        fr = ctx.ex.client.fetch_funding_rate(args.get("symbol"))
        mark, index = fr.get("markPrice"), fr.get("indexPrice")
        basis = ((mark - index) / index * 100) if (mark and index) else None
        return {"funding_rate": fr.get("fundingRate"), "mark": mark, "index": index,
                "basis_pct": round(basis, 4) if basis is not None else None,
                "next_funding": fr.get("fundingDatetime")}

    def get_open_interest(args):
        oi = ctx.ex.client.fetch_open_interest(args.get("symbol"))
        return {"open_interest": oi.get("openInterestAmount") or oi.get("openInterest"),
                "value_usd": oi.get("openInterestValue")}

    def get_lessons(args):
        if ctx.lessons is None:
            return {"lessons": []}
        return {"lessons": ctx.lessons.recent(int(args.get("limit", 5)))}

    tools = {
        "get_orderbook": {"desc": "Order book L2 simbol: spread% & imbalance bid/ask (-1..1)",
                          "fn": get_orderbook},
        "get_ticker": {"desc": "Ticker: harga terakhir, volume 24h, %perubahan 24h",
                       "fn": get_ticker},
        "get_portfolio": {"desc": "Semua posisi terbuka + eksposur USD (tanpa argumen)",
                          "fn": get_portfolio},
        "check_correlation": {"desc": "Korelasi return simbol vs posisi terbuka (hindari taruhan kembar)",
                              "fn": check_correlation},
        # --- point 3: sumber edge DI LUAR OHLCV ---
        "get_funding": {"desc": "Funding rate + basis% (mark vs index) — crowding/premium (non-OHLCV)",
                        "fn": get_funding},
        "get_open_interest": {"desc": "Open interest saat ini — partisipasi/leverage pasar (non-OHLCV)",
                              "fn": get_open_interest},
        "get_lessons": {"desc": "Pelajaran teruji terbaru (arg: limit)", "fn": get_lessons},
    }
    return {name: {"desc": t["desc"], "fn": _safe(t["fn"])} for name, t in tools.items()}
