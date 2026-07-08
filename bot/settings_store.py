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
    enabled: bool = False                       # bot aktif buka posisi? (default OFF — user yang menyalakan per mode)
    technique: str = "auto"                     # scalping | swing | auto
    symbols: list[str] = field(default_factory=lambda: ["BTC/USDC:USDC"])
    leverage: int = 100                         # default 100x (paper) — likuidasi pada gerakan ~0.5%
    bet_usd: float = 12.0                       # margin per posisi (dipakai bila bet_pct=0)
    bet_pct: float = 0.0                         # ADAPTIF: >0 → margin = %saldo (auto-scale $10→naik)
    balance_usd: float = 12.0                   # saldo akun (paper)
    dry_quote_split_usdc: float = -1.0          # DRY: porsi saldo kertas utk pool USDC (0..1);
    #                                             sisanya USDT. -1 = auto (proporsi jumlah pair USDC
    #                                             di universe). LIVE abaikan ini — pool dari saldo asli.
    target_profit_pct: float = 0.0              # 0 = pakai TP dari ATR; >0 = TP = entry×(1+ini%)
    max_open_positions: int = 2                 # slot posisi paralel maksimum
    daily_max_loss_pct: float = 3.0             # circuit breaker: stop buka posisi bila rugi harian ≥ % saldo awal hari (0 = nonaktif)
    daily_max_trades: int = 20                  # circuit breaker: stop bila jumlah trade hari ini tercapai (0 = nonaktif)
    corr_threshold: float = 0.85                 # guard korelasi: blok entry SEARAH bila korelasi return ≥ ini (0 = nonaktif)
    corr_lookback: int = 50                      # bar untuk hitung korelasi (<20 = nonaktif)
    max_drawdown_pct: float = 20.0               # KILL-SWITCH drawdown TOTAL dari puncak saldo (0 = nonaktif).
                                                 # Beda dgn daily_max_loss: ini KUMULATIF & tak reset harian —
                                                 # kebocoran pelan berhari-hari tetap tertangkap. Lepas kunci
                                                 # HANYA manual (POST /api/dd-reset) — keputusan sadar pemilik.
    poll_seconds: int = 60                      # heartbeat bot (baca setting+monitor+status). Sinyal dievaluasi per bar TF.
    # --- Penyetelan Gemini (atur frekuensi panggilan → hemat RPM/token), semua di UI ---
    gemini_decide_seconds: int = 180            # throttle keputusan Gemini per simbol (teknik gemini)
    gemini_manage_seconds: int = 60             # throttle kelola posisi Gemini (exit-only)
    gemini_min_hold_s: int = 300                # GRACE: manajer Gemini tak boleh exit sebelum posisi
    #                                             ditahan ≥ ini (anti-whipsaw "baru entry langsung close").
    #                                             SL/TP TETAP jaga selama grace. 0 = nonaktif.
    gemini_portfolio_seconds: int = 300         # throttle review portofolio (autonomous)
    gemini_plan_hours: int = 6                  # interval planner (tujuan sesi)
    gemini_tool_iters: int = 4                  # maks langkah tool-loop per keputusan
    order_type: str = "limit"                   # default maker (limit) | market (taker)
    taker_fee_pct: float = 0.05                 # fee taker USDT-M (%) — market order (VIP0)
    maker_fee_pct: float = 0.02                 # fee maker USDT-M (%) — limit order (VIP0)
    usdc_maker_fee_pct: float = 0.0             # fee maker USDC-M (%) — promo Binance = 0%
    usdc_taker_fee_pct: float = 0.04            # fee taker USDC-M (%) — promo diskon (bukan 0%)
    gemini_model: str = ""                      # model Gemini (kosong = default config.yaml)
    mode: str = ""                              # kosong = ikut .env | dry | test | live (UANG NYATA)
    # --- Agent otonom (toggle dari UI; OR dengan config.yaml; hot-reload) ---
    agent_full_auto: bool = False               # satu saklar: tool_loop + autonomous + planner
    agent_tool_loop: bool = False               # nalar + panggil tool iteratif
    agent_autonomous: bool = False              # kelola portofolio (REDUCE_RISK/FLAT)
    agent_planner: bool = False                 # tujuan sesi (stance/bias/kuota)
    agent_ab_shadow: bool = False               # A/B: ReAct catat verdict tanpa memblokir
    agent_manager_mode: bool = False             # JALAN A: agent = manajer disiplin (rules arah,
    #                                              planner+autonomous ON, tool_loop OFF, no gemini-arah)
    news_veto: bool = True                       # veto entry saat berita high-impact (toggle UI)
    # --- Gerbang SIZE berbasis confidence (kalibrasi, Phase 2): tier hot-reload ---
    conf_full: float = 0.75                      # ≥ ini → ukuran penuh
    conf_min: float = 0.55                       # < ini → ABSTAIN (tak buka posisi)
    conf_reduced_mult: float = 0.5               # di antaranya → pengali ukuran
    # --- Phase 6: pemantau drift kalibrasi (ALARM saja, TANPA auto-ubah threshold) ---
    calib_drift_margin: float = 0.05             # Brier terkini − baseline 14h > ini → drift
    calib_drift_min_n: int = 20                  # min sampel trade terkini sebelum menilai

    def clamp(self) -> "RuntimeSettings":
        self.technique = self.technique if self.technique in PRESETS else "auto"
        self.leverage = int(max(1, min(125, self.leverage)))
        self.bet_usd = max(0.01, float(self.bet_usd))
        self.bet_pct = max(0.0, min(100.0, float(self.bet_pct)))   # 0 = pakai bet_usd tetap
        self.balance_usd = max(0.0, float(self.balance_usd))
        self.target_profit_pct = max(0.0, min(100.0, float(self.target_profit_pct)))   # >100% gerak harga = tak masuk akal
        self.max_open_positions = int(max(1, min(20, self.max_open_positions)))
        self.daily_max_loss_pct = max(0.0, min(100.0, float(self.daily_max_loss_pct)))   # 0 = nonaktif; >100% saldo tak masuk akal
        self.daily_max_trades = int(max(0, min(1000, self.daily_max_trades)))            # 0 = nonaktif
        self.corr_threshold = max(0.0, min(1.0, float(self.corr_threshold)))             # korelasi ∈ [0,1]; 0 = nonaktif
        self.corr_lookback = int(max(0, min(500, self.corr_lookback)))                   # <20 = nonaktif
        self.max_drawdown_pct = max(0.0, min(90.0, float(self.max_drawdown_pct)))        # 0 = nonaktif
        self.poll_seconds = int(max(5, min(3600, self.poll_seconds)))
        self.gemini_decide_seconds = int(max(30, min(3600, self.gemini_decide_seconds)))
        self.gemini_manage_seconds = int(max(30, min(3600, self.gemini_manage_seconds)))
        self.gemini_min_hold_s = int(max(0, min(86400, self.gemini_min_hold_s)))
        self.gemini_portfolio_seconds = int(max(60, min(3600, self.gemini_portfolio_seconds)))
        self.gemini_plan_hours = int(max(1, min(24, self.gemini_plan_hours)))
        self.gemini_tool_iters = int(max(1, min(8, self.gemini_tool_iters)))
        self.order_type = self.order_type if self.order_type in ("market", "limit") else "market"
        self.taker_fee_pct = max(0.0, float(self.taker_fee_pct))
        self.maker_fee_pct = max(0.0, float(self.maker_fee_pct))
        self.usdc_maker_fee_pct = max(0.0, float(self.usdc_maker_fee_pct))
        self.usdc_taker_fee_pct = max(0.0, float(self.usdc_taker_fee_pct))
        self.gemini_model = str(self.gemini_model or "").strip()
        self.mode = self.mode if self.mode in ("", "dry", "test", "live") else ""
        for f in ("agent_full_auto", "agent_tool_loop", "agent_autonomous", "agent_planner",
                  "agent_ab_shadow", "agent_manager_mode", "news_veto"):
            setattr(self, f, bool(getattr(self, f)))
        self.conf_full = max(0.0, min(1.0, float(self.conf_full)))
        self.conf_min = max(0.0, min(self.conf_full, float(self.conf_min)))  # min ≤ full
        self.conf_reduced_mult = max(0.0, min(1.0, float(self.conf_reduced_mult)))
        self.calib_drift_margin = max(0.0, min(1.0, float(self.calib_drift_margin)))
        self.calib_drift_min_n = int(max(1, min(1000, self.calib_drift_min_n)))
        # symbols kosong = "screening SEMUA pair USDC" (di-resolve oleh bot)
        return self

    def fee_pct(self) -> float:
        """Fee per sisi sesuai jenis order: maker (limit) vs taker (market). USDT-M (backtest/fallback)."""
        return self.maker_fee_pct if self.order_type == "limit" else self.taker_fee_pct

    def fee_rate(self, settle: str, is_maker: bool) -> float:
        """Fee per-sisi per-settle (%). USDC-M dapat promo Binance (maker 0%, taker 0.04%);
        USDT-M standar VIP0 (0.02/0.05). Entry maker bila order_type='limit'; exit SL/TP/market
        SELALU taker. Sumber: promo USDC-Margined Futures Binance (aktif s.d. further notice)."""
        if str(settle).upper() == "USDC":
            return self.usdc_maker_fee_pct if is_maker else self.usdc_taker_fee_pct
        return self.maker_fee_pct if is_maker else self.taker_fee_pct

    def preset(self) -> dict:
        return PRESETS[self.technique]

    def timeframe(self) -> str:
        return self.preset()["timeframe"]

    def params(self) -> dict:
        p = self.preset()
        return {k: p[k] for k in ("entry_confidence", "sl_atr_mult", "tp_atr_mult",
                                  "use_htf", "regime", "use_funding", "use_oi", "use_of")}

    def downgrade_conf(self, confidence: float) -> float:
        """Turunkan confidence SATU tier (dipakai saat Devil's Advocate kuat menentang,
        Phase 4). full→reduced, reduced→abstain, abstain tetap. Deterministik."""
        if confidence >= self.conf_full:
            return self.conf_min                 # full → tepat di ambang reduced
        if confidence >= self.conf_min:
            return 0.0                           # reduced → di bawah conf_min = abstain
        return confidence                        # sudah abstain

    def conf_size_mult(self, confidence: float | None) -> float | None:
        """Tier gerbang SIZE (Phase 2 kalibrasi). None (jalur rule-based, tanpa angka
        confidence) → 1.0 penuh, TIDAK digerbang — terdokumentasi sebagai pilihan sadar.
        Return None = ABSTAIN (jangan buka posisi)."""
        if confidence is None:
            return 1.0
        if confidence < self.conf_min:
            return None
        return 1.0 if confidence >= self.conf_full else self.conf_reduced_mult

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


def save_settings(s: RuntimeSettings, set_active: bool = True) -> None:
    """Simpan ke bucket mode-nya sendiri. set_active=False → JANGAN sentuh mode
    aktif (dipakai POST /api/settings agar form tak bisa memindah mode)."""
    s = s.clamp()
    store.set_kv("runtime:" + _eff_mode(s.mode), asdict(s))
    if set_active:
        set_active_mode(s.mode)
