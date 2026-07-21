"""Close path: monitor, settle, post-mortem, live reconcile (dipotong dari forward.py)."""
from __future__ import annotations

import json as _json
import time
from collections import namedtuple

import pandas as pd

from . import cycle_candidate as cycle_cand
from . import decision_log
from . import mtf
from . import vrp
from .logger import LOG_DIR, journal, log
from .settings_store import RuntimeSettings


class ForwardCloseMixin:
    """Mixin — methods belong to ForwardTester."""

    def _ce_live_track_close(self, sym: str, pos: dict, outcome_r: float) -> None:
        """Live only: accumulate CE-touched R toward stop_loss_r_live (owner stop rule)."""
        if not self.live:
            return
        # Touched if stamped with reasons or size mult < 1 at open
        reasons = pos.get("cycle_candidate_reasons") or []
        sm = pos.get("cycle_candidate_size_mult")
        touched = bool(reasons) or (sm is not None and float(sm) < 0.999)
        if not touched:
            return
        try:
            st = cycle_cand.record_live_close_r(outcome_r, self.cfg, symbol=sym)
            if st.get("stopped"):
                log.warning(
                    f"CE LIVE STOP latched after {sym}: {st.get('stop_reason')} "
                    f"— size/soft_block enforce OFF until reset_live_stop")
                try:
                    self.notify.send(
                        f"⛔ <b>CE LIVE STOP</b>\n{st.get('stop_reason')}\n"
                        f"Enforce OFF — review + reset sadar")
                except Exception:
                    pass
        except Exception as e:  # boundary
            log.warning(f"ce live track {sym}: {e}")

    def _post_mortem_close(self, sym: str, pos: dict, outcome_r: float,
                           reason: str, *, decision_row: dict | None = None) -> None:
        """SQLite trade_reviews under foundation hierarchy. Fail-soft."""
        try:
            from . import trade_review as trev
            from . import decision_log as dlog
            row = decision_row
            if row is None:
                # best-effort: last ENTER for symbol with outcome just written
                try:
                    for r in reversed(dlog.read_all()):
                        if r.get("symbol") == sym and str(r.get("action", "")).startswith("ENTER"):
                            row = r
                            break
                except Exception:
                    row = None
            # enrich pos with live dump if missing
            if "dump_flag" not in pos and not (pos.get("cycle_candidate_tags") or {}).get("dump_flag"):
                try:
                    pos = {**pos, "dump_flag": bool(self._btc_lead().get("dump_flag", False))}
                except Exception:
                    pass
            trev.record_close_review(
                mode=getattr(self.settings, "mode", None) or ("live" if self.live else "dry"),
                symbol=sym,
                side=pos.get("side"),
                outcome_r=outcome_r,
                exit_reason=reason,
                pos=pos,
                decision_row=row,
            )
        except Exception as e:  # boundary — never block close
            log.warning(f"post_mortem {sym}: {e}")

    def _react_settle(self, sym: str, pos: dict, pnl: float, reason: str) -> None:
        """Paper: R dari jarak SL (akuntansi identik backtest)."""
        risk0 = pos.get("risk0") or abs(pos["entry"] - pos["sl"]) * pos["qty"]  # 1R beku saat open
        outcome_r = pnl / risk0 if risk0 else 0.0
        outcome = {"liq": "LIQ", "sl": "SL_HIT", "tp": "TP_HIT"}.get(reason, "CLOSE")
        self._react_link(sym, outcome, outcome_r,
                         extras={"mae_pct": round(pos.get("mae_pct", 0.0), 3),
                                 "mfe_pct": round(pos.get("mfe_pct", 0.0), 3)})
        self._ce_live_track_close(sym, pos, outcome_r)
        self._post_mortem_close(sym, pos, outcome_r, reason)

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
        Hanya mengetatkan (kunci no-loss); tak pernah melonggarkan. Kembalikan jumlah.

        Disabled bila `agent.allow_move_sl` false (default): pemilik minta SL fixed
        setelah open — jangan digeser manage/agent/micro-lock.
        """
        if not getattr(self, "allow_move_sl", False):
            return 0
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

    @staticmethod
    def _tp_progress(pos: dict, price: float) -> float | None:
        """Fraksi perjalanan harga menuju TP: 0=entry, 1=TP, <0 bila underwater. None bila tak ada TP."""
        tp, e = pos.get("tp"), pos.get("entry")
        if not tp or not e:
            return None
        dist = abs(tp - e) or 1e-9
        fav = (price - e) if pos["side"] == "long" else (e - price)
        return fav / dist

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
            if not getattr(self, "allow_move_sl", False):
                log.info(f"Gemini tighten_stop diabaikan {sym} (allow_move_sl=false — SL fixed)")
                return
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
            # Sertakan side/entry dari pos agar dashboard tak tampil baris yatim kosong
            # bila open event hilang / double-reconcile.
            journal("forward_close", {
                "symbol": sym, "reason": "live_exit",
                "side": _pos.get("side"),
                "entry": round(float(_pos["entry"]), 6) if _pos.get("entry") is not None else None,
                "exit": round(float(_pos.get("mark") or _pos.get("entry") or 0), 6) or None,
                "equity": round(self.balance_usdt + self.balance_usdc, 2),
            })
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
            self._ce_live_track_close(sym, pos, outcome_r)
            self._post_mortem_close(sym, pos, outcome_r, "live_exit")
        # CE stop tracking for single gemini live close too (same unambiguous PnL rule)
        if len(closed) == 1 and len(gem_closed) == 1:
            sym, pos = gem_closed[0]
            try:
                outcome_r = ((self.balance_usdt + self.balance_usdc) - prev_balance) / pos["bet"] if pos.get("bet") else 0.0
                self._ce_live_track_close(sym, pos, outcome_r)
                self._post_mortem_close(sym, pos, outcome_r, "live_exit")
            except Exception as e:  # boundary
                log.debug(f"ce live track gem {sym}: {e}")

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
            # Post-mortem SQLite for gemini path too (ReAct path does it inside _react_settle)
            self._post_mortem_close(sym, pos, r, reason)
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
        
        # Event TERPISAH dari forward_close penuh — agar build_trades tak memakan open_map
        # (bug UI: close final jadi yatim tanpa entry/side setelah partial TP).
        journal("forward_close_partial", {
            "symbol": sym, "side": pos.get("side"), "entry": round(pos["entry"], 6),
            "exit": round(exit_fill, 6), "reason": reason or "tp_partial",
            "partial": True, "partial_pct": pct,
            "pnl_usd": round(pnl, 4), "r": round(r, 4),
            "regime": pos.get("regime", "unknown"),
            "mae_pct": round(pos.get("mae_pct", 0.0), 3),
            "mfe_pct": round(pos.get("mfe_pct", 0.0), 3),
            "funding_usd": round(funding, 4),
            "equity": round(self.balance_usdt + self.balance_usdc, 2),
        })
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
            # Micro-profit lock: saat peak ≥60% TP, kunci SL ke breakeven.
            # Hanya bila allow_move_sl (default OFF — pemilik: SL fixed setelah open).
            if getattr(self, "allow_move_sl", False) and peak >= 0.6:
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

