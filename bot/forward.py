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
from . import risk_filter as risk_filter_mod
from . import cycle_candidate as cycle_cand
from .planner import SessionPlanner, default_plan
from .react_agent import ReactAgent
from .signals import Signal
from . import entry_confluence as ec_gate
from .notify import TelegramNotifier
from .orderflow import cvd_from_series, fetch_taker
from .settings_store import RuntimeSettings, liquidation_price, load_settings
from .strategy_lab import decide_v4, precompute_v4
from .gemini_client import all_keys_dead as _all_keys_dead
from .forward_gates import ForwardGatesMixin
from .forward_open import ForwardOpenMixin
from .forward_close import ForwardCloseMixin
from .forward_status import ForwardStatusMixin

_Sig = namedtuple("_Sig", ["side", "atr"])


def default_params() -> dict:
    """Satu set parameter tetap untuk forward-test (robust, sering terpilih OOS)."""
    return {"entry_confidence": 0.5, "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
            "use_htf": True, "regime": True, "use_funding": True,
            "use_oi": False, "use_of": True}


@dataclass
class ForwardTester(ForwardGatesMixin, ForwardOpenMixin, ForwardCloseMixin, ForwardStatusMixin):
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
        # Risk overlay (Jalan A): breadth_lo / corr_hi / btc_vol_hi — default OFF; shadow log only.
        _rf = risk_filter_mod.from_config(self.cfg)
        self.risk_filter_shadow = bool(_rf["shadow"])
        self.risk_filter_block = bool(_rf["block"])
        self._risk_filter_verdict = None            # diisi tiap siklus bila shadow|block
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
        # Pemilik: SL setelah open FIXED (entry ATR). Jangan trail/BE/tighten.
        self.allow_move_sl = bool(_ag.get("allow_move_sl", False))
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
        # Frugal RPD default: 900s = 1× bar 15m (di-override hot-reload dari RuntimeSettings)
        self._manage_interval = 900
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
        self._decide_interval = 900               # frugal: 1 decide/simbol per bar 15m
        self._decide_price_cache: dict[str, tuple[float, dict]] = {}
        self._decide_cache: dict[str, dict] = {}  # AI decision cache (range regime hemat RPD)
        self._last_rpd_warn = 0.0
        self._allow_live_gemini = bool(self.cfg.get("gemini", {}).get("allow_live_trader", False))
        # ── SIDEWAYS SNIPER (profit konsisten walau sideways) ─────────────────────
        _sniper = self.cfg.get("gemini", {}).get("sideways_sniper", {})
        self._sideways_sniper = bool(_sniper.get("enabled", True))
        self._sniper_pregate_atr_range = float(_sniper.get("pregate_atr_pct_range", 0.01))
        self._sniper_price_cache_range = float(_sniper.get("price_cache_pct_range", 0.05))
        self._sniper_budget_boost_pct = float(_sniper.get("budget_boost_pct", 0))
        self._sniper_micro_tp_min = float(_sniper.get("micro_tp_pct_min", 0.005))
        self._sniper_micro_tp_max = float(_sniper.get("micro_tp_pct_max", 0.30))
        self._sniper_require_scalp = bool(_sniper.get("require_setup_scalp_range", True))
        self._sniper_range_bonus_mult = float(_sniper.get("range_bonus_mult", 3.0))
        self._sniper_devil_advocate_for_scalp = bool(_sniper.get("devil_advocate_for_scalp", False))

    def seed(self) -> None:
        for sym in self.symbols:
            try:
                self.buffers[sym] = fetch_history(self.ex, sym, self.tf, self.maxlen)
                # Jangan set last_closed di sini: biarkan siklus pertama evaluasi bar
                # closed terbaru. Kalau di-set ke index[-2], entry tertunda s/d bar 15m
                # berikutnya → UI "—" / atr null pasca-restart (salah dibaca "0 sinyal").
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
            if not getattr(self, "allow_move_sl", False):
                log.info(
                    "AGENT REDUCE_RISK diabaikan (allow_move_sl=false — SL fixed): "
                    f"{dec.get('reasoning')}"
                )
                return
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
        # Risk filter overlay (breadth/corr/vol) — evaluasi 1× per siklus; shadow default.
        self._refresh_risk_filter()
        rf_block = bool(self.risk_filter_block and self._risk_filter_verdict
                        and not self._risk_filter_verdict.allow)
        if rf_block:
            log.info(
                f"Risk filter ENFORCE aktif ({self._risk_filter_verdict.reasons}) "
                "— tidak buka posisi baru siklus ini")
        ddlock = self._update_drawdown(rs)     # kill-switch drawdown TOTAL (tahan restart)
        self._apply_funding_sim()              # P3: akru funding posisi menginap (paper)
        self._refresh_plan(rs)                 # tujuan sesi (planner) → enforce di gerbang entry
        expo = self._exposure_frac()
        label = {1: "LONG", -1: "SHORT", 0: "skip"}
        gemini_pool = []                       # (sym, df, score) — di-ranking setelah loop
        _sniper_range_cache: dict[str, bool] = {}   # regime-range per simbol untuk budget boost
        _cache_hit_reserve: dict[str, tuple[pd.DataFrame, dict]] = {}  # cached decisions: sym → (df, decision)
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
            # ── AI DECIDE CACHE (lolos throttle: reuse Gemini tanpa evaluasi full) ──
            # HANYA saat jalur Gemini trader aktif. Di manager-mode (Jalan A) use_gemini_trader
            # = False → cache WAJIB dimatikan, kalau tidak `continue` di sini melewati RULES
            # dan mereplay keputusan Gemini lama (no_trade/flat) → 0 entry palsu.
            if (self.use_gemini_trader and getattr(self, '_sideways_sniper', False)
                    and sym not in self.open
                    and sym not in self.pending and c.get("price")):
                _cached = self._decide_cache.get(sym)
                if _cached:
                    try:
                        from .indicators import adx as _adx_c3
                        _at_v = float(_adx_c3(df_closed, self.cfg["signals"]["adx_period"])[0].iloc[-1])
                        _at_th = self.cfg.get("strategy", {}).get("adx_range", 18)
                        _dp = abs(c["price"] - _cached["price"]) / max(c["price"], 1e-9)
                        if _dp < 0.003 and _at_v <= _at_th and abs(_at_v - _cached["adx"]) < 2:
                            _cache_hit_reserve[sym] = (df_closed, copy.deepcopy(_cached["decision"]))
                            self._last_decide[sym] = time.time()
                            continue
                    except Exception:
                        pass
            if bar_closed or free_gemini:
                if bar_closed:
                    self.last_closed[sym] = df_closed.index[-1]
                if self.use_gemini_trader and self.gtrader is not None and not _gemini_rpd_fallback:
                    # ── DETEKSI REGIME=range (sideways sniper) ────────────────────
                    # Murah (cuma ADX) — dipakai untuk bypass pre-gate ATR & price-cache
                    # khusus regime=range (sideways tipis jangan diblok, tetap panggil Gemini).
                    _sideways_on = getattr(self, '_sideways_sniper', False)
                    _adx_v: float | None = None
                    _adx_range_th = self.cfg.get("strategy", {}).get("adx_range", 18)
                    if _sideways_on:
                        from .indicators import adx as _adx_calc
                        try:
                            _adx_v = float(_adx_calc(df_closed, self.cfg["signals"]["adx_period"])[0].iloc[-1])
                        except Exception:
                            _adx_v = None
                    _is_rng = _adx_v is not None and _adx_v <= _adx_range_th
                    self._sniper_cache_add(_sniper_range_cache if _sideways_on else None,
                                           sym, _is_rng)
                    # Blokir MURAH dulu (tanpa panggil Gemini) — jangan buang token bila
                    # jelas tak akan buka posisi.
                    pre = (None if rs.enabled else "bot OFF")
                    pre = pre or ("news veto" if news_veto else None)
                    pre = pre or ("vrp brake" if vrp_block else None)
                    pre = pre or (("risk_filter "
                                   + ",".join(self._risk_filter_verdict.reasons or ["deny"]))
                                  if rf_block else None)
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
                    elif rf_block:
                        # Hard block only when risk_filter_block=true (default OFF).
                        blocked = ("risk_filter "
                                   + ",".join(self._risk_filter_verdict.reasons or ["deny"]))
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
                    # Risk-filter SHADOW: log would-deny without blocking (Jalan A metrik risk).
                    if (blocked is None and self.risk_filter_shadow and not self.risk_filter_block
                            and self._risk_filter_verdict is not None
                            and not self._risk_filter_verdict.allow):
                        try:
                            decision_log.append({
                                "ts": pd.Timestamp.utcnow().isoformat(),
                                "symbol": sym,
                                "action": "RISK_FILTER_SHADOW",
                                "side": "long" if side == 1 else "short",
                                "outcome": None,
                                **risk_filter_mod.stamp(self._risk_filter_verdict),
                            })
                        except Exception as e:  # boundary — shadow log tak boleh ganggu
                            log.warning(f"risk_filter shadow log {sym} gagal: {e}")
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

        # TAHAP 2: AI DECIDE CACHE — cache hits sudah di-filter di main loop
        _cache_hits = [(sym, df) for sym, (df, _) in _cache_hit_reserve.items()]
        _cache_miss = list(gemini_candidates)

        decisions: dict[str, dict] = {}
        contexts: dict[str, dict] = {}
        alt_data: dict[str, dict] = {}
        _fresh_set: set[str] = set()

        # ── Cache hits: reapply cached decision langsung (tanpa Gemini) ──
        for sym, df in _cache_hits:
            cached = self._decide_cache[sym]
            decisions[sym] = copy.deepcopy(cached["decision"])
            contexts[sym] = {
                "btc_lead": self._btc_lead(),
                "halving_phase": self._halving_phase(),
                "cycle_context": self._cycle_context(sym),
            }
            alt_data[sym] = {}
            log.info(f"AI CACHE HIT {sym}: reuse range decision "
                     f"(adx={cached['adx']:.1f}, Δprice={abs(self.sig_cache.get(sym,{}).get('price',0)-cached['price'])/max(cached['price'],1e-9)*100:.3f}%)")

        if _cache_hits or _cache_miss:
            log.info(f"CACHE: {len(_cache_hits)} hit, {len(_cache_miss)} miss — hemat {len(_cache_hits)} Gemini calls")
        # Gabung cache-hit + pool candidates untuk entry gate (cache-hit sudah ada decisions-nya)
        _all_candidates = gemini_candidates + _cache_hits
        # Fresh cycle labels once per decide wave (P3 context)
        self._cycle_ctx_cache = {}

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
                                                  halving_phase=self._halving_phase(),
                                                  cycle_context=self._cycle_context(sym))
                contexts[sym] = ctx

            fresh = self.gtrader.decide_batch({s: contexts[s] for s, _ in _cache_miss})
            if fresh:
                decisions.update(fresh)
            _fresh_set = {sym for sym, _ in _cache_miss}

            # ── CONVICTION BOOST ───────────────────────────────────────────────────────
            # 1. BTC dump SHORT ×1.5 — HANYA bila btc.dump_short_boost (default false;
            #    H-CYC-01/01b: short-after-dump bukan edge OOS). dump_flag tetap di context.
            # 2. Halving Cycle: bull/bear phase → trend-following direction ×1.3
            # HARD GATE (halving): boost HANYA setup exp_R historis ≥ -0.02
            _dump_boost = self._dump_short_boost_enabled()
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
                
                # 1. BTC dump short boost (opt-in; default OFF)
                if _dump_boost and btc.get("dump_flag") and dec["side"] == "short":
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
            for sym, df_closed in _all_candidates:
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

                # AI decide cache: simpan SEMUA hasil FRESH untuk reuse di range market
                if sym in _fresh_set and _sniper_range_cache.get(sym, False):
                    try:
                        from .indicators import adx as _adx_c2, atr as _atr_c2
                        _st = self.cfg.get("strategy", {})
                        _adx_v = float(_adx_c2(df_closed, self.cfg["signals"]["adx_period"])[0].iloc[-1])
                        if _adx_v <= _st.get("adx_range", 18):
                            _atr_v = float(_atr_c2(df_closed, self.cfg["signals"]["atr_period"]).iloc[-1])
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
                        # Surface ke UI (status.symbols[].ec_shadow) — shadow TIDAK
                        # memblokir entry; user harus lihat would-skip + alasan.
                        c["ec_shadow"] = {
                            "would_enter": bool(_ec_result["decision"] == "enter"),
                            "decision": _ec_result["decision"],
                            "btc_tier": _ec_result["btc_tier"],
                            "structure_pass": bool(_ec_result["structure_pass"]),
                            "location_quality": _ec_result.get("location_quality"),
                            "reason": _ec_result.get("reason") or "",
                            "setup": _setup_id,
                            "side": dec["side"],
                            "conviction": float(dec.get("conviction", 0) or 0),
                        }
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
