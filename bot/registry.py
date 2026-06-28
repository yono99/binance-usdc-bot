"""Registry hipotesis riset — SATU SUMBER KEBENARAN (anti bug Gemini salah-ulang).

Akar bug "Gemini mengusulkan ulang ide yang sudah diuji" = daftar manual yang
tertinggal. Solusi: registry persisten yang **otomatis** dicatat tiap walk-forward,
plus **source tag terkontrol** (enum, bukan NLP bebas) agar deteksi duplikat
deterministik — bukan tebak-tebakan kata.

Tiap entry: {id, source, name, oos_exp, verdict, n, updated}. `source` adalah tag
dari `KNOWN_SOURCES`; usul Gemini wajib memetakan ke salah satu tag → kalau tag-nya
sudah ada di registry, itu duplikat, ditolak otomatis.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "research_registry.json"

# Enum sumber sinyal terkontrol. Gemini memilih dari sini (plus "other").
KNOWN_SOURCES = {
    "trend_ohlcv": "Trend/momentum/struktur dari OHLCV (EMA/ADX/MACD/RSI/breakout)",
    "htf_regime_session": "Filter HTF + regime trend/mean-reversion + mask sesi",
    "funding_oi_filter": "Funding z-score & open-interest delta sebagai FILTER",
    "orderflow_cvd": "Order flow / CVD taker-imbalance & divergensi (filter)",
    "cross_exchange_basis": "Selisih harga antar-bursa (Binance vs Bybit), mean-reversion",
    "liquidation_cascade": "Deteksi cascade likuidasi (fade/continuation) dari jejak OHLCV",
    "funding_regime_primary": "Funding ekstrem sebagai sinyal PRIMER (fade positioning)",
    # backlog (belum diuji)
    "options_flow": "Proxy aliran opsi (Deribit DVOL / skew risk-reversal)",
    "onchain_flow": "Proxy aliran on-chain (inflow/outflow exchange)",
    "time_of_day_micro": "Mikrostruktur jam UTC dengan imbalance struktural",
    "other": "Sumber lain di luar daftar (perlu definisi tag baru)",
}

# Seed awal (v1-v7 yang sudah dijalankan). Dipakai bila file registry belum ada.
_SEED = [
    {"id": "v1", "source": "trend_ohlcv", "name": "trend OHLCV murni", "oos_exp": -0.206, "verdict": "REJECTED"},
    {"id": "v2", "source": "htf_regime_session", "name": "+HTF+regime+sesi", "oos_exp": -0.105, "verdict": "REJECTED"},
    {"id": "v3", "source": "funding_oi_filter", "name": "+funding+OI (filter)", "oos_exp": -0.017, "verdict": "REJECTED"},
    {"id": "v4", "source": "orderflow_cvd", "name": "+order flow/CVD (filter)", "oos_exp": -0.007, "verdict": "REJECTED"},
    {"id": "v5", "source": "cross_exchange_basis", "name": "cross-exchange basis Binance vs Bybit", "oos_exp": -0.123, "verdict": "REJECTED"},
    {"id": "v6", "source": "liquidation_cascade", "name": "liquidation cascade FADE (proxy OHLCV)", "oos_exp": -0.430, "verdict": "REJECTED"},
    {"id": "v7", "source": "funding_regime_primary", "name": "funding regime sinyal primer (fade)", "oos_exp": -0.116, "verdict": "REJECTED"},
]


def load() -> list[dict]:
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return [dict(e) for e in _SEED]


def _save(records: list[dict]) -> None:
    REGISTRY_PATH.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


def record(entry: dict) -> list[dict]:
    """Upsert berdasarkan id (mis. 'v6'). Dipanggil OTOMATIS tiap walk-forward selesai."""
    records = load()
    entry = {**entry, "updated": datetime.date.today().isoformat()}
    for i, r in enumerate(records):
        if r.get("id") == entry.get("id"):
            records[i] = {**r, **entry}
            break
    else:
        records.append(entry)
    _save(records)
    return records


def tested_sources(records: list[dict] | None = None) -> set[str]:
    """Tag sumber yang SUDAH diuji (apa pun verdict-nya)."""
    records = records if records is not None else load()
    return {r["source"] for r in records if r.get("source") and r["source"] != "other"}


def tested_summaries(records: list[dict] | None = None) -> list[str]:
    """Ringkasan human-readable untuk prompt Gemini."""
    records = records if records is not None else load()
    out = []
    for r in records:
        exp = r.get("oos_exp")
        exp_s = f"{exp:+.3f}R" if isinstance(exp, (int, float)) else "?"
        out.append(f"[{r.get('source','?')}] {r.get('id','')}: {r.get('name','')} — "
                   f"{r.get('verdict','?')} {exp_s}")
    return out


def untested_sources(records: list[dict] | None = None) -> list[str]:
    """Tag dari KNOWN_SOURCES yang belum pernah diuji (kandidat berikutnya)."""
    done = tested_sources(records)
    return [s for s in KNOWN_SOURCES if s not in done and s != "other"]


def is_duplicate(source_tag: str, records: list[dict] | None = None) -> bool:
    """True bila tag sumber sudah ada di registry (deterministik, bukan NLP)."""
    return source_tag in tested_sources(records)


def total_trials(records: list[dict] | None = None) -> int:
    """Jumlah trial kumulatif (Σ konfigurasi yang pernah diuji) untuk koreksi Bonferroni."""
    records = records if records is not None else load()
    return int(sum(int(r.get("trials", 0) or 0) for r in records))
