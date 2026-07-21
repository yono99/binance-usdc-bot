"""Status/persist/settings/mode switch (dipotong dari forward.py)."""
from __future__ import annotations

import time

import pandas as pd

from . import decision_log
from .config import Settings
from .exchange import Exchange
from .logger import journal, log
from .settings_store import RuntimeSettings, load_settings


class ForwardStatusMixin:
    """Mixin — methods belong to ForwardTester."""

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
        # Hot-reload: SL fixed default (pemilik). True hanya bila config eksplisit.
        self.allow_move_sl = bool(_ag.get("allow_move_sl", False))
        prev_gemini = bool(getattr(self, "use_gemini_trader", False))
        self.use_gemini_trader = posture["use_gemini_trader"]
        # Jalan A / non-gemini: buang AI decide-cache agar tak mereplay flat Gemini lama
        # dan tak melewati evaluasi RULES di siklus berikutnya.
        if not self.use_gemini_trader and (
            prev_gemini or self._decide_cache or self._decide_price_cache
        ):
            n_ai = len(self._decide_cache)
            self._decide_cache.clear()
            self._decide_price_cache.clear()
            if prev_gemini or n_ai:
                log.info(
                    f"Posture non-gemini (manager/rules) — AI decide-cache dikosongkan "
                    f"(entries={n_ai}, manager={bool(rs.agent_manager_mode)})"
                )
        # Jalan A: pastikan param RULES tidak "mati total". Conf 0.65 vs skor live p90≈0.37
        # menghasilkan 0 LONG/SHORT (bukan disiplin — cuma sepi). Cap conf & matikan OF
        # ketat bila CVD tak andal; risk lock (loss/trades/pos/lev) TIDAK disentuh di sini.
        if rs.agent_manager_mode and not self.use_gemini_trader:
            p = dict(self.params)
            old_conf = float(p.get("entry_confidence", 0.65))
            if old_conf > 0.30:
                p["entry_confidence"] = 0.30
            p["use_of"] = False
            if p != self.params:
                log.info(
                    f"Manager-mode RULES params: conf {old_conf}→{p['entry_confidence']}, "
                    f"use_of={p['use_of']} (agar sinyal entry terukur di paper)"
                )
            self.params = p
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
        # Rekonsiliasi: bila journal punya forward_open tanpa close, tapi botstate
        # kosong (crash antara open & persist / wipe mode-switch) → pulihkan posisi
        # paper dari event agar UI/margin/SL tak desync. Live: exchange = sumber kebenaran.
        if not self.live:
            self._reconcile_open_from_events()

    def _reconcile_open_from_events(self) -> None:
        """Crash-recovery: pulihkan open paper HANYA bila journal OPEN sangat baru
        (<2 jam) tanpa close, tapi self.open kosong untuk simbol itu.

        Jangan bangkitkan ghost berhari-hari (SL sudah basi, margin dust).
        Ghost tua ditutup lewat scripts/reconcile_dry_ghosts.py.
        Fail-soft: error DB tak ganggu trading.
        """
        try:
            from . import store
            events = store.all_events()
        except Exception as e:  # boundary
            log.warning(f"reconcile open: baca events gagal: {e}")
            return
        mode = self.settings.mode
        opens: dict = {}
        for e in events:
            # mode bisa di payload (journal stempel) — default dry untuk data lawas
            emode = e.get("mode") or "dry"
            if emode != mode:
                continue
            ev = e.get("event")
            sym = e.get("symbol")
            if not sym:
                continue
            if ev == "forward_open":
                opens[sym] = e
            elif ev == "forward_close" and not (
                e.get("partial") or "partial" in str(e.get("reason") or "").lower()
            ):
                opens.pop(sym, None)
        if not opens:
            return
        now = pd.Timestamp.utcnow()
        max_age_h = 2.0  # hanya jendela crash, bukan zombie
        added = 0
        for sym, e in opens.items():
            if sym in self.open:
                continue
            try:
                ts_raw = e.get("ts") or e.get("opened_ts")
                if not ts_raw:
                    continue
                ts = pd.Timestamp(ts_raw)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                age_h = (now - ts).total_seconds() / 3600.0
                if age_h > max_age_h:
                    continue  # ghost tua — biarkan script cleanup, jangan restore
                entry = float(e.get("entry") or 0)
                sl = float(e.get("sl") or 0)
                tp = float(e.get("tp") or 0)
                bet = float(e.get("bet") or 0)
                lev = int(e.get("lev") or e.get("leverage") or 5)
                side = e.get("side") or "long"
                if entry <= 0 or bet <= 0:
                    continue
                qty = (bet * lev) / entry
                risk0 = abs(entry - sl) * qty if sl else bet
                self.open[sym] = {
                    "side": side, "entry": entry, "qty": qty,
                    "sl": sl, "tp": tp, "liq": float(e.get("liq") or 0),
                    "bet": bet, "risk0": risk0 or bet,
                    "opened_ts": e.get("ts") or e.get("opened_ts"),
                    "margin_type": "ISOLATED", "leverage": lev,
                    "restored_from_event": True,
                }
                added += 1
            except Exception as ex:  # boundary
                log.warning(f"reconcile open {sym}: {ex}")
        if added:
            log.warning(
                f"RECONCILE: pulihkan {added} posisi paper dari journal "
                f"(<={max_age_h:.0f}h, botstate desync; total open={len(self.open)})"
            )
            self._persist_state()

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
            # Bila set posisi terbuka berubah, invalidasi dedup screening agar baris
            # "sudah ada posisi" / margin tak menempel di UI setelah flat.
            open_fp = frozenset(self.open.keys())
            if getattr(self, "_last_open_fp", None) != open_fp:
                self._last_screen.clear()
                self._last_open_fp = open_fp
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
        """Beralih mode berjalan. live = uang nyata (butuh BINANCE_LIVE_KEY/SECRET).

        Paper: JANGAN wipe open di memori dulu lalu harap restore — bucket SQLite
        mode tujuan di-load utuh (termasuk open). Bila open kosong di state tapi
        journal punya open tanpa close, _restore_state → _reconcile_open_from_events
        menutup celah desync.
        """
        import os
        try:
            if eff == getattr(self, "_eff_mode", None):
                return  # no-op: already on this mode
            if eff == "live" and not (os.getenv("BINANCE_LIVE_KEY") and os.getenv("BINANCE_LIVE_SECRET")):
                log.error("Mode LIVE diminta tapi BINANCE_LIVE_KEY/SECRET kosong — tetap paper.")
                return
            # Persist mode LAMA dulu (jangan buang open/saldo paper ke bucket salah).
            try:
                self._persist_state()
            except Exception as e:  # boundary
                log.warning(f"persist pre-switch gagal: {e}")
            prev_open_n = len(self.open)
            new = Settings(mode=eff, raw=self.cfg, gemini_keys=self.settings.gemini_keys,
                           gemini_enabled=self.settings.gemini_enabled)
            self.ex = Exchange(new)
            self.settings = new
            self.live = (eff == "live")
            # Isolasi per-mode HARUS ikut pindah di sini — tanpa ini, _persist_state()
            # & journal terus menulis ke bucket mode LAMA setelah switch runtime,
            # mencampur saldo/riwayat lintas mode (insiden 2026-07-02).
            from .logger import set_journal_mode
            set_journal_mode(eff)
            decision_log.set_mode(eff)
            self._state_key = f"botstate_{eff}"
            # Clear in-memory open/pending HANYA setelah state key pindah; paper
            # segera diisi ulang dari botstate_{eff} (+ reconcile events).
            self.open = {}
            self.pending = {}
            self._last_screen.clear()
            self._last_open_fp = frozenset()
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
                # paper: seed dari saldo KONFIGURASI mode tujuan, lalu pulihkan
                # botstate mode itu (open + saldo hidup). Jangan biarkan open kosong
                # bila state/journal masih punya posisi.
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
            # day counters: hanya reset PnL/trades HARI INI di mode BARU; open
            # sudah dari restore. day_trades di-restore bila hari sama (di
            # _restore_state); di sini jangan paksa 0 bila restore sudah isi.
            if not self.open:
                # flat mode: aman reset day counters
                self._day_pnl_usdt = 0.0
                self._day_pnl_usdc = 0.0
                self._day_trades = 0
                self._day_start_balance_usdt = self.balance_usdt
                self._day_start_balance_usdc = self.balance_usdc
            self._eff_mode = eff
            self._persist_state()
            if self.live:
                log.warning(f"=== BERALIH KE LIVE (UANG NYATA) — saldo Binance USDT ${self.balance_usdt:.2f} + USDC ${self.balance_usdc:.2f} ===")
                self.notify.send(f"⚠️ <b>MODE LIVE AKTIF — UANG NYATA</b>\nSaldo USDT ${self.balance_usdt:.2f} + USDC ${self.balance_usdc:.2f}")
            else:
                log.warning(
                    f"=== beralih ke {eff.upper()} (paper) — open {prev_open_n}→{len(self.open)} ==="
                )
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
            # EC shadow (bila ada): would-skip + reason — BUKAN hard block.
            # UI tampilkan terpisah dari `blocked` agar user paham "gate bilang
            # skip tapi bot tetap boleh open (mode shadow)".
            _ec = c.get("ec_shadow")
            if isinstance(_ec, dict):
                _ec_view = {
                    "would_enter": bool(_ec.get("would_enter")),
                    "decision": _ec.get("decision") or ("enter" if _ec.get("would_enter") else "skip"),
                    "btc_tier": _ec.get("btc_tier"),
                    "structure_pass": bool(_ec.get("structure_pass")),
                    "location_quality": _ec.get("location_quality"),
                    "reason": _ec.get("reason") or "",
                    "setup": _ec.get("setup"),
                }
            else:
                _ec_view = None
            syms.append({"symbol": sym, "price": price, "atr_pct": c.get("atr_pct"),
                         "signal": c.get("side", "-"), "in_position": bool(pos),
                         "blocked": c.get("blocked"), "position": pos_view,
                         "rationale": c.get("rationale"), "setup": c.get("setup"),
                         "ec_shadow": _ec_view})
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

