"""Gerbang entry / risk / ReAct / cycle context (dipotong dari forward.py)."""
from __future__ import annotations

import time
from collections import namedtuple
from datetime import datetime, timezone

import pandas as pd

from . import decision_log
from . import risk_filter as risk_filter_mod
from .logger import journal, log
from .settings_store import RuntimeSettings
from .signals import Signal


class ForwardGatesMixin:
    """Mixin — methods belong to ForwardTester."""

    def _btc_lead(self) -> dict:
        """Gerak BTC (pemimpin pasar) pada bar TERTUTUP: 1bar & 3bar % + arah.
        Alt ber-beta lebih tinggi → gerak turun BTC sering diperbesar/diperpanjang di alt.

        Context BTC untuk prompt/audit (bukan auto-edge):
        - `dump_flag`: True saat BTC turun >= ~2% (4× dump_pct) pada 3 bar TF buffer.
          Deskriptif beta>1 CONFIRMED; short boost terpisah (lihat dump_short_boost).
        - `dominance_dir`: +1 risk-off proxy, -1 risk-on proxy (kasar).
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

    def _dump_short_boost_enabled(self) -> bool:
        """SHORT conviction ×1.5 saat dump_flag. Default OFF (H-CYC-01/01b)."""
        btc = (getattr(self, "cfg", None) or {}).get("btc") or {}
        return bool(btc.get("dump_short_boost", False))

    def _cycle_context(self, symbol: str | None = None) -> dict:
        """P2/P3: fase harga + BTC.D + unlock window — CONTEXT ONLY (bukan hard gate).

        Cache per poll cycle (invalidate tiap on_cycle lewat _cycle_ctx_cache clear).
        Fail-soft: {} minimal dengan phase unknown.
        """
        cache = getattr(self, "_cycle_ctx_cache", None)
        key = symbol or "*"
        if isinstance(cache, dict) and key in cache:
            return cache[key]
        if not isinstance(cache, dict):
            cache = {}
            self._cycle_ctx_cache = cache
        try:
            from . import cycle_regime as cr
            btc_close = None
            for btc_sym in ("BTC/USDC:USDC", "BTC/USDT:USDT"):
                buf = self.buffers.get(btc_sym)
                if buf is not None and len(buf) > 50 and "close" in buf.columns:
                    btc_close = buf["close"]
                    break
            btcdom = None
            for k, buf in self.buffers.items():
                if "BTCDOM" in k.upper() and buf is not None and "close" in getattr(buf, "columns", []):
                    btcdom = buf["close"]
                    break
            if btcdom is None:
                # daily snap fallback (regime TF kasar, OK untuk stance)
                from pathlib import Path
                for p in (Path("data/snap/BTCDOM_USDT_USDT__1d.pkl"),
                          Path("data/snap_smallcap1800/BTCDOM_USDT_USDT__1d.pkl")):
                    if p.exists():
                        try:
                            btcdom = pd.read_pickle(p)["close"]
                        except Exception:
                            btcdom = None
                        break
            cal = getattr(self, "_unlock_cal", None)
            if cal is None:
                from pathlib import Path
                cp = Path("data/unlock_calendar.csv")
                try:
                    cal = cr.load_unlock_calendar(cp) if cp.exists() else cr.load_unlock_calendar(
                        Path("data/unlock_calendar.example.csv"))
                except Exception:
                    cal = cr.load_unlock_calendar(Path("__missing__"))
                self._unlock_cal = cal
            ctx = cr.build_cycle_context(btc_close, btcdom, symbol=symbol, unlock_calendar=cal)
        except Exception as e:
            log.debug(f"cycle_context: {e}")
            ctx = {"phase": "unknown", "calendar_phase": self._halving_phase(),
                   "dominance": {"regime": "unknown"}, "unlock": {"in_window": False}}
        cache[key] = ctx
        # also cache generic for reuse of phase/dom without symbol unlock
        if key != "*" and "*" not in cache:
            cache["*"] = {k: v for k, v in ctx.items() if k != "unlock"}
        return ctx

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

    def _refresh_risk_filter(self) -> None:
        """Evaluate risk overlay once per cycle. Shadow: log only. Block: only if flag on.
        Fail-open: any error → allow=True (never block trading on filter failure)."""
        self._risk_filter_verdict = None
        # Hot-reload flags from cfg (YAML) each cycle — UI does not yet expose these.
        try:
            _rf = risk_filter_mod.from_config(self.cfg)
            self.risk_filter_shadow = bool(_rf["shadow"])
            self.risk_filter_block = bool(_rf["block"])
        except Exception:
            pass
        if not (getattr(self, "risk_filter_shadow", False)
                or getattr(self, "risk_filter_block", False)):
            return
        try:
            self._risk_filter_verdict = risk_filter_mod.check(self.cfg)
            if (self.risk_filter_shadow and self._risk_filter_verdict is not None
                    and not self._risk_filter_verdict.allow):
                log.info(
                    f"risk_filter SHADOW would-skip reasons="
                    f"{self._risk_filter_verdict.reasons} "
                    f"metrics={self._risk_filter_verdict.metrics}")
        except Exception as e:  # boundary — filter gagal ≠ blokir trade
            log.warning(f"risk_filter check gagal (fail-open): {e}")
            self._risk_filter_verdict = risk_filter_mod.FilterVerdict(
                True, [], {"note": "error", "err": str(e)[:120]})

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
        # Lessons = classic + injectable trade_reviews (foundation-filtered). Soft only.
        try:
            from . import trade_review as _trev
            _les = _trev.merge_lessons_for_prompt(
                self.lessons.recent(10),
                mode=getattr(self.settings, "mode", None) or None,
            )
        except Exception:
            _les = self.lessons.recent(10)
        kw = dict(regime=regime, alt=alt, n_positions=len(self.open),
                  max_positions=self.max_open, daily_pnl_r=daily_pnl_r,
                  lessons=_les, shadow=self.ab_shadow,
                  memory=self.agent_memory,     # ingat observasi/keputusan lintas-tick
                  btc_lead=self._btc_lead(),                # dominansi BTC (alt ber-beta lebih tinggi)
                  halving_phase=self._halving_phase(),      # fase kalender (legacy boost path)
                  cycle_context=self._cycle_context(sym))   # P3: phase/dom/unlock CONTEXT only
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

