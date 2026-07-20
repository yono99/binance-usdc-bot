"""GeminiTrader — Gemini sebagai praktisi trader (FONDASI).

Alur: build_context (data kaya, diringkas) → decide (JSON terstruktur, fail-safe FLAT)
→ commit (catat ke SQLite saat trade dibuka) → settle (isi outcome R saat ditutup).
Pelajaran (playbook) hanya disuntik bila LOLOS evidence-gate (`store.active_lessons`).

Guardrail: Gemini hanya menentukan ARAH + keyakinan + alasan. Sizing/SL/TP/leverage
deterministik di pemanggil. Gagal/timeout → FLAT (tak buka posisi).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import store
from .config import Settings
from .gemini_client import GeminiClient
from .indicators import adx, atr, ema, rsi
from .logger import log
from .trader_curriculum import DECISION_MODULES, SETUPS, curriculum_prompt, manage_prompt

_FLAT = {"setup": "no_trade", "side": "flat", "conviction": 0.0, "rationale": ""}


def valid_tighten(side: str, old_sl: float, new_sl, price: float,
                  entry: float | None = None) -> bool:
    """GUARDRAIL: True hanya bila stop bergerak MENDEKAT harga (kurangi risiko) & belum
    terpicu. Menjamin Gemini TAK PERNAH bisa melonggarkan stop. Pure → mudah diuji.
    Bila `entry` diberikan, pastikan SL tetap di sisi benar (long: sl<entry, short: sl>entry)."""
    try:
        new_sl = float(new_sl)
    except (TypeError, ValueError):
        return False
    if side == "long":
        if entry is not None and new_sl >= entry:
            return False
        return old_sl < new_sl < price       # naik (lebih ketat), masih di bawah harga
    if entry is not None and new_sl <= entry:
        return False
    return price < new_sl < old_sl            # turun (lebih ketat), masih di atas harga


def track_record(mode: str | None = None,
                 share_across_modes: bool = False) -> dict:
    """Rekam jejak Gemini + VERDICT SIGNIFIKANSI (Fase 3). Demo-only sampai lolos.

    Tidak butuh Gemini (murni statistik dari SQLite) → bisa dipanggil dashboard.

    Tahap 0 (plan-sess): mode=None default = lintas-mode (back-compat dashboard lama),
    pemanggil produksi (forward.py/_track_record) pass mode eksplisit."""
    from .stats import significance_report
    _eff_mode = None if share_across_modes else mode
    decs = store.settled_decisions(mode=_eff_mode)
    rs = [float(d["outcome_r"]) for d in decs if d.get("outcome_r") is not None]
    setups = sorted({d["setup"] for d in decs if d.get("setup")})
    per_setup = [store.setup_stats(s, mode=_eff_mode) for s in setups]
    base = {"n": len(rs), "per_setup": per_setup,
            "active_lessons": store.active_lessons(mode=_eff_mode,
                                                   share_across_modes=share_across_modes),
            "recent": store.recent_decisions(limit=15, mode=_eff_mode)}
    if not rs:
        return {**base, "verdict": "INSUFFICIENT", "verdict_reason": "belum ada trade settled"}
    rep = significance_report(rs, n_trials=1)
    exp = rep["mean_r"]
    wins = sum(1 for r in rs if r > 0)
    if exp <= 0:
        verdict, reason = "REJECTED", f"exp_R {exp:+.3f} ≤ 0 — belum ada edge"
    elif rep["eff_n"] >= 30 and rep["significant"] and exp > 0.05:
        verdict, reason = "PROMISING", (f"exp_R +{exp:.3f}, eff_n={rep['eff_n']}, "
                                        f"p_adj={rep['p_adj']} — lanjut uji (TETAP demo/paper)")
    else:
        verdict, reason = "WEAK", (f"exp_R {exp:+.3f} tapi belum signifikan "
                                   f"(eff_n={rep['eff_n']}, p_adj={rep['p_adj']})")
    return {**base, "win_rate": round(wins / len(rs) * 100, 1), "exp_r": round(exp, 4),
            "significance": rep, "verdict": verdict, "verdict_reason": reason}


def _market_summary(df: pd.DataFrame, cfg: dict) -> dict:
    """Ringkasan numerik yang mudah dibaca Gemini (bukan array mentah)."""
    c = cfg["signals"]
    close = df["close"]
    price = float(close.iloc[-1])
    ef = float(ema(close, c["ema_fast"]).iloc[-1])
    em = float(ema(close, c["ema_mid"]).iloc[-1])
    es = float(ema(close, c["ema_slow"]).iloc[-1])
    adx_v = float(adx(df, c["adx_period"])[0].iloc[-1])
    rsi_v = float(rsi(close, c["rsi_period"]).iloc[-1])
    atr_v = float(atr(df, c["atr_period"]).iloc[-1])
    atr_pct = atr_v / price * 100 if price else 0.0
    ret_5 = float(close.pct_change(5).iloc[-1] * 100) if len(close) > 5 else 0.0
    trend = 1 if ef > em > es else (-1 if ef < em < es else 0)
    st = cfg["strategy"]
    if atr_pct >= st.get("max_atr_pct_chaos", 8.0):
        regime = "chaos"
    elif adx_v >= st["adx_strong"]:
        regime = "trend"
    elif adx_v <= st["adx_range"]:
        regime = "range"
    else:
        regime = "mixed"
    summary = {"price": round(price, 6), "ret_5bar_pct": round(ret_5, 3),
            "ema_align": trend, "adx": round(adx_v, 1), "rsi": round(rsi_v, 1),
            "atr_pct": round(atr_pct, 3), "regime": regime}
    if len(df) >= 20:
        window = min(20, len(df))
        swing_hi = round(float(df["high"].iloc[-window:].max()), 6)
        swing_lo = round(float(df["low"].iloc[-window:].min()), 6)
        range_width_pct = (swing_hi - swing_lo) / price * 100 if price else 0.0
        pos_in_range = (price - swing_lo) / max(swing_hi - swing_lo, 1e-9)
        summary["swing_high"] = swing_hi
        summary["swing_low"] = swing_lo
        summary["range_width_pct"] = round(range_width_pct, 3)
        summary["pos_in_range"] = round(pos_in_range, 2)
        if atr_v > 0:
            summary["range_in_atr"] = round((swing_hi - swing_lo) / atr_v, 1)
    return summary


class GeminiTrader:
    def __init__(self, settings: Settings, cfg: dict):
        gcfg = cfg.get("gemini", {})
        self.cfg = cfg
        self.mode = settings.mode                    # untuk kalibrasi per-mode di konteks
        self.client = GeminiClient(settings.gemini_keys, gcfg.get("model", "gemini-3-flash-preview"))
        self.enabled = settings.gemini_enabled and self.client.available
        # Tahap 0 (plan-sess): default False = pelajaran/rekam-jejak diisolasi PER-MODE
        # (track record live tak mencemari bukti dry, dsb). Opt-in admin True = share bukti
        # antar mode (untuk evaluator yang ingin agregat lintas-mode SENGaja).
        self.share_lessons_across_modes = bool(gcfg.get("share_lessons_across_modes", False))

    def _track_record(self) -> list[dict]:
        """Rekam jejak per-setup DIHITUNG KODE dari SQLite (deterministik, anti-halusinasi):
        win rate, expectancy R, seberapa sering SL tersambar & MAE/MFE-nya. Memberi Gemini
        BUKTI performa tiap setup-nya sendiri — bukan klaim, bukan ramalan.

        Tahap 0: pemfilteran per-mode agar track record tak bercampur (dry-track ≠
        live-track). share_lessons_across_modes=True (opt-in admin) → lintas mode."""
        try:
            from .stats import effective_sample_size
            decs = store.settled_decisions()
            # settled_decisions() tetap lintas-mode — filter di Tier sini:
            if not self.share_lessons_across_modes:
                decs = [d for d in decs if (d.get("mode") or "dry") == self.mode]
            setups = sorted({d["setup"] for d in decs if d.get("setup")})
            out = []
            for s in setups:
                stats = store.setup_stats(s, mode=None if self.share_lessons_across_modes
                                          else self.mode)
                rs = [float(d["outcome_r"]) for d in decs if d["setup"] == s]
                eff_n = effective_sample_size(rs)
                stats["eff_n"] = round(eff_n, 1)
                stats["evidence"] = "adequate" if eff_n >= 30 else "insufficient"
                out.append(stats)
            return out
        except Exception:  # boundary — konteks opsional
            return []

    def _exit_track_record(self) -> list[dict]:
        """Scorecard per exit_reason (sl/tp/cut-loss/gemini_exit) DIHITUNG KODE — agar Gemini
        BELAJAR cara-keluar mana yang -EV (mis. gemini_exit / cut prematur) & berhentikan.

        Tahap 0: agregat per-mode jika tak share (store.exit_stats(mode=self.mode))."""
        try:
            return store.exit_stats(mode=None if self.share_lessons_across_modes else self.mode)
        except Exception:  # boundary — konteks opsional
            return []

    # ---------- konteks ----------
    def build_context(self, symbol: str, df: pd.DataFrame, *, alt: dict | None = None,
                      position: dict | None = None,
                      balance_usdt: float | None = None,
                      balance_usdc: float | None = None,
                      news_note: str = "", portfolio: dict | None = None,
                      btc_lead: dict | None = None, halving_phase: str = "") -> dict:
        ctx = {
            "symbol": symbol,
            "market": _market_summary(df, self.cfg),
            "alt": alt or {},                       # funding_z, oi_delta, cvd_imb, basis_z (skalar)
            "btc_lead": btc_lead or {},             # MOTHERCOIN: gerak BTC 1bar/3bar % + arah.
            #   Alt ber-beta lebih tinggi → BTC turun 1-4%+ sering diperbesar/diperpanjang di alt.
            #   dump_flag=True saat BTC turun >=2% 3-bar (konteks regime; short boost default OFF).
            #   dominance_dir=+1 = risk-off (BTC.D naik), -1 = risk-on (alt outperform).
            "halving_phase": halving_phase or "unknown",  # fase siklus 4-tahun: macro regime.
            #   'accumulation' / 'pre-halving' / 'post-halving' / 'bull' / 'blow-off' / 'bear'
            #   Bull/bear kalibrasi conviction: trend-following LONG saat bull, SHORT saat bear.
            "position": position,                   # posisi terbuka di simbol INI (atau None)
            "portfolio": portfolio,                 # SEMUA posisi terbuka + eksposur (korelasi/risiko)
            "balance_usdt": round(balance_usdt, 2) if balance_usdt is not None else None,
            "balance_usdc": round(balance_usdc, 2) if balance_usdc is not None else None,
            "news": news_note,
            "recent_decisions": store.recent_decisions(symbol, limit=5,
                                                      mode=None if self.share_lessons_across_modes
                                                      else self.mode),
            "tested_lessons": store.active_lessons(limit=10,
                                                   mode=None if self.share_lessons_across_modes
                                                   else self.mode,
                                                   share_across_modes=self.share_lessons_across_modes),
            # Grounding tambahan dari SQLite (agar Gemini PAHAM performa nyatanya):
            "setup_track_record": self._track_record(),         # stats per-setup (dihitung kode)
            "exit_track_record": self._exit_track_record(),      # BELAJAR dari SL/CL/TP/gemini_exit
            "calibration": self._calibration(),                 # kejujuran confidence-nya (Brier)
            "sl_feedback": self._sl_feedback(symbol),           # ADAPTASI pasca SL/cut-loss
            "loss_postmortem": self._postmortem(symbol),        # TANYA-JAWAB kekalahan (koreksi diri)
        }
        return ctx

    def _sl_feedback(self, symbol: str, lookback: int = 8) -> dict | None:
        """Umpan balik ADAPTASI SL/cut-loss untuk simbol INI (dari SQLite). Bila entry
        terakhir tersapu SL/likuidasi, Gemini harus MENYESUAIKAN — bukan mengulang entry
        yang sama. MFE sebelum SL membedakan 'SL kemepetan' vs 'arah/timing salah'.

        Tahap 0: filter per-mode (kecuali share_across_modes)."""
        try:
            decs = [d for d in store.recent_decisions(symbol, limit=lookback,
                                                      mode=None if self.share_lessons_across_modes
                                                      else self.mode)
                    if d.get("status") == "settled" and d.get("outcome_r") is not None]
        except Exception:  # boundary — konteks opsional
            return None
        if not decs:
            return None
        streak = 0                                   # beruntun rugi TERBARU (paling atas)
        for d in decs:
            if float(d["outcome_r"]) < 0:
                streak += 1
            else:
                break
        sl_hits = [d for d in decs if d.get("exit_reason") in ("sl", "liq")]
        if streak == 0 and not sl_hits:
            return None                              # tak ada yang perlu diadaptasi
        def _avg(key):
            vals = [float(d[key]) for d in sl_hits if d.get(key) is not None]
            return round(sum(vals) / len(vals), 3) if vals else None
        return {"loss_streak": streak, "recent_sl_or_liq": len(sl_hits),
                "avg_mfe_before_sl_pct": _avg("mfe_pct"),   # besar = SL terlalu mepet
                "avg_mae_pct": _avg("mae_pct"),
                "last_reasons": [d.get("exit_reason") for d in decs[:4]],
                "last_sides": [d.get("side") for d in decs[:4]]}

    def _postmortem(self, symbol: str, limit: int = 4) -> list[dict]:
        """Post-mortem SL/cut-loss simbol INI sbg tanya-jawab (dari SQLite). Gemini
        melihat alasan entry-nya sendiri yang GAGAL → koreksi, bukan ulangi."""
        try:
            return store.loss_postmortems(symbol, limit=limit)
        except Exception:  # boundary — konteks opsional
            return []

    def _calibration(self) -> dict:
        """Kalibrasi confidence Gemini di mode ini (Brier rolling) dari SQLite. Memberi
        Gemini umpan balik apakah angka conviction-nya SELAMA INI jujur (0.25 = koin)."""
        try:
            rep = store.calibration_report(self.mode, last_n=50, days=14)
            return rep.get("last_50_trades", {})
        except Exception:  # boundary — konteks opsional
            return {}

    # ---------- keputusan ----------
    def decide(self, context: dict) -> dict:
        """Kembalikan {setup, side, conviction, rationale}. Fail-safe = FLAT."""
        if not self.enabled:
            return {**_FLAT, "rationale": "gemini off → flat"}
        # Phase 4: modul evidence-based (buang hafalan pola harga OHLCV yang breakeven).
        prompt = (curriculum_prompt(modules=DECISION_MODULES) + "\n\nKONTEKS PASAR (JSON):\n"
                  + json.dumps(context, default=str))
        text = self.client.generate(prompt, purpose="trader")
        if not text:
            return {**_FLAT, "rationale": "gemini gagal → flat"}
        try:
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        except Exception as e:  # boundary — jangan trading karena parse gagal
            log.warning(f"trader parse gagal → flat: {e}")
            return {**_FLAT, "rationale": "parse gagal → flat"}
        return self._sanitize(data)

    # Field GLOBAL (sama utk semua simbol dalam satu siklus) → kirim SEKALI di batch,
    # bukan diduplikasi per simbol. sl_feedback/recent_decisions/loss_postmortem PER-SIMBOL.
    _SHARED_KEYS = ("setup_track_record", "exit_track_record", "calibration",
                    "tested_lessons", "btc_lead", "halving_phase", "portfolio",
                    "balance_usdt", "balance_usdc", "news")

    def _split_batch(self, contexts: dict[str, dict]) -> tuple[dict, list[dict]]:
        """Pisahkan konteks bersama (global) dari data per-simbol → hemat token."""
        first = next(iter(contexts.values()))
        shared = {k: first.get(k) for k in self._SHARED_KEYS}
        per = [{k: v for k, v in ctx.items() if k not in self._SHARED_KEYS}
               for ctx in contexts.values()]
        return shared, per

    def decide_batch(self, contexts: dict[str, dict]) -> dict[str, dict]:
        """Satu atau beberapa panggilan Gemini untuk BANYAK simbol → hemat RPD/TPM.

        Kurikulum + grounding global dikirim SEKALI per sub-batch (bukan per simbol).
        Batch besar dipecah menjadi sub-batch ≤ batch_chunk_size (default 4) agar:
          1. Prompt tidak terlalu panjang → Gemini tak "lupa" simbol awal (attention OK).
          2. Error parse satu chunk hanya flat chunk itu — chunk lain tetap jalan.
          3. Token per request lebih terkontrol → reliabilitas parse JSON naik.

        Balas JSON {symbol: keputusan}. Simbol hilang / parse gagal → FLAT (fail-safe)."""
        def _all_flat(reason: str) -> dict[str, dict]:
            return {s: {**_FLAT, "rationale": reason} for s in contexts}

        if not contexts:
            return {}
        if not self.enabled:
            return _all_flat("gemini off → flat")

        chunk_size = int(self.cfg.get("gemini", {}).get("batch_chunk_size", 4))
        chunk_size = max(1, chunk_size)
        items = list(contexts.items())
        chunks = [dict(items[i:i + chunk_size]) for i in range(0, len(items), chunk_size)]
        n_chunks = len(chunks)

        out: dict[str, dict] = {}
        for ci, chunk_ctx in enumerate(chunks, 1):
            if n_chunks > 1:
                log.debug(f"trader_batch sub-batch {ci}/{n_chunks} ({len(chunk_ctx)} simbol)")
            shared, per = self._split_batch(chunk_ctx)
            syms_in_chunk = list(chunk_ctx.keys())
            prompt = (
                curriculum_prompt(modules=DECISION_MODULES)
                + "\n\nBANYAK SIMBOL: 'KONTEKS BERSAMA' berlaku untuk SEMUA. Untuk SETIAP simbol di\n"
                  "array 'symbols', hasilkan keputusan dengan SKEMA OUTPUT yang sama seperti di atas.\n"
                  "Balas HANYA JSON object, KUNCI = symbol persis (mis. \"BTC/USDC:USDC\"):\n"
                  '{"<symbol>": {<skema keputusan>}, ...}. Sertakan SEMUA simbol; ragu → flat.\n\n'
                + "KONTEKS BERSAMA (JSON):\n" + json.dumps(shared, default=str)
                + "\n\nsymbols (JSON array):\n" + json.dumps(per, default=str))
            text = self.client.generate(prompt, purpose="trader_batch")
            if not text:
                log.warning(f"trader_batch chunk {ci}/{n_chunks} gagal → flat {syms_in_chunk}")
                for s in syms_in_chunk:
                    out[s] = {**_FLAT, "rationale": "gemini gagal → flat"}
                continue
            try:
                data = json.loads(text[text.find("{"):text.rfind("}") + 1])
            except Exception as e:
                log.warning(f"trader_batch chunk {ci}/{n_chunks} parse gagal → flat: {e}")
                for s in syms_in_chunk:
                    out[s] = {**_FLAT, "rationale": "parse gagal → flat"}
                continue
            missing = [s for s in syms_in_chunk if not isinstance(data.get(s), dict)]
            if missing:  # jaring pengaman: batch drop = entry hilang diam-diam (spt zero-entry PLAY)
                log.warning(f"trader_batch chunk {ci}/{n_chunks}: {len(missing)}/"
                            f"{len(syms_in_chunk)} simbol HILANG dari balasan → flat: {missing}")
            for s in syms_in_chunk:
                d = data.get(s)
                out[s] = self._sanitize(d) if isinstance(d, dict) else {
                    **_FLAT, "rationale": "tak ada di balasan → flat"}
        return out

    def _sanitize(self, data: dict) -> dict:
        """Validasi keras output AI; apa pun aneh → FLAT (fail-safe)."""
        setup = data.get("setup")
        side = data.get("side")
        if setup not in SETUPS or side not in ("long", "short", "flat"):
            return {**_FLAT, "rationale": "output tak valid → flat"}
        if side == "flat":
            return {"setup": "no_trade", "side": "flat", "conviction": 0.0,
                    "rationale": str(data.get("rationale", ""))[:200]}
        try:
            conv = float(data.get("conviction", 0.0))
        except (TypeError, ValueError):
            conv = 0.0
        conv = max(0.0, min(conv, 1.0))
        # Gemini trader penuh: WAJIB sertakan SL (invalidasi). Tanpa SL valid → FLAT (fail-safe).
        try:
            sl = float(data.get("sl"))
        except (TypeError, ValueError):
            return {**_FLAT, "rationale": "tanpa SL valid → flat (invalidasi wajib)"}
        try:
            tp = float(data.get("tp"))
        except (TypeError, ValueError):
            tp = None                                  # TP opsional → kode fallback ke ATR
        regime = data.get("regime_classification")
        regime = regime if regime in ("trend", "range", "chaos", "mixed") else None
        return {"setup": setup, "side": side, "conviction": round(conv, 3),
                "sl": sl, "tp": tp, "regime_classification": regime,
                "rationale": str(data.get("rationale", ""))[:200]}

    # ---------- kelola posisi terbuka (exit-only, ~1 menit) ----------
    def manage(self, context: dict) -> dict:
        """Tinjau posisi terbuka. Kembalikan {action, new_sl?, reason}. Fail-safe = HOLD.
        Hanya boleh MENGURANGI risiko; guardrail final di-enforce pemanggil (forward)."""
        if not self.enabled:
            return {"action": "hold", "reason": "gemini off"}
        prompt = manage_prompt() + "\n\nPOSISI & PASAR (JSON):\n" + json.dumps(context, default=str)
        text = self.client.generate(prompt, purpose="manage")
        if not text:
            return {"action": "hold", "reason": "gemini gagal → hold"}
        try:
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        except Exception as e:  # boundary
            log.warning(f"manage parse gagal → hold: {e}")
            return {"action": "hold", "reason": "parse gagal → hold"}
        return self._sanitize_manage(data)

    def _sanitize_manage(self, data: dict) -> dict:
        action = data.get("action")
        if action not in ("hold", "exit", "tighten_stop"):
            return {"action": "hold", "reason": "aksi tak valid → hold"}
        reason = str(data.get("reason", ""))[:200]
        if action == "tighten_stop":
            try:
                return {"action": "tighten_stop", "new_sl": float(data.get("new_sl")), "reason": reason}
            except (TypeError, ValueError):
                return {"action": "hold", "reason": "tighten tanpa new_sl valid → hold"}
        return {"action": action, "reason": reason}

    # ---------- persistensi (dipanggil saat trade dibuka/ditutup) ----------
    def commit(self, symbol: str, decision: dict, context: dict) -> int | None:
        """Catat keputusan actionable ke SQLite (flat tak dicatat). Tahap 0: kirim mode
        self.mode agar terisolasi per-mode (paper/dry tak mencemari live)."""
        if decision["side"] == "flat":
            return None
        return store.record_decision(
            symbol, decision["setup"], decision["side"], decision["conviction"],
            decision["rationale"], context, model=self.cfg.get("gemini", {}).get("model", ""),
            mode=self.mode)

    def settle(self, decision_id: int, outcome_r: float, mae_pct: float | None = None,
               mfe_pct: float | None = None, exit_reason: str | None = None) -> None:
        store.settle_decision(decision_id, outcome_r, mae_pct=mae_pct,
                              mfe_pct=mfe_pct, exit_reason=exit_reason)

    # ---------- playbook (evidence-gated) ----------
    def propose_lesson(self, scope: str, setup: str, text: str) -> int:
        return store.add_lesson(scope, setup, text, mode=self.mode)

    def promote_lessons(self, min_n: int = 20) -> int:
        """Aktifkan pelajaran yang setup-nya cukup bukti (anti-takhayul).
        Tahap 0: bukti digabung (lintas-mode) untuk aktivasi; les.mode dipertahankan."""
        return store.promote_lessons(min_n=min_n,
                                     share_across_modes=self.share_lessons_across_modes)

    # ---------- refleksi: "Gemini belajar dari rekam jejaknya" ----------
    def reflect(self, lookback: int = 80, min_settled: int = 10, min_n_promote: int = 20) -> dict:
        """Loop belajar mandiri:
        1) KODE hitung statistik NYATA per setup (deterministik, anti-halusinasi).
        2) Bila cukup data & Gemini aktif: Gemini evaluasi diri & USULKAN pelajaran
           (terikat setup yang ada). Pelajaran masuk sebagai 'proposed' (belum aktif).
        3) EVIDENCE-GATE: promote_lessons mengaktifkan HANYA yang cukup bukti.
        Selalu aman dipanggil (boundary); mengembalikan ringkasan.

        Tahap 0 (plan-sess): agregasi per-mode (default) — dry-track tak mencemari
        live-track. share_across_modes=True opt-in admin untuk agregat lintas-mode."""
        decisions = store.recent_decisions(limit=lookback,
                                           mode=None if self.share_lessons_across_modes
                                           else self.mode)
        settled = [d for d in decisions if d["status"] == "settled" and d.get("outcome_r") is not None]
        setups = sorted({d["setup"] for d in settled if d.get("setup")})
        stats = {s: store.setup_stats(s, mode=None if self.share_lessons_across_modes
                                      else self.mode) for s in setups}

        summary = ("data kurang untuk refleksi bermakna" if len(settled) < min_settled
                   else f"{len(settled)} trade settled di {len(setups)} setup")

        if self.enabled and len(settled) >= min_settled:
            prompt = (curriculum_prompt(modules=["meta", "risk"])
                      + "\n\nINI STATISTIK NYATA REKAM JEJAKMU (dihitung sistem, bukan klaim). "
                      "Evaluasi JUJUR: setup mana yang bekerja/merugi & kenapa, tanda overtrading "
                      "atau bias. Lalu USULKAN pelajaran konkret. Pelajaran HARUS terikat satu "
                      "setup yang ada & akan diverifikasi bukti sebelum dipakai. Balas HANYA JSON: "
                      '{"summary":"<evaluasi singkat>","lessons":[{"setup":"<setup>",'
                      '"scope":"*","text":"<pelajaran>"}]}\n'
                      f"STATISTIK:\n{json.dumps(stats, default=str)}")
            text = self.client.generate(prompt, purpose="reflect")
            if text:
                try:
                    data = json.loads(text[text.find("{"):text.rfind("}") + 1])
                    summary = str(data.get("summary", summary))[:400]
                    for les in data.get("lessons", [])[:6]:
                        if les.get("setup") in SETUPS and les.get("text"):
                            store.add_lesson(str(les.get("scope", "*")), les["setup"],
                                             str(les["text"])[:300], mode=self.mode)
                except Exception as e:  # boundary
                    log.warning(f"reflect parse gagal: {e}")

        active = self.promote_lessons(min_n=min_n_promote)   # evidence-gate (deterministik)
        store.add_reflection(period=f"last_{lookback}", summary=summary,
                             metrics={"settled": len(settled), "stats": stats, "active_lessons": active},
                             mode=self.mode)
        return {"settled": len(settled), "setups": setups, "active_lessons": active, "summary": summary}
