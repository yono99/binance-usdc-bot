"""Strategy Co-Pilot — Gemini sebagai PARTNER riset, bukan pengambil keputusan.

Peran (sesuai arahan: "Gemini sebagai co-pilot"):
1. MENAFSIRKAN hasil walk-forward OOS dalam bahasa natural (kenapa menang/kalah,
   divergensi antar-simbol, tanda overfit).
2. MENGUSULKAN hipotesis berikutnya yang STRUKTURAL berbeda, lengkap dengan
   rasional ekonomi + apa yang memfalsifikasinya.

GUARDRAIL KERAS (di-enforce di KODE, bukan dipercayakan ke AI):
- Verdict (CANDIDATE/WEAK/REJECTED) dihitung DETERMINISTIK dari metrik OOS di
  sini — Gemini TIDAK boleh mengubahnya. Backtest walk-forward = satu-satunya hakim.
- Rekomendasi LIVE hanya bila OOS exp_R > 0.05R DAN ≥3 window positif DAN ≥3 simbol
  konsisten. Gemini tak bisa melonggarkan ini.
- Fail-open: tanpa GEMINI_ENABLED/keys, co-pilot tetap memberi interpretasi
  deterministik (degradasi anggun), hanya tanpa narasi natural-language.

Memakai GeminiClient yang sama (rotasi banyak key) dengan layer veto/news.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import registry
from .config import Settings
from .gemini_client import GeminiClient
from .logger import log
from .stats import significance_report

# Peta strategi → tag sumber terkontrol (untuk auto-record ke registry).
STRATEGY_SOURCE = {
    "v1": "trend_ohlcv", "v2": "htf_regime_session", "v3": "funding_oi_filter",
    "v4": "orderflow_cvd", "v5": "cross_exchange_basis", "v6": "liquidation_cascade",
    "v7": "funding_regime_primary",
}


@dataclass
class CycleResult:
    """Ringkasan satu siklus riset — JSON-serializable, dipasok ke Gemini."""
    strategy: str
    hypothesis: str
    per_symbol: dict = field(default_factory=dict)   # sym -> list[ {oos_exp, oos_n, params} ]
    aggregate: dict = field(default_factory=dict)     # expectancy_r, profit_factor, win_rate, ...
    chosen_params: list = field(default_factory=list)
    oos_r: list = field(default_factory=list)         # R-multiple tiap trade OOS (untuk signifikansi)
    trials: int = 1                                    # jumlah trial kumulatif (multiple-testing)


# Ambang minimum sample EFEKTIF (bukan mentah) untuk klaim signifikansi.
MIN_EFF_N = 30
# Param terpilih harus stabil antar-window (share modal ≥ ini); loncat-loncat = noise.
MIN_PARAM_STABILITY = 0.5


def _param_stability(per_symbol: dict) -> float:
    """Rata-rata (antar simbol) share window yang memilih parameter MODAL.
    1.0 = parameter sama di semua window (stabil); rendah = tidak stabil (overfit/noise)."""
    from collections import Counter
    shares = []
    for _sym, wins in per_symbol.items():
        ps = [w.get("params") for w in wins if w.get("params")]
        if not ps:
            continue
        shares.append(Counter(ps).most_common(1)[0][1] / len(ps))
    return sum(shares) / len(shares) if shares else 0.0


def verdict(cycle: CycleResult) -> tuple[str, str]:
    """Verdict DETERMINISTIK (otoritas tunggal). (label, alasan ringkas).

    CANDIDATE kini menuntut BUKTI STATISTIK, bukan sekadar tanda exp_R>0.05:
    lolos konsistensi → lalu WAJIB lolos bootstrap (Bonferroni atas jumlah trial)
    DAN effective-n ≥ 30. Ini pertahanan inti melawan false positive multiple-testing."""
    agg = cycle.aggregate
    exp = agg.get("expectancy_r", float("-inf"))
    n = agg.get("trades", 0)
    pos_windows = 0
    pos_symbols = 0
    for _sym, wins in cycle.per_symbol.items():
        sym_oos = [w["oos_exp"] for w in wins]
        pos_windows += sum(1 for x in sym_oos if x > 0.05)
        if sym_oos and sum(sym_oos) / len(sym_oos) > 0.05:
            pos_symbols += 1

    if exp <= 0:
        return "REJECTED", f"OOS {exp:+.3f}R ≤ 0 — tidak ada edge general."

    strong = exp > 0.05 and n >= 30 and pos_windows >= 3 and pos_symbols >= 3
    if not strong:
        return "WEAK", (f"OOS +{exp:.3f}R; konsistensi/level kurang "
                        f"({pos_symbols}/3 simbol, {pos_windows} window positif, n={n}).")

    # Gerbang konsistensi lolos → WAJIB lolos signifikansi statistik.
    if not cycle.oos_r:
        return "WEAK", (f"OOS +{exp:.3f}R & konsisten, TAPI signifikansi tak dapat dihitung "
                        f"(oos_r kosong) — perlakukan sebagai belum-terbukti.")
    rep = significance_report(cycle.oos_r, n_trials=cycle.trials)
    if not (rep["eff_n"] >= MIN_EFF_N and rep["significant"]):
        return "WEAK", (f"OOS +{exp:.3f}R & konsisten TAPI gagal signifikansi: eff_n={rep['eff_n']} "
                        f"(min {MIN_EFF_N}), p_adj={rep['p_adj']} atas {cycle.trials} trial — "
                        f"kemungkinan besar artefak multiple-testing.")

    # Signifikan → syarat terakhir: parameter harus STABIL antar-window.
    stab = _param_stability(cycle.per_symbol)
    if stab < MIN_PARAM_STABILITY:
        return "WEAK", (f"OOS +{exp:.3f}R & signifikan TAPI parameter tidak stabil "
                        f"(stabilitas {stab:.0%} < {MIN_PARAM_STABILITY:.0%}) — edge loncat-loncat, "
                        f"ciri overfit; perlu sumber lebih kokoh.")
    return "CANDIDATE", (f"OOS +{exp:.3f}R, {pos_symbols}/3 simbol, eff_n={rep['eff_n']}, "
                         f"p_adj={rep['p_adj']} (trials={cycle.trials}), stabilitas {stab:.0%} — "
                         f"signifikan & stabil; uji lockbox/paper (BUKAN live).")


class StrategyCopilot:
    def __init__(self, settings: Settings, cfg: dict):
        gcfg = cfg.get("gemini", {})
        self.client = GeminiClient(settings.gemini_keys, gcfg.get("model", "gemini-2.5-flash"))
        self.enabled = settings.gemini_enabled and self.client.available

    def _ask(self, cycle: CycleResult, label: str, reason: str) -> dict | None:
        recs = registry.load()
        allowed = registry.untested_sources(recs)   # tag yang BELUM diuji = pilihan sah
        payload = {
            "strategy": cycle.strategy,
            "hypothesis": cycle.hypothesis,
            "deterministic_verdict": label,
            "verdict_reason": reason,
            "aggregate_oos": cycle.aggregate,
            "per_symbol_oos": {
                s: [{"oos_exp": round(w["oos_exp"], 3), "oos_n": w["oos_n"]} for w in wins]
                for s, wins in cycle.per_symbol.items()
            },
            "already_tested": registry.tested_summaries(recs),
            "allowed_next_source_tags": {s: registry.KNOWN_SOURCES[s] for s in allowed},
        }
        prompt = (
            "Kamu CO-PILOT riset strategi trading kuantitatif. Backtest walk-forward "
            "out-of-sample (OOS) adalah SATU-SATUNYA hakim; verdict deterministik sudah "
            "ditetapkan dan TIDAK boleh kamu ubah. Tugasmu: (1) tafsirkan KENAPA hasil "
            "begini (perhatikan divergensi antar-simbol & tanda overfit: IS bagus tapi OOS "
            "jelek, atau n_trades<30 per window), (2) usulkan SATU hipotesis berikutnya. "
            "WAJIB: 'next_source_tag' HARUS salah satu kunci di 'allowed_next_source_tags' "
            "(itu sumber yang BELUM diuji) — DILARANG memilih sumber di 'already_tested'. "
            "Sertakan rasional ekonomi & kondisi yang memfalsifikasinya. JANGAN pernah "
            "menyarankan live trading. Balas HANYA JSON: {\"interpretation\":\"...\","
            "\"overfit_risk\":\"low|med|high\",\"next_source_tag\":\"<salah satu allowed>\","
            "\"next_hypothesis\":\"...\",\"economic_rationale\":\"...\",\"falsifier\":\"...\"}\n"
            f"DATA:\n{json.dumps(payload, default=str)}"
        )
        text = self.client.generate(prompt, purpose="copilot")
        if not text:
            return None
        try:
            return json.loads(text[text.find("{"):text.rfind("}") + 1])
        except Exception as e:  # boundary
            log.warning(f"copilot parse gagal: {e}")
            return None

    def _fallback_next(self) -> tuple[str, str]:
        """Tanpa Gemini: ambil sumber belum-teruji pertama dari registry."""
        unt = registry.untested_sources()
        if not unt:
            return "other", "Semua sumber terdaftar sudah diuji — definisikan tag/sumber baru."
        tag = unt[0]
        return tag, registry.KNOWN_SOURCES[tag]

    def advise(self, cycle: CycleResult) -> dict:
        """Kembalikan dict siap-cetak: verdict deterministik + narasi Gemini (bila ada).

        Catatan: pencatatan siklus ke registry dilakukan pemanggil (optimize.main)
        SEBELUM advise(), sehingga 'already_tested' sudah memuat siklus saat ini →
        Gemini tak mungkin mengusulkan ulang sumber yang baru saja diuji."""
        label, reason = verdict(cycle)
        out = {"verdict": label, "verdict_reason": reason, "source": "deterministic"}

        if not self.enabled:
            tag, desc = self._fallback_next()
            out["interpretation"] = (
                "Gemini co-pilot non-aktif (set GEMINI_ENABLED=true & GEMINI_API_KEYS). "
                "Interpretasi deterministik: " + reason)
            out["next_source_tag"] = tag
            out["next_hypothesis"] = desc
            if label != "CANDIDATE":
                out["live_trading"] = "DILARANG — OOS belum lolos ambang (deterministik)."
            return out

        ai = self._ask(cycle, label, reason)
        if ai is None:
            tag, desc = self._fallback_next()
            out["interpretation"] = "Gemini gagal merespons; pakai verdict deterministik."
            out["next_source_tag"] = tag
            out["next_hypothesis"] = desc
            if label != "CANDIDATE":
                out["live_trading"] = "DILARANG — OOS belum lolos ambang (deterministik)."
            return out

        out.update({
            "source": "gemini",
            "interpretation": ai.get("interpretation", ""),
            "overfit_risk": ai.get("overfit_risk", ""),
            "next_source_tag": ai.get("next_source_tag", ""),
            "next_hypothesis": ai.get("next_hypothesis", ""),
            "economic_rationale": ai.get("economic_rationale", ""),
            "falsifier": ai.get("falsifier", ""),
        })
        # VALIDASI DETERMINISTIK: tolak usul yang menyentuh sumber sudah-teruji.
        tag = out["next_source_tag"]
        if not tag or registry.is_duplicate(tag) or tag not in registry.KNOWN_SOURCES:
            new_tag, desc = self._fallback_next()
            out["dedup_warning"] = (
                f"Usul Gemini ('{tag or 'kosong'}') sudah-teruji/tidak valid → "
                f"diganti otomatis ke sumber belum-teruji: {new_tag}.")
            out["next_source_tag"] = new_tag
            out["next_hypothesis"] = desc
        # SAFETY NET: apa pun kata Gemini, verdict deterministik tetap menang.
        if label != "CANDIDATE":
            out["live_trading"] = "DILARANG — OOS belum lolos ambang (deterministik)."
        return out
