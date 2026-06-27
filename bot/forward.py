"""Forward-test (paper) strategi v4 di data LIVE real-time — tanpa uang.

Tujuan: kumpulkan bukti out-of-sample SUNGGUHAN (masa depan, bukan histori) atas
satu set parameter TETAP. Tidak ada re-optimize saat jalan (itu menipu diri).

- Data: live publik (OHLCV + funding + OI + taker/CVD) — harga nyata.
- Eksekusi: paper (disimulasikan lokal, akuntansi identik backtest: fee+slippage).
- Opsional: mirror order ke Binance Futures Testnet untuk uji jalur eksekusi.
- Output: logs/forward_trades.jsonl + statistik berjalan (win%, expectancy R).
"""
from __future__ import annotations

import time
from collections import namedtuple
from dataclasses import dataclass, field

import pandas as pd

from .altdata import align, fetch_funding, fetch_oi, funding_zscore, oi_delta
from .backtest import Backtester, Trade, fetch_history
from .config import Settings
from .exchange import Exchange
from .logger import journal, log
from .orderflow import cvd_from_series, fetch_taker
from .strategy_lab import decide_v4, precompute_v4

_Sig = namedtuple("_Sig", ["side", "atr"])


def default_params() -> dict:
    """Satu set parameter tetap untuk forward-test (robust, sering terpilih OOS)."""
    return {"entry_confidence": 0.5, "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
            "use_htf": True, "regime": True, "use_funding": True,
            "use_oi": False, "use_of": True}


