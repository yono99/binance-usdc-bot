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
import json as _json

from .logger import LOG_DIR, journal, log
from .news import NewsVeto
from .notify import TelegramNotifier
from .orderflow import cvd_from_series, fetch_taker
from .settings_store import RuntimeSettings, liquidation_price, load_settings
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
    use_store: bool = False        # baca pengaturan UI (runtime.json) tiap siklus

    buffers: dict = field(default_factory=dict)
    last_closed: dict = field(default_factory=dict)
    alt_raw: dict = field(default_factory=dict)
    open: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)

    def __post_init__(self):
        self.ex = Exchange(self.settings)
        self.cfg = self.settings.raw
        self.htf_mult = self.cfg["strategy"]["htf_mult"]
        self.sessions = set(self.cfg["strategy"]["sessions"]) or None
        self.max_open = self.cfg["rotate"]["max_open_positions"]
        self.bt = Backtester(self.cfg, fee_pct=self.fee, slippage_pct=self.slippage)
        self.risk_frac = self.cfg["risk"]["account_risk_pct"] / 100.0
        self.news = NewsVeto(self.settings, self.cfg)
        self.notify = TelegramNotifier()
        self.rs: RuntimeSettings | None = None
        self.balance_usd = 0.0
        self._last_cfg_balance = 0.0   # untuk deteksi saat user mengubah saldo dari UI
        self._last_news = None         # dedup histori news veto
        self._last_screen: dict = {}   # dedup histori screening per simbol
        self._base_slippage = self.slippage   # slippage market; limit (maker) = 0
        if self.use_store:
            self.rs = load_settings()
            self.symbols = self.rs.symbols or self.ex.usdc_symbols()   # kosong = semua USDC
            self.params = self.rs.params()
            self.tf = self.rs.timeframe()
            self.balance_usd = self.rs.balance_usd
            self._last_cfg_balance = self.rs.balance_usd
            self._restore_state()           # pulihkan saldo+posisi hidup dari SQLite (tahan-restart)
        else:
            self.tf = self.cfg["market"]["timeframe"]
        self.bt.sl_mult = self.params["sl_atr_mult"]
        self.bt.tp_mult = self.params["tp_atr_mult"]
        self.sig_cache: dict = {}                 # sinyal terakhir per simbol (utk status UI)

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
        eq = round(self.balance_usd, 2) if self.use_store else round(self.equity, 2)
        n = len(self.trades)
        if n == 0:
            return {"trades": 0, "equity": eq}
        rs = [t.r for t in self.trades]
        wins = [r for r in rs if r > 0]
        return {"trades": n, "win_rate": len(wins) / n * 100,
                "expectancy_r": sum(rs) / n, "equity": eq, "open": len(self.open)}

    # ---------- mode store (USD leverage + likuidasi, diatur dari UI) ----------

    def _apply_settings(self) -> RuntimeSettings:
        rs = load_settings()
        resolved = rs.symbols or self.ex.usdc_symbols()   # kosong = semua USDC
        if rs.timeframe() != self.tf or set(resolved) != set(self.symbols):
            self.tf = rs.timeframe()
            self.symbols = resolved
            self.buffers.clear()
            self.last_closed.clear()
            self.alt_raw.clear()
            self.seed()
        self.params = rs.params()
        self.bt.sl_mult = rs.params()["sl_atr_mult"]
        self.bt.tp_mult = rs.params()["tp_atr_mult"]
        self.max_open = rs.max_open_positions   # hot-reload dari UI
        self.fee = rs.fee_pct()                 # taker (market) / maker (limit)
        self.slippage = 0.0 if rs.order_type == "limit" else self._base_slippage
        if rs.gemini_model:                     # model Gemini pilihan UI (hot-reload) + fallback
            self.news.client.set_model(rs.gemini_model)
        # Jika user mengubah Saldo dari UI -> terapkan ke saldo hidup (tanpa restart).
        # PnL biasa tidak menyentuh _last_cfg_balance, jadi tak terdeteksi sebagai edit.
        if abs(rs.balance_usd - self._last_cfg_balance) > 1e-9:
            self.balance_usd = rs.balance_usd
            log.info(f"Saldo diubah dari UI -> ${self.balance_usd:.2f}")
        self._last_cfg_balance = rs.balance_usd
        self.rs = rs
        return rs

    # ---------- state hidup (saldo + posisi) durable di SQLite ----------

    def _restore_state(self) -> None:
        try:
            from .store import get_kv
            st = get_kv("botstate")
        except Exception as e:  # boundary
            log.warning(f"restore state gagal: {e}")
            return
        if not st:
            return
        # hanya pulihkan bila konfigurasi saldo tak diubah user sejak terakhir simpan
        if abs(st.get("cfg_balance", self._last_cfg_balance) - self._last_cfg_balance) < 1e-9:
            self.balance_usd = float(st.get("balance", self.balance_usd))
        self.open = st.get("open", {}) or {}
        if self.open:
            log.info(f"State dipulihkan dari SQLite: saldo ${self.balance_usd:.2f}, "
                     f"{len(self.open)} posisi terbuka")

    def _persist_logs(self, news_veto: bool, note: str) -> None:
        """Simpan histori news veto & screening ke SQLite, hanya saat BERUBAH
        (hindari banjir 1 baris/siklus). Boundary aman: gagal log tak ganggu bot."""
        try:
            from . import store
            if (news_veto, note) != self._last_news:
                store.log_news(news_veto, note)
                self._last_news = (news_veto, note)
            for sym in self.symbols:
                c = self.sig_cache.get(sym, {})
                cur = (c.get("side"), c.get("blocked"))
                if cur != self._last_screen.get(sym):
                    store.log_screen(sym, c.get("side"), c.get("price"),
                                     c.get("atr_pct"), c.get("blocked"))
                    self._last_screen[sym] = cur
        except Exception as e:  # boundary
            log.warning(f"persist logs gagal: {e}")

    def _persist_state(self) -> None:
        try:
            from .store import set_kv
            set_kv("botstate", {"balance": round(self.balance_usd, 6),
                                "open": self.open,
                                "cfg_balance": self._last_cfg_balance})
        except Exception as e:  # boundary
            log.warning(f"persist state gagal: {e}")

    def _open_usd(self, sym: str, side: int, atr: float, rs: RuntimeSettings) -> None:
        if self.balance_usd < rs.bet_usd:
            return
        price = float(self.ex.ticker(sym)["last"])
        is_long = side == 1
        slip = 1 + self.slippage / 100 if is_long else 1 - self.slippage / 100
        entry = price * slip
        qty = (rs.bet_usd * rs.leverage) / entry
        sl = entry - atr * self.bt.sl_mult if is_long else entry + atr * self.bt.sl_mult
        if rs.target_profit_pct > 0:
            tp = entry * (1 + rs.target_profit_pct / 100) if is_long else entry * (1 - rs.target_profit_pct / 100)
        else:
            tp = entry + atr * self.bt.tp_mult if is_long else entry - atr * self.bt.tp_mult
        liq = liquidation_price(entry, is_long, rs.liquidation_frac())
        self.open[sym] = {"side": "long" if is_long else "short", "entry": entry, "qty": qty,
                          "sl": sl, "tp": tp, "liq": liq, "bet": rs.bet_usd}
        journal("forward_open", {"symbol": sym, "side": self.open[sym]["side"], "entry": entry,
                                 "sl": sl, "tp": tp, "liq": liq, "lev": rs.leverage, "bet": rs.bet_usd})
        log.info(f"OPEN {self.open[sym]['side'].upper()} {sym} x{rs.leverage} bet=${rs.bet_usd} "
                 f"@ {entry:.4f} SL={sl:.4f} TP={tp:.4f} LIQ={liq:.4f}")
        self.notify.send(
            f"🟢 <b>OPEN {self.open[sym]['side'].upper()}</b> {sym} x{rs.leverage}\n"
            f"Entry {entry:.4f} · SL {sl:.4f} · TP {tp:.4f}\n"
            f"LIQ {liq:.4f} · bet ${rs.bet_usd}")

    def _close_usd(self, sym: str, price: float, reason: str) -> None:
        pos = self.open.pop(sym)
        is_long = pos["side"] == "long"
        exit_fill = price * (1 - self.slippage / 100 if is_long else 1 + self.slippage / 100)
        if reason == "liq":
            pnl = -pos["bet"]                       # rugi seluruh margin
        else:
            move = (exit_fill - pos["entry"]) if is_long else (pos["entry"] - exit_fill)
            fee = self.fee / 100 * (pos["entry"] + exit_fill) * pos["qty"]
            pnl = max(pos["qty"] * move - fee, -pos["bet"])  # rugi maksimum = margin
        self.balance_usd += pnl
        r = pnl / pos["bet"] if pos["bet"] else 0.0
        self.trades.append(namedtuple("T", ["r"])(r))
        journal("forward_close", {"symbol": sym, "exit": round(exit_fill, 6), "reason": reason,
                                  "pnl_usd": round(pnl, 4), "r": round(r, 4),
                                  "equity": round(self.balance_usd, 2)})
        log.info(f"CLOSE {reason.upper()} {sym} pnl=${pnl:+.2f} bal=${self.balance_usd:.2f}")
        icon = {"liq": "💥 <b>LIKUIDASI</b>", "sl": "🛑 SL", "tp": "✅ TP",
                "manual": "✋ CLOSE", "eod": "⏹ EOD"}.get(reason, reason)
        self.notify.send(f"{icon} {sym}\nPnL ${pnl:+.2f} · R {r:+.2f} · saldo ${self.balance_usd:.2f}")

    def _monitor_usd(self, sym: str) -> None:
        if sym not in self.open:
            return
        try:
            price = float(self.ex.ticker(sym)["last"])
        except Exception as e:  # boundary
            log.warning(f"ticker {sym}: {e}")
            return
        pos = self.open[sym]
        long = pos["side"] == "long"
        if (price <= pos["liq"]) if long else (price >= pos["liq"]):
            self._close_usd(sym, pos["liq"], "liq")           # likuidasi lebih dulu
        elif (price <= pos["sl"]) if long else (price >= pos["sl"]):
            self._close_usd(sym, pos["sl"], "sl")
        elif (price >= pos["tp"]) if long else (price <= pos["tp"]):
            self._close_usd(sym, pos["tp"], "tp")

    def on_cycle(self) -> None:
        if self.use_store:
            return self._on_cycle_store()
        news_veto, note = self.news.check()
        if news_veto:
            log.info(f"News veto aktif ({note}) — tidak buka posisi baru siklus ini")
        for sym in self.symbols:
            self._monitor(sym)
            buf = self._update_buffer(sym)
            if buf is None or len(buf) < 60:
                continue
            df_closed = buf.iloc[:-1]  # bar terakhir masih terbentuk
            if df_closed.index[-1] != self.last_closed.get(sym):
                self.last_closed[sym] = df_closed.index[-1]
                if not news_veto:
                    self._maybe_open(sym, df_closed)

    def _process_close_requests(self) -> None:
        """Tutup paksa posisi yang diminta dari UI (logs/close_requests.json)."""
        p = LOG_DIR / "close_requests.json"
        if not p.exists():
            return
        try:
            reqs = _json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return
        if not reqs:
            return
        targets = list(self.open.keys()) if "*" in reqs else reqs  # "*" = tutup semua
        remaining = []
        for sym in targets:
            if sym in self.open:
                try:
                    price = float(self.ex.ticker(sym)["last"])
                except Exception:  # boundary — coba lagi siklus berikutnya
                    remaining.append(sym)
                    continue
                self._close_usd(sym, price, "manual")
        try:
            p.write_text(_json.dumps(remaining), encoding="utf-8")
        except Exception as e:  # boundary
            log.warning(f"tulis close_requests gagal: {e}")

    def _on_cycle_store(self) -> None:
        rs = self._apply_settings()
        self._process_close_requests()
        news_veto, note = (self.news.check() if rs.enabled else (False, "off"))
        if news_veto:
            log.info(f"News veto aktif ({note}) — tidak buka posisi baru siklus ini")
        label = {1: "LONG", -1: "SHORT", 0: "skip"}
        for sym in self.symbols:
            self._monitor_usd(sym)
            buf = self._update_buffer(sym)
            c = self.sig_cache.setdefault(sym, {})
            if buf is None or len(buf) < 60:
                c["blocked"] = "data kurang"
                continue
            df_closed = buf.iloc[:-1]
            c["price"] = float(df_closed["close"].iloc[-1])
            if df_closed.index[-1] != self.last_closed.get(sym):
                self.last_closed[sym] = df_closed.index[-1]
                side, atr = self._signal(sym, df_closed)
                c["side"] = label[side]
                c["atr_pct"] = round(atr / c["price"] * 100, 3) if c["price"] else None
                blocked = None
                if not rs.enabled:
                    blocked = "bot OFF"
                elif news_veto:
                    blocked = f"news veto ({note})"
                elif sym in self.open:
                    blocked = "sudah ada posisi"
                elif len(self.open) >= self.max_open:
                    blocked = "slot penuh"
                elif side == 0:
                    blocked = "tak ada sinyal"
                elif atr <= 0:
                    blocked = "ATR nol"
                c["blocked"] = blocked
                if blocked is None:
                    self._open_usd(sym, side, atr, rs)
                    c["blocked"] = "→ posisi dibuka"
        self._write_status(rs, news_veto, note)
        self._persist_state()        # saldo+posisi durable -> tahan restart
        self._persist_logs(news_veto, note)   # histori news + screening (on-change)

    def _write_status(self, rs, news_active: bool, news_note: str) -> None:
        syms = []
        for sym in self.symbols:
            c = self.sig_cache.get(sym, {})
            pos = self.open.get(sym)
            price = c.get("price")
            pos_view = None
            if pos and price:
                d = price - pos["entry"] if pos["side"] == "long" else pos["entry"] - price
                pos_view = {"side": pos["side"], "entry": round(pos["entry"], 6),
                            "sl": round(pos["sl"], 6), "tp": round(pos["tp"], 6),
                            "liq": round(pos["liq"], 6), "pnl_usd": round(pos["qty"] * d, 4)}
            syms.append({"symbol": sym, "price": price, "atr_pct": c.get("atr_pct"),
                         "signal": c.get("side", "-"), "in_position": bool(pos),
                         "blocked": c.get("blocked"), "position": pos_view})
        status = {
            "ts": pd.Timestamp.utcnow().isoformat(),
            "mode": self.settings.mode,
            "enabled": rs.enabled,
            "technique": rs.technique,
            "timeframe": self.tf,
            "leverage": rs.leverage,
            "bet_usd": rs.bet_usd,
            "balance_usd": round(self.balance_usd, 2),
            "open_count": len(self.open),
            "max_open": self.max_open,
            "poll_seconds": rs.poll_seconds,
            "order_type": rs.order_type,
            "fee_pct": rs.fee_pct(),
            "news_veto": {"active": news_active, "note": news_note},
            "symbols": syms,
        }
        try:
            from .store import set_kv
            set_kv("status", status)
        except Exception as e:  # boundary
            log.warning(f"tulis status gagal: {e}")

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
            # interval screening hot-reload dari UI bila pakai store
            sleep_s = self.rs.poll_seconds if (self.use_store and self.rs) else poll_s
            time.sleep(sleep_s)
