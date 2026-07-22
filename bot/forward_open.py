"""Open path: sizing, live open, _open_usd (dipotong dari forward.py)."""
from __future__ import annotations

import time
from collections import namedtuple

import pandas as pd

from . import cycle_candidate as cycle_cand
from . import decision_log
from . import risk_filter as risk_filter_mod
from .logger import journal, log
from .settings_store import RuntimeSettings, liquidation_price

# Same helper used by legacy open path (forward.default_params era)
_Sig = namedtuple("_Sig", ["side", "atr"])


class ForwardOpenMixin:
    """Mixin — methods belong to ForwardTester."""

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
        pos.update(risk_filter_mod.stamp(getattr(self, "_risk_filter_verdict", None)))
        self.open[sym] = pos
        journal("forward_open", {"symbol": sym, "side": sig.side, "entry": pos["entry"],
                                 "sl": pos["sl"], "tp": pos["tp"]})
        log.info(f"OPEN {sig.side.upper()} {sym} @ {pos['entry']:.6f} SL={pos['sl']:.6f} TP={pos['tp']:.6f}")

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

    def _open_usd(self, sym: str, side: int, atr: float, rs: RuntimeSettings) -> None:
        if sym in self.open:
            log.warning(f"DUPOPEN BLOCKED {sym}: already in self.open")
            return
        # Tahap 4a: cooldown/blacklist per-mode (opsional via config rotate.*).
        # Kebijakan pemilik: default OFF (0) — rugi dicatat sebagai pelajaran,
        # BUKAN hukuman pair. Bila config >0, gate tetap hormati (opt-in lama).
        try:
            from . import cooldown as _cd
            _r_cfg = self.cfg.get("rotate", {}) or {}
            _cd_on = (float(_r_cfg.get("cooldown_minutes", 0) or 0) > 0
                      or int(_r_cfg.get("blacklist_after_sl", 0) or 0) > 0)
            if _cd_on and not _cd.available(self.settings.mode, sym):
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
        # ── ASYMMETRIC SHORT SIZING (opsional; default OFF):
        # Historis: dump_flag → SHORT conviction ×1.5. H-CYC-01/01b (2026-07-20): short-after-dump
        # bukan edge OOS → btc.dump_short_boost default false. dump_flag tetap di context.
        if gem and side == -1 and self._dump_short_boost_enabled():
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
        # ── CANDIDATE EDGE (ilmu siklus pemilik) — shadow/size/soft_block; NOT PROMOTE_PAPER.
        # Live enforce only if allow_live AND risk_ack (owner understands unproven risk).
        # Default: long size-down / soft-skip; never auto-short dump/unlock.
        ce_verdict = None
        try:
            _dump = bool(self._btc_lead().get("dump_flag", False))
            _cctx = self._cycle_context(sym)
            ce_verdict = cycle_cand.evaluate(
                side=side, cfg=self.cfg, live=bool(self.live),
                dump_flag=_dump, cycle_context=_cctx)
            if cycle_cand.should_log(ce_verdict):
                # size_would / skip_would = counterfactual even in shadow.
                # size_mult_after = what actually ships (only if applied).
                _would_mult = max(0.05, float(size_mult) * float(ce_verdict.size_mult or 1.0))
                _cc_flags = cycle_cand.cfg_from(self.cfg)
                _skip_would = bool(ce_verdict.skip) or (
                    side == 1 and bool(_cc_flags.get("soft_block_long_on_dump"))
                    and bool(_dump))
                decision_log.append({
                    "ts": pd.Timestamp.utcnow().isoformat(),
                    "symbol": sym,
                    "action": "CANDIDATE_EDGE_SHADOW",
                    "side": "long" if side == 1 else "short" if side == -1 else "flat",
                    "outcome": None,
                    "size_mult_before": size_mult,
                    "size_would": round(_would_mult, 4),
                    "skip_would": _skip_would,
                    "size_mult_after": (cycle_cand.apply_size(size_mult, ce_verdict)
                                       if ce_verdict.applied else size_mult),
                    **cycle_cand.stamp(ce_verdict),
                })
            if ce_verdict.applied and ce_verdict.skip:
                c = self.sig_cache.setdefault(sym, {})
                c["blocked"] = ("cycle_candidate soft_block "
                                + ",".join(ce_verdict.reasons or ["skip"]))
                journal("forward_skip", {"symbol": sym, "reason": "cycle_candidate",
                                         "reasons": ce_verdict.reasons,
                                         "side": "long" if side == 1 else "short"})
                log.info(f"SKIP {sym}: cycle_candidate {ce_verdict.reasons}")
                return
            if ce_verdict.applied and ce_verdict.size_mult < 0.999:
                size_mult = cycle_cand.apply_size(size_mult, ce_verdict)
                log.info(f"cycle_candidate size {sym}: mult→{size_mult:.3f} "
                         f"reasons={ce_verdict.reasons}")
        except Exception as e:  # boundary — candidate must never block on error
            log.warning(f"cycle_candidate {sym} fail-open: {e}")
            ce_verdict = None
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
                                     if self.buffers.get(sym) is not None else {})),
                    **risk_filter_mod.stamp(getattr(self, "_risk_filter_verdict", None)),
                    **cycle_cand.stamp(ce_verdict)}
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
                          **mtf_stamp,
                          **risk_filter_mod.stamp(getattr(self, "_risk_filter_verdict", None)),
                          **cycle_cand.stamp(ce_verdict)}
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
                                 "conviction": gem_conv, "size_mult": size_mult,
                                 **cycle_cand.stamp(ce_verdict)})
        # Persist SEGERA setelah open — crash/restart antara open & end-of-cycle
        # dulu bikin journal ada OPEN tapi botstate.open kosong (ghost).
        self._persist_state()
        # Status UI juga di-refresh mid-cycle: siklus Gemini bisa >5 menit; tanpa ini
        # open_count di header tetap 0 meski botstate sudah punya posisi.
        try:
            if self.rs is not None:
                self._write_status(self.rs, False, "")
        except Exception as e:  # boundary
            log.debug(f"write_status after open: {e}")
        log.info(f"OPEN {self.open[sym]['side'].upper()} {sym} x{rs.leverage} bet=${bet:.2f} "
                 f"@ {entry:.4f} SL={sl:.4f} TP={tp:.4f} LIQ={liq:.4f}")
        self.notify.send(
            f"🟢 <b>OPEN {self.open[sym]['side'].upper()}</b> {sym} x{rs.leverage}\n"
            f"Entry {entry:.4f} · SL {sl:.4f} · TP {tp:.4f}\n"
            f"LIQ {liq:.4f} · bet ${bet:.2f}")