@dataclass
class ForwardTester:
    settings: Settings
    symbols: list[str]
    params: dict
    equity: float = 1000.0
    maxlen: int = 400
    fee: float = 0.04
    slippage: float = 0.02
    alt_refresh_s: int = 600

    buffers: dict = field(default_factory=dict)
    last_closed: dict = field(default_factory=dict)
    alt_raw: dict = field(default_factory=dict)
    open: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)

    def __post_init__(self):
        self.ex = Exchange(self.settings)
        self.cfg = self.settings.raw
        self.tf = self.cfg["market"]["timeframe"]
        self.htf_mult = self.cfg["strategy"]["htf_mult"]
        self.sessions = set(self.cfg["strategy"]["sessions"]) or None
        self.max_open = self.cfg["rotate"]["max_open_positions"]
        self.bt = Backtester(self.cfg, fee_pct=self.fee, slippage_pct=self.slippage)
        self.bt.sl_mult = self.params["sl_atr_mult"]
        self.bt.tp_mult = self.params["tp_atr_mult"]
        self.risk_frac = self.cfg["risk"]["account_risk_pct"] / 100.0

    def seed(self) -> None:
        for sym in self.symbols:
            try:
                self.buffers[sym] = fetch_history(self.ex, sym, self.tf, self.maxlen)
                self.last_closed[sym] = self.buffers[sym].index[-2]
                log.info(f"seed {sym}: {len(self.buffers[sym])} bar {self.tf}")
            except Exception as e:  # boundary
                log.error(f"seed {sym} gagal: {e}")

    def _update_buffer(self, sym: str) -> pd.DataFrame | None:
        try:
            recent = self.ex.ohlcv(sym, self.tf, limit=5)
        except Exception as e:  # boundary
            log.warning(f"update {sym} gagal: {e}")
            return None
        buf = pd.concat([self.buffers.get(sym, recent), recent])
        buf = buf[~buf.index.duplicated(keep="last")].sort_index().iloc[-self.maxlen:]
        self.buffers[sym] = buf
        return buf

    def _alt_arrays(self, sym: str, df: pd.DataFrame):
        now = time.time()
        raw = self.alt_raw.get(sym)
        if not raw or now - raw["ts"] > self.alt_refresh_s:
            since = int(df.index[0].timestamp() * 1000)
            raw = {"ts": now,
                   "fund": fetch_funding(self.ex, sym, since),
                   "oi": fetch_oi(self.ex, sym, self.tf, since),
                   "taker": fetch_taker(self.ex, sym, self.tf, len(df) + 20)}
            self.alt_raw[sym] = raw
        fz = align(df.index, funding_zscore(raw["fund"], self.cfg["strategy"]["funding_z_window"]), 0.0)
        oid = oi_delta(df.index, raw["oi"], self.cfg["strategy"]["oi_delta_lookback"])
        if raw["taker"].empty:
            import numpy as np
            imb = np.zeros(len(df)); div = np.zeros(len(df), dtype=bool)
        else:
            vol = raw["taker"]["volume"].reindex(df.index).fillna(0.0)
            tb = raw["taker"]["taker_buy"].reindex(df.index).fillna(0.0)
            imb, div = cvd_from_series(df["close"], vol, tb, self.cfg["strategy"]["cvd_lookback"])
        return fz, oid, imb, div

    def _signal(self, sym: str, df_closed: pd.DataFrame):
        fz, oid, imb, div = self._alt_arrays(sym, df_closed)
        f4 = precompute_v4(df_closed, self.cfg, self.htf_mult, fz, oid, imb, div)
        side = decide_v4(f4, self.params, self.cfg, self.sessions)
        return int(side[-1]), float(f4.v3.v2.base.atr[-1])

    def _close_trade(self, sym: str, price: float, reason: str) -> None:
        pos = self.open.pop(sym)
        tr = self.bt._close(pos, price, pd.Timestamp.utcnow(), 0, reason)
        self.trades.append(tr)
        self.equity *= (1 + self.risk_frac * tr.r)
        journal("forward_close", {"symbol": sym, "exit": price, "r": round(tr.r, 4),
                                  "reason": reason, "equity": round(self.equity, 2)})
        log.info(f"CLOSE {reason.upper()} {sym} @ {price:.6f} R={tr.r:+.2f} eq={self.equity:.2f}")

    def _monitor(self, sym: str) -> None:
        if sym not in self.open:
            return
        try:
            price = float(self.ex.ticker(sym)["last"])
        except Exception as e:  # boundary
            log.warning(f"ticker {sym}: {e}")
            return
        pos = self.open[sym]
        long = pos["side"] == "long"
        if (price <= pos["sl"]) if long else (price >= pos["sl"]):
            self._close_trade(sym, pos["sl"], "sl")
        elif (price >= pos["tp"]) if long else (price <= pos["tp"]):
            self._close_trade(sym, pos["tp"], "tp")

    def _maybe_open(self, sym: str, df_closed: pd.DataFrame) -> None:
        if sym in self.open or len(self.open) >= self.max_open:
            return
        side, atr = self._signal(sym, df_closed)
        if side == 0 or atr <= 0:
            return
        price = float(self.ex.ticker(sym)["last"])
        sig = _Sig("long" if side == 1 else "short", atr)
        pos = self.bt._open(sym, sig, {"open": price}, pd.Timestamp.utcnow(), 0)
        self.open[sym] = pos
        journal("forward_open", {"symbol": sym, "side": sig.side, "entry": pos["entry"],
                                 "sl": pos["sl"], "tp": pos["tp"]})
        log.info(f"OPEN {sig.side.upper()} {sym} @ {pos['entry']:.6f} SL={pos['sl']:.6f} TP={pos['tp']:.6f}")

    def stats(self) -> dict:
        n = len(self.trades)
        if n == 0:
            return {"trades": 0, "equity": self.equity}
        rs = [t.r for t in self.trades]
        wins = [r for r in rs if r > 0]
        return {"trades": n, "win_rate": len(wins) / n * 100,
                "expectancy_r": sum(rs) / n, "equity": round(self.equity, 2),
                "open": len(self.open)}

    def on_cycle(self) -> None:
        for sym in self.symbols:
            self._monitor(sym)
            buf = self._update_buffer(sym)
            if buf is None or len(buf) < 60:
                continue
            df_closed = buf.iloc[:-1]  # bar terakhir masih terbentuk
            if df_closed.index[-1] != self.last_closed.get(sym):
                self.last_closed[sym] = df_closed.index[-1]
                self._maybe_open(sym, df_closed)

    def run(self, poll_s: int = 30) -> None:
        self.seed()
        log.info(f"=== FORWARD-TEST mode={self.settings.mode} params={self.params} ===")
        log.info("Paper-trade di data LIVE. Ctrl+C untuk berhenti. Log: logs/forward_trades.jsonl")
        while True:
            try:
                self.on_cycle()
                s = self.stats()
                if s["trades"]:
                    log.info(f"[stats] trades={s['trades']} win={s.get('win_rate',0):.0f}% "
                             f"expR={s.get('expectancy_r',0):+.3f} eq={s['equity']} open={s.get('open',0)}")
            except KeyboardInterrupt:
                log.info(f"Berhenti. Statistik akhir: {self.stats()}")
                break
            except Exception as e:  # boundary — loop tak boleh mati
                log.error(f"cycle error: {e}")
            time.sleep(poll_s)
