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

from . import decision_log
from .altdata import align, fetch_funding, fetch_oi, funding_zscore, oi_delta
from .backtest import Backtester, Trade, fetch_history
from .config import Settings
from .exchange import Exchange
import json as _json

from .lessons import LessonsEngine
from .logger import LOG_DIR, journal, log
from .memory import AgentMemory
from .news import NewsVeto
from . import vrp
from . import mtf
from .planner import SessionPlanner, default_plan
from .react_agent import ReactAgent
from .signals import Signal
from .notify import TelegramNotifier
from .orderflow import cvd_from_series, fetch_taker
from .settings_store import RuntimeSettings, liquidation_price, load_settings
from .strategy_lab import decide_v4, precompute_v4
from .gemini_client import all_keys_dead as _all_keys_dead

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
    pin_mode: bool = False         # KUNCI ke settings.mode: baca bucket sendiri, tak ikut
                                   # mode aktif UI & tak pernah switch — untuk menjalankan
                                   # beberapa proses bot paralel (satu per mode)

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
        _r = self.cfg["risk"]
        self.daily_max_loss_pct = float(_r.get("daily_max_loss_pct", 0) or 0)
        self.daily_max_trades = int(_r.get("daily_max_trades", 0) or 0)
        self.corr_threshold = float(_r.get("corr_threshold", 0) or 0)
        self.corr_lookback = int(_r.get("corr_lookback", 0) or 0)
        from .logger import set_journal_mode
        set_journal_mode(self.settings.mode)             # jurnal per-mode
        decision_log.set_mode(self.settings.mode)        # decision log per-mode
        self._state_key = f"botstate_{self.settings.mode}"  # state per-mode
        self.news = NewsVeto(self.settings, self.cfg)
        self._news_base = self.news.enabled     # kemampuan dasar (Gemini+config); UI bisa mematikan
        self.vrp = vrp.VRPBrake(self.ex, self.cfg)   # rem-VRP (shadow: catat, tak blokir)
        self.mtf = mtf.MTFAgree(self.cfg)            # kesepakatan multi-TF (shadow: catat, tak blokir)
        # ReAct agent: gerbang entry AKTIF utk teknik non-gemini (Gemini mati → fallback ikut sinyal).
        self.react = ReactAgent(self.settings, self.cfg)
        self.lessons = LessonsEngine(self.settings, self.cfg)
        self.agent_memory = AgentMemory()          # memori lintas-tick (point 4)
        self.ab_shadow = bool(self.cfg.get("agent", {}).get("ab_shadow", False))  # A/B: tak blokir
        _ag = self.cfg.get("agent", {})
        full = bool(_ag.get("full_auto", False))   # SATU saklar: nyalakan seluruh stack otonom
        self.tool_loop = bool(_ag.get("tool_loop", False)) or full       # nalar+panggil tool
        self.tool_max_iters = int(_ag.get("tool_max_iters", 4))
        self.autonomous = bool(_ag.get("autonomous", False)) or full     # kelola portofolio
        self._autonomous_interval = int(_ag.get("autonomous_interval_s", 300))
        self._last_portfolio = 0.0
        # Planner tipis: tujuan sesi (stance/bias/kuota) → enforce di gerbang entry.
        self.use_planner = bool(_ag.get("planner", False)) or full
        self.planner = SessionPlanner(self.settings, self.cfg)
        self._plan_horizon_h = int(_ag.get("plan_horizon_h", 6))
        self._session_plan = default_plan(self.daily_max_trades or 1_000_000)
        self._session_trades = 0
        self._plan_day = None
        self._last_plan_ts = 0.0
        if full:
            log.info("AGENT full-auto AKTIF — tool-loop + otonomi portofolio + planner menyala.")
        self.notify = TelegramNotifier()
        self.rs: RuntimeSettings | None = None
        self.balance_usd = 0.0
        self._last_cfg_balance = 0.0   # untuk deteksi saat user mengubah saldo dari UI
        self._last_news = None         # dedup histori news veto
        self._last_screen: dict = {}   # dedup histori screening per simbol
        self._base_slippage = self.slippage   # slippage market; limit (maker) = 0
        # state circuit breaker harian (reset tiap hari UTC) — default sebelum restore
        self._day = pd.Timestamp.utcnow().date()
        self._day_pnl = 0.0
        self._day_trades = 0
        self._day_start_balance = 0.0
        self._eff_mode = self.settings.mode          # mode efektif berjalan
        self.live = (self.settings.mode == "live")   # True = order UANG NYATA
        # Kill-switch drawdown TOTAL (P1 tujuan compounding): puncak saldo per mode,
        # kunci permanen sampai reset manual — CB harian saja tak menangkap bleed pelan.
        self._peak_balance = 0.0
        self._dd_lock = False
        self._dd_reason = ""
        if self.use_store:
            self.rs = load_settings(self.settings.mode if self.pin_mode else None)
            self.params = self.rs.params()
            self.tf = self.rs.timeframe()          # _screened() di bawah butuh self.tf siap dulu
            # kosong = semua settle config → SARING lewat screener (Layer 2) SEBELUM seed, jangan
            # seed universe RAW (ratusan pair) — _apply_settings() sudah begini di siklus hot-reload,
            # init harus cermin yang sama agar startup tak mencoba seed seluruh universe mentah.
            resolved = self.rs.symbols or self.ex.perp_symbols(
                tuple(self.cfg["market"].get("settles", ["USDC"])))
            self.symbols = self._screened(resolved) if not self.rs.symbols else resolved
            self.balance_usd = self.rs.balance_usd
            self._last_cfg_balance = self.rs.balance_usd
            self._day_start_balance = self.balance_usd
            self._restore_state()           # pulihkan saldo+posisi+state-harian dari SQLite (tahan-restart)
            # Posisi restored WAJIB dipantau sejak siklus pertama, walau simbolnya gugur
            # screening (lihat _apply_settings — union yg sama, cegah orphan sejak boot).
            self.symbols = sorted(set(self.symbols) | set(self.open.keys()))
        else:
            self.tf = self.cfg["market"]["timeframe"]
        self.bt.sl_mult = self.params["sl_atr_mult"]
        self.bt.tp_mult = self.params["tp_atr_mult"]
        self.sig_cache: dict = {}                 # sinyal terakhir per simbol (utk status UI)
        # Gemini praktisi trader (teknik "gemini") — diaktifkan via _apply_settings
        self.gtrader = None
        self.use_gemini_trader = False
        self._gem_closes = 0                      # pemicu refleksi berkala
        self._calib_drifting = False              # Phase 6: status drift (anti-spam alarm)
        self._last_news_note = ""
        self._last_manage: dict = {}              # throttle kelola-posisi per simbol
        self._manage_interval = 60                # detik minimum antar review posisi (~1 menit)
        self._min_hold_s = 300                    # GRACE anti-whipsaw: manajer tak exit sblm ditahan segini
        _gcfg = self.cfg.get("gemini", {})        # pemicu give-back menuju TP (knob kalibrasi)
        self._giveback_tp_frac = float(_gcfg.get("giveback_tp_frac", 0.5))
        self._giveback_margin = float(_gcfg.get("giveback_margin", 0.2))
        # Kuota panggilan Gemini per-SIKLUS (bukan per-simbol): universe besar + _last_decide
        # kosong saat boot/restart bikin SEMUA simbol jadi "bebas panggil" serentak dlm satu
        # loop sekuensial → ledakan 429 + _monitor_usd (SL/TP keras) simbol lain ikut tertunda.
        # DINAMIS (_recompute_decide_budget): budget = minimum agar SEMUA simbol dapat giliran
        # sekali per _decide_interval, dibatasi cap wall-clock (budget × latensi < poll_seconds).
        self._gemini_decide_cap = int(_gcfg.get("gemini_decide_cap", 24))  # batas serial: ~cap×2dtk < poll
        self._gemini_decide_budget = self._gemini_decide_cap
        self._gemini_decide_used = 0              # reset tiap awal siklus (_on_cycle_store)
        self._last_decide: dict = {}              # throttle keputusan-entry Gemini per simbol
        self._decide_interval = 180               # detik min antar keputusan entry (~3 mnt) — hemat token
        # Cache harga saat Gemini terakhir memutuskan: {sym → (price, decision)}.
        # Skip Gemini jika harga belum bergerak melewati threshold (hemat RPD saat pasar stagnan).
        self._decide_price_cache: dict[str, tuple[float, dict]] = {}
        # Anti-spam log peringatan RPD habis (log sekali per jam maksimal)
        self._last_rpd_warn = 0.0
        # KESELAMATAN: Gemini-trader boleh order LIVE hanya bila di-set eksplisit di config.
        self._allow_live_gemini = bool(self.cfg.get("gemini", {}).get("allow_live_trader", False))

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

    def _btc_lead(self) -> dict:
        """Gerak BTC (pemimpin pasar) pada bar TERTUTUP: 1bar & 3bar % + arah.
        Alt ber-beta lebih tinggi → gerak turun BTC sering diperbesar/diperpanjang di alt."""
        buf = self.buffers.get("BTC/USDC:USDC")
        if buf is None or len(buf) < 5:
            return {}
        c = buf["close"]
        last, prev, prev3 = float(c.iloc[-2]), float(c.iloc[-3]), float(c.iloc[-5])
        r1 = (last / prev - 1) * 100 if prev else 0.0
        r3 = (last / prev3 - 1) * 100 if prev3 else 0.0
        return {"ret_1bar_pct": round(r1, 3), "ret_3bar_pct": round(r3, 3),
                "dir": 1 if r1 > 0 else (-1 if r1 < 0 else 0)}

    def _react_gate(self, sym: str, side: int, atr: float, df_closed, price: float):
        """Konsultasi ReactAgent sbg gerbang entry (teknik NON-gemini). OBSERVE pakai
        alt-data nyata (funding/OI/CVD) + regime + pelajaran. Kembalikan (permitted, action,
        reasoning). LLM mati/timeout → fallback IZINKAN (ikut sinyal) — tak pernah blokir."""
        from .gemini_trader import _market_summary
        side_str = "long" if side == 1 else ("short" if side == -1 else "skip")
        try:
            fz, oid, imb, _div = self._alt_arrays(sym, df_closed)
            alt = {"funding": round(float(fz[-1]), 3), "oi_change": round(float(oid[-1]), 4),
                   "cvd": round(float(imb[-1]), 3)}
        except Exception:  # boundary — alt opsional
            alt = {}
        try:
            regime = _market_summary(df_closed, self.cfg)["regime"]
        except Exception:
            regime = "unknown"
        sig = Signal(sym, side_str, 0.0, price, atr, "v4",
                     long_score=(1.0 if side == 1 else 0.0),
                     short_score=(1.0 if side == -1 else 0.0), regime=regime)
        one_r = self.balance_usd * self.risk_frac
        daily_pnl_r = self._day_pnl / one_r if one_r else 0.0
        kw = dict(regime=regime, alt=alt, n_positions=len(self.open),
                  max_positions=self.max_open, daily_pnl_r=daily_pnl_r,
                  lessons=self.lessons.recent(10), shadow=self.ab_shadow,
                  memory=self.agent_memory,     # ingat observasi/keputusan lintas-tick
                  btc_lead=self._btc_lead())    # dominansi BTC (alt ber-beta lebih tinggi)
        if self.tool_loop:                      # agent OTONOM: nalar+panggil tool iteratif
            from .tools import ToolContext, build_tools
            ctx = ToolContext(ex=self.ex, open_positions=self.open, buffers=self.buffers,
                              cfg=self.cfg, lessons=self.lessons)
            dec = self.react.decide_with_tools(sig, build_tools(ctx),
                                               max_iters=self.tool_max_iters, **kw)
        else:
            dec = self.react.decide(sig, **kw)
        # Shadow (A/B): permits() True (eksekusi ikut rules); verdict asli ada di react_action.
        return dec.permits(sig), (dec.react_action or dec.action), dec.reasoning

    def _react_link(self, sym: str, outcome: str, outcome_r: float,
                    extras: dict | None = None) -> None:
        """Tautkan outcome ke keputusan ReAct terakhir (decision_log) + update pelajaran.
        Dipakai jalur paper & live. Boundary: pencatatan agen tak boleh ganggu trading."""
        try:
            did = decision_log.record_outcome(sym, outcome, outcome_r, extras=extras)
            if did:                                  # hanya entry yang lewat gerbang ReAct
                row = decision_log.get(did)
                if row:
                    if row.get("lesson_triggered"):
                        self.lessons.record_trigger(row["lesson_triggered"], correct=outcome_r > 0)
                    self.lessons.derive_from_trade(row)
                self.lessons.score_and_retire()
        except Exception as e:  # boundary
            log.warning(f"react link {sym} gagal: {e}")

    def _react_settle(self, sym: str, pos: dict, pnl: float, reason: str) -> None:
        """Paper: R dari jarak SL (akuntansi identik backtest)."""
        risk0 = abs(pos["entry"] - pos["sl"]) * pos["qty"]
        outcome_r = pnl / risk0 if risk0 else 0.0
        outcome = {"liq": "LIQ", "sl": "SL_HIT", "tp": "TP_HIT"}.get(reason, "CLOSE")
        self._react_link(sym, outcome, outcome_r,
                         extras={"mae_pct": round(pos.get("mae_pct", 0.0), 3),
                                 "mfe_pct": round(pos.get("mfe_pct", 0.0), 3)})

    def _last_price(self, sym: str) -> float | None:
        buf = self.buffers.get(sym)
        if buf is not None and len(buf):
            return float(buf["close"].iloc[-1])
        try:
            return float(self.ex.ticker(sym)["last"])
        except Exception:  # boundary
            return None

    def _tighten_to_breakeven(self) -> int:
        """REDUCE_RISK: pindahkan SL ke entry (breakeven) untuk posisi yang sedang PROFIT.
        Hanya mengetatkan (kunci no-loss); tak pernah melonggarkan. Kembalikan jumlah."""
        n = 0
        for sym, pos in self.open.items():
            price = self._last_price(sym)
            if not price:
                continue
            long = pos["side"] == "long"
            in_profit = price > pos["entry"] if long else price < pos["entry"]
            be = pos["entry"]
            tighter = be > pos["sl"] if long else be < pos["sl"]
            if in_profit and tighter:
                pos["sl"] = be
                n += 1
        return n

    def _exposure_frac(self) -> float:
        if self.balance_usd <= 0:
            return 1.0
        return sum((p.get("bet") or 0) for p in self.open.values()) / self.balance_usd

    def _refresh_plan(self, rs) -> None:
        """POINT planner — bentuk rencana sesi (stance/bias/kuota) berkala/awal-hari."""
        if not self.use_planner or not rs.enabled:
            return
        now = time.time()
        day = pd.Timestamp.utcnow().date()
        due = (day != self._plan_day) or (now - self._last_plan_ts > self._plan_horizon_h * 3600)
        if not due:
            return
        self._plan_day, self._last_plan_ts, self._session_trades = day, now, 0
        hard = self.daily_max_trades or 1_000_000
        ctx = {"balance_usd": round(self.balance_usd, 2), "day_pnl_usd": round(self._day_pnl, 2),
               "bet_usd": rs.bet_usd, "leverage": rs.leverage,   # sizing: margin kecil × leverage
               "portfolio": self._portfolio_view(), "news": self._last_news_note,
               "lessons": self.lessons.recent(5)}
        self._session_plan = self.planner.make_plan(ctx, hard_max_trades=hard)
        p = self._session_plan
        log.info(f"SESSION PLAN — stance={p['stance']} bias={p['bias']} "
                 f"max_trades={p['max_new_trades']} max_expo={p['max_exposure_frac']}: {p['reasoning']}")
        decision_log.append({
            "ts": p["ts"], "id": __import__("uuid").uuid4().hex, "symbol": "*PLAN*",
            "action": f"PLAN_{p['stance'].upper()}", "reasoning": p["reasoning"],
            "confidence": 0.0, "key_risks": [], "lesson_triggered": "",
            "source": "LLM" if self.planner.enabled else "LLM_DISABLED",
            "signal_scores": {}, "react_action": "", "market_state": {"plan": p},
            "outcome": None, "outcome_r": None, "filled_at_close": False})

    def _agent_portfolio_review(self, rs) -> None:
        """POINT 2 — agen otonom meninjau SEMUA posisi terbuka berkala (REDUCE_RISK/FLAT).
        Teknik gemini punya jalur kelola sendiri (_gemini_manage) → dilewati di sini."""
        if not self.autonomous or self.use_gemini_trader or not rs.enabled or not self.open:
            return
        now = time.time()
        if now - self._last_portfolio < self._autonomous_interval:
            return
        self._last_portfolio = now
        one_r = self.balance_usd * self.risk_frac
        dec = self.react.manage_portfolio(self._portfolio_view(),
                                          daily_pnl_r=(self._day_pnl / one_r if one_r else 0.0),
                                          lessons=self.lessons.recent(5))
        act = dec.get("action")
        if act == "FLAT":
            if self.live and not self._allow_live_gemini:    # destruktif di LIVE → butuh izin
                log.warning("AGENT FLAT diblokir di LIVE (set gemini.allow_live_trader: true)")
                return
            for sym in list(self.open):
                price = self._last_price(sym)
                if price:
                    self._close_usd(sym, price, "agent_flat")
            log.info(f"AGENT FLAT — tutup semua posisi: {dec.get('reasoning')}")
            self.notify.send(f"🤖 <b>AGENT FLAT</b> — {dec.get('reasoning')}")
        elif act == "REDUCE_RISK":
            n = self._tighten_to_breakeven()
            if n:
                log.info(f"AGENT REDUCE_RISK — {n} stop → breakeven: {dec.get('reasoning')}")

    def _portfolio_view(self) -> dict:
        """Snapshot SEMUA posisi terbuka + eksposur — konteks korelasi/risiko untuk Gemini."""
        positions = [{"symbol": s, "side": p["side"], "entry": round(p["entry"], 6),
                      "bet": p.get("bet")} for s, p in self.open.items()]
        return {"positions": positions, "count": len(positions),
                "exposure_usd": round(sum(p.get("bet", 0) or 0 for p in self.open.values()), 2),
                "balance_usd": round(self.balance_usd, 2), "max_open": self.max_open}

    def _gemini_manage(self, sym: str, df_closed: pd.DataFrame) -> None:
        """Review posisi terbuka Gemini (~1 menit). Hanya boleh KURANGI risiko."""
        pos = self.open.get(sym)
        if not pos or not pos.get("gdecision") or self.gtrader is None:
            return
        # GRACE anti-whipsaw: jangan izinkan exit-LLM sebelum posisi ditahan ≥ _min_hold_s.
        # Bukti: gemini_exit dini sering memotong posisi yg lalu PULIH di atas entry. SL/TP di
        # _monitor_usd/exit-sweep TETAP jalan selama grace → proteksi keras utuh. Return SEBELUM
        # panggil LLM → hemat token juga. opened_ts hilang (live-reconcile) → fail-open (tak blokir).
        opened = pos.get("opened_ts")
        if opened and self._min_hold_s > 0:
            try:
                held = (pd.Timestamp.utcnow() - pd.Timestamp(opened)).total_seconds()
            except Exception:  # boundary — gagal parse → anggap sudah lewat grace
                held = self._min_hold_s
            if held < self._min_hold_s:
                return
        try:
            price = float(self.ex.ticker(sym)["last"])
        except Exception as e:  # boundary
            log.warning(f"manage ticker {sym}: {e}")
            return
        from .gemini_trader import _market_summary
        is_long = pos["side"] == "long"
        risk = abs(pos["entry"] - pos["sl"]) or 1e-9
        unreal_r = ((price - pos["entry"]) if is_long else (pos["entry"] - price)) / risk
        prog = self._tp_progress(pos, price)            # fraksi perjalanan ke TP (bisa <0)
        if prog is not None:                            # jaga puncak juga di jalur live (monitor exit lebih dulu di paper)
            pos["peak_tp_prog"] = max(pos.get("peak_tp_prog", prog), prog)
        ctx = {
            "position": {"symbol": sym, "side": pos["side"], "entry": round(pos["entry"], 6),
                         "sl": round(pos["sl"], 6), "tp": round(pos["tp"], 6),
                         "mark": round(price, 6), "unrealized_r": round(unreal_r, 2),
                         "setup": pos.get("setup"),
                         "tp_progress_pct": round(prog * 100, 1) if prog is not None else None,
                         "peak_tp_progress_pct": round(pos.get("peak_tp_prog", 0.0) * 100, 1),
                         "alert": pos.pop("giveback_note", None)},   # sinyal give-back (sekali tampil)
            "market": _market_summary(df_closed, self.cfg),
            "portfolio": self._portfolio_view(),
        }
        act = self.gtrader.manage(ctx)
        self._apply_manage(sym, act, price)

    @staticmethod
    def _tp_progress(pos: dict, price: float) -> float | None:
        """Fraksi perjalanan harga menuju TP: 0=entry, 1=TP, <0 bila underwater. None bila tak ada TP."""
        tp, e = pos.get("tp"), pos.get("entry")
        if not tp or not e:
            return None
        dist = abs(tp - e) or 1e-9
        fav = (price - e) if pos["side"] == "long" else (e - price)
        return fav / dist

    def _apply_manage(self, sym: str, act: dict, price: float) -> None:
        from .gemini_trader import valid_tighten
        pos = self.open.get(sym)
        if not pos:
            return
        action = act.get("action")
        if action == "exit":
            log.info(f"Gemini EXIT {sym} @ {price:.6f} — {act.get('reason', '')[:80]}")
            self._close_usd(sym, price, "gemini_exit")
        elif action == "tighten_stop" and not self.live:   # live = exit-only (jaga proteksi)
            if valid_tighten(pos["side"], pos["sl"], act.get("new_sl"), price):
                old = pos["sl"]
                pos["sl"] = round(float(act["new_sl"]), 6)
                log.info(f"Gemini tighten SL {sym}: {old:.6f} → {pos['sl']:.6f}")

    @staticmethod
    def _regime_stamp(df, cfg) -> dict:
        """Stempel regime pasar saat OPEN → untuk laporan EV per-regime (regime_ev.py).
        Non-throw. Bentuk dict mengikuti pola vrp.stamp() agar mudah di-**spread.
        HANYA observasi (tak mengubah pnl/keputusan)."""
        try:
            from .gemini_trader import _market_summary
            return {"regime": _market_summary(df, cfg)["regime"]}
        except Exception:  # boundary — regime opsional, jangan pernah blokir trading
            return {"regime": "unknown"}

    def _close_trade(self, sym: str, price: float, reason: str) -> None:
        pos = self.open.pop(sym)
        tr = self.bt._close(pos, price, pd.Timestamp.utcnow(), 0, reason)
        self.trades.append(tr)
        vrp.log_close(sym, pos, tr.r, mode=self.settings.mode)  # shadow log ber-mode
        self.equity *= (1 + self.risk_frac * tr.r)
        journal("forward_close", {"symbol": sym, "exit": price, "r": round(tr.r, 4),
                                  "reason": reason, "regime": pos.get("regime", "unknown"),
                                  "equity": round(self.equity, 2)})
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
        pos.update(self.vrp.stamp())            # stempel regime VRP saat open (A/B shadow)
        pos.update(self._regime_stamp(df_closed, self.cfg))  # stempel regime → laporan EV
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

    @staticmethod
    def _agent_posture(cfg_agent: dict, rs) -> dict:
        """Resolusi flag agent: config.yaml OR toggle UI; full_auto → semua. Manager-mode
        (Jalan A) MENG-OVERRIDE jadi manajer disiplin: tool_loop OFF, autonomous+planner ON,
        arah dari RULES (bukan gemini). PURE → mudah diuji."""
        full = bool(cfg_agent.get("full_auto", False)) or rs.agent_full_auto
        tool_loop = bool(cfg_agent.get("tool_loop", False)) or rs.agent_tool_loop or full
        autonomous = bool(cfg_agent.get("autonomous", False)) or rs.agent_autonomous or full
        use_planner = bool(cfg_agent.get("planner", False)) or rs.agent_planner or full
        ab_shadow = bool(cfg_agent.get("ab_shadow", False)) or rs.agent_ab_shadow
        use_gemini_trader = (rs.technique == "gemini")
        if rs.agent_manager_mode:                    # JALAN A: manajer disiplin, frugal
            tool_loop, autonomous, use_planner, use_gemini_trader = False, True, True, False
        return {"tool_loop": tool_loop, "autonomous": autonomous, "use_planner": use_planner,
                "ab_shadow": ab_shadow, "use_gemini_trader": use_gemini_trader}

    def _screened(self, base: list[str]) -> list[str]:
        """Layer 2 utk jalur forward (dulu hanya di engine.py): saring universe
        dgn 4 metrik screener (volume 24h, spread, ATR min/maks) sebelum dipakai.
        Biaya per trade adalah musuh utama akun mikro — pair spread-lebar (CRV/
        BOME ~10-15bps terukur) membocorkan tiap round-trip. Cache 15 menit
        (universe stabil; re-seed buffer mahal). Fail-open: screener meledak
        (API down) → pakai universe apa adanya, jangan matikan bot krn infra."""
        import time as _time
        from .screener import screen
        key = tuple(sorted(base))
        c = getattr(self, "_screen_cache", None)
        if c and c[0] == key and _time.time() - c[1] < c[3]:
            return c[2]
        try:
            todo = list(base)
            if len(todo) > 80:                    # universe raksasa (USDT+USDC ±800)
                from .screener import prefilter_volume
                todo = prefilter_volume(self.ex, todo,
                                        self.cfg["screener"]["min_quote_volume_24h"])
            passed = screen(self.ex, todo, self.cfg, self.tf)
            from .screener import dedup_prefer_usdc
            passed = dedup_prefer_usdc(passed)    # anti eksposur-dobel base sama
        except Exception as e:  # boundary — infra gagal ≠ bot mati
            log.warning(f"screener gagal ({e}) — pakai universe tanpa saring")
            return list(base)
        # kosong = tak ada yang lolos ambang SAAT INI (pasar sepi) → hormati
        # (bot idle), cache pendek supaya cepat pulih saat pasar bangun.
        ttl = 900 if passed else 180
        self._screen_cache = (key, _time.time(), passed, ttl)
        return passed

    def _apply_funding_sim(self) -> None:
        """P3: simulasi biaya FUNDING di PAPER (di live, exchange memotongnya
        sendiri dari saldo — jangan dobel). Posisi yang menginap melewati jam
        settlement (tiap 8 jam: 00/08/16 UTC) membayar/menerima funding; tanpa
        simulasi ini expectancy paper menggelembung, padahal dialah gerbang
        compounding (addendum #2 registri). Biaya diakru per-posisi
        (pos['funding_paid'], >0 = kita membayar) dan dipotong saat _close_usd
        → masuk ke R/expectancy, bukan sekadar mengurangi saldo diam-diam."""
        if self.live or not self.open:
            return
        epoch = int(time.time() // 28800)            # jendela 8 jam sejak epoch UTC
        last = getattr(self, "_fund_epoch", None)
        self._fund_epoch = epoch
        if last is None or epoch <= last:            # start: jangan tagih mundur
            return
        crossings = epoch - last
        for sym, pos in self.open.items():
            try:
                fr = self.ex.client.fetch_funding_rate(sym)
                rate = float(fr.get("fundingRate") or 0.0)
                price = float(self.ex.ticker(sym)["last"])
            except Exception as e:  # boundary — gagal fetch ≠ ganggu siklus
                log.warning(f"funding sim {sym} gagal: {e}")
                continue
            sign = 1.0 if pos["side"] == "long" else -1.0
            cost = sign * rate * pos["qty"] * price * crossings
            if abs(cost) > 1e-12:
                pos["funding_paid"] = pos.get("funding_paid", 0.0) + cost
                log.info(f"funding sim {sym}: {'bayar' if cost > 0 else 'terima'} "
                         f"${abs(cost):.4f} (rate {rate:+.5%} × {crossings}×)")

    @staticmethod
    def _sl_floor(entry: float, is_long: bool, sl: float, atr_val: float,
                  last_range: float, k_atr: float = 1.75, k_range: float = 0.5) -> float:
        """PURE — Fix A: jarak SL MINIMUM = max(k_atr×ATR, k_range×range candle
        tertutup terakhir). ATR(14) Wilder telat bereaksi thd candle raksasa,
        padahal sinyal momentum menyala TEPAT sesudahnya → SL 1.5×ATR-lama lebih
        sempit dari retrace wajar candle itu → tersambar lalu harga lanjut tanpa
        kita. SL yang sudah lebih lebar dari lantai TIDAK disentuh.

        k_atr=1.75 DIKALIBRASI DATA (sl_calibrate.py, 1 thn 15m × 11 pair,
        ~21rb pemenang/pair, hasil konsisten q80 1.70–1.81): pemenang (MFE≥
        2.5×ATR) perlu ruang ~1.76×ATR agar ~80% selamat; SL 1.5×ATR lama
        hanya menyelamatkan ~75%. Konsekuensi R:R 2.5/1.75 → breakeven
        winrate 41% (sebelumnya 37.5%) — dibayar oleh lebih banyak pemenang
        yang hidup. data/sl_calibration.json = bukti; ubah HANYA dgn data."""
        dist = max(k_atr * atr_val, k_range * last_range)
        if dist <= 0:
            return sl
        return min(sl, entry - dist) if is_long else max(sl, entry + dist)

    @staticmethod
    def _dd_check(peak: float, balance: float, max_dd_pct: float) -> tuple[bool, float]:
        """PURE. (tembus_ambang, drawdown_pct dari puncak). max_dd_pct<=0 = nonaktif."""
        if peak <= 0 or max_dd_pct <= 0:
            return False, 0.0
        dd = (peak - balance) / peak * 100.0
        return dd >= max_dd_pct, dd

    def _update_drawdown(self, rs) -> str | None:
        """Kill-switch drawdown TOTAL per siklus: proses permintaan reset manual,
        update puncak saldo, kunci bila tembus ambang. Return alasan blokir/None.
        Kunci PERMANEN (persisten, tahan restart) — lepas HANYA via /api/dd-reset:
        setelah rugi besar, keputusan lanjut harus dibuat manusia dgn kepala
        dingin, bukan oleh reset kalender otomatis (beda dgn CB harian)."""
        from . import store as _store
        try:
            if _store.get_kv(f"dd_reset_{self.settings.mode}"):
                _store.set_kv(f"dd_reset_{self.settings.mode}", {})   # habis pakai
                self._dd_lock, self._dd_reason = False, ""
                self._peak_balance = self.balance_usd                  # puncak mulai ulang
                log.warning("DRAWDOWN LOCK direset MANUAL — puncak saldo di-set ulang "
                            f"ke ${self.balance_usd:.2f}.")
        except Exception as e:  # boundary — kegagalan store tak boleh ganggu siklus
            log.warning(f"cek dd_reset gagal: {e}")
        self._peak_balance = max(self._peak_balance, self.balance_usd)
        hit, dd = self._dd_check(self._peak_balance, self.balance_usd, rs.max_drawdown_pct)
        if hit and not self._dd_lock:
            self._dd_lock = True
            self._dd_reason = (f"drawdown total {dd:.1f}% ≥ {rs.max_drawdown_pct:.0f}% "
                               f"dari puncak ${self._peak_balance:.2f}")
            log.error(f"DRAWDOWN LOCK: {self._dd_reason} — entry DIBLOKIR sampai "
                      "reset manual (POST /api/dd-reset).")
            self.notify.send(f"🛑 <b>DRAWDOWN LOCK</b>\n{self._dd_reason}\n"
                             "Entry diblokir sampai reset manual dari dashboard.")
        return self._dd_reason if self._dd_lock else None

    def _apply_settings(self) -> RuntimeSettings:
        rs = load_settings(self.settings.mode if self.pin_mode else None)
        eff = rs.mode or self.settings.mode               # mode diminta dari UI (atau .env)
        if eff != self._eff_mode and not self.pin_mode:   # pinned: tak pernah switch
            self._switch_mode(eff)
        resolved = rs.symbols or self.ex.perp_symbols(
            tuple(self.cfg["market"].get("settles", ["USDC"])))   # kosong = semua settle config
        resolved = self._screened(resolved)               # Layer 2: likuiditas/spread/ATR
        # Posisi TERBUKA wajib tetap dipantau meski simbolnya gugur dari screening (mis. volume/
        # spread berubah) — kalau tidak, SL/TP/give-back-nya berhenti dicek diam-diam (orphan:
        # posisi tetap hidup tapi tak ada yg mengawasi). Screener hanya boleh menyaring simbol
        # BARU, tak pernah "membutakan" bot dari eksposur yg sudah ada.
        resolved = sorted(set(resolved) | set(self.open.keys()))
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
        # circuit breaker harian diatur user dari UI (0 = nonaktif) — hot-reload
        self.daily_max_loss_pct = float(rs.daily_max_loss_pct)
        self.daily_max_trades = int(rs.daily_max_trades)
        self.corr_threshold = float(rs.corr_threshold)   # guard korelasi hot-reload dari UI
        self.corr_lookback = int(rs.corr_lookback)
        # Penyetelan Gemini dari UI (hot-reload) — atur frekuensi panggilan → hemat RPM/token.
        self._decide_interval = int(rs.gemini_decide_seconds)
        self._manage_interval = int(rs.gemini_manage_seconds)
        self._min_hold_s = int(rs.gemini_min_hold_s)
        self._autonomous_interval = int(rs.gemini_portfolio_seconds)
        self._plan_horizon_h = int(rs.gemini_plan_hours)
        self.tool_max_iters = int(rs.gemini_tool_iters)
        # Flag agent: config.yaml OR toggle UI (hot-reload tanpa restart). full_auto → semua.
        # Manager-mode (Jalan A) meng-override jadi posture manajer disiplin (lihat _agent_posture).
        _ag = self.cfg.get("agent", {})
        posture = self._agent_posture(_ag, rs)
        self.tool_loop = posture["tool_loop"]
        self.autonomous = posture["autonomous"]
        self.use_planner = posture["use_planner"]
        self.ab_shadow = posture["ab_shadow"]
        self.use_gemini_trader = posture["use_gemini_trader"]
        self.news.enabled = self._news_base and bool(rs.news_veto)   # toggle news-veto dari UI
        if self.use_gemini_trader and self.gtrader is None:
            from .gemini_trader import GeminiTrader
            self.gtrader = GeminiTrader(self.settings, self.cfg)
            log.info("Teknik GEMINI aktif — Gemini menentukan arah + SL/TP (entry timing bebas); "
                     "ukuran & leverage dari UI, level divalidasi kode.")
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
            st = get_kv(self._state_key)
        except Exception as e:  # boundary
            log.warning(f"restore state gagal: {e}")
            return
        if not st:
            return
        # hanya pulihkan bila konfigurasi saldo tak diubah user sejak terakhir simpan
        if abs(st.get("cfg_balance", self._last_cfg_balance) - self._last_cfg_balance) < 1e-9:
            self.balance_usd = float(st.get("balance", self.balance_usd))
        self.open = st.get("open", {}) or {}
        self.agent_memory.restore(st.get("agent_memory"))   # memori lintas-tick tahan restart
        # pulihkan state circuit breaker harian bila masih hari yang sama (UTC)
        if st.get("day") == str(pd.Timestamp.utcnow().date()):
            self._day_pnl = float(st.get("day_pnl", 0.0))
            self._day_trades = int(st.get("day_trades", 0))
            self._day_start_balance = float(st.get("day_start_balance", self.balance_usd))
        # drawdown-total: puncak & kunci BERTAHAN melewati restart (beda dgn state harian)
        self._peak_balance = max(float(st.get("peak_balance", 0.0)), self.balance_usd)
        self._dd_lock = bool(st.get("dd_lock", False))
        self._dd_reason = str(st.get("dd_reason", ""))
        if self.open:
            log.info(f"State dipulihkan dari SQLite: saldo ${self.balance_usd:.2f}, "
                     f"{len(self.open)} posisi terbuka")

    def _persist_logs(self, news_veto: bool, note: str) -> None:
        """Simpan histori news veto & screening ke SQLite, hanya saat BERUBAH
        (hindari banjir 1 baris/siklus). Boundary aman: gagal log tak ganggu bot."""
        try:
            from . import store
            # Catat HANYA saat STATUS veto berubah (on/off), bukan tiap Gemini ganti redaksi
            # catatan (LLM menulis beda-beda walau keputusan sama) → hindari banjir baris.
            if self._last_news is None or news_veto != self._last_news[0]:
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
            set_kv(self._state_key, {"balance": round(self.balance_usd, 6),
                                "open": self.open,
                                "cfg_balance": self._last_cfg_balance,
                                "day": str(self._day), "day_pnl": round(self._day_pnl, 4),
                                "day_trades": self._day_trades,
                                "day_start_balance": round(self._day_start_balance, 6),
                                "peak_balance": round(self._peak_balance, 6),
                                "dd_lock": self._dd_lock, "dd_reason": self._dd_reason,
                                "agent_memory": self.agent_memory.snapshot()})   # memori lintas-tick
        except Exception as e:  # boundary
            log.warning(f"persist state gagal: {e}")

    # ---------- mode switching & eksekusi LIVE (UANG NYATA) ----------

    def _switch_mode(self, eff: str) -> None:
        """Beralih mode berjalan. live = uang nyata (butuh BINANCE_LIVE_KEY/SECRET)."""
        import os
        try:
            if eff == "live" and not (os.getenv("BINANCE_LIVE_KEY") and os.getenv("BINANCE_LIVE_SECRET")):
                log.error("Mode LIVE diminta tapi BINANCE_LIVE_KEY/SECRET kosong — tetap paper.")
                return
            new = Settings(mode=eff, raw=self.cfg, gemini_keys=self.settings.gemini_keys,
                           gemini_enabled=self.settings.gemini_enabled)
            self.ex = Exchange(new)
            self.settings = new
            self.live = (eff == "live")
            self.open = {}                      # posisi lama (paper/mode lain) tak valid
            # Isolasi per-mode HARUS ikut pindah di sini — tanpa ini, _persist_state()
            # & journal terus menulis ke bucket mode LAMA setelah switch runtime,
            # mencampur saldo/riwayat lintas mode (insiden 2026-07-02).
            from .logger import set_journal_mode
            set_journal_mode(eff)
            decision_log.set_mode(eff)
            self._state_key = f"botstate_{eff}"
            if self.live:
                self.balance_usd = self.ex.equity_usdc(self.balance_usd)
                self._sync_live_positions()     # ambil posisi nyata yang sudah ada
            else:
                # paper: mulai dari saldo KONFIGURASI mode tujuan (bukan carry-over
                # dari mode sebelumnya), lalu pulihkan bucket SQLite milik mode itu.
                try:
                    self.balance_usd = load_settings(eff).balance_usd
                    self._last_cfg_balance = self.balance_usd
                except Exception as e:  # boundary
                    log.warning(f"load balance mode {eff} gagal: {e}")
                self._restore_state()
            self._day = pd.Timestamp.utcnow().date()
            self._day_pnl = 0.0
            self._day_trades = 0
            self._day_start_balance = self.balance_usd
            self._eff_mode = eff
            if self.live:
                log.warning(f"=== BERALIH KE LIVE (UANG NYATA) — saldo Binance ${self.balance_usd:.2f} ===")
                self.notify.send(f"⚠️ <b>MODE LIVE AKTIF — UANG NYATA</b>\nSaldo Binance ${self.balance_usd:.2f}")
            else:
                log.warning(f"=== beralih ke {eff.upper()} (paper) ===")
        except Exception as e:  # boundary
            log.error(f"gagal beralih mode {eff}: {e}")

    def _sync_live_positions(self) -> None:
        """Tarik posisi terbuka nyata dari Binance ke self.open."""
        try:
            for p in self.ex.positions():
                sym = p.get("symbol")
                contracts = float(p.get("contracts") or 0)
                if not sym or contracts == 0:
                    continue
                side = "long" if (p.get("side") == "long" or contracts > 0) else "short"
                entry = float(p.get("entryPrice") or 0)
                self.open[sym] = {"side": side, "entry": entry, "qty": abs(contracts),
                                  "sl": 0.0, "tp": 0.0, "liq": float(p.get("liquidationPrice") or 0),
                                  "bet": float(p.get("initialMargin") or 0)}
        except Exception as e:  # boundary
            log.error(f"sync posisi live gagal: {e}")

    def _live_open(self, sym, is_long, qty, entry, sl, tp, rs) -> tuple[bool, float]:
        """Tempatkan order ENTRY nyata + SL/TP sisi-exchange. Return (ok, fill_price).
        KRITIS: entry & SL/TP di try/except TERPISAH. Bila entry terisi (uang REAL sudah
        masuk) tapi SL/TP gagal ditempatkan (mis. harga sudah lewat trigger dlm hitungan
        ms), JANGAN klaim gagal total (posisi_open=False) — itu membuat posisi TELANJANG
        (tanpa proteksi) HILANG TOTAL dari self.open (live: _monitor_usd tak enforce SL/TP
        sendiri, percaya penuh ke order exchange). Coba emergency-close dulu; kalau itu
        JUGA gagal, tetap lacak (return ok=True) drpd bot buta total thd eksposur nyata."""
        try:
            self.ex.set_leverage(sym, rs.leverage)
            side_str = "buy" if is_long else "sell"
            if rs.order_type == "limit":
                order = self.ex.client.create_order(sym, "limit", side_str, qty, entry, {"timeInForce": "GTX"})
            else:
                order = self.ex.client.create_order(sym, "market", side_str, qty)
            fill = float(order.get("average") or entry)
        except Exception as e:  # boundary — entry sendiri gagal → aman, tak ada eksposur
            log.error(f"LIVE OPEN {sym} gagal (entry): {e}")
            self.notify.send(f"❌ <b>LIVE OPEN GAGAL</b> {sym}\n{str(e)[:140]}")
            return False, entry
        try:
            close_side = "sell" if is_long else "buy"
            # SL/TP dijaga exchange (tetap aktif walau bot mati)
            self.ex.client.create_order(sym, "STOP_MARKET", close_side, qty, None,
                                        {"stopPrice": sl, "reduceOnly": True})
            self.ex.client.create_order(sym, "TAKE_PROFIT_MARKET", close_side, qty, None,
                                        {"stopPrice": tp, "reduceOnly": True})
            return True, fill
        except Exception as e:  # boundary — entry SUDAH terisi, SL/TP gagal → posisi telanjang
            log.error(f"LIVE OPEN {sym}: entry terisi TAPI SL/TP gagal ({e}) — emergency close")
            try:
                self.ex.client.create_order(sym, "market", close_side, qty, None, {"reduceOnly": True})
                self.ex.client.cancel_all_orders(sym)
                self.notify.send(f"⚠️ <b>LIVE OPEN {sym}</b>: SL/TP gagal dipasang → "
                                 f"emergency-close berhasil. Tak ada posisi tersisa.\n{str(e)[:140]}")
                return False, fill                       # posisi sudah ditutup lagi → aman
            except Exception as e2:  # boundary — emergency close JUGA gagal: posisi telanjang NYATA
                self.notify.send(
                    f"🚨 <b>DARURAT</b> {sym}: entry live terisi, SL/TP GAGAL, emergency-close "
                    f"JUGA GAGAL — posisi TELANJANG tanpa proteksi!\nTutup MANUAL segera di exchange.\n"
                    f"{str(e)[:100]} | {str(e2)[:100]}")
                log.error(f"{sym}: posisi telanjang tak terlindungi — intervensi manual WAJIB")
                return True, fill                        # WAJIB tetap dilacak — jangan hilang total

    def _live_close(self, sym: str, pos: dict) -> None:
        """Tutup posisi nyata (reduceOnly market) + batalkan SL/TP tersisa."""
        try:
            close_side = "sell" if pos["side"] == "long" else "buy"
            self.ex.client.create_order(sym, "market", close_side, pos["qty"], None, {"reduceOnly": True})
            self.ex.client.cancel_all_orders(sym)
        except Exception as e:  # boundary
            log.error(f"LIVE CLOSE {sym} gagal: {e}")

    def _live_reconcile(self) -> None:
        """Sinkron posisi nyata dari Binance: deteksi yang sudah tertutup (SL/TP/liq),
        update saldo dari equity nyata, bersihkan order yatim."""
        try:
            real = {}
            for p in self.ex.positions():
                if float(p.get("contracts") or 0) != 0:
                    real[p.get("symbol")] = p
        except Exception as e:  # boundary
            log.error(f"reconcile live gagal: {e}")
            return
        prev_balance = self.balance_usd
        closed = [(sym, self.open[sym]) for sym in list(self.open) if sym not in real]
        for sym, _pos in closed:
            self.open.pop(sym, None)
            try:
                self.ex.client.cancel_all_orders(sym)   # bersihkan SL/TP yatim
            except Exception:
                pass
            journal("forward_close", {"symbol": sym, "reason": "live_exit",
                                      "equity": round(self.balance_usd, 2)})
            log.info(f"LIVE CLOSE terdeteksi {sym}")
            self.notify.send(f"✋ <b>LIVE CLOSE</b> {sym} (SL/TP/manual)")
        self.balance_usd = self.ex.equity_usdc(self.balance_usd)
        self._day_pnl = self.balance_usd - self._day_start_balance   # PnL harian dari equity nyata
        # BELAJAR di LIVE — HANYA dengan data PnL NYATA & TAK AMBIGU (tepat satu posisi tutup
        # siklus ini). Jika banyak tutup bersamaan, lewati (jangan ajari Gemini data kotor).
        gem_closed = [(s, p) for s, p in closed if p.get("gdecision") and self.gtrader is not None]
        if len(gem_closed) == 1:
            sym, pos = gem_closed[0]
            try:
                r = (self.balance_usd - prev_balance) / pos["bet"] if pos.get("bet") else 0.0
                if pos.get("conviction") is not None:   # skor Brier LIVE (Phase 1): PnL TAK
                    from .store import log_calibration   # ambigu di sini (tepat 1 posisi tutup)
                    log_calibration(pos.get("gdecision"), sym, float(pos["conviction"]),
                                    1 if r > 0 else 0, self.settings.mode)
                self.gtrader.settle(pos["gdecision"], r)
                self._gem_closes += 1
                if self._gem_closes % 20 == 0:
                    res = self.gtrader.reflect()
                    log.info(f"Gemini refleksi (live): {res['active_lessons']} pelajaran aktif")
                    self._check_calib_drift()            # Phase 6: alarm drift (tak blokir)
            except Exception as e:  # boundary
                log.warning(f"settle gemini live {sym} gagal: {e}")
        # Tautkan close LIVE non-gemini (ReAct) ke decision_log — HANYA bila TEPAT SATU posisi
        # tutup siklus ini, agar PnL agregat (Δequity) tak ambigu (prinsip sama spt Gemini).
        react_closed = [(s, p) for s, p in closed if not p.get("gdecision")]
        if len(closed) == 1 and len(react_closed) == 1:
            sym, pos = react_closed[0]
            outcome_r = (self.balance_usd - prev_balance) / pos["bet"] if pos.get("bet") else 0.0
            self._react_link(sym, "LIVE_CLOSE", outcome_r)

    @staticmethod
    def _valid_entry_sl(is_long: bool, entry: float, sl, liq: float) -> float | None:
        """Validasi SL usulan Gemini. Kembalikan SL terpakai (mungkin di-clamp ke DALAM
        likuidasi) atau None bila tak bisa valid (→ pemanggil batal buka posisi).
        GUARDRAIL: SL harus di sisi benar & memicu SEBELUM likuidasi, kalau tidak SL percuma."""
        try:
            sl = float(sl)
        except (TypeError, ValueError):
            return None
        buf = 0.0005                                   # 0.05% buffer di dalam likuidasi
        if is_long:
            if sl >= entry:                            # SL long wajib di bawah entry
                return None
            floor = liq * (1 + buf)                    # jangan di/ bawah likuidasi
            if floor >= entry:                         # leverage terlalu tinggi → tak ada ruang SL
                return None
            return max(sl, floor)                      # clamp naik bila terlalu dekat likuidasi
        if sl <= entry:                                # SL short wajib di atas entry
            return None
        cap = liq * (1 - buf)
        if cap <= entry:
            return None
        return min(sl, cap)                            # clamp turun bila terlalu dekat likuidasi

    @staticmethod
    def _adaptive_bet(balance: float, bet_usd: float, bet_pct: float,
                      open_positions: dict, gem_conv: float | None = None) -> float:
        """Ukuran margin ADAPTIF: bet_pct>0 → %saldo (auto-scale $10→naik), else bet_usd tetap.
        Skala conviction Gemini (lantai 20%), lalu CAP ke margin BEBAS (saldo − terkunci) agar
        akun modal-minim tak pernah 'diam total'. Kembalikan 0.0 bila tak ada margin bebas."""
        bet = balance * (bet_pct / 100.0) if bet_pct > 0 else bet_usd
        if gem_conv is not None:
            bet = max(bet * gem_conv, bet * 0.2)
        locked = sum((p.get("bet") or 0) for p in open_positions.values())
        avail = balance - locked
        if avail < 0.10:
            return 0.0
        return min(bet, avail)

    def _open_usd(self, sym: str, side: int, atr: float, rs: RuntimeSettings) -> None:
        gem = self.sig_cache.get(sym, {}).get("gemini") if self.use_gemini_trader else None
        # GERBANG LIVE: Gemini-trader tak boleh order UANG NYATA tanpa izin eksplisit.
        if gem and self.live and not self._allow_live_gemini:
            log.warning(f"{sym}: Gemini-trader DIBLOKIR di LIVE (set gemini.allow_live_trader: "
                        "true di config.yaml untuk mengizinkan order uang nyata).")
            c = self.sig_cache.setdefault(sym, {})
            c["blocked"] = "gemini-live dimatikan (config)"
            return
        gem_conv = float(gem["dec"].get("conviction", 0.0) or 0.0) if gem else None
        # Gerbang SIZE berbasis confidence (Phase 2 kalibrasi): tier menggantikan skala
        # conviction kontinu lama. Jalur rule-based (gem_conv=None) TIDAK digerbang —
        # tak punya angka confidence; selalu ukuran penuh (pilihan terdokumentasi).
        size_mult = rs.conf_size_mult(gem_conv)
        if size_mult is None:                       # ABSTAIN: confidence < conf_min
            c = self.sig_cache.setdefault(sym, {})
            c["blocked"] = f"SKIPPED: low_confidence ({gem_conv:.2f} < {rs.conf_min:.2f})"
            journal("forward_skip", {"symbol": sym, "reason": "low_confidence",
                                     "conviction": round(gem_conv, 3),
                                     "conf_min": rs.conf_min})
            log.info(f"SKIP {sym}: confidence {gem_conv:.2f} < {rs.conf_min:.2f} (abstain)")
            return
        bet = self._adaptive_bet(self.balance_usd, rs.bet_usd, rs.bet_pct, self.open, size_mult)
        if bet <= 0:
            c = self.sig_cache.setdefault(sym, {})
            c["blocked"] = "margin bebas habis"
            return
        price = float(self.ex.ticker(sym)["last"])
        is_long = side == 1
        slip = 1 + self.slippage / 100 if is_long else 1 - self.slippage / 100
        entry = price * slip
        qty = (bet * rs.leverage) / entry
        sl = entry - atr * self.bt.sl_mult if is_long else entry + atr * self.bt.sl_mult
        if rs.target_profit_pct > 0:
            tp = entry * (1 + rs.target_profit_pct / 100) if is_long else entry * (1 - rs.target_profit_pct / 100)
        else:
            tp = entry + atr * self.bt.tp_mult if is_long else entry - atr * self.bt.tp_mult
        liq = liquidation_price(entry, is_long, rs.liquidation_frac())
        # Gemini trader penuh: SL/TP dari Gemini menggantikan ATR — TAPI divalidasi KODE.
        # Guardrail: SL wajib di sisi benar & DI DALAM likuidasi (di-clamp bila perlu); TP sisi benar.
        if gem:
            g_sl = self._valid_entry_sl(is_long, entry, gem["dec"].get("sl"), liq)
            if g_sl is None:
                c = self.sig_cache.setdefault(sym, {})
                c["blocked"] = "SL Gemini invalid → skip"
                log.warning(f"{sym}: SL Gemini {gem['dec'].get('sl')} invalid vs "
                            f"entry={entry:.6f}/liq={liq:.6f} → tak buka posisi")
                return
            sl = g_sl
            g_tp = gem["dec"].get("tp")
            if isinstance(g_tp, (int, float)) and ((is_long and g_tp > entry) or
                                                   (not is_long and g_tp < entry)):
                tp = float(g_tp)                    # TP Gemini valid; selain itu pakai TP ATR
        # Fix A: LANTAI jarak SL (berlaku utk SL rule-based & usulan Gemini) —
        # lalu jaga tetap DI DALAM likuidasi bila pelebaran menabraknya.
        buf = self.buffers.get(sym)
        last_range = (float(buf["high"].iloc[-2] - buf["low"].iloc[-2])
                      if buf is not None and len(buf) >= 2 else 0.0)
        sl = self._sl_floor(entry, is_long, sl, atr, last_range)
        if (is_long and sl <= liq) or (not is_long and sl >= liq):
            sl = (entry + liq) / 2                  # kompromi: selebar mungkin, tetap aman
        if self.live:                               # UANG NYATA: order asli + SL/TP exchange
            try:
                qty = float(self.ex.client.amount_to_precision(sym, qty))
            except Exception:
                pass
            if qty <= 0:
                c = self.sig_cache.setdefault(sym, {})
                c["blocked"] = "qty live nol setelah presisi → skip"
                return
            ok, entry = self._live_open(sym, is_long, qty, entry, sl, tp, rs)
            if not ok:
                c = self.sig_cache.setdefault(sym, {})
                c["blocked"] = "order live gagal → skip"
                return
        buf_full = self.buffers.get(sym)
        mtf_stamp = (self.mtf.stamp(buf_full, self.tf, side)
                     if buf_full is not None else {})   # kesepakatan multi-TF (shadow)
        settle = "USDC" if sym.endswith(":USDC") else "USDT"
        entry_fee_rate = rs.fee_rate(settle, rs.order_type == "limit")   # kaki ENTRY: maker bila limit
        self.open[sym] = {"side": "long" if is_long else "short", "entry": entry, "qty": qty,
                          "sl": sl, "tp": tp, "liq": liq, "bet": bet,
                          "entry_fee_rate": entry_fee_rate,   # fee kaki-entry (per-settle); exit selalu taker
                          "opened_ts": pd.Timestamp.utcnow().isoformat(),  # utk marker panah di chart
                          **self.vrp.stamp(),   # stempel regime VRP saat open (A/B shadow)
                          **self._regime_stamp(buf_full, self.cfg),  # regime → laporan EV
                          **mtf_stamp}
        if gem:                                     # catat keputusan Gemini → settle saat tutup
            self.open[sym]["conviction"] = gem_conv   # untuk skor Brier saat close
            try:
                did = self.gtrader.commit(sym, gem["dec"], gem["ctx"])
                self.open[sym]["gdecision"] = did
                self.open[sym]["setup"] = gem["dec"].get("setup")
            except Exception as e:  # boundary
                log.warning(f"commit keputusan gemini {sym} gagal: {e}")
        self._day_trades += 1                       # untuk circuit breaker harian
        journal("forward_open", {"symbol": sym, "side": self.open[sym]["side"], "entry": entry,
                                 "sl": sl, "tp": tp, "liq": liq, "lev": rs.leverage, "bet": bet,
                                 "conviction": gem_conv, "size_mult": size_mult})
        log.info(f"OPEN {self.open[sym]['side'].upper()} {sym} x{rs.leverage} bet=${bet:.2f} "
                 f"@ {entry:.4f} SL={sl:.4f} TP={tp:.4f} LIQ={liq:.4f}")
        self.notify.send(
            f"🟢 <b>OPEN {self.open[sym]['side'].upper()}</b> {sym} x{rs.leverage}\n"
            f"Entry {entry:.4f} · SL {sl:.4f} · TP {tp:.4f}\n"
            f"LIQ {liq:.4f} · bet ${bet:.2f}")

    def _close_usd(self, sym: str, price: float, reason: str) -> None:
        if self.live:                               # close NYATA; reconcile yang catat
            pos = self.open.get(sym)
            if pos:
                self._live_close(sym, pos)
            return
        pos = self.open.pop(sym)
        self._decide_price_cache.pop(sym, None)  # invalidasi cache: state berubah, butuh decide baru
        is_long = pos["side"] == "long"
        exit_fill = price * (1 - self.slippage / 100 if is_long else 1 + self.slippage / 100)
        if reason == "liq":
            pnl = -pos["bet"]                       # rugi seluruh margin
        else:
            move = (exit_fill - pos["entry"]) if is_long else (pos["entry"] - exit_fill)
            # Fee PER-KAKI PER-SETTLE (realita Binance): kaki-entry sesuai order_type saat open
            # (maker bila limit → USDC-M 0%), kaki-exit SL/TP/market SELALU taker (USDC-M 0.04%).
            settle = "USDC" if sym.endswith(":USDC") else "USDT"
            entry_rate = pos.get("entry_fee_rate")
            if entry_rate is None:                  # posisi lama / live-reconcile tanpa stempel
                entry_rate = self.rs.fee_rate(settle, self.rs.order_type == "limit") if self.rs else self.fee
            exit_rate = self.rs.fee_rate(settle, False) if self.rs else self.fee  # exit = taker
            fee = (entry_rate / 100 * pos["entry"] + exit_rate / 100 * exit_fill) * pos["qty"]
            funding = pos.get("funding_paid", 0.0)  # akrual simulasi funding (paper, P3)
            pnl = max(pos["qty"] * move - fee - funding, -pos["bet"])  # rugi maks = margin
        self.balance_usd += pnl
        self._day_pnl += pnl                        # untuk circuit breaker harian
        risk0 = abs(pos["entry"] - pos["sl"]) * pos["qty"]   # R = jarak-SL (identik backtest & _react_settle)
        r = pnl / risk0 if risk0 else 0.0
        self.trades.append(namedtuple("T", ["r"])(r))
        vrp.log_close(sym, pos, r, mode=self.settings.mode)   # shadow log ber-mode
        mtf.log_close(sym, pos, r, pos.get("conviction"), self.settings.mode)  # shadow MTF
        if pos.get("conviction") is not None:       # skor Brier (Phase 1 kalibrasi)
            try:
                from .store import log_calibration
                log_calibration(pos.get("gdecision"), sym, float(pos["conviction"]),
                                1 if pnl > 0 else 0, self.settings.mode)
            except Exception as e:  # boundary
                log.warning(f"log kalibrasi {sym} gagal: {e}")
        if pos.get("gdecision") and self.gtrader is not None:   # umpan balik ke Gemini
            try:
                self.gtrader.settle(pos["gdecision"], r,
                                    mae_pct=round(pos.get("mae_pct", 0.0), 3),
                                    mfe_pct=round(pos.get("mfe_pct", 0.0), 3),
                                    exit_reason=reason)
                self._gem_closes += 1
                if self._gem_closes % 20 == 0:                  # refleksi berkala (belajar)
                    res = self.gtrader.reflect()
                    log.info(f"Gemini refleksi: {res['settled']} settled, "
                             f"{res['active_lessons']} pelajaran aktif")
                    self._check_calib_drift()                   # Phase 6: alarm drift (tak blokir)
            except Exception as e:  # boundary
                log.warning(f"settle/reflect gemini {sym} gagal: {e}")
        else:
            self._react_settle(sym, pos, pnl, reason)   # umpan balik ReAct (teknik non-gemini)
        journal("forward_close", {"symbol": sym, "exit": round(exit_fill, 6), "reason": reason,
                                  "pnl_usd": round(pnl, 4), "r": round(r, 4),
                                  "regime": pos.get("regime", "unknown"),
                                  "mae_pct": round(pos.get("mae_pct", 0.0), 3),
                                  "mfe_pct": round(pos.get("mfe_pct", 0.0), 3),
                                  "funding_usd": round(pos.get("funding_paid", 0.0), 4),
                                  "equity": round(self.balance_usd, 2)})
        log.info(f"CLOSE {reason.upper()} {sym} pnl=${pnl:+.2f} bal=${self.balance_usd:.2f}")
        icon = {"liq": "💥 <b>LIKUIDASI</b>", "sl": "🛑 SL", "tp": "✅ TP",
                "manual": "✋ CLOSE", "eod": "⏹ EOD"}.get(reason, reason)
        self.notify.send(f"{icon} {sym}\nPnL ${pnl:+.2f} · R {r:+.2f} · saldo ${self.balance_usd:.2f}")

    def _check_calib_drift(self) -> None:
        """Phase 6: pantau DRIFT kalibrasi (Brier terkini vs baseline 14-hari, per mode).
        Bila memburuk melewati margin → ALARM Telegram + SARAN dicatat (jurnal), TANPA
        mengubah threshold otomatis (keputusan manusia). Anti-spam: alarm HANYA saat MASUK
        kondisi drift; reset saat pulih. Gagal/sampel kurang → diam (fail-safe)."""
        from . import store
        try:
            rep = store.calibration_report(self.settings.mode, last_n=50, days=14)
        except Exception as e:  # boundary — instrumentasi, tak boleh ganggu trading
            log.warning(f"cek drift kalibrasi gagal: {e}")
            return
        cur, base = rep.get("last_50_trades", {}), rep.get("last_14_days", {})
        cb, bb, n = cur.get("brier"), base.get("brier"), cur.get("n", 0) or 0
        margin = float(getattr(self.rs, "calib_drift_margin", 0.05)) if self.rs else 0.05
        min_n = int(getattr(self.rs, "calib_drift_min_n", 20)) if self.rs else 20
        if cb is None or bb is None or n < min_n:
            return
        drifting = (cb - bb) > margin and cb > 0.25       # memburuk vs baseline & di bawah koin
        if drifting and not self._calib_drifting:
            self._calib_drifting = True
            cur_min = float(getattr(self.rs, "conf_min", 0.55)) if self.rs else 0.55
            suggest = round(min(0.95, cur_min + 0.05), 2)  # SARAN saja — tak diterapkan
            self.notify.send(
                f"⚠️ <b>DRIFT KALIBRASI</b> [{self.settings.mode}]\n"
                f"Brier {n} trade terakhir {cb:.3f} > baseline 14h {bb:.3f} (+{cb - bb:.3f})\n"
                f"Saran MANUAL: naikkan conf_min {cur_min:.2f}→{suggest:.2f}. Tidak diubah otomatis.")
            journal("calib_drift", {"mode": self.settings.mode, "brier_recent": cb,
                                    "brier_baseline": bb, "n": n, "margin": margin,
                                    "suggest_conf_min": suggest})
            log.warning(f"DRIFT KALIBRASI {self.settings.mode}: recent {cb:.3f} vs baseline "
                        f"{bb:.3f} (+{cb - bb:.3f}) — saran conf_min→{suggest:.2f} (MANUAL)")
        elif not drifting and self._calib_drifting:
            self._calib_drifting = False                   # pulih → boleh alarm lagi nanti

    def _monitor_usd(self, sym: str, buf: "pd.DataFrame | None" = None) -> None:
        if self.live:                               # live: SL/TP/liq ditangani exchange + reconcile
            return
        if sym not in self.open:
            return
        try:
            last_price = float(self.ex.ticker(sym)["last"])
        except Exception as e:  # boundary
            log.warning(f"ticker {sym}: {e}")
            return
        pos = self.open[sym]
        long = pos["side"] == "long"
        # INTRABAR: nilai sentuhan SL/TP/liq pakai high/low candle terbaru (+ last sbg cadangan),
        # bukan hanya 'last' sesaat — agar wick yang lewat ANTAR-POLL tak terlewat (selaras backtest).
        hi = lo = last_price
        if buf is None:
            buf = self.buffers.get(sym)
        if buf is not None and len(buf):
            recent = buf.iloc[-2:]                   # candle tertutup terakhir + yang sedang terbentuk
            hi = max(float(recent["high"].max()), last_price)
            lo = min(float(recent["low"].min()), last_price)
        # Fix B: lacak MFE/MAE (% dari entry) — bahan evaluasi "SL kepencet lalu
        # balik arah?" utk refleksi Gemini & kalibrasi lantai SL.
        e = pos["entry"]
        if long:
            pos["mfe_pct"] = max(pos.get("mfe_pct", 0.0), (hi - e) / e * 100)
            pos["mae_pct"] = max(pos.get("mae_pct", 0.0), (e - lo) / e * 100)
        else:
            pos["mfe_pct"] = max(pos.get("mfe_pct", 0.0), (e - lo) / e * 100)
            pos["mae_pct"] = max(pos.get("mae_pct", 0.0), (hi - e) / e * 100)
        # GIVE-BACK menuju TP (permintaan pemilik): posisi sempat >= giveback_tp_frac perjalanan
        # ke TP lalu berbalik >= giveback_margin → PAKSA review Gemini agar kunci profit / exit,
        # jangan tunggu jadwal ~1 menit. Fire sekali per puncak baru (anti-spam).
        prog = self._tp_progress(pos, last_price)
        if prog is not None:
            peak = max(pos.get("peak_tp_prog", prog), prog)
            pos["peak_tp_prog"] = peak
            fired_at = pos.get("giveback_fired_at", 0.0)
            if (peak >= self._giveback_tp_frac and (peak - prog) >= self._giveback_margin
                    and peak > fired_at + 1e-9):
                pos["giveback_fired_at"] = peak
                pos["giveback_note"] = (f"sempat {peak * 100:.0f}% menuju TP lalu balik ke "
                                        f"{prog * 100:.0f}% — pertimbangkan kunci profit / exit")
                self._last_manage[sym] = 0.0      # buka throttle → _gemini_manage jalan siklus ini
                log.info(f"GIVE-BACK {sym}: puncak {peak * 100:.0f}%→{prog * 100:.0f}% TP — panggil Gemini")
        # Urutan konservatif: likuidasi → SL → TP (bila satu bar menyentuh dua sisi, ambil yg merugikan).
        if (lo <= pos["liq"]) if long else (hi >= pos["liq"]):
            self._close_usd(sym, pos["liq"], "liq")
        elif (lo <= pos["sl"]) if long else (hi >= pos["sl"]):
            self._close_usd(sym, pos["sl"], "sl")
        elif (hi >= pos["tp"]) if long else (lo <= pos["tp"]):
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

    def _corr_conflict(self, sym: str, side: int) -> str | None:
        """Guard korelasi: bila ada posisi terbuka SEARAH yg return-nya berkorelasi
        >= threshold dengan kandidat, kembalikan simbolnya (blok entry). None = aman."""
        # Toggle non-gemini (config strategy.gate_corr, default OFF): teknik non-gemini hanya
        # kena guard bila di-ON-kan. Gemini SELALU pakai guard ini (via corr_threshold) — tak berubah.
        if not self.use_gemini_trader and not self.cfg.get("strategy", {}).get("gate_corr", False):
            return None
        if self.corr_threshold <= 0 or self.corr_lookback < 20:
            return None
        cand = self.buffers.get(sym)
        if cand is None or len(cand) < 20:
            return None
        want = "long" if side == 1 else "short"
        a = cand["close"].pct_change().dropna().tail(self.corr_lookback).reset_index(drop=True)
        for osym, pos in self.open.items():
            if osym == sym or pos["side"] != want:
                continue
            ob = self.buffers.get(osym)
            if ob is None or len(ob) < 20:
                continue
            b = ob["close"].pct_change().dropna().tail(self.corr_lookback).reset_index(drop=True)
            n = min(len(a), len(b))
            if n < 20:
                continue
            corr = a.tail(n).reset_index(drop=True).corr(b.tail(n).reset_index(drop=True))
            if corr is not None and corr == corr and corr >= self.corr_threshold:
                return osym
        return None

    def _circuit_breaker(self) -> str | None:
        """Kembalikan alasan stop bila circuit breaker harian aktif, else None."""
        if self.daily_max_trades and self._day_trades >= self.daily_max_trades:
            return f"limit trade harian ({self._day_trades}/{self.daily_max_trades})"
        if self.daily_max_loss_pct > 0 and self._day_start_balance > 0:
            limit = self._day_start_balance * self.daily_max_loss_pct / 100
            if self._day_pnl <= -limit:
                return f"circuit breaker: rugi harian ${-self._day_pnl:.2f} ≥ ${limit:.2f}"
        return None

    def _on_cycle_store(self) -> None:
        rs = self._apply_settings()
        self._gemini_decide_used = 0      # kuota panggilan Gemini per-siklus, reset tiap cycle
        # Budget dinamis: pas untuk memberi SEMUA simbol satu giliran per _decide_interval,
        # dibatasi cap wall-clock. cycles = berapa siklus muat dalam satu jendela giliran.
        cycles = max(1, self._decide_interval // max(1, rs.poll_seconds))
        need = -(-len(self.symbols) // cycles)                       # ceil(simbol / cycles)
        new_budget = max(1, min(need, self._gemini_decide_cap))
        if new_budget != self._gemini_decide_budget:                # log HANYA saat berubah (anti-spam)
            log.info(f"Budget Gemini/siklus: {new_budget} "
                     f"(simbol={len(self.symbols)}, cycles={cycles}, cap={self._gemini_decide_cap}"
                     f"{', MENTOK CAP' if need > self._gemini_decide_cap else ''})")
        self._gemini_decide_budget = new_budget
        # ── RPD FALLBACK ──────────────────────────────────────────────────────────────
        # Jika SEMUA key habis kuota RPD harian untuk model utama → fallback ke rules-based
        # trading sementara sehingga bot tidak diam total sampai reset tengah malam UTC.
        # Cek hanya saat Gemini aktif; model utama diambil dari GeminiTrader.client.models[0].
        _gemini_rpd_fallback = False
        if self.use_gemini_trader and self.gtrader is not None and rs.enabled:
            _all_models = (self.gtrader.client.models
                          if self.gtrader.client.models else ["gemini-3-flash-preview"])
            # Fallback ke rules-based HANYA bila SEMUA model (primary + fallback) mati di
            # SEMUA key — bukan cuma model index-0. Sebelumnya cek [0] saja menyebabkan bot
            # nyerah ke rules-based walau model fallback (mis. 3.1-flash-lite-preview) masih
            # segar & bisa dipakai.
            if all(_all_keys_dead(self.gtrader.client.keys, m) for m in _all_models):
                _gemini_rpd_fallback = True
                now_t = time.time()
                if now_t - self._last_rpd_warn > 3600:   # log+notif sekali per jam (anti-spam)
                    msg = (f"Semua {len(self.gtrader.client.keys)} key Gemini habis RPD harian "
                           f"untuk SEMUA model ({', '.join(_all_models)}) — fallback ke "
                           "rules-based trading siklus ini.")
                    log.warning(msg)
                    self.notify.send(f"⚠️ <b>GEMINI RPD HABIS</b>\n{msg}")
                    self._last_rpd_warn = now_t
        # ─────────────────────────────────────────────────────────────────────────────
        self._process_close_requests()
        if self.live:
            self._live_reconcile()        # sinkron posisi & saldo nyata dari Binance
        # EXIT DULU: SL/TP/liq murni aritmetika (harga Binance vs level) — WAJIB jalan tiap
        # siklus SEBELUM Gemini disentuh. Cegah posisi kena-TP tak tertutup saat LLM lambat/
        # hang (generate_content tanpa timeout bisa membekukan sisa siklus). Guard per-simbol:
        # error satu simbol tak boleh membatalkan sapuan exit yang lain.
        for sym in list(self.open):
            try:
                self._monitor_usd(sym, self._update_buffer(sym))
            except Exception as e:  # boundary — exit adalah jalur keselamatan, jangan pernah putus
                log.warning(f"exit-sweep {sym}: {e}")
        # rollover hari UTC → reset state circuit breaker
        today = pd.Timestamp.utcnow().date()
        if today != self._day:
            self._day, self._day_pnl, self._day_trades = today, 0.0, 0
            self._day_start_balance = self.balance_usd
        cb = self._circuit_breaker()
        if cb:
            log.info(f"Circuit breaker aktif ({cb}) — tidak buka posisi baru")
        news_veto, note = (self.news.check() if rs.enabled else (False, "off"))
        self._last_news_note = note if news_veto else ""
        if news_veto:
            log.info(f"News veto aktif ({note}) — tidak buka posisi baru siklus ini")
        vrp_on, _vrp_gap = (self.vrp.check() if rs.enabled else (False, None))
        vrp_block = self.vrp.mode == "enforce" and vrp_on   # shadow: TIDAK blokir
        if vrp_block:
            log.info("VRP brake ENFORCE aktif — tidak buka posisi baru siklus ini")
        ddlock = self._update_drawdown(rs)     # kill-switch drawdown TOTAL (tahan restart)
        self._apply_funding_sim()              # P3: akru funding posisi menginap (paper)
        self._refresh_plan(rs)                 # tujuan sesi (planner) → enforce di gerbang entry
        expo = self._exposure_frac()
        label = {1: "LONG", -1: "SHORT", 0: "skip"}
        gemini_candidates = []
        for sym in self.symbols:
            buf = self._update_buffer(sym)        # refresh DULU → monitor lihat high/low terbaru
            self._monitor_usd(sym, buf)           # cek SL/TP intrabar (tangkap wick antar-poll)
            c = self.sig_cache.setdefault(sym, {})
            if buf is None or len(buf) < 60:
                c["blocked"] = "data kurang"
                continue
            df_closed = buf.iloc[:-1]
            c["price"] = float(df_closed["close"].iloc[-1])
            # kelola posisi terbuka Gemini (~1 menit, exit-only) — terpisah dari entry per-bar
            now = time.time()
            if (self.use_gemini_trader and sym in self.open and rs.enabled
                    and now - self._last_manage.get(sym, 0) >= self._manage_interval):
                self._last_manage[sym] = now
                self._gemini_manage(sym, df_closed)
            # KAPAN evaluasi entry?
            #  - Gemini trader: timing BEBAS, TAPI di-throttle (≥ _decide_interval per simbol)
            #    & di-PRE-GATE (hanya tanya Gemini bila tak ada blokir murah & pasar cukup
            #    hidup — bukan arah rules) → mencegah ledakan token/rate-limit.
            #  - Teknik rules: hanya saat bar baru tertutup (sinyal berbasis bar).
            bar_closed = df_closed.index[-1] != self.last_closed.get(sym)
            throttled = now - self._last_decide.get(sym, 0) < self._decide_interval
            free_gemini = (self.use_gemini_trader and self.gtrader is not None
                           and rs.enabled and sym not in self.open and not throttled)
            if bar_closed or free_gemini:
                if bar_closed:
                    self.last_closed[sym] = df_closed.index[-1]
                if self.use_gemini_trader and self.gtrader is not None and not _gemini_rpd_fallback:
                    # Blokir MURAH dulu (tanpa panggil Gemini) — jangan buang token bila
                    # jelas tak akan buka posisi.
                    pre = (None if rs.enabled else "bot OFF")
                    pre = pre or ("news veto" if news_veto else None)
                    pre = pre or ("vrp brake" if vrp_block else None)
                    pre = pre or ("drawdown lock" if ddlock else None)
                    pre = pre or (cb or None)
                    pre = pre or ("sudah ada posisi" if sym in self.open else None)
                    pre = pre or ("slot penuh" if len(self.open) >= self.max_open else None)
                    if pre is None:                      # PRE-GATE murah: pasar HIDUP? (ARAH
                        #   diserahkan ke Gemini — jangan sandera trader pintar di balik rules lama).
                        #   Hanya saring pasar mati (ATR% < lantai) agar tak buang token. Lantai =
                        #   knob (calibration): naikkan bila token boros, turunkan bila sinyal langka.
                        _, gate_atr = self._signal(sym, df_closed)
                        floor = self.cfg.get("gemini", {}).get("pregate_atr_pct", 0.3)
                        if c["price"] and gate_atr / c["price"] * 100 < floor:
                            pre = "pre-gate: pasar terlalu sepi"
                    if pre is not None:
                        c["blocked"] = pre
                        continue
                    # ── PRICE CACHE ───────────────────────────────────────────────────
                    # Skip Gemini jika harga belum bergerak melewati threshold sejak decide
                    # terakhir — hemat RPD saat pasar stagnan tanpa melewatkan pergerakan nyata.
                    _price_cache_pct = float(
                        self.cfg.get("gemini", {}).get("price_cache_pct", 0.15))
                    _cached = self._decide_price_cache.get(sym)
                    if _cached is not None and _price_cache_pct > 0 and c["price"]:
                        _cached_price, _cached_dec = _cached
                        _price_delta_pct = abs(c["price"] - _cached_price) / _cached_price * 100
                        if _price_delta_pct < _price_cache_pct:
                            log.debug(f"{sym}: skip gemini price cache (Δ={_price_delta_pct:.3f}% < {_price_cache_pct}%)")
                            c["blocked"] = f"price cache: Δ{_price_delta_pct:.2f}%<{_price_cache_pct}%"
                            continue
                    # ─────────────────────────────────────────────────────────────────
                    if self._gemini_decide_used >= self._gemini_decide_budget:
                        # Kuota per-siklus habis — JANGAN set _last_decide (bukan throttle
                        # normal) agar simbol ini tetap prioritas dicoba di siklus BERIKUTNYA,
                        # bukan menunggu penuh _decide_interval lagi.
                        c["blocked"] = "kuota gemini per-siklus habis"
                        continue
                    self._gemini_decide_used += 1
                    self._last_decide[sym] = now         # throttle: tandai panggilan Gemini
                    gemini_candidates.append((sym, df_closed))
                else:
                    side, atr = self._signal(sym, df_closed)
                    c.pop("gemini", None)
                    c["side"] = label[side]
                    c["atr_pct"] = round(atr / c["price"] * 100, 3) if c["price"] else None
                    blocked = None
                    if not rs.enabled:
                        blocked = "bot OFF"
                    elif news_veto:
                        blocked = "news veto"   # alasan STABIL → dedup screening jalan;
                        #                          catatan detail ada di panel Riwayat News Veto
                    elif vrp_block:
                        blocked = "vrp brake"
                    elif ddlock:
                        blocked = "drawdown lock"   # detail alasan di status.drawdown
                    elif cb:
                        blocked = cb
                    elif sym in self.open:
                        blocked = "sudah ada posisi"
                    elif len(self.open) >= self.max_open:
                        blocked = "slot penuh"
                    elif side == 0:
                        blocked = "tak ada sinyal"
                    elif atr <= 0:
                        blocked = "ATR nol"
                    elif (conflict := self._corr_conflict(sym, side)):
                        blocked = f"korelasi tinggi dgn {conflict.split('/')[0]}"
                    elif self.use_planner and (pblock := self.planner.enforce(
                            self._session_plan, "long" if side == 1 else "short",
                            new_trades=self._session_trades, exposure_frac=expo)):
                        blocked = pblock                 # tunduk pada tujuan sesi (planner)
                    # Gerbang ReAct (teknik NON-gemini): keputusan AKTIF + dicatat ke decision_log.
                    # Hanya dipanggil saat semua cek deterministik lolos → hemat panggilan LLM.
                    if blocked is None and not self.use_gemini_trader:
                        permitted, action, reasoning = self._react_gate(sym, side, atr, df_closed, c["price"])
                        if not permitted:
                            blocked = f"agent {action}"
                            c["rationale"] = reasoning
                    c["blocked"] = blocked
                    if blocked is None:
                        self._open_usd(sym, side, atr, rs)
                        if sym in self.open:             # trade nyata terbuka → hitung utk kuota sesi
                            self._session_trades += 1
                            c["blocked"] = "→ posisi dibuka"

        # TAHAP 2: Jika ada kandidat Gemini, lakukan keputusan batch (decide_batch)
        if gemini_candidates:
            contexts = {}
            alt_data = {}
            for sym, df_closed in gemini_candidates:
                try:
                    fz, oid, imb, div = self._alt_arrays(sym, df_closed)
                    ret = df_closed["close"].pct_change().tail(20)
                    rv_pct = float(ret.std() * 100) if len(ret) > 2 else 0.0
                    alt = {"funding_z": round(float(fz[-1]), 3), "oi_delta": round(float(oid[-1]), 4),
                           "cvd_imb": round(float(imb[-1]), 3), "cvd_divergence": bool(div[-1]),
                           "realized_vol_pct": round(rv_pct, 3)}
                except Exception:
                    alt = {}
                alt_data[sym] = alt
                pos = self.open.get(sym)
                posview = {"side": pos["side"], "entry": round(pos["entry"], 6)} if pos else None
                ctx = self.gtrader.build_context(sym, df_closed, alt=alt, position=posview,
                                                 balance=self.balance_usd, news_note=self._last_news_note,
                                                 portfolio=self._portfolio_view(),
                                                 btc_lead=self._btc_lead())
                contexts[sym] = ctx

            decisions = self.gtrader.decide_batch(contexts)

            from .indicators import atr as _atr
            for sym, df_closed in gemini_candidates:
                dec = decisions.get(sym)
                if not dec:
                    dec = {"setup": "no_trade", "side": "flat", "conviction": 0.0, "rationale": "no decision in batch"}
                ctx = contexts.get(sym, {})
                alt = alt_data.get(sym, {})

                # Devil's Advocate
                if dec["side"] in ("long", "short") and self.rs is not None:
                    verdict = self.react.challenge_gemini(sym, dec["side"], dec.get("rationale", ""),
                                                          ctx.get("market", {}), alt)
                    if verdict and verdict["strength"] >= self.react.devil_threshold:
                        old = dec["conviction"]
                        dec["conviction"] = round(self.rs.downgrade_conf(old), 3)
                        top = verdict["objections"][0] if verdict["objections"] else "objection kuat"
                        dec["rationale"] = (dec.get("rationale", "") +
                                            f" | DEVIL {verdict['strength']:.2f} "
                                            f"({old:.2f}→{dec['conviction']:.2f}): {top}")[:200]

                atr_val = float(_atr(df_closed, self.cfg["signals"]["atr_period"]).iloc[-1])
                side = 1 if dec["side"] == "long" else (-1 if dec["side"] == "short" else 0)

                c = self.sig_cache.get(sym, {})
                c["gemini"] = {"dec": dec, "ctx": ctx}
                c["rationale"] = dec.get("rationale")
                c["setup"] = dec.get("setup")
                c["side"] = label[side]
                c["atr_pct"] = round(atr_val / c["price"] * 100, 3) if c["price"] else None
                # Update price cache: simpan harga & keputusan Gemini untuk siklus berikutnya
                if c.get("price"):
                    self._decide_price_cache[sym] = (c["price"], dec)

                blocked = None
                if not rs.enabled:
                    blocked = "bot OFF"
                elif news_veto:
                    blocked = "news veto"
                elif vrp_block:
                    blocked = "vrp brake"
                elif ddlock:
                    blocked = "drawdown lock"
                elif cb:
                    blocked = cb
                elif sym in self.open:
                    blocked = "sudah ada posisi"
                elif len(self.open) >= self.max_open:
                    blocked = "slot penuh"
                elif side == 0:
                    blocked = "tak ada sinyal"
                elif atr_val <= 0:
                    blocked = "ATR nol"
                elif (conflict := self._corr_conflict(sym, side)):
                    blocked = f"korelasi tinggi dgn {conflict.split('/')[0]}"
                elif self.use_planner and (pblock := self.planner.enforce(
                        self._session_plan, "long" if side == 1 else "short",
                        new_trades=self._session_trades, exposure_frac=expo)):
                    blocked = pblock

                c["blocked"] = blocked
                if blocked is None:
                    self._open_usd(sym, side, atr_val, rs)
                    if sym in self.open:
                        self._session_trades += 1
                        c["blocked"] = "→ posisi dibuka"
                    # else: _open_usd GAGAL diam-diam (margin habis/SL invalid/abstain/dll) —
                    # JANGAN timpa. _open_usd SUDAH menulis alasan gagal yang akurat ke
                    # sig_cache[sym]['blocked'] di titik early-return-nya sendiri; menimpanya
                    # dgn "posisi dibuka" di sini membuat UI klaim sukses padahal gagal.
        self._agent_portfolio_review(rs)   # POINT 2: agen otonom kelola portofolio (REDUCE_RISK/FLAT)
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
                pnl_usd = round(pos["qty"] * d, 4)
                roi = round(pnl_usd / pos["bet"] * 100, 2) if pos.get("bet") else 0.0
                pos_view = {"side": pos["side"], "entry": round(pos["entry"], 6),
                            "sl": round(pos["sl"], 6), "tp": round(pos["tp"], 6),
                            "liq": round(pos["liq"], 6), "pnl_usd": pnl_usd, "roi_pct": roi,
                            "qty": pos["qty"], "bet": pos.get("bet"), "mark": round(price, 6),
                            "opened_ts": pos.get("opened_ts")}   # utk marker panah entry di chart
            syms.append({"symbol": sym, "price": price, "atr_pct": c.get("atr_pct"),
                         "signal": c.get("side", "-"), "in_position": bool(pos),
                         "blocked": c.get("blocked"), "position": pos_view,
                         "rationale": c.get("rationale"), "setup": c.get("setup")})
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
            "gemini_decide_budget": self._gemini_decide_budget,   # budget DINAMIS siklus ini
            "gemini_decide_cap": self._gemini_decide_cap,
            "order_type": rs.order_type,
            "fee_pct": rs.fee_pct(),
            "day_pnl": round(self._day_pnl, 2),
            "day_trades": self._day_trades,
            "circuit_breaker": self._circuit_breaker(),
            "drawdown": {"locked": self._dd_lock, "reason": self._dd_reason or None,
                         "peak_balance": round(self._peak_balance, 2),
                         "dd_pct": round(self._dd_check(self._peak_balance, self.balance_usd,
                                                        100.0)[1], 2)},
            "corr_threshold": self.corr_threshold,
            "news_veto": {"active": news_active, "note": news_note},
            "symbols": syms,
        }
        try:
            from .store import set_kv
            set_kv(f"status:{self.settings.mode}", status)   # per-mode (multi-proses paralel)
            if not self.pin_mode:
                set_kv("status", status)                     # kompat: proses tunggal lama
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
