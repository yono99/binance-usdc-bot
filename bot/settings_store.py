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
}

MAINT_MARGIN = 0.005  # asumsi maintenance margin ~0.5%


@dataclass
class RuntimeSettings:
    enabled: bool = False                       # bot aktif buka posisi?
    technique: str = "auto"                     # scalping | swing | auto
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDC:USDC"])
    leverage: int = 100                         # default 100x (paper) — likuidasi pada gerakan ~0.5%
    bet_usd: float = 12.0                       # margin per posisi
    balance_usd: float = 12.0                   # saldo akun (paper)
    target_profit_pct: float = 0.0              # 0 = pakai TP dari ATR; >0 = TP = entry×(1+ini%)
    max_open_positions: int = 2                 # slot posisi paralel maksimum
    poll_seconds: int = 30                      # interval screening/siklus (detik)

    def clamp(self) -> "RuntimeSettings":
        self.technique = self.technique if self.technique in PRESETS else "auto"
        self.leverage = int(max(1, min(125, self.leverage)))
        self.bet_usd = max(0.1, float(self.bet_usd))
        self.balance_usd = max(0.0, float(self.balance_usd))
        self.target_profit_pct = max(0.0, float(self.target_profit_pct))
        self.max_open_positions = int(max(1, min(20, self.max_open_positions)))
        self.poll_seconds = int(max(5, min(3600, self.poll_seconds)))
        if not self.symbols:
            self.symbols = ["BTC/USDC:USDC"]
        return self

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


def _from_dict(data: dict) -> RuntimeSettings:
    known = {f for f in RuntimeSettings().__dict__}
    return RuntimeSettings(**{k: v for k, v in data.items() if k in known}).clamp()


def load_settings() -> RuntimeSettings:
    try:
        data = store.get_kv("runtime")
        if data is None:
            # migrasi sekali dari runtime.json lama bila ada
            if LEGACY_STORE.exists():
                data = json.loads(LEGACY_STORE.read_text(encoding="utf-8"))
                store.set_kv("runtime", asdict(_from_dict(data)))
            else:
                return RuntimeSettings()
        return _from_dict(data)
    except Exception:
        return RuntimeSettings()


def save_settings(s: RuntimeSettings) -> None:
    store.set_kv("runtime", asdict(s.clamp()))
