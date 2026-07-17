"""Pengaturan runtime yang bisa diubah dari UI web (disimpan di SQLite, key 'runtime').

UI menulis, bot membaca tiap siklus (hot-reload). Termasuk leverage, bet, teknik
(scalping/swing/auto = smart autopilot), dan target profit. Semua di-clamp & divalidasi.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from . import store

ROOT = Path(__file__).resolve().parent.parent
# file lama — hanya dipakai untuk migrasi sekali ke SQLite
LEGACY_STORE = ROOT / "logs" / "runtime.json"


def _to_decimal_str(value: float) -> str:
    """Konversi float ke string Decimal dengan presisi 8 digit (standar Binance)."""
    d = Decimal(str(value)).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
    # Avoid scientific notation for zero
    if d == 0:
        return "0"
    return format(d, 'f')


def fetch_live_balances() -> dict[str, str]:
    """Ambil saldo USDT & USDC dari Binance LIVE (mode=live).
    Return Decimal string untuk presisi penuh. Raise Exception jika gagal."""
    import os
    import ccxt
    from dotenv import load_dotenv
    from pathlib import Path
    
    ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(ROOT / ".env")
    
    key = os.getenv("BINANCE_LIVE_KEY", "").strip()
    secret = os.getenv("BINANCE_LIVE_SECRET", "").strip()
    if not key or not secret:
        raise ValueError("BINANCE_LIVE_KEY/SECRET tidak di-set di .env")
    client = ccxt.binanceusdm({
        "apiKey": key,
        "secret": secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    total = client.fetch_balance().get("total", {})
    usdt = Decimal(str(total.get("USDT") or 0))
    usdc = Decimal(str(total.get("USDC") or 0))
    return {"USDT": _to_decimal_str(float(usdt)), "USDC": _to_decimal_str(float(usdc))}

# Preset teknik → timeframe + parameter strategi v4.
PRESETS: dict[str, dict] = {
    "scalping": {"timeframe": "5m", "entry_confidence": 0.5, "sl_atr_mult": 1.0,
                 "tp_atr_mult": 1.5, "use_htf": False, "regime": True,
                 "use_funding": False, "use_oi": False, "use_of": True},
    "swing": {"timeframe": "1h", "entry_confidence": 0.6, "sl_atr_mult": 2.0,
              "tp_atr_mult": 4.0, "use_htf": True, "regime": True,
              "use_funding": True, "use_oi": False, "use_of": False},
    # smart autopilot: v4 penuh, regime auto trend/mean-reversion
    "auto": {"timeframe": "15m", "entry_confidence": 0.65, "sl_atr_mult": 1.75,
             "tp_atr_mult": 2.6, "use_htf": True, "regime": True,
             "use_funding": True, "use_oi": False, "use_of": True},
    # Gemini praktisi trader: ARAH dari Gemini, SL/TP tetap ATR-deterministik (param di sini).
    "gemini": {"timeframe": "15m", "entry_confidence": 0.65, "sl_atr_mult": 1.75,
               "tp_atr_mult": 2.6, "use_htf": True, "regime": True,
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
    # balance_usd DIHAPUS — sistem sekarang split per-wallet (balance_usdt + balance_usdc).
    # Migrasi inline di _from_dict tetap jalan untuk KV lama yang punya 'balance_usd'.
    balance_usdt: float = 12.0                  # saldo USDT (wallet USDT-M) — Tahap 0 split per-quote
    balance_usdc: float = 6.0                   # saldo USDC (wallet USDC-M) — Tahap 0 split per-quote
    # DEPRECATED setelah Tahap 0; hanya disimpan untuk back-compat KV lama. Field ini di-ignore
    # oleh code-path baru; baca dr kv lama → pecah jadi balance_usdt/balance_usdc (lihat
    # migrate_balance_split.py). dry_quote_split_usdc dihapus (porsi eksplisit via split di atas).
    dry_quote_split_usdc: float = -1.0          # DEPRECATED setelah Tahap 0 — di-ignore code-path
    #                                             baru. Hanya dipakai migrasi KV lama (lihat
    #                                             migrate_balance_split.py) untuk pecah ke
    #                                             balance_usdt/balance_usdc.
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
    gemini_decide_seconds: int = 180           # throttle keputusan Gemini per simbol (teknik gemini) — 180s = 3 menit
    gemini_manage_seconds: int = 30             # throttle kelola posisi Gemini (exit-only) — turun dr 60 utk responsif
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
    gemini_keys: list[str] = field(default_factory=list)
    gemini_enabled: bool = False
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
    conf_min: float = 0.30                       # < ini → ABSTAIN (tak buka posisi)
    conf_reduced_mult: float = 0.5               # di antaranya → pengali ukuran
    # --- Phase 6: pemantau drift kalibrasi (ALARM saja, TANPA auto-ubah threshold) ---
    calib_drift_margin: float = 0.05             # Brier terkini − baseline 14h > ini → drift
    calib_drift_min_n: int = 20                  # min sampel trade terkini sebelum menilai

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_dry(self) -> bool:
        return self.mode in ("dry", "test")

    def credentials(self) -> tuple[str, str]:
        """Bridge compat dengan bot.exchange.Exchange (butuh .credentials()).

        BACAAN .env langsung: 'live' butuh key/secret nyata; dry/test = paper.
        Catatan: kelas ini tidak menyimpan key — dibaca on-demand supaya restart
        tak menyimpan secret di memori. Aman walau dipanggil tiap tick.
        """
        import os
        if self.mode == "live":
            return os.getenv("BINANCE_LIVE_KEY", ""), os.getenv("BINANCE_LIVE_SECRET", "")
        return "", ""

    def clamp(self) -> "RuntimeSettings":
        self.technique = self.technique if self.technique in PRESETS else "auto"
        self.leverage = int(max(1, min(125, self.leverage)))
        self.bet_usd = max(0.01, float(self.bet_usd))
        self.bet_pct = max(0.0, min(100.0, float(self.bet_pct)))   # 0 = pakai bet_usd tetap
        # Tahap 0 (plan-sess): split saldo per-wallet (USDT + USDC).
        self.balance_usdt = max(0.0, float(self.balance_usdt))
        self.balance_usdc = max(0.0, float(self.balance_usdc))
        # dry_quote_split_usdc DEPRECATED: di-clamp nilai waras tapi TAK dipakai code baru.
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
    # Tahap 0 (plan-sess): migrasi inline settings KV — bila KV lama hanya punya balance_usd
    # (tanpa balance_usdt/balance_usdc), pecah ke dua wallet sama rata. Idempoten — pemanggil
    # boleh menyimpan hasilnya kembali untuk KV jadi baru.
    if ("balance_usdt" not in data and "balance_usdc" not in data
            and "balance_usd" in data):
        legacy = float(data.get("balance_usd") or 0.0)
        if legacy > 0:
            half = round(legacy / 2.0, 6)
            data = {**data, "balance_usdt": half, "balance_usdc": round(legacy - half, 6)}
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
    aktif (dipakai POST /api/settings agar form tak bisa memindah mode).
    
    LIVE MODE: balance_usdt & balance_usdc TIDAK bisa di-set manual — diambil
    otomatis dari Binance API (fetch_live_balances). Mencegah input manual salah."""
    s = s.clamp()
    eff = _eff_mode(s.mode)
    if eff == "live":
        # Di mode LIVE: ambil saldo real dari Binance, abaikan nilai dari form
        try:
            live_bal = fetch_live_balances()
            s.balance_usdt = float(live_bal["USDT"])
            s.balance_usdc = float(live_bal["USDC"])
        except Exception as e:
            # Jika gagal fetch, JANGAN simpan nilai manual — log error & keep existing
            import logging
            logging.getLogger(__name__).error(f"LIVE: Gagal fetch saldo Binance: {e} — saldo TIDAK diupdate")
            # Ambil nilai yang sudah tersimpan (jika ada)
            existing = store.get_kv("runtime:live")
            if existing:
                s.balance_usdt = float(existing.get("balance_usdt", s.balance_usdt))
                s.balance_usdc = float(existing.get("balance_usdc", s.balance_usdc))
    store.set_kv("runtime:" + eff, asdict(s))
    if set_active:
        set_active_mode(s.mode)


def reset_all_enabled() -> None:
    """Reset rs.enabled=False untuk SEMUA mode (dry/test/live) di SQLite.

    Dipanggil saat startup forwardtest.py & dashboard.py agar setiap kali aplikasi
    dijalankan, bot default dalam keadaan OFF — user wajib menyalakan ON secara
    sadar dari dashboard. Mencegah bot auto-aktif pakai state enabled=True dari
    sesi sebelumnya (risiko: paper/live jalan tanpa pengawasan setelah restart).
    Field lain (leverage, bet, symbols, dll) tak direset — hanya saklar ON/OFF.

    Skip jika env SKIP_ENABLED_RESET=1 (untuk PM2 production deployment).
    """
    import os
    if os.getenv("SKIP_ENABLED_RESET"):
        return
    for m in ("dry", "test", "live"):
        try:
            rs = load_settings(m)
            if rs.enabled:
                rs.enabled = False
                save_settings(rs, set_active=False)
        except Exception:
            pass
