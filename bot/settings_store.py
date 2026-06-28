"""Pengaturan runtime yang bisa diubah dari UI web (disimpan di SQLite, key 'runtime').

UI menulis, bot membaca tiap siklus (hot-reload). Termasuk leverage, bet, teknik
(scalping/swing/auto = smart autopilot), dan target profit. Semua di-clamp & divalidasi.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import store

ROOT = Path(__file__).resolve().parent.parent
# file lama — hanya dipakai untuk migrasi sekali ke SQLite
LEGACY_STORE = ROOT / "logs" / "runtime.json"

# Preset teknik → timeframe + parameter strategi v4.
PRESETS: dict[str, dict] = {
    "scalping": {"timeframe": "5m", "entry_confidence": 0.5, "sl_atr_mult": 1.0,
                 "tp_atr_mult": 1.5, "use_htf": False, "regime": True,
                 "use_funding": False, "use_oi": False, "use_of": True},
    "swing": {"timeframe": "1h", "entry_confidence": 0.6, "sl_atr_mult": 2.0,
              "tp_atr_mult": 4.0, "use_htf": True, "regime": True,
              "use_funding": True, "use_oi": False, "use_of": False},
    # smart autopilot: v4 penuh, regime auto trend/mean-reversion
    "auto": {"timeframe": "15m", "entry_confidence": 0.5, "sl_atr_mult": 1.5,
             "tp_atr_mult": 2.5, "use_htf": True, "regime": True,
             "use_funding": True, "use_oi": False, "use_of": True},
    # Gemini praktisi trader: ARAH dari Gemini, SL/TP tetap ATR-deterministik (param di sini).
    "gemini": {"timeframe": "15m", "entry_confidence": 0.5, "sl_atr_mult": 1.5,
               "tp_atr_mult": 2.5, "use_htf": True, "regime": True,
               "use_funding": True, "use_oi": False, "use_of": True},
}

MAINT_MARGIN = 0.005  # asumsi maintenance margin ~0.5%


@dataclass
class RuntimeSettings:
    enabled: bool = True                        # bot aktif buka posisi? (default ON)
    technique: str = "auto"                     # scalping | swing | auto
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDC:USDC"])
    leverage: int = 100                         # default 100x (paper) — likuidasi pada gerakan ~0.5%
    bet_usd: float = 12.0                       # margin per posisi
    balance_usd: float = 12.0                   # saldo akun (paper)
    target_profit_pct: float = 0.0              # 0 = pakai TP dari ATR; >0 = TP = entry×(1+ini%)
    max_open_positions: int = 2                 # slot posisi paralel maksimum
    poll_seconds: int = 60                      # heartbeat bot (baca setting+monitor+status). Sinyal dievaluasi per bar TF.
    order_type: str = "limit"                   # default maker (limit) | market (taker)
    taker_fee_pct: float = 0.05                 # fee taker (%) — market order
    maker_fee_pct: float = 0.02                 # fee maker (%) — limit order
    gemini_model: str = ""                      # model Gemini (kosong = default config.yaml)
    mode: str = ""                              # kosong = ikut .env | dry | test | live (UANG NYATA)

    def clamp(self) -> "RuntimeSettings":
        self.technique = self.technique if self.technique in PRESETS else "auto"
        self.leverage = int(max(1, min(125, self.leverage)))
        self.bet_usd = max(0.01, float(self.bet_usd))
        self.balance_usd = max(0.0, float(self.balance_usd))
        self.target_profit_pct = max(0.0, min(100.0, float(self.target_profit_pct)))   # >100% gerak harga = tak masuk akal
        self.max_open_positions = int(max(1, min(20, self.max_open_positions)))
        self.poll_seconds = int(max(5, min(3600, self.poll_seconds)))
        self.order_type = self.order_type if self.order_type in ("market", "limit") else "market"
        self.taker_fee_pct = max(0.0, float(self.taker_fee_pct))
        self.maker_fee_pct = max(0.0, float(self.maker_fee_pct))
        self.gemini_model = str(self.gemini_model or "").strip()
        self.mode = self.mode if self.mode in ("", "dry", "test", "live") else ""
        # symbols kosong = "screening SEMUA pair USDC" (di-resolve oleh bot)
        return self

    def fee_pct(self) -> float:
        """Fee per sisi sesuai jenis order: maker (limit) vs taker (market)."""
        return self.maker_fee_pct if self.order_type == "limit" else self.taker_fee_pct

    def preset(self) -> dict:
        return PRESETS[self.technique]

    def timeframe(self) -> str:
        return self.preset()["timeframe"]

    def params(self) -> dict:
        p = self.preset()
        return {k: p[k] for k in ("entry_confidence", "sl_atr_mult", "tp_atr_mult",
                                  "use_htf", "regime", "use_funding", "use_oi", "use_of")}

    def liquidation_frac(self) -> float:
        """Fraksi gerakan harga melawan sampai likuidasi (isolated, perkiraan)."""
        return max(1.0 / self.leverage - MAINT_MARGIN, 0.0005)


def liquidation_price(entry: float, is_long: bool, frac: float) -> float:
    return entry * (1 - frac) if is_long else entry * (1 + frac)


def _from_dict(data: dict, mode: str | None = None) -> RuntimeSettings:
    known = {f for f in RuntimeSettings().__dict__}
    s = RuntimeSettings(**{k: v for k, v in data.items() if k in known}).clamp()
    if mode is not None:
        s.mode = mode
    return s


def _env_mode() -> str:
    import os
    return (os.getenv("MODE", "dry") or "dry").strip().lower()


def _eff_mode(requested: str) -> str:
    """Mode efektif: pilihan UI (requested) atau .env bila kosong."""
    return requested or _env_mode()


def get_active_mode() -> str:
    """Mode yang sedang dipilih dari UI ('' = ikut .env)."""
    return (store.get_kv("active_mode") or {}).get("mode", "")


def set_active_mode(mode: str) -> None:
    store.set_kv("active_mode", {"mode": mode})


def load_settings(mode: str | None = None) -> RuntimeSettings:
    """Pengaturan PER-MODE. mode=None → mode aktif (pilihan UI/.env).
    Tiap mode (dry/test/live) punya setting terpisah di kv 'runtime:<mode>'."""
    requested = get_active_mode() if mode is None else mode
    eff = _eff_mode(requested)
    try:
        data = store.get_kv("runtime:" + eff)
        if data is None:
            # migrasi sekali: dari kv 'runtime' lama (single), lalu runtime.json
            legacy = store.get_kv("runtime")
            if legacy is None and LEGACY_STORE.exists():
                legacy = json.loads(LEGACY_STORE.read_text(encoding="utf-8"))
            data = legacy or {}
        return _from_dict(data, mode=requested)
    except Exception:
        return _from_dict({}, mode=requested)


def save_settings(s: RuntimeSettings) -> None:
    """Simpan ke bucket mode-nya sendiri + set mode aktif."""
    s = s.clamp()
    store.set_kv("runtime:" + _eff_mode(s.mode), asdict(s))
    set_active_mode(s.mode)
