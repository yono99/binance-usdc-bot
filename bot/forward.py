"""Forward-test (paper) strategi v4 di data LIVE real-time — tanpa uang.

Tujuan: kumpulkan bukti out-of-sample SUNGGUHAN (masa depan, bukan histori) atas
satu set parameter TETAP. Tidak ada re-optimize saat jalan (itu menipu diri).

- Data: live publik (OHLCV + funding + OI + taker/CVD) — harga nyata.
- Eksekusi: paper (disimulasikan lokal, akuntansi identik backtest: fee+slippage).
- Opsional: mirror order ke Binance Futures Testnet untuk uji jalur eksekusi.
- Output: logs/forward_trades.jsonl + statistik berjalan (win%, expectancy R).
"""
from __future__ import annotations

import copy
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
from . import flat_shadow
from .planner import SessionPlanner, default_plan
from .react_agent import ReactAgent
from .signals import Signal
from . import entry_confluence as ec_gate
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
        # Tahap 1 (plan-sess): saldo PER-WALLET (USDT/USDC). balance_usd dihapus —
        # sistem sepenuhnya split per-wallet. Total saldo = balance_usdt + balance_usdc.
        self.balance_usdt = 0.0
        self.balance_usdc = 0.0
        self._last_cfg_balance_usdt = 0.0
        self._last_cfg_balance_usdc = 0.0
        self._last_news = None         # dedup histori news veto
        self._last_screen: dict = {}   # dedup histori screening per simbol
        self._recently_closed: dict = {}  # sym -> timestamp: prevent duplicate close journal
        self._base_slippage = self.slippage   # slippage market; limit (maker) = 0
        # state circuit breaker harian (reset tiap hari UTC) — default sebelum restore
        self._day = pd.Timestamp.utcnow().date()
        # Tahap 1 (plan-sess): PnL/start per-wallet TERPISAH (USDT vs USDC). _day_trades
        # GLOBAL (jumlah trade, bukan finansial; bisa lintas wallet). DD puncak per-wallet
        # juga — kill-switch independen agar keruntuhan wallet A tak lock wallet B.
        self._day_pnl_usdt = 0.0
        self._day_pnl_usdc = 0.0
        self._day_trades = 0
        self._day_start_balance_usdt = 0.0
        self._day_start_balance_usdc = 0.0
        self._eff_mode = self.settings.mode          # mode efektif berjalan
        self.live = (self.settings.mode == "live")   # True = order UANG NYATA
        # LIMIT entry resting (post-only/GTX) belum terisi: ditelusuri sbg PENDING, BUKAN posisi.
        self.pending: dict[str, dict] = {}
        self._pending_timeout_s = int(
            (self.cfg.get("execution") or {}).get("pending_timeout_s", 300) or 0)
        # Kill-switch drawdown TOTAL per-wallet (P1 tujuan compounding): puncak saldo per
        # wallet, kunci permanen (manual release via /api/dd-reset).  Default-nya: satu
        # reset melepas SEMUA wallet di mode itu (konsisten dengan semantik 'reset per-mode').
        self._peak_balance_usdt = 0.0
        self._peak_balance_usdc = 0.0
        self._dd_lock = False
        self._dd_reason = ""
        if self.use_store:
            self.rs = load_settings(self.settings.mode if self.pin_mode else None)
            self.params = self.rs.params()
            self.tf = self.rs.timeframe()
            resolved = self.rs.symbols or self.ex.perp_symbols(
                tuple(self.cfg["market"].get("settles", ["USDC"])))
            self.symbols = self._screened(resolved) if not self.rs.symbols else resolved
            # Tahap 1: saldo per-wallet distinct dari rs.balance_usdt/balance_usdc;
            # settings_store._from_dict sudah migrasi legacy single balance_usd → split.
            self.balance_usdt = float(self.rs.balance_usdt)
            self.balance_usdc = float(self.rs.balance_usdc)
            self._last_cfg_balance_usdt = self.balance_usdt
            self._last_cfg_balance_usdc = self.balance_usdc
            self._day_start_balance_usdt = self.balance_usdt
            self._day_start_balance_usdc = self.balance_usdc
            self._restore_state()
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
        self._manage_interval = 120               # detik minimum antar review posisi (~2 menit)
        self._min_hold_s = 300                    # GRACE anti-whipsaw: manajer tak exit sblm ditahan segini
        _gcfg = self.cfg.get("gemini", {})        # pemicu give-back menuju TP (knob kalibrasi)
        self._giveback_tp_frac = float(_gcfg.get("giveback_tp_frac", 0.5))
        self._giveback_margin = float(_gcfg.get("giveback_margin", 0.2))
        # Kuota panggilan Gemini per-SIKLUS (bukan per-simbol): budget dinamis (lihat
        # _recompute_decide_budget di bawah).
        self._gemini_decide_cap = int(_gcfg.get("gemini_decide_cap", 100))
        self._gemini_decide_budget = self._gemini_decide_cap
        self._gemini_decide_used = 0
        self._last_decide: dict = {}
        self._decide_interval = 60
        self._decide_price_cache: dict[str, tuple[float, dict]] = {}
        self._decide_cache: dict[str, dict] = {}  # AI decision cache (range regime hemat RPD)
        self._last_rpd_warn = 0.0
        self._allow_live_gemini = bool(self.cfg.get("gemini", {}).get("allow_live_trader", False))
        # ── SIDEWAYS SNIPER (profit konsisten walau sideways) ─────────────────────
        _sniper = self.cfg.get("gemini", {}).get("sideways_sniper", {})
        self._sideways_sniper = bool(_sniper.get("enabled", True))
        self._sniper_pregate_atr_range = float(_sniper.get("pregate_atr_pct_range", 0.01))
        self._sniper_price_cache_range = float(_sniper.get("price_cache_pct_range", 0.0))
        self._sniper_budget_boost_pct = float(_sniper.get("budget_boost_pct", 300))
        self._sniper_micro_tp_min = float(_sniper.get("micro_tp_pct_min", 0.005))
        self._sniper_micro_tp_max = float(_sniper.get("micro_tp_pct_max", 0.30))
        self._sniper_require_scalp = bool(_sniper.get("require_setup_scalp_range", True))
        self._sniper_range_bonus_mult = float(_sniper.get("range_bonus_mult", 3.0))
        self._sniper_devil_advocate_for_scalp = bool(_sniper.get("devil_advocate_for_scalp", False))

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
        Alt ber-beta lebih tinggi → gerak turun BTC sering diperbesar/diperpanjang di alt.

        Arsitektur profit konsisten: tambah `dump_flag` & `dominance_dir` utk short-priority.
        - `dump_flag`: True saat BTC turun >= dump_threshold (default 2%) 3-bar → alt beta>1
          akan turun LEBIH DALAM → SHORT alt = edge struktural (asymmetry logika user).
        - `dominance_dir`: +1 = BTC menguat vs alt (risk-off, alt lemah), -1 = alt outperform
          (risk-on, altseason). Pendekatan: bandingkan return BTC vs rata-rata top alt.
        - `halving_phase`: fase siklus 4-tahun (accumulation/pre-halving/bull/blow-off/bear).
        """
        buf = self.buffers.get("BTC/USDC:USDC")
        if buf is None or len(buf) < 5:
            return {}
        c = buf["close"]
        last, prev, prev3 = float(c.iloc[-2]), float(c.iloc[-3]), float(c.iloc[-5])
        r1 = (last / prev - 1) * 100 if prev else 0.0
        r3 = (last / prev3 - 1) * 100 if prev3 else 0.0
        # Defensive: self.cfg mungkin belum di-set (test ForwardTester.__new__)
        btc_cfg = getattr(self, "cfg", {}) or {}
        btc_dump_thr = float(btc_cfg.get("btc", {}).get("dump_pct", 0.5))
        dump_flag = r3 <= -abs(btc_dump_thr) * 4.0  # 4× dump_pct = 2% default → "lebih dari 2-5%"
        # dominance_dir: BTC vs rata-rata alt (proxy: cek jika ALT juga ada di buffer)
        # Bila alt turun lebih dari BTC saat BTC turun → BTC.D naik (risk-off) → short alt
        dominance_dir = 0
        if r3 < 0:  # BTC turun → cek apakah alt ikut lebih dalam
            # Sederhana: kalau BTC turun, dominance cenderung naik (alt lemah)
            dominance_dir = 1  # risk-off (BTC.D naik)
        elif r3 > 0:
            # BTC naik → dominance bisa turun (alt ikut naik lebih, risk-on) atau naik
            dominance_dir = -1  #假设 risk-on (alt outperform)
        return {
            "ret_1bar_pct": round(r1, 3),
            "ret_3bar_pct": round(r3, 3),
            "dir": 1 if r1 > 0 else (-1 if r1 < 0 else 0),
            "dump_flag": dump_flag,
            "dominance_dir": dominance_dir,
        }

    def _pair_cleanliness_check(self, symbol: str, df: pd.DataFrame) -> dict:
        """C3: Pair Cleanliness Filter for fade family setups.
        
        Filters out pairs with:
        1. High ADX (trending = bad for fade)
        2. Repeated long wicks (manipulation/liquidation risk)
        3. Unstable ATR (high std/mean of ATR)
        
        Args:
            symbol: trading pair
            df: OHLCV DataFrame (closed bars)
            
        Returns:
            {"allow": bool, "reason": str}
        """
        cfg = self.cfg.get("cleanliness", {})
        if not cfg.get("enabled", True):
            return {"allow": True, "reason": "cleanliness filter disabled"}
        
        lookback = cfg.get("lookback_bars", 50)
        if df is None or len(df) < lookback:
            return {"allow": False, "reason": f"insufficient data ({len(df)} < {lookback}) for cleanliness check"}
        
        from . import indicators as ind
        
        # 1. ADX check (trending = bad for fade)
        adx_val = float(ind.adx(df, 14)[0].iloc[-1])
        adx_max = cfg.get("max_adx", 25)
        if adx_val > adx_max:
            return {"allow": False, "reason": f"ADX {adx_val:.1f} > max {adx_max} (trending)"}
        
        # 2. Wick/body ratio (long wicks = manipulation/liq)
        recent = df.iloc[-20:]  # last 20 candles
        body = abs(recent["close"] - recent["open"])
        total_range = recent["high"] - recent["low"]
        total_range = total_range.replace(0, 1e-9)
        upper_wick = recent["high"] - recent[["open", "close"]].max(axis=1)
        lower_wick = recent[["open", "close"]].min(axis=1) - recent["low"]
        max_wick = (upper_wick.combine(lower_wick, max) / total_range).mean()
        wick_threshold = cfg.get("max_wick_body_ratio", 0.7)
        if max_wick > wick_threshold:
            return {"allow": False, "reason": f"avg wick/body {max_wick:.2f} > {wick_threshold} (manipulation risk)"}
        
        # 3. ATR stability
        atr_vals = ind.atr(df, 14).dropna()
        if len(atr_vals) < 20:
            return {"allow": False, "reason": "insufficient ATR data"}
        atr_recent = atr_vals.iloc[-20:]
        atr_mean = float(atr_recent.mean())
        atr_std = float(atr_recent.std())
        cv = atr_std / atr_mean if atr_mean > 0 else float('inf')
        cv_threshold = cfg.get("max_atr_cv", 0.5)
        if cv > cv_threshold:
            return {"allow": False, "reason": f"ATR CV {cv:.2f} > {cv_threshold} (unstable volatility)"}
        
        return {"allow": True, "reason": "cleanliness passed"}

    def _halving_phase(self) -> str:
        """Deteksi fase siklus halving BTC (~4 tahun). Tanggal halving historis:
        2012-11-28, 2016-07-09, 2020-05-11, 2024-04-19 (estimasi).
        Fase: 'accumulation' (jauh sebelum halving), 'pre-halving' (6 bulan sebelum),
        'post-halving' (1 tahun setelah = bull market), 'blow-off' (1.5-2 tahun setelah),
        'bear' (>2 tahun setelah).  """
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            # Halving berikutnya (estimasi 2028-03 kira-kira)
            halvings = [datetime(2012, 11, 28), datetime(2016, 7, 9),
                        datetime(2020, 5, 11), datetime(2024, 4, 19)]
            # Cari halving terakhir & berikutnya
            last_h = max(h for h in halvings if h.replace(tzinfo=timezone.utc) <= now)
            years_since = (now - last_h.replace(tzinfo=timezone.utc)).days / 365.25
            if years_since < 0.5:
                return "post-halving"  # awal bull
            elif years_since < 1.0:
                return "bull"           # bull market aktif
            elif years_since < 2.0:
                return "blow-off"      # menjelang puncak/bear
            elif years_since < 3.0:
                return "bear"          # bear market
            else:
                return "accumulation"  # menuju halving berikutnya
        except Exception:
            return "unknown"

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
        one_r = (self.balance_usdt + self.balance_usdc) * self.risk_frac
        day_pnl_total = self._day_pnl_usdt + self._day_pnl_usdc
        daily_pnl_r = day_pnl_total / one_r if one_r else 0.0
        kw = dict(regime=regime, alt=alt, n_positions=len(self.open),
                  max_positions=self.max_open, daily_pnl_r=daily_pnl_r,
                  lessons=self.lessons.recent(10), shadow=self.ab_shadow,
                  memory=self.agent_memory,     # ingat observasi/keputusan lintas-tick
                  btc_lead=self._btc_lead(),                # dominansi BTC (alt ber-beta lebih tinggi)
                  halving_phase=self._halving_phase())       # fase siklus halving (macro regime)
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
        risk0 = pos.get("risk0") or abs(pos["entry"] - pos["sl"]) * pos["qty"]  # 1R beku saat open
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
        """Fraksi eksposur TOTAL terhadap saldo agregat (display). Sizing policy per-wallet
        (lihat _open_usd → pool per-quote via _quote_pool) — guard ini informatif saja."""
        total = self.balance_usdt + self.balance_usdc
        if total <= 0:
            return 1.0
        return sum((p.get("bet") or 0) for p in self.open.values()) / total

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
        # Tahap 1: planner lihat saldo per-wallet (USDC vs USDT) untuk konteks kekuatan
        # modal — egocentered book; Gemini bisa saja punya bias berbeda per wallet.
        ctx = {"balance_usdc": round(self.balance_usdc, 2),
               "balance_usdt": round(self.balance_usdt, 2),
               "day_pnl_usd": round(self._day_pnl_usdt + self._day_pnl_usdc, 2),
               "bet_usd": rs.bet_usd, "leverage": rs.leverage,
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
        # Tahap 1: risk denominated per saldo agregat (informasi planner/agent — sizing
        # aktual tetap per-wallet di _open_usd).
        one_r = (self.balance_usdt + self.balance_usdc) * self.risk_frac
        day_pnl_total = self._day_pnl_usdt + self._day_pnl_usdc
        dec = self.react.manage_portfolio(self._portfolio_view(),
                                          daily_pnl_r=(day_pnl_total / one_r if one_r else 0.0),
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
        """Snapshot SEMUA posisi terbuka + eksposur — konteks korelasi/risiko untuk Gemini.
        Tahap 1 (plan-sess): expose saldo per-wallet sehingga Gemini paham modal available
        di masing-masing quote saat memberi sinyal."""
        positions = [{"symbol": s, "side": p["side"], "entry": round(p["entry"], 6),
                      "bet": p.get("bet"), "quote": ("USDC" if s.endswith(":USDC") else "USDT")}
                     for s, p in self.open.items()]
        return {"positions": positions, "count": len(positions),
                "exposure_usd": round(sum(p.get("bet", 0) or 0 for p in self.open.values()), 2),
                "balance_usdc": round(self.balance_usdc, 2),
                "balance_usdt": round(self.balance_usdt, 2),
                "max_open": self.max_open}

    def _gemini_manage(self, sym: str, df_closed: pd.DataFrame) -> None:
        """Review posisi terbuka Gemini (~1 menit). Hanya boleh KURANGI risiko.

        Lapis 1 — Hard gate progress (lihat fix_exit_gemini.md):
        GEMINI_EXIT terbukti -EV (exp_R=-0.253, n=11, sum_R=-2.785 dari 67 trade
        live per 2026-07-09). Sumbang lebih dari total kerugian seluruh sistem.
        Gate ini memastikan Gemini exit-review HANYA dipanggil saat posisi PERNAH
        mencapai >=50% ke TP DAN reversal signifikan >=15pp dari puncak.
        """
        # ── Konstanta gate progress (tunable) ────────────────────────────────
        MIN_PEAK_TO_ASK = 0.5    # progress ke TP minimal PERNAH tercapai
        REVERSAL_BUFFER = 0.15   # turun ≥15pp dari puncak = reversal signifikan
        # ─────────────────────────────────────────────────────────────────────
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
        if prog is not None:
            pos["peak_tp_prog"] = max(pos.get("peak_tp_prog", prog), prog)
        # Micro-profit lock SUDAH dipindah ke _monitor_usd (threshold 0.6)
        # ── Lapis 1: Hard gate progress (bukti: gemini_exit exp_R=-0.253) ──
        peak = pos.get("peak_tp_prog")
        if prog is not None and peak is not None:
            if peak < MIN_PEAK_TO_ASK:
                # Belum pernah mencapai ≥50% TP → skip Gemini exit-review
                return
            if peak - prog < REVERSAL_BUFFER:
                # Reversal belum cukup signifikan → skip
                return
        # ────────────────────────────────────────────────────────────────────
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
            # ── Lapis 2: Kill-switch empiris gemini_exit (bukti: exp_R=-0.253,n=11) ──
            # Ambil exit_track_record terbaru; cari entry "gemini_exit".
            # Bila n≥10 dan exp_r<0 → blokir eksekusi, biarkan SL/TP native jalan.
            _blocked = False
            try:
                _records = self.gtrader._exit_track_record() if self.gtrader else []
                for _r in _records:
                    if _r.get("reason") == "gemini_exit" and _r.get("n", 0) >= 10 and _r.get("exp_r", 0.0) < 0:
                        log.warning(f"GEMINI_EXIT DIBLOKIR (kill-switch empiris): "
                                    f"exp_r={_r['exp_r']:.3f} n={_r['n']} — "
                                    f"SL/TP native yang tentukan nasib {sym}")
                        _blocked = True
                        break
            except Exception as _e:
                log.debug(f"exit_track_record {sym}: {_e}")   # opsional, jangan blokir
            if _blocked:
                return
            # ── Lapis 3: Anti-cut prematur (profit mikro jangan dipotong) ──
            # Bukti: gemini_exit avg_R=-0.056 → memotong profit kecil yang lalu pulih ke TP.
            # Jangan exit bila unrealized_R positif kecil (biarkan TP bekerja). Cut hanya bila
            # tesis benar-benar rusak (R negatif signifikan atau reversal besar).
            try:
                _risk = abs(pos.get("entry", 0) - pos.get("sl", 0)) or 1e-9
                _is_long = pos.get("side") == "long"
                _unreal_r = ((price - pos["entry"]) if _is_long else (pos["entry"] - price)) / _risk
                if _unreal_r > 0.2:   # posisi profit > +0.2R → jangan dipotong, sabar ke TP
                    log.info(f"GEMINI_EXIT ditunda {sym}: unreal_R=+{_unreal_r:.2f} (profit mikro, "
                             f"sabar ke TP) — SL/TP native yang tentukan nasib")
                    return
            except Exception:
                pass
            # ──────────────────────────────────────────────────────────────────────
            log.info(f"Gemini EXIT {sym} @ {price:.6f} — {act.get('reason', '')[:80]}")
            self._close_usd(sym, price, "gemini_exit")
        elif action == "tighten_stop" and not self.live:   # live = exit-only (jaga proteksi)
            if valid_tighten(pos["side"], pos["sl"], act.get("new_sl"), price,
                             entry=pos.get("entry")):
                old = pos["sl"]
                pos["sl"] = round(float(act["new_sl"]), 6)
                log.info(f"Gemini tighten SL {sym}: {old:.6f} → {pos['sl']:.6f}")

    @staticmethod
    def _gemini_score(df, price: float, atr: float, since_decide_s: float,
                      range_bonus_mult: float = 1.0) -> float:
        """Skor "menarik" kandidat kuota Gemini: volatilitas × gerakan terakhir +
        boost anti-starvation (jam sejak decide terakhir, cap 2j). Murah & deterministik.

        Sideways sniper (arsitektur 26-key): bila regime=range (ADX ≤ adx_range, default 15),
        kalikan skor dgn range_bonus_mult (default 1.0 = sunyi; 3.0 = 3× boost agar range
        tak kalah bersaing dgn simbol trend di ranking budget). ATR rendah di range → skor
        kecil tanpa boost → budget habis di simbol trend → scalp_range tak pernah dapat giliran."""
        try:
            ret5 = abs(float(df["close"].iloc[-1] / df["close"].iloc[-6] - 1)) * 100
        except Exception:
            ret5 = 0.0
        atr_pct = atr / price * 100 if price else 0.0
        base = atr_pct * (1 + ret5) + min(since_decide_s / 3600, 2.0) * 0.1
        return base * range_bonus_mult

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

    @staticmethod
    def _is_range(df, cfg) -> bool:
        """Deteksi regime=range MURAH untuk sideways sniper (tak panggil _market_summary
        penuh — hemat CPU saat dipakai tiap siklus utk tiap simbol). Pure."""
        try:
            from .indicators import adx as _adx, atr as _atr
            st = cfg["strategy"]
            adx_v = float(_adx(df, cfg["signals"]["adx_period"])[0].iloc[-1])
            return adx_v <= st.get("adx_range", 18)
        except Exception:  # boundary — tak yakin regime = tak sideways sniper
            return False

    @staticmethod
    def _swing_range_tp(df, entry: float, is_long: bool) -> float | None:
        """TP dinamis berbasis swing range untuk scalp_range:
        - LONG (pos_in_range < 0.5 = support) → TP ke swing_high (resisten)
        - SHORT (pos_in_range > 0.5 = resisten) → TP ke swing_low (support)
        Mengambil seluruh osilasi range yang tersedia.
        Return None bila swing terlalu dekat / tidak valid (fallback ke micro-TP config)."""
        try:
            from .indicators import atr as _atr
            lookback = min(50, len(df) - 1)
            if lookback < 20:
                return None
            recent = df.iloc[-lookback:]
            high = recent["high"].max()
            low = recent["low"].min()
            if high <= low:
                return None
            pos_in_range = (entry - low) / (high - low) if high > low else 0.5
            if is_long:
                # LONG di support (pos < 0.5) → target resisten
                if pos_in_range < 0.5:
                    return high
            else:
                # SHORT di resisten (pos > 0.5) → target support
                if pos_in_range > 0.5:
                    return low
            return None
        except Exception:
            return None

    @staticmethod
    def _sniper_cache_add(cache: dict, sym: str, is_range: bool) -> None:
        """Catat regime-range per simbol untuk decision budget boost (dipakai ranking)."""
        if cache is not None:
            cache[sym] = is_range

    def _close_trade(self, sym: str, price: float, reason: str) -> None:
        """Legacy single-callback close (dipakai backtest live forward). PnL ke wallet
        yang sesuai quote pair (Tahap 1)."""
        pos = self.open.pop(sym)
        tr = self.bt._close(pos, price, pd.Timestamp.utcnow(), 0, reason)
        self.trades.append(tr)
        vrp.log_close(sym, pos, tr.r, mode=self.settings.mode)
        self.equity *= (1 + self.risk_frac * tr.r)
        # legacy path menggunakan single equity multiplier — tak ada split wallet.
        journal("forward_close", {"symbol": sym, "side": pos.get("side"), "entry": round(pos["entry"], 6),
                                  "exit": price, "r": round(tr.r, 4),
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
        # Tahap 1: equity via agregasi saldo per-wallet kalau store-path; legacy pakai self.equity.
        eq = round(self.balance_usdt + self.balance_usdc, 2) if self.use_store else round(self.equity, 2)
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
        update puncak saldo, kunci bila tembus ambang (PER-WALLET). Return alasan
        blokir/None. Kunci PERMANEN (persisten, tahan restart) — lepas HANYA via
        /api/dd-reset — keputusan manusia dengan kepala dingin (bukan auto-reset)."""
        from . import store as _store
        try:
            if _store.get_kv(f"dd_reset_{self.settings.mode}"):
                _store.set_kv(f"dd_reset_{self.settings.mode}", {})   # habis pakai
                self._dd_lock, self._dd_reason = False, ""
                self._peak_balance_usdt = self.balance_usdt
                self._peak_balance_usdc = self.balance_usdc
                log.warning("DRAWDOWN LOCK direset MANUAL — puncak saldo per-wallet "
                            f"di-set ulang (USDT ${self.balance_usdt:.2f}, "
                            f"USDC ${self.balance_usdc:.2f}).")
        except Exception as e:  # boundary
            log.warning(f"cek dd_reset gagal: {e}")
        # Tahap 1 (plan-sess): puncak & cek PER-WALLET — keruntuhan wallet A tak lock B.
        self._peak_balance_usdt = max(self._peak_balance_usdt, self.balance_usdt)
        self._peak_balance_usdc = max(self._peak_balance_usdc, self.balance_usdc)
        hit_usdt, dd_usdt = self._dd_check(self._peak_balance_usdt, self.balance_usdt,
                                           rs.max_drawdown_pct)
        hit_usdc, dd_usdc = self._dd_check(self._peak_balance_usdc, self.balance_usdc,
                                           rs.max_drawdown_pct)
        hit = hit_usdt or hit_usdc
        if hit and not self._dd_lock:
            self._dd_lock = True
            wallet = "USDT+USDC" if (hit_usdt and hit_usdc) else ("USDT" if hit_usdt else "USDC")
            dd = max(dd_usdt, dd_usdc)
            self._dd_reason = (f"drawdown total ({wallet}) {dd:.1f}% ≥ "
                               f"{rs.max_drawdown_pct:.0f}% "
                               f"dari puncak USDT ${self._peak_balance_usdt:.2f} / "
                               f"USDC ${self._peak_balance_usdc:.2f}")
            log.error(f"DRAWDOWN LOCK: {self._dd_reason} — entry DIBLOKIR sampai "
                      "reset manual (POST /api/dd-reset).")
            self.notify.send(f"🛑 <b>DRAWDOWN LOCK</b>\n{self._dd_reason}\n"
                             "Entry diblokir sampai reset manual dari dashboard.")
        return self._dd_reason if self._dd_lock else None

    def _apply_settings(self) -> RuntimeSettings:
        rs = load_settings(self.settings.mode if self.pin_mode else None)
        from .settings_store import get_active_mode, _eff_mode
        requested = self.settings.mode if self.pin_mode else get_active_mode()
        eff = _eff_mode(requested)
        log.info(f"_apply_settings: requested={requested!r}, eff={eff!r}, _eff_mode={self._eff_mode!r}, pin_mode={self.pin_mode}")
        if eff != self._eff_mode and not self.pin_mode:   # pinned: tak pernah switch
            log.warning(f"MODE SWITCH: {self._eff_mode} -> {eff}")
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
        # PnL biasa tidak menyentuh _last_cfg_balance*, jadi tak terdeteksi sebagai edit.
        changed = (abs(rs.balance_usdt - self._last_cfg_balance_usdt) > 1e-9
                   or abs(rs.balance_usdc - self._last_cfg_balance_usdc) > 1e-9)
        if changed:
            # Safety: jangan overwrite balance kalau perbedaannya <2% dari total.
            # Mencegah dashboard stale-save merusak balance hasil PnL.
            _old_total = self.balance_usdt + self.balance_usdc
            _new_total = rs.balance_usdt + rs.balance_usdc
            _pct_diff = abs(_new_total - _old_total) / max(_old_total, 0.01) * 100
            if _pct_diff < 2.0:
                log.info(f"Balance change {_pct_diff:.2f}% terlalu kecil — "
                         f"dilewati (old=${_old_total:.2f} new=${_new_total:.2f})")
            else:
                self.balance_usdt = rs.balance_usdt
                self.balance_usdc = rs.balance_usdc
                # Reset day_start_balance agar PnL hari ini dihitung dari saldo BARU (bukan saldo lama).
                # Hindari PnL palsu positif/negatif saat user hanya set saldo awal.
                self._day_start_balance_usdt = self.balance_usdt
                self._day_start_balance_usdc = self.balance_usdc
                self._day_pnl_usdt = 0.0
                self._day_pnl_usdc = 0.0
                log.info(f"Saldo diubah dari UI -> USDT ${self.balance_usdt:.2f}, "
                         f"USDC ${self.balance_usdc:.2f} (day_start direset, PnL=0)")
        self._last_cfg_balance_usdt = rs.balance_usdt
        self._last_cfg_balance_usdc = rs.balance_usdc
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
        # Tahap 1 (plan-sess): state lama 'balance' tunggal di-migrasi in-memory ke per-wallet
        # bila belum ada field balance_usdt/balance_usdc (back-compat: setara split 50/50).
        if "balance_usdt" not in st and "balance_usdc" not in st:
            legacy = float(st.get("balance", 0.0))
            if legacy > 0:
                half = legacy / 2.0
                st["balance_usdt"] = half
                st["balance_usdc"] = legacy - half
        # hanya pulihkan bila konfigurasi saldo tak diubah user sejak terakhir simpan
        # (per-wallet comparison — fix latent bug: total comparison tak tangkap rebalance)
        cfg_usdt = float(st.get("cfg_balance_usdt", self._last_cfg_balance_usdt))
        cfg_usdc = float(st.get("cfg_balance_usdc", self._last_cfg_balance_usdc))
        if (abs(cfg_usdt - self._last_cfg_balance_usdt) < 1e-9
                and abs(cfg_usdc - self._last_cfg_balance_usdc) < 1e-9):
            self.balance_usdt = float(st.get("balance_usdt", self.balance_usdt))
            self.balance_usdc = float(st.get("balance_usdc", self.balance_usdc))
        self.open = st.get("open", {}) or {}
        self.pending = st.get("pending", {}) or {}           # LIMIT resting pulih dari restart
        self.agent_memory.restore(st.get("agent_memory"))   # memori lintas-tick tahan restart
        # pulihkan state circuit breaker harian bila masih hari yang sama (UTC)
        if st.get("day") == str(pd.Timestamp.utcnow().date()):
            # Tahap 1: pulihkan PnL per-wallet terpisah (back-compat: field 'day_pnl' lawas)
            self._day_pnl_usdt = float(st.get("day_pnl_usdt", st.get("day_pnl", 0.0)))
            self._day_pnl_usdc = float(st.get("day_pnl_usdc", 0.0))
            self._day_trades = int(st.get("day_trades", 0))
            self._day_start_balance_usdt = float(st.get(
                "day_start_balance_usdt", st.get("day_start_balance", self.balance_usdt)))
            self._day_start_balance_usdc = float(st.get("day_start_balance_usdc", self.balance_usdc))
        # drawdown-total: puncak & kunci PER-WALLET, BERTAHAN melewati restart (Tahap 1).
        # LIVE MODE: jangan pulihkan dd_lock dari state lama (hindari carry-over dari test/dry)
        if self.live:
            self._peak_balance_usdt = self.balance_usdt
            self._peak_balance_usdc = self.balance_usdc
            self._dd_lock = False
            self._dd_reason = ""
        else:
            self._peak_balance_usdt = max(float(st.get("peak_balance_usdt", 0.0)), self.balance_usdt)
            self._peak_balance_usdc = max(float(st.get("peak_balance_usdc", 0.0)), self.balance_usdc)
            self._dd_lock = bool(st.get("dd_lock", False))
            self._dd_reason = str(st.get("dd_reason", ""))
        if self.open:
            log.info(f"State dipulihkan dari SQLite: saldo USDC ${self.balance_usdc:.2f}, "
                     f"USDT ${self.balance_usdt:.2f}, {len(self.open)} posisi terbuka")

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
            # Tahap 1: tulis state per-wallet. Legacy 'balance'/'cfg_balance' dihapus.
            set_kv(self._state_key, {"balance_usdt": round(self.balance_usdt, 6),
                                "balance_usdc": round(self.balance_usdc, 6),
                                "open": self.open,
                                "pending": self.pending,            # LIMIT resting tahan restart
                                "cfg_balance_usdt": self._last_cfg_balance_usdt,
                                "cfg_balance_usdc": self._last_cfg_balance_usdc,
                                "day": str(self._day),
                                "day_pnl_usdt": round(self._day_pnl_usdt, 4),
                                "day_pnl_usdc": round(self._day_pnl_usdc, 4),
                                "day_trades": self._day_trades,
                                "day_start_balance_usdt": round(self._day_start_balance_usdt, 6),
                                "day_start_balance_usdc": round(self._day_start_balance_usdc, 6),
                                "peak_balance_usdt": round(self._peak_balance_usdt, 6),
                                "peak_balance_usdc": round(self._peak_balance_usdc, 6),
                                "dd_lock": self._dd_lock, "dd_reason": self._dd_reason,
                                "agent_memory": self.agent_memory.snapshot()})
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
            self.pending = {}                   # pending order lama tak valid
            # Isolasi per-mode HARUS ikut pindah di sini — tanpa ini, _persist_state()
            # & journal terus menulis ke bucket mode LAMA setelah switch runtime,
            # mencampur saldo/riwayat lintas mode (insiden 2026-07-02).
            from .logger import set_journal_mode
            set_journal_mode(eff)
            decision_log.set_mode(eff)
            self._state_key = f"botstate_{eff}"
            if self.live:
                balances = self.ex.balances(self.balance_usdt + self.balance_usdc)
                self.balance_usdt = float(balances.get("USDT", 0.0))
                self.balance_usdc = float(balances.get("USDC", 0.0))
                self._last_cfg_balance_usdt = self.balance_usdt
                self._last_cfg_balance_usdc = self.balance_usdc
                # Reset peak balances & drawdown lock saat masuk LIVE (hindari carry-over dari test/dry)
                self._peak_balance_usdt = self.balance_usdt
                self._peak_balance_usdc = self.balance_usdc
                self._dd_lock = False
                self._dd_reason = ""
                self._sync_live_positions()     # ambil posisi nyata yang sudah ada
            else:
                # paper: mulai dari saldo KONFIGURASI mode tujuan (bukan carry-over
                # dari mode sebelumnya), lalu pulihkan bucket SQLite milik mode itu.
                try:
                    rs_eff = load_settings(eff)
                    self.balance_usdt = float(rs_eff.balance_usdt)
                    self.balance_usdc = float(rs_eff.balance_usdc)
                    self._last_cfg_balance_usdt = self.balance_usdt
                    self._last_cfg_balance_usdc = self.balance_usdc
                except Exception as e:  # boundary
                    log.warning(f"load balance mode {eff} gagal: {e}")
                self._restore_state()
            self._day = pd.Timestamp.utcnow().date()
            self._day_pnl_usdt = 0.0
            self._day_pnl_usdc = 0.0
            self._day_trades = 0
            self._day_start_balance_usdt = self.balance_usdt
            self._day_start_balance_usdc = self.balance_usdc
            self._eff_mode = eff
            if self.live:
                log.warning(f"=== BERALIH KE LIVE (UANG NYATA) — saldo Binance USDT ${self.balance_usdt:.2f} + USDC ${self.balance_usdc:.2f} ===")
                self.notify.send(f"⚠️ <b>MODE LIVE AKTIF — UANG NYATA</b>\nSaldo USDT ${self.balance_usdt:.2f} + USDC ${self.balance_usdc:.2f}")
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

    def _live_open(self, sym, is_long, qty, entry, sl, tp, rs) -> tuple[bool, float | None, dict | None]:
        """Tempatkan order ENTRY nyata. Return (ok, fill_price|None, pending|None).
        - LIMIT + GTX (post-only): hampir selalu RESTING (status 'open'/'new') → JANGAN
          pasang SL/TP atau daftar self.open. Kembalikan pending={order_id,...} agar
          caller menelusurinya; reconcile akan pasang SL/TP & pindah ke self.open saat terisi.
        - MARKET / limit yang langsung terisi: pasang SL/TP sisi-exchange → (True, fill, None).
        - Lengkapnya krn KRITIS: entry & SL/TP di try/except TERPISAH. Bila entry TERISI
          (uang REAL sudah masuk) tapi SL/TP gagal ditempatkan, JANGAN klaim gagal total
          (posisi_open=False) — itu membuat posisi TELANJANG hilang dari self.open (live:
          _monitor_usd tak enforce SL/TP sendiri, percaya penuh ke order exchange). Coba
          emergency-close; kalau JUGA gagal, tetap lacak (ok=True) drpd bot buta thd eksposur."""
        try:
            self.ex.set_leverage(sym, rs.leverage)
            side_str = "buy" if is_long else "sell"
            if rs.order_type == "limit":
                order = self.ex.client.create_order(sym, "limit", side_str, qty, entry, {"timeInForce": "GTX"})
            else:
                order = self.ex.client.create_order(sym, "market", side_str, qty)
        except Exception as e:  # boundary — entry sendiri gagal → aman, tak ada eksposur
            log.error(f"LIVE OPEN {sym} gagal (entry): {e}")
            self.notify.send(f"❌ <b>LIVE OPEN GAGAL</b> {sym}\n{str(e)[:140]}")
            return False, entry, None
        status = str(order.get("status") or "").lower()
        order_id = order.get("id")
        # LIMIT post-only RESTING (tak langsung terisi) → telusuri sbg pending, BUKAN posisi.
        # Tanda: status 'open'/'new'/'untriggered' DAN average kosong (= belum ada fill).
        if rs.order_type == "limit" and status in ("open", "new", "untriggered") and not order.get("average"):
            log.info(f"LIVE LIMIT {sym} RESTING (order_id={order_id}) — telusuri sbg pending, "
                     f"SL/TP belum dipasang sampai terisi")
            return True, None, {"order_id": order_id, "qty": qty, "entry": entry,
                                "sl": sl, "tp": tp, "is_long": is_long,
                                "placed_ts": pd.Timestamp.utcnow().isoformat()}
        fill = float(order.get("average") or entry)
        try:
            close_side = "sell" if is_long else "buy"
            # SL/TP dijaga exchange (tetap aktif walau bot mati)
            self.ex.client.create_order(sym, "STOP_MARKET", close_side, qty, None,
                                        {"stopPrice": sl, "reduceOnly": True})
            self.ex.client.create_order(sym, "TAKE_PROFIT_MARKET", close_side, qty, None,
                                        {"stopPrice": tp, "reduceOnly": True})
            return True, fill, None
        except Exception as e:  # boundary — entry SUDAH terisi, SL/TP gagal → posisi telanjang
            log.error(f"LIVE OPEN {sym}: entry terisi TAPI SL/TP gagal ({e}) — emergency close")
            try:
                self.ex.client.create_order(sym, "market", close_side, qty, None, {"reduceOnly": True})
                self.ex.client.cancel_all_orders(sym)
                self.notify.send(f"⚠️ <b>LIVE OPEN {sym}</b>: SL/TP gagal dipasang → "
                                 f"emergency-close berhasil. Tak ada posisi tersisa.\n{str(e)[:140]}")
                return False, fill, None                  # posisi sudah ditutup lagi → aman
            except Exception as e2:  # boundary — emergency close JUGA gagal: posisi telanjang NYATA
                self.notify.send(
                    f"🚨 <b>DARURAT</b> {sym}: entry live terisi, SL/TP GAGAL, emergency-close "
                    f"JUGA GAGAL — posisi TELANJANG tanpa proteksi!\nTutup MANUAL segera di exchange.\n"
                    f"{str(e)[:100]} | {str(e2)[:100]}")
                log.error(f"{sym}: posisi telanjang tak terlindungi — intervensi manual WAJIB")
                return True, fill, None                   # WAJIB tetap dilacak — jangan hilang total

    def _live_close(self, sym: str, pos: dict) -> None:
        """Tutup posisi nyata (reduceOnly market) + batalkan SL/TP + pending tersisa."""
        try:
            close_side = "sell" if pos["side"] == "long" else "buy"
            self.ex.client.create_order(sym, "market", close_side, pos["qty"], None, {"reduceOnly": True})
            self.ex.client.cancel_all_orders(sym)
        except Exception as e:  # boundary
            log.error(f"LIVE CLOSE {sym} gagal: {e}")
        self.pending.pop(sym, None)               # bersihkan pending bila ada

    def _live_partial_close(self, sym: str, pos: dict, close_qty: float, price: float, reason: str) -> None:
        """Tutup SEBAGIAN posisi nyata (reduceOnly market) — utk partial TP."""
        try:
            close_side = "sell" if pos["side"] == "long" else "buy"
            self.ex.client.create_order(sym, "market", close_side, close_qty, None, {"reduceOnly": True})
            log.info(f"LIVE PARTIAL CLOSE {reason} {sym}: qty={close_qty:.6f} @ {price:.6f}")
        except Exception as e:  # boundary
            log.error(f"LIVE PARTIAL CLOSE {sym} gagal: {e}")

    def _live_reconcile(self) -> None:
        """Sinkron posisi nyata dari Binance: deteksi yang sudah tertutup (SL/TP/liq),
        update saldo dari equity nyata, bersihkan order yatim.
        JUGA: reconcile LIMIT entry yang masih RESTING (self.pending → self.open bila
        terisi, atau dibuang bila cancel/expire/timeout)."""
        try:
            real = {}
            for p in self.ex.positions():
                if float(p.get("contracts") or 0) != 0:
                    real[p.get("symbol")] = p
        except Exception as e:  # boundary
            log.error(f"reconcile live gagal: {e}")
            return
        # --- RECONCILE PENDING: LIMIT resting → filled / canceled / timeout ---
        if self.pending:
            try:
                oo_by_id = {}
                for o in self.ex.open_orders():
                    oid = o.get("id")
                    if oid:
                        oo_by_id[oid] = o
            except Exception as e:  # boundary
                log.warning(f"fetch open_orders untuk reconcile gagal: {e}")
                oo_by_id = {}
            now = pd.Timestamp.utcnow()
            for sym, pend in list(self.pending.items()):
                oid = pend.get("order_id")
                odata = oo_by_id.get(oid)
                # Terisi: posisi muncul di real ATAU order sudah closed/filled
                is_filled = sym in real or (odata is None and not odata)
                if sym in real:
                    is_filled = True
                if odata is not None:
                    ost = str(odata.get("status") or "").lower()
                    if ost in ("closed", "filled", "expired"):
                        is_filled = True if ost in ("closed", "filled") else False
                    else:
                        is_filled = False
                # Timeout: pending terlalu lama resting → cancel order & skip
                is_timeout = False
                if self._pending_timeout_s > 0 and not is_filled:
                    try:
                        placed = pd.Timestamp(pend.get("placed_ts"))
                        elapsed = (now - placed).total_seconds()
                        if elapsed > self._pending_timeout_s:
                            is_timeout = True
                    except Exception:
                        pass
                if is_timeout:
                    log.warning(f"LIMIT PENDING TIMEOUT {sym} ({oid}) — cancel")
                    try:
                        self.ex.client.cancel_order(oid, sym)
                    except Exception:
                        pass
                    self.pending.pop(sym, None)
                    journal("forward_pending_timeout", {"symbol": sym, "order_id": oid})
                    self.notify.send(f"⏰ <b>LIMIT TIMEOUT</b> {sym} — dibatalkan (terlalu lama)")
                elif is_filled:
                    # Pindah pending → open: pasang SL/TP sekarang
                    fill_price = float(real[sym].get("entryPrice") or pend["entry"]) if sym in real else float(odata.get("average") or pend["entry"]) if odata else pend["entry"]
                    sl, tp = pend["sl"], pend["tp"]
                    qty = pend["qty"]
                    is_long = pend["is_long"]
                    close_side = "sell" if is_long else "buy"
                    sl_tp_ok = True
                    try:
                        self.ex.client.create_order(sym, "STOP_MARKET", close_side, qty, None,
                                                    {"stopPrice": sl, "reduceOnly": True})
                        self.ex.client.create_order(sym, "TAKE_PROFIT_MARKET", close_side, qty, None,
                                                    {"stopPrice": tp, "reduceOnly": True})
                    except Exception as e:  # boundary — SL/TP gagal setelah fill
                        log.error(f"LIMIT FILLED {sym} tapi SL/TP gagal ({e}) — emergency close")
                        sl_tp_ok = False
                        try:
                            self.ex.client.create_order(sym, "market", close_side, qty, None,
                                                        {"reduceOnly": True})
                            self.ex.client.cancel_all_orders(sym)
                            self.notify.send(f"⚠️ <b>LIMIT FILLED {sym}</b> tapi SL/TP gagal → "
                                             f"emergency close\n{str(e)[:140]}")
                        except Exception as e2:
                            self.notify.send(f"🚨 <b>DARURAT</b> {sym}: limit terisi, SL/TP gagal, "
                                             f"emergency close JUGA gagal — tutup MANUAL\n{str(e)[:100]}")
                    self.pending.pop(sym, None)
                    if sl_tp_ok:
                        # Daftar sebagai posisi terisi
                        self.open[sym] = {
                            "side": "long" if is_long else "short",
                            "entry": fill_price, "qty": qty, "sl": sl, "tp": tp,
                            "liq": pend.get("liq", 0.0), "bet": pend.get("bet", 0.0),
                            "risk0": pend.get("risk0", 0.0),
                            "entry_fee_rate": pend.get("entry_fee_rate", 0.0),
                            "opened_ts": pend.get("placed_ts"),
                            **self.vrp.stamp(), **self._regime_stamp(None, self.cfg)}
                        # Gemini data
                        for k in ("gdecision", "setup", "conviction"):
                            if k in pend:
                                self.open[sym][k] = pend[k]
                        journal("forward_open_filled", {"symbol": sym, "side": self.open[sym]["side"],
                                                         "entry": fill_price, "sl": sl, "tp": tp,
                                                         "liq": pend.get("liq"), "order_id": oid})
                        log.info(f"LIMIT FILLED {sym} @ {fill_price:.4f} SL={sl:.4f} TP={tp:.4f}")
                        self.notify.send(
                            f"✅ <b>LIMIT FILLED</b> {sym}\n"
                            f"Entry {fill_price:.4f} · SL {sl:.4f} · TP {tp:.4f}")
                else:
                    # Masih resting — cek jika posisi TIDAK ada di real DAN order TIDAK ada di oo
                    # → berarti cancel/expire manual
                    if odata is None and sym not in real:
                        self.pending.pop(sym, None)
                        journal("forward_pending_cancel", {"symbol": sym, "order_id": oid})
                        log.info(f"LIMIT PENDING BATAL {sym} (cancel/expire) — dibuang")
                        self.notify.send(f"❌ <b>LIMIT BATAL</b> {sym} — cancel/expire")
        # --- RECONCILE POSISI: deteksi close (SL/TP/liq/manual) ---
        # Tahap 1: live R belajar dr Δequity agregat (backtest uang riil; multi-wallet
        # delta gabung-normal). Akurat bila TEPAT SATU posisi tutup dalam satu siklus
        # (lihat guard `if len(gem_closed) == 1` dsb).
        prev_balance = self.balance_usdt + self.balance_usdc
        prev_balance_usdt = self.balance_usdt
        prev_balance_usdc = self.balance_usdc
        closed = [(sym, self.open[sym]) for sym in list(self.open) if sym not in real]
        for sym, _pos in closed:
            self.open.pop(sym, None)
            try:
                self.ex.client.cancel_all_orders(sym)   # bersihkan SL/TP yatim
            except Exception:
                pass
            journal("forward_close", {"symbol": sym, "reason": "live_exit",
                                      "equity": round(self.balance_usdt + self.balance_usdc, 2)})
            log.info(f"LIVE CLOSE terdeteksi {sym}")
            self.notify.send(f"✋ <b>LIVE CLOSE</b> {sym} (SL/TP/manual)")
        balances = self.ex.balances(self.balance_usdt + self.balance_usdc)
        self.balance_usdt = float(balances.get("USDT", 0.0))
        self.balance_usdc = float(balances.get("USDC", 0.0))
        # Tahap 1 (plan-sess): PnL harian per-wallet dari equity nyata. Untuk paper/dry,
        # reconcile ini tak dipanggil — _close_usd sudah update _day_pnl_<wallet>.
        self._day_pnl_usdt = self.balance_usdt - self._day_start_balance_usdt
        self._day_pnl_usdc = self.balance_usdc - self._day_start_balance_usdc
        # BELAJAR di LIVE — HANYA dengan data PnL NYATA & TAK AMBIGU (tepat satu posisi tutup
        # siklus ini). Jika banyak tutup bersamaan, lewati (jangan ajari Gemini data kotor).
        gem_closed = [(s, p) for s, p in closed if p.get("gdecision") and self.gtrader is not None]
        if len(gem_closed) == 1:
            sym, pos = gem_closed[0]
            try:
                r = ((self.balance_usdt + self.balance_usdc) - prev_balance) / pos["bet"] if pos.get("bet") else 0.0
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
            outcome_r = ((self.balance_usdt + self.balance_usdc) - prev_balance) / pos["bet"] if pos.get("bet") else 0.0
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

    def _quote_pool(self, quote: str) -> float:
        """Saldo margin untuk satu quote (USDC/USDT). LIVE: dompet asli terpisah (balances()).
        DRY: ambil langsung dari self.balance_<wallet> yang sudah independen per-wallet
        (Tahap 1 — split eksplisit; tak lagi pakai dry_quote_split_usdc)."""
        if self.live:
            return self.ex.balances(0.0).get(quote, 0.0)
        return self.balance_usdt if quote == "USDT" else self.balance_usdc

    @staticmethod
    def _adaptive_bet(pool: float, bet_usd: float, bet_pct: float,
                      locked: float, gem_conv: float | None = None) -> float:
        """Ukuran margin ADAPTIF: bet_pct>0 → %pool (auto-scale $10→naik), else bet_usd tetap.
        Skala conviction Gemini (lantai 20%), lalu CAP ke margin BEBAS pool (pool − terkunci
        di quote yang SAMA) agar akun modal-minim tak 'diam total'. 0.0 bila pool habis."""
        bet = pool * (bet_pct / 100.0) if bet_pct > 0 else bet_usd
        if gem_conv is not None:
            bet = max(bet * gem_conv, bet * 0.2)
        avail = pool - locked
        if avail < 0.10:
            return 0.0
        return min(bet, avail)

    def _open_usd(self, sym: str, side: int, atr: float, rs: RuntimeSettings) -> None:
        if sym in self.open:
            log.warning(f"DUPOPEN BLOCKED {sym}: already in self.open")
            return
        # Tahap 4a (plan-sess): cooldown/blacklist per-mode PERSEISTEN di SQLite via
        # bot.cooldown — skip simbol yg masih cooldown atau di-blacklist setelah SL streak
        # (config rotate.cooldown_minutes/blacklist_after_sl).
        try:
            from . import cooldown as _cd
            if not _cd.available(self.settings.mode, sym):
                c = self.sig_cache.setdefault(sym, {})
                snap = _cd.snapshot(self.settings.mode)
                _until = max(snap.get("cooldown_until", {}).get(sym, 0),
                             snap.get("blacklist_until", {}).get(sym, 0))
                _rem = max(0, int(_until - time.time()))
                c["blocked"] = (f"cooldown/blacklist mode={self.settings.mode} ({_rem}s)")
                return
        except Exception as e:  # boundary — jangan gagalkan entry karena cooldown handler
            log.debug(f"cooldown check {sym}: {e}")
        gem = self.sig_cache.get(sym, {}).get("gemini") if self.use_gemini_trader else None
        # GERBANG LIVE: Gemini-trader tak boleh order UANG NYATA tanpa izin eksplisit.
        if gem and self.live and not self._allow_live_gemini:
            log.warning(f"{sym}: Gemini-trader DIBLOKIR di LIVE (set gemini.allow_live_trader: "
                        "true di config.yaml untuk mengizinkan order uang nyata).")
            c = self.sig_cache.setdefault(sym, {})
            c["blocked"] = "gemini-live dimatikan (config)"
            return
        gem_conv = float(gem["dec"].get("conviction", 0.0) or 0.0) if gem else None
        # ── ASYMMETRIC SHORT SIZING (arsitektur profit konsisten):
        # Saat BTC dump ≥2% (dump_flag=True) → alt beta>1 turun LEBIH DALAM → SHORT edge.
        # Boost conviction 1.5× utk SHORT saat dump_flag (cap 1.0).
        if gem and side == -1:  # SHORT
            btc_lead = gem.get("ctx", {}).get("btc_lead", {})
            if btc_lead.get("dump_flag", False):
                gem_conv = min(gem_conv * 1.5, 1.0)
                log.info(f"ASYMMETRIC SHORT {sym}: dump_flag=True → conviction boost 1.5× = {gem_conv:.3f}")
        # Gerbang SIZE berbasis confidence (Phase 2 kalibrasi): tier menggantikan skala
        # conviction kontinu lama. Jalur rule-based (gem_conv=None) TIDAK digerbang —
        # tak punya angka confidence; selalu ukuran penuh (pilihan terdokumentasi).
        size_mult = rs.conf_size_mult(gem_conv)
        if size_mult is None:                       # ABSTAIN: confidence < conf_min
            c = self.sig_cache.setdefault(sym, {})
            c["blocked"] = f"SKIPPED: low_confidence ({gem_conv:.2f} < {rs.conf_min:.2f})"
            journal("forward_skip", {"symbol": sym, "reason": "low_confidence",
                                     "conviction": round(gem_conv, 3),
                                     "conf_min": rs.conf_min,
                                     "side": "long" if side == 1 else "short" if side == -1 else None,
                                     "setup": (gem["dec"].get("setup") if gem else None)})
            log.info(f"SKIP {sym}: confidence {gem_conv:.2f} < {rs.conf_min:.2f} (abstain)")
            return
        quote = "USDC" if sym.endswith(":USDC") else "USDT"
        pool = self._quote_pool(quote)            # margin per-quote (dompet terpisah di live)
        # Tahap 1 (plan-sess): notional locked per-WALLET (book per-quote) — bukan agregat.
        # Position USDC tak menambah eksposur wallet USDT dan sebaliknya.
        locked_q = sum((p.get("bet") or 0) for s, p in self.open.items()
                       if (s.endswith(":USDC") and quote == "USDC")
                       or (not s.endswith(":USDC") and quote == "USDT"))
        # Portfolio exposure guard per-wallet: max_portfolio_exposure_pct dihitung dr
        # notional pair X vs balance_<wallet> — bukan total saldo. Wallets independen.
        # Default 100% (no guard) → backward-compat bila field tak di-set user.
        try:
            max_pct = float(getattr(rs, "max_portfolio_exposure_pct", 100.0) or 100.0)
        except Exception:
            max_pct = 100.0
        if max_pct < 100.0:
            wallet_bal = self.balance_usdt if quote == "USDT" else self.balance_usdc
            cap = wallet_bal * max_pct / 100.0
            if locked_q >= cap:
                c = self.sig_cache.setdefault(sym, {})
                c["blocked"] = (f"exposure {quote} cap {locked_q:.2f}≥${cap:.2f} "
                                f"({max_pct:.0f}% of ${wallet_bal:.2f})")
                return
        bet = self._adaptive_bet(pool, rs.bet_usd, rs.bet_pct, locked_q, size_mult)
        if bet <= 0:
            c = self.sig_cache.setdefault(sym, {})
            c["blocked"] = f"margin {quote} habis (pool ${pool:.2f})"
            return
        price = float(self.ex.ticker(sym)["last"])
        is_long = side == 1
        slip = 1 + self.slippage / 100 if is_long else 1 - self.slippage / 100
        entry = price * slip
        qty = (bet * rs.leverage) / entry
        # USE CONFIG SL/TP MULTIPLIERS FOR ALL REGIMES (1.75/2.6 per config.yaml)
        _sl_mult = self.bt.sl_mult
        _tp_mult = self.bt.tp_mult
        sl = entry - atr * _sl_mult if is_long else entry + atr * _sl_mult
        # ── SIDEWAYS SNIPER: micro-TP override untuk setup scalp_range di regime=range.
        # Saat gemini setup='scalp_range' & sideways_sniper aktif → override target_profit_pct
        # ke nilai mikro (config: 0.005-0.30%) WALAU tanpa target_profit_pct runtime. Tujuan:
        # profit konsisten walau kecil — jangan tunggu RR 1.2×ATR (lambat di sideways tipis).
        _sniper_setup = (gem and self._sideways_sniper and self._sniper_require_scalp
                         and gem["dec"].get("setup") == "scalp_range")
        _sniper_rng = False
        _rb2 = self.buffers.get(sym)
        if _sniper_setup and _rb2 is not None and len(_rb2) >= 30:
            _sniper_rng = self._is_range(_rb2.iloc[:-1], self.cfg)
        if _sniper_setup and _sniper_rng:
            # TP DINAMIS berbasis pos_in_range: entry di support (pos<0.5) → TP ke swing_high
            # (resisten); entry di resisten (pos>0.5) → TP ke swing_low. Mengambil seluruh
            # osilasi range yang tersedia — lebih konsisten daripada TP fixed ATR-based.
            # Jika swing terlalu dekat (< micro_tp_min) → fallback micro-TP config.
            _micro_tp_pct = None
            try:
                _swing_tp = self._swing_range_tp(_rb2, entry, is_long)
                if _swing_tp is not None:
                    _swing_pct = abs(_swing_tp - entry) / entry * 100
                    if _swing_pct >= self._sniper_micro_tp_min:
                        tp = _swing_tp
                        _micro_tp_pct = _swing_pct
                        log.info(f"SIDEWAYS SNIPER {sym}: swing-TP {_swing_pct:.3f}% "
                                 f"(scalp_range, pos_in_range dinamis)")
            except Exception:
                pass
            if _micro_tp_pct is None:   # fallback: micro-TP config statis
                _user_tp = rs.target_profit_pct if rs.target_profit_pct > 0 else self._sniper_micro_tp_max
                _micro_tp = max(self._sniper_micro_tp_min, min(_user_tp, self._sniper_micro_tp_max))
                tp = entry * (1 + _micro_tp / 100) if is_long else entry * (1 - _micro_tp / 100)
                log.info(f"SIDEWAYS SNIPER {sym}: micro-TP {_micro_tp:.3f}% (scalp_range regime=range)")
        elif rs.target_profit_pct > 0:
            tp = entry * (1 + rs.target_profit_pct / 100) if is_long else entry * (1 - rs.target_profit_pct / 100)
        else:
            # ── PHASE 3: Structured TP for fade family v2 (range_fade_v2, scalp_range_v2)
            # Only applies to fade family v2 setups (not regular range_fade/scalp_range)
            gem = self.sig_cache.get(sym, {}).get("gemini") if self.use_gemini_trader else None
            setup_id = gem["dec"].get("setup") if gem else None
            fade_v2_setups = ("range_fade_v2", "scalp_range_v2")
            
            if setup_id in fade_v2_setups:
                # Get opposite valid level from S/R detection
                try:
                    from . import levels as lvl_mod
                    side_str = "long" if is_long else "short"
                    level_type = "resistance" if is_long else "support"  # opposite level for TP
                    has_level, level = lvl_mod.find_nearest_level(
                        sym, entry, level_type, max_distance_atr_mult=10.0)  # wide search
                    structural_tp = level.price if has_level else None
                except Exception:
                    structural_tp = None
                
                # Cap target: 5% price move from entry
                cap_pct = 5.0  # 5% price move cap
                cap_tp = entry * (1 + cap_pct / 100) if is_long else entry * (1 - cap_pct / 100)
                
                # Final TP = min(structural, cap) — whichever is closer to entry
                if structural_tp is not None:
                    dist_structural = abs(structural_tp - entry)
                    dist_cap = abs(cap_tp - entry)
                    tp = structural_tp if dist_structural <= dist_cap else cap_tp
                    log.info(f"FADE v2 {sym}: structured TP={'structural' if dist_structural <= dist_cap else 'cap'} @ {tp:.6f} "
                             f"(structural={structural_tp:.6f}, cap_5%={cap_tp:.6f})")
                else:
                    # Fallback to ATR-based TP if no structural level
                    tp = entry + atr * _tp_mult if is_long else entry - atr * _tp_mult
                    log.info(f"FADE v2 {sym}: fallback ATR TP @ {tp:.6f} (no structural level)")
                
                # Partial TP: 75% at target, 25% trailing
                # Store partial TP info in position for exit logic
                c = self.sig_cache.setdefault(sym, {})
                c["partial_tp_pct"] = 0.75  # 75% at first target
                c["partial_tp_price"] = tp
            else:
                tp = entry + atr * _tp_mult if is_long else entry - atr * _tp_mult
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
        # Fix A: LANTAI jarak SL — SKIP bila regime=range (SL ketat 1×ATR sengaja pendek;
        # lantai 1.75×ATR akan melebarkan kembali, membatalkan ketatnya range scalping).
        # Lantai Kalibrasi untuk SEMUA regime (anti-SL-kemepet setelah candle raksasa).
        buf = self.buffers.get(sym)
        last_range = (float(buf["high"].iloc[-2] - buf["low"].iloc[-2])
                      if buf is not None and len(buf) >= 2 else 0.0)
        sl = self._sl_floor(entry, is_long, sl, atr, last_range)
        if (is_long and sl <= liq) or (not is_long and sl >= liq):
            sl = (entry + liq) / 2                  # kompromi: selebar mungkin, tetap aman
        # Tahap 2 (plan-sess): MARGIN ISOLATED — set leverage + margin_type SEBELUM order.
        # Idempotent: cache per-simbol + skip bila posisi sudah terbuka (catch error,
        # log, lanjut). DRY: cukup stempel metadata; tidak ada API call.
        if self.live:
            try:
                # set_margin_isolated hanya bila belum ada posisi di simbol tsb
                if sym not in self.open:
                    self.ex.set_margin_isolated(sym)
                self.ex.set_leverage(sym, rs.leverage)   # idempotent di Binance
            except Exception as e:  # boundary — set margin/leverage gagal TIDAK memblokir entry
                log.warning(f"margin/leverage setup {sym} (best-effort, lanjut): {e}")
        if self.live:                               # UANG NYATA: order asli + SL/TP exchange
            try:
                qty = float(self.ex.client.amount_to_precision(sym, qty))
            except Exception:
                pass
            if qty <= 0:
                c = self.sig_cache.setdefault(sym, {})
                c["blocked"] = "qty live nol setelah presisi → skip"
                return
            ok, entry, pending = self._live_open(sym, is_long, qty, entry, sl, tp, rs)
            if not ok:
                c = self.sig_cache.setdefault(sym, {})
                c["blocked"] = "order live gagal → skip"
                return
            if pending:                          # LIMIT resting: telusuri, BUKAN posisi
                self.pending[sym] = {
                    **pending,                   # order_id, qty, entry, sl, tp, is_long, placed_ts
                    "bet": bet, "liq": liq, "risk0": abs(entry - sl) * qty,
                    "opened_ts": pending["placed_ts"],
                    "entry_fee_rate": entry_fee_rate, "leverage": rs.leverage,
                    **self.vrp.stamp(), **self._regime_stamp(self.buffers.get(sym), self.cfg),
                    **(mtf_stamp := (self.mtf.stamp(self.buffers.get(sym), self.tf, side)
                                     if self.buffers.get(sym) is not None else {}))}
                if gem:
                    self.pending[sym].update(conviction=gem_conv)
                    try:
                        self.pending[sym]["gdecision"] = self.gtrader.commit(sym, gem["dec"], gem["ctx"])
                        self.pending[sym]["setup"] = gem["dec"].get("setup")
                    except Exception as e:  # boundary
                        log.warning(f"commit keputusan gemini {sym} (pending) gagal: {e}")
                self._day_trades += 1
                journal("forward_open_pending", {"symbol": sym, "side": "long" if is_long else "short",
                                                  "entry": entry, "sl": sl, "tp": tp, "liq": liq,
                                                  "order_id": pending.get("order_id"), "bet": bet,
                                                  "conviction": gem_conv})
                log.info(f"LIMIT RESTING {sym} bet=${bet:.2f} @ {entry:.4f} "
                         f"(order_id={pending.get('order_id')}) — menunggu terisi")
                self.notify.send(
                    f"⏳ <b>LIMIT RESTING</b> {sym}\n"
                    f"@ {entry:.4f} · SL {sl:.4f} · TP {tp:.4f} (menunggu terisi)")
                c = self.sig_cache.setdefault(sym, {})
                c["blocked"] = "→ limit order resting (menunggu terisi)"
                return
        buf_full = self.buffers.get(sym)
        mtf_stamp = (self.mtf.stamp(buf_full, self.tf, side)
                     if buf_full is not None else {})   # kesepakatan multi-TF (shadow)
        settle = "USDC" if sym.endswith(":USDC") else "USDT"
        entry_fee_rate = rs.fee_rate(settle, rs.order_type == "limit")   # kaki ENTRY: maker bila limit
        self.open[sym] = {"side": "long" if is_long else "short", "entry": entry, "qty": qty,
                          "sl": sl, "tp": tp, "liq": liq, "bet": bet,
                          "risk0": abs(entry - sl) * qty,   # 1R BEKU saat open — SL boleh di-trail, R tidak ikut bergeser
                          "entry_fee_rate": entry_fee_rate,   # fee kaki-entry (per-settle); exit selalu taker
                          "opened_ts": pd.Timestamp.utcnow().isoformat(),  # utk marker panah di chart
                          "margin_type": "ISOLATED",  # Tahap 2: metadata isolated (default; live=exchange, paper=stempel)
                          "leverage": rs.leverage,
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
                # Tahap 4a: catat cooldown/blacklist per-mode (DRY-only; live sudah rekonsiliasi).
            return
        # Prevent duplicate close journaling (race condition / re-processing guard)
        now = time.time()
        last = self._recently_closed.get(sym, 0)
        if now - last < 600:  # skip if same symbol closed within 600 seconds (10 menit)
            log.warning(f"DUPLICATE CLOSE BLOCKED {sym}: last close {now - last:.0f}s ago (reason={reason})")
            return
        # Double-check DB: sudah ada forward_close untuk simbol ini <600 detik lalu?
        try:
            from .store import close_exists
            if close_exists(self.settings.mode, sym, since=now - 600):
                log.warning(f"DUPLICATE CLOSE BLOCKED (DB) {sym}: close exists in DB within 600s")
                self._recently_closed[sym] = now  # sync guard
                return
        except Exception:
            pass
        self._recently_closed[sym] = now
        pos = self.open.pop(sym)
        # Tahap 4a: cooldown/blacklist per-mode (DRY) — config rotate.cooldown_minutes &
        # blacklist_after_sl. Live juga di-catat tapi efektif di mode tersebut.
        try:
            from . import cooldown as _cd
            _r_cfg = self.cfg.get("rotate", {}) or {}
            _cd.record_close(self.settings.mode, sym, was_sl=(reason == "sl"),
                             cooldown_minutes=float(_r_cfg.get("cooldown_minutes", 0) or 0),
                             blacklist_after_sl=int(_r_cfg.get("blacklist_after_sl", 0) or 0),
                             blacklist_hours=float(_r_cfg.get("blacklist_hours", 6) or 6))
        except Exception as e:  # boundary
            log.debug(f"cooldown record_close {sym}: {e}")
        self._decide_price_cache.pop(sym, None)  # invalidasi cache: state berubah, butuh decide baru
        self._decide_cache.pop(sym, None)        # AI cache juga invalid
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
        # Tahap 1 (plan-sess): PnL apply PER-WALLET sesuai quote pair — wallet USDC ke
        # posisi pair USDC, wallet USDT ke posisi pair USDT (DOMPET TERPISAH di Binance).
        if sym.endswith(":USDC"):
            self.balance_usdc += pnl
            self._day_pnl_usdc += pnl
        else:
            self.balance_usdt += pnl
            self._day_pnl_usdt += pnl
        # R = jarak-SL AWAL (risk0 dibekukan saat open). Fallback ke SL sekarang hanya
        # untuk posisi lama tanpa stempel — dengan catatan SL ter-trail (breakeven/tighten)
        # membuat penyebut ~0 → R meledak (bug PLAY/USDT R=-231).
        risk0 = pos.get("risk0") or abs(pos["entry"] - pos["sl"]) * pos["qty"]
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
        # Settle Entry Confluence shadow outcome_r (sync last shadow record for this symbol)
        try:
            from . import store
            _ec_recent = store.entry_confluence_shadow_stats(limit=10)
            for _er in _ec_recent:
                if _er.get("symbol") == sym and _er.get("side") == pos.get("side") and _er.get("outcome_r") is None:
                    store.settle_entry_confluence_outcome(_er["id"], outcome_r=r)
                    break
        except Exception as _e:
            log.debug(f"settle ec shadow {sym}: {_e}")

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
        journal("forward_close", {"symbol": sym, "side": pos.get("side"), "entry": round(pos["entry"], 6),
                                  "exit": round(exit_fill, 6), "reason": reason,
                                  "pnl_usd": round(pnl, 4), "r": round(r, 4),
                                  "regime": pos.get("regime", "unknown"),
                                  "mae_pct": round(pos.get("mae_pct", 0.0), 3),
                                  "mfe_pct": round(pos.get("mfe_pct", 0.0), 3),
                                  "funding_usd": round(pos.get("funding_paid", 0.0), 4),
                                  "equity": round(self.balance_usdt + self.balance_usdc, 2)})
        _wallet_total = self.balance_usdt + self.balance_usdc
        log.info(f"CLOSE {reason.upper()} {sym} pnl=${pnl:+.2f} bal=${_wallet_total:.2f}")
        icon = {"liq": "💥 <b>LIKUIDASI</b>", "sl": "🛑 SL", "tp": "✅ TP",
                "manual": "✋ CLOSE", "eod": "⏹ EOD"}.get(reason, reason)
        self.notify.send(f"{icon} {sym}\nPnL ${pnl:+.2f} · R {r:+.2f} · saldo ${_wallet_total:.2f}")
        # Cleanup old entries (older than 10 minutes) to prevent memory leak
        cutoff = time.time() - 600
        self._recently_closed = {k: v for k, v in self._recently_closed.items() if v > cutoff}
        # Persist state segera setelah close. Tanpa ini, crash antara pop() dan _persist_state()
        # di akhir cycle bikin _restore_state load posisi LAMA → SL trigger lagi → close duplikat.
        self._persist_state()

    def _close_partial_usd(self, sym: str, price: float, pct: float, reason: str) -> None:
        """Close a fraction (pct) of position at given price.
        
        Args:
            sym: symbol
            price: exit price
            pct: fraction to close (0.75 = 75%)
            reason: close reason (e.g., "tp_partial")
        """
        if sym not in self.open:
            return
        pos = self.open[sym]
        is_long = pos["side"] == "long"
        
        # Calculate partial quantities
        orig_qty = pos["qty"]
        close_qty = orig_qty * pct
        remain_qty = orig_qty - close_qty
        
        if close_qty <= 0:
            return
            
        # Calculate PnL for partial close
        exit_fill = price * (1 - self.slippage / 100 if is_long else 1 + self.slippage / 100)
        move = (exit_fill - pos["entry"]) if is_long else (pos["entry"] - exit_fill)
        
        # Fee calculation (same as _close_usd)
        settle = "USDC" if sym.endswith(":USDC") else "USDT"
        entry_rate = pos.get("entry_fee_rate")
        if entry_rate is None:
            entry_rate = self.rs.fee_rate(settle, self.rs.order_type == "limit") if self.rs else self.fee
        exit_rate = self.rs.fee_rate(settle, False) if self.rs else self.fee
        fee = (entry_rate / 100 * pos["entry"] + exit_rate / 100 * exit_fill) * close_qty
        funding = pos.get("funding_paid", 0.0) * (close_qty / orig_qty) if orig_qty > 0 else 0.0
        pnl = max(close_qty * move - fee - funding, -pos["bet"] * pct)
        
        # Apply PnL to correct wallet
        if sym.endswith(":USDC"):
            self.balance_usdc += pnl
            self._day_pnl_usdc += pnl
        else:
            self.balance_usdt += pnl
            self._day_pnl_usdt += pnl
            
        # R calculation
        risk0 = pos.get("risk0") or abs(pos["entry"] - pos["sl"]) * orig_qty
        r = pnl / risk0 if risk0 else 0.0
        self.trades.append(namedtuple("T", ["r"])(r * pct))  # scale R by partial
        
        # Update position
        pos["qty"] = remain_qty
        pos["bet"] = pos["bet"] * (1 - pct)  # reduce bet proportionally
        
        # Log
        journal("forward_close", {"symbol": sym, "side": pos.get("side"), "entry": round(pos["entry"], 6),
                                  "exit": round(exit_fill, 6), "reason": reason,
                                  "pnl_usd": round(pnl, 4), "r": round(r, 4),
                                  "regime": pos.get("regime", "unknown"),
                                  "mae_pct": round(pos.get("mae_pct", 0.0), 3),
                                  "mfe_pct": round(pos.get("mfe_pct", 0.0), 3),
                                  "funding_usd": round(funding, 4),
                                  "equity": round(self.balance_usdt + self.balance_usdc, 2)})
        _wallet_total = self.balance_usdt + self.balance_usdc
        log.info(f"PARTIAL CLOSE {reason.upper()} {sym} pct={pct*100:.0f}% pnl=${pnl:+.2f} bal=${_wallet_total:.2f}")
        
        # Live mode: actually close partial on exchange
        if self.live:
            try:
                self._live_partial_close(sym, pos, close_qty, exit_fill, reason)
            except Exception as e:
                log.warning(f"live partial close {sym}: {e}")

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
            # Micro-profit lock: saat peak ≥60% TP, kunci SL ke breakeven
            if peak >= 0.6:
                _be = pos["entry"]
                _tighter = _be > pos["sl"] if long else _be < pos["sl"]
                if _tighter:
                    pos["sl"] = _be
                    log.info(f"Micro-profit lock {sym}: SL→breakeven (peak_prog={peak:.2f})")
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
            # Partial TP for fade family v2 (structured TP with partial close)
            if pos.get("partial_tp_pct") and pos.get("partial_tp_price"):
                partial_pct = pos["partial_tp_pct"]
                tp_price = pos["partial_tp_price"]
                # Close partial_pct (e.g., 75%) at TP, remaining trails
                self._close_partial_usd(sym, tp_price, partial_pct, "tp_partial")
                # Remaining position: switch to trailing stop
                pos["trailing_active"] = True
                pos["trailing_sl"] = pos["sl"]  # start trailing from original SL
                log.info(f"PARTIAL TP {sym}: closed {partial_pct*100:.0f}% @ {tp_price:.6f}, "
                         f"remaining {1-partial_pct:.0%} trailing")
            else:
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
        closed_any = False
        for sym in targets:
            if sym in self.open:
                try:
                    price = float(self.ex.ticker(sym)["last"])
                except Exception:  # boundary — coba lagi siklus berikutnya
                    remaining.append(sym)
                    continue
                self._close_usd(sym, price, "manual")
                closed_any = True
        try:
            p.write_text(_json.dumps(remaining), encoding="utf-8")
        except Exception as e:  # boundary
            log.warning(f"tulis close_requests gagal: {e}")
        # Sync status & state SEGERA setelah manual close → dashboard real-time
        if closed_any and self.use_store:
            rs = self._apply_settings()
            self._write_status(rs, self._last_news_note != "", self._last_news_note)
            self._persist_state()

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
        """Kembalikan alasan stop bila circuit breaker harian trip, else None.

        Tahap 1 (plan-sess): per-wallet TERPISAH — rugi USDC tak trigger stop trading USDT.
        Trade count tetap GLOBAL (jumlah, bukan finansial). Limit kerugian per-wallet dihitung
        dr `daily_max_loss_pct * day_start_balance_<wallet>` — kapitalisasi eksposur sama
        dengan risk per-wallet tak tertular."""
        if self.daily_max_trades and self._day_trades >= self.daily_max_trades:
            return f"limit trade harian ({self._day_trades}/{self.daily_max_trades})"
        if self.daily_max_loss_pct > 0:
            # Per-wallet independent check. Return label wallet mana yang trip (debugging jelas).
            usdt_limit = self._day_start_balance_usdt * self.daily_max_loss_pct / 100
            usdc_limit = self._day_start_balance_usdc * self.daily_max_loss_pct / 100
            usdt_trip = (self._day_start_balance_usdt > 0
                         and self._day_pnl_usdt <= -usdt_limit)
            usdc_trip = (self._day_start_balance_usdc > 0
                         and self._day_pnl_usdc <= -usdc_limit)
            if usdt_trip and usdc_trip:
                return (f"circuit breaker: rugi harian USDC+USDT "
                        f"${-(self._day_pnl_usdt + self._day_pnl_usdc):.2f} ≥ ambang")
            if usdt_trip:
                return (f"circuit breaker (USDT): rugi harian ${-self._day_pnl_usdt:.2f} "
                        f"≥ ${usdt_limit:.2f}")
            if usdc_trip:
                return (f"circuit breaker (USDC): rugi harian ${-self._day_pnl_usdc:.2f} "
                        f"≥ ${usdc_limit:.2f}")
        return None

    def _on_cycle_store(self) -> None:
        # Guard: pastikan default sideways-sniper attributes (bila __init__ di-bypass test)
        if not hasattr(self, '_sideways_sniper'):
            self._sideways_sniper = False
            self._sniper_pregate_atr_range = 0.02
            self._sniper_price_cache_range = 0.0
            self._sniper_budget_boost_pct = 0.0
            self._sniper_micro_tp_min = 0.01
            self._sniper_micro_tp_max = 0.30
            self._sniper_require_scalp = True
            self._sniper_range_bonus_mult = 3.0
            self._sniper_devil_advocate_for_scalp = False

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
        # rollover hari UTC → reset state circuit breaker (Tahap 1: per-wallet)
        today = pd.Timestamp.utcnow().date()
        if today != self._day:
            self._day = today
            self._day_pnl_usdt, self._day_pnl_usdc = 0.0, 0.0
            self._day_trades = 0
            self._day_start_balance_usdt = self.balance_usdt
            self._day_start_balance_usdc = self.balance_usdc
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
        gemini_pool = []                       # (sym, df, score) — di-ranking setelah loop
        _sniper_range_cache: dict[str, bool] = {}   # regime-range per simbol untuk budget boost
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
                    # ── DETEKSI REGIME=range (sideways sniper) ────────────────────
                    # Murah (cuma ADX) — dipakai untuk bypass pre-gate ATR & price-cache
                    # khusus regime=range (sideways tipis jangan diblok, tetap panggil Gemini).
                    _sideways_on = getattr(self, '_sideways_sniper', False)
                    _is_rng = self._is_range(df_closed, self.cfg) if _sideways_on else False
                    self._sniper_cache_add(_sniper_range_cache if _sideways_on else None,
                                           sym, _is_rng)
                    # Blokir MURAH dulu (tanpa panggil Gemini) — jangan buang token bila
                    # jelas tak akan buka posisi.
                    pre = (None if rs.enabled else "bot OFF")
                    pre = pre or ("news veto" if news_veto else None)
                    pre = pre or ("vrp brake" if vrp_block else None)
                    pre = pre or ("drawdown lock" if ddlock else None)
                    pre = pre or (cb or None)
                    pre = pre or ("sudah ada posisi" if sym in self.open else None)
                    pre = pre or ("slot penuh" if len(self.open) >= self.max_open else None)
                    # Anti re-entry: jangan buka posisi baru untuk simbol yang baru ditutup
                    _last_close = self._recently_closed.get(sym, 0)
                    if _last_close and now - _last_close < 600:
                        pre = pre or f"recent close {now - _last_close:.0f}s ago"
                    if pre is None:                      # PRE-GATE murah: pasar HIDUP? (ARAH
                        #   diserahkan ke Gemini — jangan sandera trader pintar di balik rules lama).
                        #   Hanya saring pasar mati (ATR% < lantai) agar tak buang token. Lantai =
                        #   knob (calibration): naikkan bila token boros, turunkan bila sinyal langka.
                        _, gate_atr = self._signal(sym, df_closed)
                        # ── SIDEWAYS SNIPER: lantai ATR DILENGGARKAN khusus regime=range.
                        # Default pregate_atr_pct=0.08 blok pair ATR<0.08% — tapi sideways
                        # ideal justru ATR 0.02-0.10%. Setup `scalp_range` butuh ATR rendah.
                        # Saat regime=range & sideways_sniper aktif → pakai lantai lebih rendah.
                        if self._sideways_sniper and _is_rng:
                            floor = self._sniper_pregate_atr_range
                        else:
                            floor = self.cfg.get("gemini", {}).get("pregate_atr_pct", 0.3)
                        if c["price"] and gate_atr / c["price"] * 100 < floor:
                            pre = "pre-gate: pasar terlalu sepi"
                    if pre is not None:
                        c["blocked"] = pre
                        continue
                    # ── PRICE CACHE ───────────────────────────────────────────────────
                    # Skip Gemini jika harga belum bergerak melewati threshold sejak decide
                    # terakhir — hemat RPD saat pasar stagnan tanpa melewatkan pergerakan nyata.
                    # SIDEWAYS SNIPER: price-cache DIMATIKAN khusus regime=range (harga sideways
                    # memang diam, tapi tetap harus dievaluasi tiap siklus → jangan skip).
                    if self._sideways_sniper and _is_rng:
                        _price_cache_pct = self._sniper_price_cache_range   # 0.0 = bypass cache
                    else:
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
                    # Kumpulkan SEMUA yang lolos pre-gate dgn skor "menarik" — kuota
                    # dialokasikan by-ranking setelah loop (bukan first-come-first-served,
                    # yang membuat simbol ekor daftar kelaparan permanen).
                    # SIDEWAYS SNIPER: boost 3× skor saat regime=range (ADX ≤ adx_range) agar
                    # simbol range (ATR rendah = base score kecil) tak kalah bersaing dgn
                    # simbol trend. Tanpa boost, budget habis di trend → scalp_range kelaparan.
                    _range_bonus = self._sniper_range_bonus_mult if (
                        self._sideways_sniper and _is_rng) else 1.0
                    score = self._gemini_score(df_closed, c["price"], gate_atr,
                                               now - self._last_decide.get(sym, 0),
                                               range_bonus_mult=_range_bonus)
                    gemini_pool.append((sym, df_closed, score))
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

        # RANKING: kuota decide dialokasikan ke kandidat paling "hidup" (skor), bukan
        # urutan daftar. Sisa slot dari siklus ini (budget dikurangi pemakaian manage dll).
        # ── SIDEWAYS SNIPER: budget BOOST saat mayoritas simbol regime=range. 26 key aman
        # RPD → panggil lebih sering saat sideways agar scalp_range dapat kesempatan walau
        # skor volatilitasnya rendah (range = ATR rendah = skor _gemini_score kecil →
        # tanpa boost kalah bersaing dengan trend setup di simbol lain).
        gemini_pool.sort(key=lambda t: (-t[2], t[0]))    # skor desc, tie-break nama (deterministik)
        # Hitung boost budget berdasarkan rasio simbol regime=range di pool kandidat
        _eff_budget = self._gemini_decide_budget
        if self._sideways_sniper and self._sniper_budget_boost_pct > 0 and gemini_pool:
            n_range = sum(1 for t in gemini_pool
                          if _sniper_range_cache.get(t[0], False))
            n_total = len(gemini_pool)
            if n_total > 0 and n_range / n_total >= 0.5:   # ≥50% sideways → apply boost
                _boost_mult = 1.0 + self._sniper_budget_boost_pct / 100.0
                _eff_budget = min(
                    int(self._gemini_decide_budget * _boost_mult),
                    self._gemini_decide_cap * 2)            # cap 2× default 24 = 48 (26 key aman)
                if _eff_budget != self._gemini_decide_budget:
                    log.info(f"SIDEWAYS SNIPER: budget boost "
                             f"{self._gemini_decide_budget}→{_eff_budget} "
                             f"({n_range}/{n_total} simbol range, +{self._sniper_budget_boost_pct:.0f}%)")
        slots = max(_eff_budget - self._gemini_decide_used, 0)
        gemini_candidates = [(sym, df) for sym, df, _ in gemini_pool[:slots]]
        for sym, df, _ in gemini_pool[slots:]:
            # JANGAN set _last_decide (bukan throttle normal) — tetap ikut ranking siklus depan.
            self.sig_cache.setdefault(sym, {})["blocked"] = "prioritas rendah siklus ini"
        _now_rank = time.time()
        for sym, _df in gemini_candidates:
            self._gemini_decide_used += 1
            self._last_decide[sym] = _now_rank           # throttle: tandai panggilan Gemini

        # TAHAP 2: AI DECIDE CACHE — skip Gemini bila range market & ga berubah signifikan
        _cache_hits: list[tuple[str, pd.DataFrame]] = []
        _cache_miss: list[tuple[str, pd.DataFrame]] = []
        if gemini_candidates and self._sideways_sniper:
            _idx_adx_period = self.cfg.get("signals", {}).get("adx_period", 14)
            from .indicators import adx as _adx_calc
            for sym, df in gemini_candidates:
                cached = self._decide_cache.get(sym)
                if cached and sym not in self.open and sym not in self.pending and _sniper_range_cache.get(sym, False):
                    try:
                        price = self.sig_cache.get(sym, {}).get("price")
                        if price and abs(price - cached["price"]) / max(price, 1e-9) < 0.003:
                            df_closed = df.iloc[:-1]
                            adx_now = float(_adx_calc(df_closed, _idx_adx_period).iloc[-1])
                            if adx_now < 20 and abs(adx_now - cached["adx"]) < 2:
                                _cache_hits.append((sym, df))
                                continue
                    except Exception:
                        pass
                _cache_miss.append((sym, df))
        else:
            _cache_miss = list(gemini_candidates)

        decisions: dict[str, dict] = {}
        contexts: dict[str, dict] = {}
        alt_data: dict[str, dict] = {}
        _fresh_set: set[str] = set()

        # ── Cache hits: reapply cached decision langsung (tanpa Gemini) ──
        for sym, df in _cache_hits:
            cached = self._decide_cache[sym]
            decisions[sym] = copy.deepcopy(cached["decision"])
            contexts[sym] = {"btc_lead": self._btc_lead(), "halving_phase": self._halving_phase()}
            alt_data[sym] = {}
            log.info(f"AI CACHE HIT {sym}: reuse range decision "
                     f"(adx={cached['adx']:.1f}, Δprice={abs(self.sig_cache.get(sym,{}).get('price',0)-cached['price'])/max(cached['price'],1e-9)*100:.3f}%)")

        # ── Cache miss: Gemini decide_batch untuk simbol yang beneran baru ──
        if _cache_miss:
            for sym, df_closed in _cache_miss:
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
                                                 balance_usdt=self.balance_usdt,
                                                 balance_usdc=self.balance_usdc,
                                                 news_note=self._last_news_note,
                                                 portfolio=self._portfolio_view(),
                                                  btc_lead=self._btc_lead(),
                                                  halving_phase=self._halving_phase())
                contexts[sym] = ctx

            fresh = self.gtrader.decide_batch({s: contexts[s] for s, _ in _cache_miss})
            decisions.update(fresh)
            _fresh_set = {sym for sym, _ in _cache_miss}

            # ── CONVICTION BOOST (arsitektur profit konsisten) ─────────────────────────
            # 1. BTC Dominance Short-Priority: btc_lead.dump_flag=True & SHORT → ×1.5
            #    Logika: BTC dump ≥2% 3-bar → BTC.D naik → alt beta>1 turun LEBIH DALAM
            # 2. Halving Cycle: bull/bear phase → trend-following direction ×1.3
            #    bull→LONG boost, bear→SHORT boost (macro awareness tanpa override mikro)
            # HARD GATE: boost HANYA untuk setup dengan exp_R historis ≥ 0 (atau ≥ -0.02 toleransi)
            # Setup gagal (scalp_range, range_fade, trend_pullback) TIDAK BOLEH dapat boost
            for sym, dec in decisions.items():
                if dec["side"] not in ("long", "short"):
                    continue
                ctx = contexts.get(sym, {})
                btc = ctx.get("btc_lead", {})
                halving = ctx.get("halving_phase", "")
                setup_id = dec.get("setup")
                
                # Check setup exp_R for halving boost gate
                allow_halving_boost = True
                if setup_id:
                    try:
                        from . import store
                        st = store.setup_stats(setup_id, mode=self.settings.mode)
                        if st["n"] >= 10 and st["exp_r"] < -0.02:  # toleransi noise kecil
                            allow_halving_boost = False
                            log.debug(f"{sym}: halving boost SKIPPED for setup {setup_id} (exp_R={st['exp_r']:.3f}, n={st['n']})")
                    except Exception as e:
                        log.debug(f"exp_R check failed for {setup_id}: {e}")
                
                # 1. BTC dump asymmetry (always allowed - structural edge)
                if btc.get("dump_flag") and dec["side"] == "short":
                    old = dec["conviction"]
                    dec["conviction"] = round(min(old * 1.5, 1.0), 3)
                    dec["rationale"] = (dec.get("rationale", "") +
                                        f" | BTC_DUMP_BOOST ×1.5 ({old:.2f}→{dec['conviction']:.2f})")[:200]
                # 2. Halving cycle macro (GATED by setup exp_R)
                if allow_halving_boost and halving in ("bull", "bear"):
                    if (halving == "bull" and dec["side"] == "long") or \
                       (halving == "bear" and dec["side"] == "short"):
                        old = dec["conviction"]
                        dec["conviction"] = round(min(old * 1.3, 1.0), 3)
                        dec["rationale"] = (dec.get("rationale", "") +
                                            f" | HALVING_{halving.upper()}_BOOST ×1.3 ({old:.2f}→{dec['conviction']:.2f})")[:200]

            from .indicators import atr as _atr
            for sym, df_closed in gemini_candidates:
                dec = decisions.get(sym)
                if not dec:
                    dec = {"setup": "no_trade", "side": "flat", "conviction": 0.0, "rationale": "no decision in batch"}
                ctx = contexts.get(sym, {})
                alt = alt_data.get(sym, {})

                # Devil's Advocate (arsitektur 26-key: hemat RPD, Devil di-skip utk scalp_range)
                if dec["side"] in ("long", "short") and self.rs is not None:
                    _skip_devil = (self._sniper_devil_advocate_for_scalp is False
                                   and dec.get("setup") == "scalp_range")
                    if not _skip_devil:
                        verdict = self.react.challenge_gemini(sym, dec["side"], dec.get("rationale", ""),
                                                              ctx.get("market", {}), alt)
                        if verdict and verdict["strength"] >= self.react.devil_threshold:
                            old = dec["conviction"]
                            dec["conviction"] = round(self.rs.downgrade_conf(old), 3)
                            top = verdict["objections"][0] if verdict["objections"] else "objection kuat"
                            dec["rationale"] = (dec.get("rationale", "") +
                                                f" | DEVIL {verdict['strength']:.2f} "
                                                f"({old:.2f}→{dec['conviction']:.2f}): {top}")[:200]
                    else:
                        log.debug(f"{sym}: Devil's Advocate di-skip (scalp_range, hemat RPD)")

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

                # AI decide cache: simpan hasil FRESH (bukan cached) untuk reuse di range market
                if sym in _fresh_set and dec["side"] in ("long", "short") and _sniper_range_cache.get(sym, False):
                    try:
                        _adx_p = self.cfg.get("signals", {}).get("adx_period", 14)
                        from .indicators import adx as _adx_calc2, atr as _atr_calc2
                        _adx_v = float(_adx_calc2(df_closed, _adx_p).iloc[-1])
                        if _adx_v < 20:
                            _atr_v = float(_atr_calc2(df_closed, self.cfg["signals"]["atr_period"]).iloc[-1])
                            self._decide_cache[sym] = {
                                "adx": _adx_v, "price": c["price"], "atr": _atr_v,
                                "decision": copy.deepcopy(dec), "ts": time.time()
                            }
                    except Exception:
                        pass

                # ── ENTRY CONFLUENCE GATE (SHADOW: catat, jangan blokir) ──────────────
                try:
                    _setup_id = dec.get("setup", "")
                    if _setup_id not in ("no_trade", "") and side != 0:
                        from . import levels as _lvl_mod
                        from . import signals as _sig_mod
                        from . import altdata as _alt_mod
                        _bns = ctx.get("market", {})
                        _trend = _bns.get("ema_align", 0)
                        _rsi = _bns.get("rsi", 50)
                        _trend_score = float(_trend) * 0.4
                        _mom_score = ((float(_rsi) - 50) / 25.0) * 0.3
                        _btc_lead_val = ctx.get("btc_lead", {}).get("ret_1bar_pct")
                        _ec_result = ec_gate.entry_confluence_gate(
                            sym, dec["side"], _setup_id,
                            c.get("price", 0.0), atr_val,
                            _trend_score, _mom_score,
                            _btc_lead_val, _lvl_mod, _sig_mod, _alt_mod, self.cfg)
                        _ec_shadow_rec = ec_gate.GateResult(
                            ts=pd.Timestamp.utcnow().isoformat(),
                            symbol=sym, side=dec["side"], setup=_setup_id,
                            btc_tier=_ec_result["btc_tier"],
                            structure_pass=_ec_result["structure_pass"],
                            location_quality=_ec_result["location_quality"],
                            would_enter=(_ec_result["decision"] == "enter"),
                            actually_entered=False,
                            conviction=float(dec.get("conviction", 0)),
                            price=c.get("price", 0.0),
                            reason=_ec_result["reason"])
                        ec_gate.log_shadow(_ec_shadow_rec)
                        log.info(f"ENTRY CONFLUENCE SHADOW {sym} {_setup_id} "
                                 f"btc={_ec_result['btc_tier']} struct={_ec_result['structure_pass']} "
                                 f"loc={_ec_result['location_quality']} "
                                 f"decision={_ec_result['decision']}")
                except Exception as e:
                    log.debug(f"entry_confluence shadow {sym}: {e}")
                # ─────────────────────────────────────────────────────────────────────

                # Post-decision: hanya cek hal yang tergantung output Gemini (side, setup).
                # Pre-gates (bot OFF, news, VRP, ddlock, cb, slot, posisi) sudah lolos
                # SEBELUM Gemini decide → tak perlu diulang.
                blocked = None
                if side == 0:
                    blocked = "tak ada sinyal"
                    # Shadow: catat flat ASLI Gemini (semua blokir murah sudah lolos) →
                    # nanti diukur apakah ada gerakan tradeable yang terlewat (miss).
                    flat_shadow.record_flat(self.settings.mode, sym, c.get("price"), atr_val,
                                            dec, self._regime_stamp(df_closed, self.cfg)["regime"],
                                            df_closed.index[-1], self.cfg)
                elif atr_val <= 0:
                    blocked = "ATR nol"
                elif (conflict := self._corr_conflict(sym, side)):
                    blocked = f"korelasi tinggi dgn {conflict.split('/')[0]}"
                elif self.use_planner and (pblock := self.planner.enforce(
                        self._session_plan, "long" if side == 1 else "short",
                        new_trades=self._session_trades, exposure_frac=expo)):
                    blocked = pblock

                # Evidence-gate hard block: cek apakah setup ini sudah retired di lessons
                elif not blocked:
                    setup_id = c.get("setup")
                    if setup_id:
                        try:
                            from . import store
                            if store.is_setup_retired(setup_id, mode=self.settings.mode):
                                blocked = f"evidence-gate: setup {setup_id} RETIRED (akurasi < 0.4, ≥10 pemicu)"
                        except Exception as e:
                            log.debug(f"evidence-gate check {sym}: {e}")

                # S/R Level Validity Gate (Phase 1): HARD BLOCK fade setups without valid level
                elif not blocked:
                    setup_id = c.get("setup")
                    fade_setups = ("range_fade", "scalp_range", "range_fade_v2", "scalp_range_v2")
                    if setup_id in fade_setups:
                        try:
                            from . import levels as lvl_mod
                            price = c.get("price") or float(self.ex.ticker(sym)["last"])
                            side_str = "long" if side == 1 else "short"
                            has_level, level = lvl_mod.is_price_at_valid_level(
                                sym, price, side_str, max_dist_atr_mult=0.5)
                            if not has_level:
                                blocked = f"S/R-gate: {setup_id} requires valid {'support' if side == 1 else 'resistance'} within 0.5×ATR (none found)"
                            else:
                                log.info(f"{sym}: S/R-gate PASSED - {side_str} at {level.level_type} {level.price:.4f} (strength={level.strength:.1f}, dist={level.dist_atr:.2f}ATR)")
                                
                                # C2: BTC Directional Confirmation Gate (stricter than btc_gate)
                                if not blocked:
                                    try:
                                        from . import altdata
                                        btc_confirm = altdata.btc_fade_confirm(side_str, self.cfg)
                                        if not btc_confirm["allow"]:
                                            blocked = f"BTC-fade-gate: {btc_confirm['reason']}"
                                        else:
                                            log.info(f"{sym}: BTC-fade-gate PASSED - {btc_confirm['reason']}")
                                    except Exception as e:
                                        log.debug(f"BTC-fade-gate {sym}: {e}")
                                        pass
                                
                                # C3: Pair Cleanliness Filter
                                if not blocked:
                                    try:
                                        clean = self._pair_cleanliness_check(sym, df_closed)
                                        if not clean["allow"]:
                                            blocked = f"Cleanliness-gate: {clean['reason']}"
                                        else:
                                            log.info(f"{sym}: Cleanliness-gate PASSED")
                                    except Exception as e:
                                        log.debug(f"Cleanliness-gate {sym}: {e}")
                                        pass
                        except Exception as e:
                            log.debug(f"S/R-gate check {sym}: {e}")
                            # Fail-open for S/R gate (don't block on error)
                            pass

                c["blocked"] = blocked
                if blocked is None:
                    self._open_usd(sym, side, atr_val, rs)
                    if sym in self.open:
                        self._session_trades += 1
                        c["blocked"] = "→ posisi dibuka"
                        # Update shadow: actually_entered=True untuk record terakhir simbol ini
                        try:
                            from . import store
                            _recent = store.entry_confluence_shadow_stats(limit=5)
                            for _r in _recent:
                                if _r.get("symbol") == sym and not _r.get("actually_entered"):
                                    store.settle_entry_confluence_outcome(
                                        _r["id"], actually_entered=True)
                                    break
                        except Exception as _e:
                            log.debug(f"update ec shadow {sym}: {_e}")
                    # else: _open_usd GAGAL diam-diam (margin habis/SL invalid/abstain/dll) —
                    # JANGAN timpa. _open_usd SUDAH menulis alasan gagal yang akurat ke
                    # sig_cache[sym]['blocked'] di titik early-return-nya sendiri; menimpanya
                    # dgn "posisi dibuka" di sini membuat UI klaim sukses padahal gagal.
        flat_shadow.settle_pending(self.settings.mode, getattr(self, "buffers", {}) or {},
                                   self.cfg)   # shadow: nilai flat yang horizonnya lewat
        self._agent_portfolio_review(rs)   # POINT 2: agen otonom kelola portofolio (REDUCE_RISK/FLAT)
        self._write_status(rs, news_veto, note)
        self._persist_state()        # saldo+posisi durable -> tahan restart
        self._persist_logs(news_veto, note)   # histori news + screening (on-change)

    def _write_status(self, rs, news_active: bool, news_note: str) -> None:
        syms = []
        for sym in self.symbols:
            c = self.sig_cache.get(sym, {})
            pos = self.open.get(sym)
            # PnL pakai ticker live biar dashboard real-time, bukan bar close
            if pos:
                try:
                    price = float(self.ex.ticker(sym)["last"])
                    c["price"] = price
                except Exception:
                    price = c.get("price")
            else:
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
            "balance_usdt": self.balance_usdt if self.live else round(self.balance_usdt, 2),
            "balance_usdc": self.balance_usdc if self.live else round(self.balance_usdc, 2),
            "open_count": len(self.open),
            "max_open": self.max_open,
            "poll_seconds": rs.poll_seconds,
            "gemini_decide_budget": self._gemini_decide_budget,
            "gemini_decide_cap": self._gemini_decide_cap,
            "order_type": rs.order_type,
            "fee_pct": rs.fee_pct(),
            "day_pnl": self._day_pnl_usdt + self._day_pnl_usdc if self.live else round(self._day_pnl_usdt + self._day_pnl_usdc, 2),
            "day_pnl_usdt": self._day_pnl_usdt if self.live else round(self._day_pnl_usdt, 2),
            "day_pnl_usdc": self._day_pnl_usdc if self.live else round(self._day_pnl_usdc, 2),
            "day_trades": self._day_trades,
            "circuit_breaker": self._circuit_breaker(),
            "drawdown": {"locked": self._dd_lock, "reason": self._dd_reason or None,
                         "peak_balance_usdt": self._peak_balance_usdt if self.live else round(self._peak_balance_usdt, 2),
                         "peak_balance_usdc": self._peak_balance_usdc if self.live else round(self._peak_balance_usdc, 2),
                         "dd_pct_usdt": self._dd_check(self._peak_balance_usdt,
                                                         self.balance_usdt, 100.0)[1] if self.live else round(self._dd_check(self._peak_balance_usdt,
                                                                                             self.balance_usdt, 100.0)[1], 2),
                         "dd_pct_usdc": self._dd_check(self._peak_balance_usdc,
                                                         self.balance_usdc, 100.0)[1] if self.live else round(self._dd_check(self._peak_balance_usdc,
                                                                                             self.balance_usdc, 100.0)[1], 2)},
            "corr_threshold": self.corr_threshold,
            "news_veto": {"active": news_active, "note": news_note},
            "pending_orders": [{"symbol": s, "side": "buy" if p["is_long"] else "sell",
                                 "type": "LIMIT", "price": p["entry"], "qty": p["qty"],
                                 "order_id": p.get("order_id"),
                                 "opened_ts": p.get("placed_ts")}
                                for s, p in self.pending.items()],
            "symbols": syms,
        }
        try:
            from .store import set_kv
            set_kv(f"status:{self.settings.mode}", status)   # per-mode (multi-proses paralel)
            if not self.pin_mode:
                set_kv("status", status)                     # kompat: proses tunggal lama
            # push real-time ke dashboard SSE (fire-and-forget)
            from .notify_sse import notify
            notify("status", status)
        except Exception as e:  # boundary
            log.warning(f"tulis status gagal: {e}")

    def run(self, poll_s: int = 30) -> None:
        self.seed()
        log.info(f"=== FORWARD-TEST mode={self.settings.mode} params={self.params} ===")
        log.info("Paper-trade di data LIVE. Ctrl+C untuk berhenti. Log: logs/forward_trades.jsonl")
        # Tahap 5 (plan-sess): ForwardTester LIVE opt-in subscribe Account/Order WS
        # dari EventHub (mode='live'). ACCOUNT_UPDATE / ORDER_TRADE_UPDATE → invalidate
        # cache saldo/posisi → panggil _live_reconcile() di siklus berikut (REST tetap
        # authority; WS cuma trigger lebih responsif). Hindari double-process: satu
        # EventHub global, satu proses forward per mode.
        ws_sub_q = None
        if self.live and self.use_store:
            try:
                from .eventhub import hub as _hub
                ws_sub_q = _hub.subscribe()
                # bukan async di sini — flag trigger ringan via '_ws_trigger' queue
                self._ws_trigger = ws_sub_q
                log.info("FORWARD-TEST live → subscribe EventHub WS untuk reconcile trigger.")
            except Exception as e:
                log.warning(f"subscribe EventHub WS skip (fallback REST reconcile): {e}")
        _last_market_reload = 0.0
        while True:
            try:
                # Tahap 5: if WS memberi sinyal baru, drain queue (sinyal → flag reconcile).
                if getattr(self, "_ws_trigger", None) is not None:
                    drained = 0
                    try:
                        while not self._ws_trigger.empty() and drained < 5:
                            self._ws_trigger.get_nowait()
                            drained += 1
                    except Exception:
                        pass
                    if drained:
                        log.debug(f"WS trigger live reconcile: drained {drained} frame(s)")
                
                # Periodic market reload (setiap 1 jam) untuk pair baru/delisted
                import time as _time
                now = _time.time()
                if now - _last_market_reload >= 3600:
                    try:
                        self.ex.reload_markets()
                    except Exception as e:
                        log.warning(f"periodic reload_markets gagal: {e}")
                    _last_market_reload = now

                self.on_cycle()
                s = self.stats()
                if s["trades"]:
                    log.info(f"[stats] trades={s['trades']} win={s.get('win_rate',0):.0f}% "
                             f"expR={s.get('expectancy_r',0):+.3f} eq={s['equity']} open={s.get('open',0)}")
            except KeyboardInterrupt:
                log.info(f"Berhenti. Statistik akhir: {self.stats()}")
                if ws_sub_q is not None:
                    try:
                        from .eventhub import hub as _hub
                        _hub.unsubscribe(ws_sub_q)
                    except Exception:
                        pass
                break
            except Exception as e:  # boundary — loop tak boleh mati
                log.error(f"cycle error: {e}")
            # interval screening hot-reload dari UI bila pakai store
            sleep_s = self.rs.poll_seconds if (self.use_store and self.rs) else poll_s
            time.sleep(sleep_s)
