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


def valid_tighten(side: str, old_sl: float, new_sl, price: float) -> bool:
    """GUARDRAIL: True hanya bila stop bergerak MENDEKAT harga (kurangi risiko) & belum
    terpicu. Menjamin Gemini TAK PERNAH bisa melonggarkan stop. Pure → mudah diuji."""
    try:
        new_sl = float(new_sl)
    except (TypeError, ValueError):
        return False
    if side == "long":
        return old_sl < new_sl < price       # naik (lebih ketat), masih di bawah harga
    return price < new_sl < old_sl            # turun (lebih ketat), masih di atas harga


def track_record() -> dict:
    """Rekam jejak Gemini + VERDICT SIGNIFIKANSI (Fase 3). Demo-only sampai lolos.

    Tidak butuh Gemini (murni statistik dari SQLite) → bisa dipanggil dashboard."""
    from .stats import significance_report
    decs = store.settled_decisions()
    rs = [float(d["outcome_r"]) for d in decs if d.get("outcome_r") is not None]
    setups = sorted({d["setup"] for d in decs if d.get("setup")})
    per_setup = [store.setup_stats(s) for s in setups]
    base = {"n": len(rs), "per_setup": per_setup, "active_lessons": store.active_lessons(),
            "recent": store.recent_decisions(limit=15)}
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
    return {"price": round(price, 6), "ret_5bar_pct": round(ret_5, 3),
            "ema_align": trend, "adx": round(adx_v, 1), "rsi": round(rsi_v, 1),
            "atr_pct": round(atr_pct, 3), "regime": regime}


class GeminiTrader:
    def __init__(self, settings: Settings, cfg: dict):
        gcfg = cfg.get("gemini", {})
        self.cfg = cfg
        self.mode = settings.mode                    # untuk kalibrasi per-mode di konteks
        self.client = GeminiClient(settings.gemini_keys, gcfg.get("model", "gemini-3-flash-preview"))
        self.enabled = settings.gemini_enabled and self.client.available

    def _track_record(self) -> list[dict]:
        """Rekam jejak per-setup DIHITUNG KODE dari SQLite (deterministik, anti-halusinasi):
        win rate, expectancy R, seberapa sering SL tersambar & MAE/MFE-nya. Memberi Gemini
        BUKTI performa tiap setup-nya sendiri — bukan klaim, bukan ramalan."""
        try:
            setups = sorted({d["setup"] for d in store.settled_decisions() if d.get("setup")})
            return [store.setup_stats(s) for s in setups]
        except Exception:  # boundary — konteks opsional
            return []

    # ---------- konteks ----------
    def build_context(self, symbol: str, df: pd.DataFrame, *, alt: dict | None = None,
                      position: dict | None = None, balance: float | None = None,
                      news_note: str = "", portfolio: dict | None = None) -> dict:
        ctx = {
            "symbol": symbol,
            "market": _market_summary(df, self.cfg),
            "alt": alt or {},                       # funding_z, oi_delta, cvd_imb, basis_z (skalar)
            "position": position,                   # posisi terbuka di simbol INI (atau None)
            "portfolio": portfolio,                 # SEMUA posisi terbuka + eksposur (korelasi/risiko)
            "balance_usd": round(balance, 2) if balance is not None else None,
            "news": news_note,
            "recent_decisions": store.recent_decisions(symbol, limit=5),
            "tested_lessons": store.active_lessons(limit=10),   # HANYA yang lolos bukti
            # Grounding tambahan dari SQLite (agar Gemini PAHAM performa nyatanya):
            "setup_track_record": self._track_record(),         # stats per-setup (dihitung kode)
            "calibration": self._calibration(),                 # kejujuran confidence-nya (Brier)
            "sl_feedback": self._sl_feedback(symbol),           # ADAPTASI pasca SL/cut-loss
            "loss_postmortem": self._postmortem(symbol),        # TANYA-JAWAB kekalahan (koreksi diri)
        }
        return ctx

    def _sl_feedback(self, symbol: str, lookback: int = 8) -> dict | None:
        """Umpan balik ADAPTASI SL/cut-loss untuk simbol INI (dari SQLite). Bila entry
        terakhir tersapu SL/likuidasi, Gemini harus MENYESUAIKAN — bukan mengulang entry
        yang sama. MFE sebelum SL membedakan 'SL kemepetan' vs 'arah/timing salah'."""
        try:
            decs = [d for d in store.recent_decisions(symbol, limit=lookback)
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
        """Catat keputusan actionable ke SQLite (flat tak dicatat)."""
        if decision["side"] == "flat":
            return None
        return store.record_decision(
            symbol, decision["setup"], decision["side"], decision["conviction"],
            decision["rationale"], context, model=self.cfg.get("gemini", {}).get("model", ""))

    def settle(self, decision_id: int, outcome_r: float, mae_pct: float | None = None,
               mfe_pct: float | None = None, exit_reason: str | None = None) -> None:
        store.settle_decision(decision_id, outcome_r, mae_pct=mae_pct,
                              mfe_pct=mfe_pct, exit_reason=exit_reason)

    # ---------- playbook (evidence-gated) ----------
    def propose_lesson(self, scope: str, setup: str, text: str) -> int:
        return store.add_lesson(scope, setup, text)

    def promote_lessons(self, min_n: int = 20) -> int:
        """Aktifkan pelajaran yang setup-nya cukup bukti (anti-takhayul)."""
        return store.promote_lessons(min_n=min_n)

    # ---------- refleksi: "Gemini belajar dari rekam jejaknya" ----------
    def reflect(self, lookback: int = 80, min_settled: int = 10, min_n_promote: int = 20) -> dict:
        """Loop belajar mandiri:
        1) KODE hitung statistik NYATA per setup (deterministik, anti-halusinasi).
        2) Bila cukup data & Gemini aktif: Gemini evaluasi diri & USULKAN pelajaran
           (terikat setup yang ada). Pelajaran masuk sebagai 'proposed' (belum aktif).
        3) EVIDENCE-GATE: promote_lessons mengaktifkan HANYA yang cukup bukti.
        Selalu aman dipanggil (boundary); mengembalikan ringkasan."""
        decisions = store.recent_decisions(limit=lookback)
        settled = [d for d in decisions if d["status"] == "settled" and d.get("outcome_r") is not None]
        setups = sorted({d["setup"] for d in settled if d.get("setup")})
        stats = {s: store.setup_stats(s) for s in setups}

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
                                             str(les["text"])[:300])
                except Exception as e:  # boundary
                    log.warning(f"reflect parse gagal: {e}")

        active = store.promote_lessons(min_n=min_n_promote)   # evidence-gate (deterministik)
        store.add_reflection(period=f"last_{lookback}", summary=summary,
                             metrics={"settled": len(settled), "stats": stats, "active_lessons": active})
        return {"settled": len(settled), "setups": setups, "active_lessons": active, "summary": summary}
