"""Klien Gemini terpusat dengan SMART KEY ROTATION — port dari project elearning
(lib/gemini/key-pool.ts). Dipakai bersama regime layer & news veto.

Konsep:
- Lacak kesehatan tiap key LINTAS panggilan (module-level): cooldown, fails, last_used.
- ordered_keys(): key sehat dulu, diurut LRU (sebar beban); bila semua cooldown →
  yang paling cepat pulih dulu.
- mark_bad: 429/kuota → cooldown 60s; 403/key invalid → 5 menit.
- Fallback antar-model (FALLBACK_MODELS, sama dgn elearning) + retry beberapa
  putaran dengan backoff saat semua transien.
- Catat token tiap panggilan ke SQLite (gemini_usage) untuk pemantauan.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time

from .logger import log
from . import store

try:
    from google import genai
except Exception:  # SDK belum terpasang
    genai = None

# Daftar model + urutan fallback — URUTAN = KUOTA RPD (per-hari) TERLONGGAR dulu.
# Insiden 2026-07-06: 3.5-flash (RPD 20) & 2.5-flash (RPD 20-250) habis dalam
# hitungan menit di siklus sibuk → all_keys_dead() jatuh ke rules-based semalaman
# → rugi besar → drawdown lock. Kuota RPD nyata (free tier, Juli 2026):
#   3-flash-preview        RPD longgar (bukti empiris 3 Juli, preview 0 gagal)
#   3.1-flash-lite-preview RPD 500-1000
#   2.5-flash-lite         RPD 1000 (bukan preview, stabil)
#   2.5-flash              RPD 20-250 — ketat
#   3.5-flash              RPD 20 — PALING ketat, last-resort murni
FALLBACK_MODELS = [
    "gemini-3-flash-preview",        # utama — kuota free longgar
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.5-flash",              # last resort — RPD 20/hari saja
]

COOLDOWN_RATE = 60.0        # 429 RPM (per-menit) → istirahatkan 60 dtk (jendela bergulir)
COOLDOWN_AUTH = 5 * 60.0    # 403 / key invalid → 5 menit

# THROTTLE PER-KEY: jeda WAJIB antar-request UNTUK KEY YANG SAMA (batas RPM Google
# = per PROJECT, bukan per key → key dari project BERBEDA punya kuota terpisah dan
# boleh jalan paralel). Arsitektur 26-key: default 1.0s (RPM efektif ~N×10 = 260 dgn
# 26 key beda-project). Knob env: GEMINI_MIN_INTERVAL_S (set "6.5" utk single-key/tradisional,
# "0" utk paid tier RPM tinggi). Catatan: 1.0s mensyaratkan 26 key dari 26 project berbeda.
_MIN_INTERVAL = float(os.getenv("GEMINI_MIN_INTERVAL_S", "1.0"))
# Timeout HTTP per panggilan (ms) — batasi satu call yang hang agar tak membekukan siklus
# (loop entry kini bisa banyak call/siklus krn budget dinamis). Timeout → error transien →
# generate() merotasi key/model seperti error lain. Knob: GEMINI_TIMEOUT_S.
# MINIMUM 10s — Google SDK menolak deadline <10s dgn 400 INVALID_ARGUMENT (error permanen,
# BUKAN 429) yang tak ter-rotasi (kode pikir "request salah" → return None tanpa coba key lain).
_TIMEOUT_MS = int(max(float(os.getenv("GEMINI_TIMEOUT_S", "15")), 10.0) * 1000)
_throttle_lock = threading.Lock()
_last_call: dict[str, float] = {}      # per-key: ts panggilan terakhir


def _throttle(key: str) -> None:
    """Blok sampai ≥_MIN_INTERVAL detik berlalu sejak panggilan TERAKHIR key ini."""
    with _throttle_lock:
        wait = _MIN_INTERVAL - (time.time() - _last_call.get(key, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[key] = time.time()

# State per-key LINTAS panggilan & instance (module-level, seperti elearning `states`).
_states: dict[str, dict] = {}
# Cache client per key (seperti elearning `clientCache`) — JANGAN buat Client baru
# tiap panggilan: httpx-nya bisa ketutup ("Cannot send a request, client closed").
_clients: dict = {}


# Persist cooldown PANJANG (rate_day/auth) ke SQLite (tabel kv) → restart tak menghajar ulang
# key yang kuota hariannya sudah habis. Simpan HASH key (jangan taruh API key mentah di DB).
_KV_COOLDOWN = "gemini_key_cooldowns"
_PERSIST_MIN = 120.0                  # hanya persist cooldown > ini; RPM 60s tak relevan lintas-restart
_persisted: dict[str, float] = {}     # key_hash → cooldown_until (epoch)
_persist_loaded = False


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _model_key(key: str, model: str) -> str:
    return f"{_key_hash(key)}|{model}"       # rate_day per (key,model) — beda dari cooldown per-key


def _model_dead(key: str, model: str) -> bool:
    """True bila (key,model) ini kehabisan kuota HARIAN (RPD). Disimpan di _persisted dgn
    komposit-key (bare-hash utk cooldown per-key tetap terpisah), jadi sukses model fallback
    di key yg sama TAK menghapusnya (dulu _mark_ok mengulanginya → primary di-retry tiap call)."""
    _load_persisted()
    return _persisted.get(_model_key(key, model), 0.0) > time.time()


def _load_persisted() -> None:
    global _persist_loaded
    if _persist_loaded:
        return
    _persist_loaded = True
    try:
        d = store.get_kv(_KV_COOLDOWN) or {}
        now = time.time()
        _persisted.update({h: float(u) for h, u in d.items() if float(u) > now})
    except Exception:   # boundary — persistence tak boleh ganggu trading
        pass


def _save_persisted() -> None:
    try:
        now = time.time()
        alive = {h: u for h, u in _persisted.items() if u > now}
        _persisted.clear()
        _persisted.update(alive)
        store.set_kv(_KV_COOLDOWN, alive)
    except Exception:
        pass


def _st(key: str) -> dict:
    s = _states.get(key)
    if s is None:                     # state baru → warisi cooldown durable bila masih berlaku
        _load_persisted()
        s = {"cooldown_until": _persisted.get(_key_hash(key), 0.0), "fails": 0, "last_used": 0.0}
        _states[key] = s
    return s


# Circuit-breaker PER-KEY (arsitektur 26-key): 1 key gagal → cooldown key itu saja,
# 25 key lain tetap jalan. Dulu global (5 fail → kill ALL) → throughput jatuh ke 0 walau
# cuma 1 key bermasalah. Knob: BREAKER_FAILS_PER_KEY & BREAKER_COOLDOWN_PER_KEY.
# Catatan: _st(key) sudah punya field "fails" + "cooldown_until" → kita reuse sbg breaker
# key-level (fails ≥ ambang → cooldown jangka pendek). Lihat _breaker_record_for(key).
BREAKER_FAILS_PER_KEY = 8       # gagal beruntun per-key sebelum cooldown paksa (tahan noise)
BREAKER_COOLDOWN_PER_KEY = 30.0 # dtk cooldown breaker per-key (lebih singkat dr cooldown 429=60s)

# Model health tracking — sliding window sukses/gagal PER (KEY, MODEL). Dipakai untuk
# rotasi CERDAS saat 504/overload: prefer kombinasi (key,model) dengan success rate
# tertinggi dalam N panggilan terakhir (bukan urutan FALLBACK_MODELS tetap). Per-key:model
# agar 1 key yg overload di model A tak menurunkan skor model A di key lain.
_MODEL_HEALTH_WINDOW = 20
_model_health: dict[str, list[bool]] = {}   # _model_key(key,model) → [True=sukses, False=gagal]


def _record_model_health(key: str, model: str, success: bool) -> None:
    h = _model_health.setdefault(_model_key(key, model), [])
    h.append(success)
    if len(h) > _MODEL_HEALTH_WINDOW:
        h.pop(0)


def _model_health_score(key: str, model: str) -> float:
    """Skor kesehatan (key,model) 0-1 (success rate), dgn penalti sampel kecil.
    Skor netral 0.5 utk kombination baru → tidak lebih tinggi dari yg terbukti OK."""
    h = _model_health.get(_model_key(key, model), [])
    if not h:
        return 0.5
    rate = sum(h) / len(h)
    n_penalty = max(0, 1.0 - (_MODEL_HEALTH_WINDOW - len(h)) / _MODEL_HEALTH_WINDOW)
    return rate * (0.5 + 0.5 * n_penalty)


def _breaker_open(keys: list[str]) -> bool:
    """True bila SEMUA key sedang dalam cooldown breaker → tak ada key sehat → fail-open.
    (Arsitektur 26-key: breaker global hanya efektif bila SEMUA key mati, bukan 5 fail.)"""
    if not keys:
        return True
    now = time.time()
    return all(_st(k)["cooldown_until"] > now for k in keys)


def _breaker_record_for(key: str, ok: bool) -> None:
    """Catat hasil panggilan untuk key ini. Gagal beruntun ≥ ambang → cooldown key itu 30s."""
    s = _st(key)
    if ok:
        s["fails"] = 0
        return
    s["fails"] += 1
    if s["fails"] >= BREAKER_FAILS_PER_KEY:
        #Cooldown breaker (30s) HANYA bila belum ada cooldown lebih lama (mis. 429=60s/auth=5m).
        breaker_cd = time.time() + BREAKER_COOLDOWN_PER_KEY
        if breaker_cd > s["cooldown_until"]:
            s["cooldown_until"] = breaker_cd
        log.warning(f"Gemini key {_key_hash(key)[:8]} breaker: {s['fails']} fail beruntun "
                    f"→ cooldown {BREAKER_COOLDOWN_PER_KEY:.0f}s (key-level, 25 key lain tetap jalan).")
        s["fails"] = 0


def _get_client(key: str):
    c = _clients.get(key)
    if c is None:
        try:
            c = genai.Client(api_key=key, http_options={"timeout": _TIMEOUT_MS})
        except Exception:  # SDK lawas tak dukung http_options → tanpa timeout (fail-open)
            c = genai.Client(api_key=key)
        _clients[key] = c
    return c


def _secs_to_rpd_reset() -> float:
    """Detik hingga reset RPD (tengah malam Pacific ≈ 08:00 UTC; abaikan DST — ±1 jam)."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    reset = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if reset <= now:
        reset += _dt.timedelta(days=1)
    return (reset - now).total_seconds()


def _classify(err: Exception) -> str:
    """Gemini error → tindakan: rate | rate_day | auth | model | request | other."""
    msg = str(err).lower()
    status = getattr(err, "status_code", None) or getattr(err, "code", None)
    if "api key not valid" in msg or "api_key_invalid" in msg or status == 403 or "permission_denied" in msg:
        return "auth"
    if status == 429 or "quota" in msg or "exhausted" in msg or "resource_exhausted" in msg or "rate limit" in msg:
        # RPD (per-hari) vs RPM (per-menit) → cooldown beda. Google sebut "PerDay" di detail.
        if "perday" in msg.replace(" ", "").replace("_", "") or "per day" in msg or "daily" in msg:
            return "rate_day"
        return "rate"
    if status == 504:
        return "overload"                     # server sibuk: rotasi key+model cepat
    if status in (500, 502, 503, 404) or "overloaded" in msg or "unavailable" in msg or "not found" in msg:
        return "model"
    if status == 400 or "invalid argument" in msg:
        return "request"
    return "other"


def _ordered_keys(keys: list[str]) -> list[str]:
    now = time.time()
    healthy = [k for k in keys if _st(k)["cooldown_until"] <= now]
    if healthy:
        return sorted(healthy, key=lambda k: _st(k)["last_used"])     # LRU: sebar beban
    return sorted(keys, key=lambda k: _st(k)["cooldown_until"])       # semua cooldown → tercepat pulih


def _mark_ok(key: str) -> None:
    s = _st(key)
    s["fails"] = 0
    s["cooldown_until"] = 0.0
    s["last_used"] = time.time()
    h = _key_hash(key)
    if h in _persisted:              # key pulih → buang catatan cooldown durable yg basi
        _persisted.pop(h, None)
        _save_persisted()


def _mark_bad(key: str, kind: str, model: str | None = None) -> None:
    s = _st(key)
    s["fails"] += 1
    if kind == "rate_day" and model:
        # RPD habis = per (KEY, MODEL): model lain di key ini MASIH boleh jalan, dan sukses
        # model fallback TAK BOLEH menghapus tanda ini. Durable sampai reset harian.
        _persisted[_model_key(key, model)] = time.time() + _secs_to_rpd_reset()
        _save_persisted()
        return
    if kind in ("rate", "rate_day"):   # RPM (atau rate_day tanpa info model) → cooldown key singkat
        s["cooldown_until"] = time.time() + COOLDOWN_RATE
    elif kind == "auth":
        s["cooldown_until"] = time.time() + COOLDOWN_AUTH
    if s["cooldown_until"] - time.time() > _PERSIST_MIN:   # cooldown panjang (auth) → durable
        _persisted[_key_hash(key)] = s["cooldown_until"]
        _save_persisted()


def _next_available_s(keys: list[str]) -> float:
    now = time.time()
    if any(_st(k)["cooldown_until"] <= now for k in keys):
        return 0.0
    return max(0.0, min(_st(k)["cooldown_until"] for k in keys) - now)


def all_keys_dead(keys: list[str], model: str) -> bool:
    """True bila SEMUA key sudah kehabisan kuota RPD HARIAN untuk model ini.

    Dipakai forward.py untuk fallback ke rules-based trading saat tak ada key
    yang tersedia — mencegah bot diam total sampai reset harian.
    Hanya cek RPD (rate_day); cooldown RPM singkat (~60s) TIDAK dihitung dead
    karena key akan pulih dalam hitungan menit."""
    if not keys:
        return False
    _load_persisted()
    now = time.time()
    return all(_persisted.get(_model_key(k, model), 0.0) > now for k in keys)


class GeminiClient:
    def __init__(self, keys: list[str], model: str | list[str] = "", rounds: int = 2):
        self.keys = list(keys or [])
        self.rounds = rounds
        self.set_model(model)

    def set_model(self, model: str | list[str]) -> None:
        """Set model utama + fallback. Model utama dipilih user/config, sisanya FALLBACK."""
        if isinstance(model, list) and model:
            base = list(model)
        elif isinstance(model, str) and model:
            base = [model] + [m for m in FALLBACK_MODELS if m != model]
        else:
            base = list(FALLBACK_MODELS)
        seen: set = set()
        self.models = [m for m in base if not (m in seen or seen.add(m))]

    @property
    def available(self) -> bool:
        return genai is not None and bool(self.keys)

    def generate(self, prompt: str, purpose: str = "") -> str | None:
        """Teks respons, atau None bila semua key/model gagal (fail-open)."""
        if not self.available:
            return None
        if _breaker_open(self.keys):         # SEMUA key breaker-cooldown → jangan tembak (fail-open)
            return None
        now = time.time()                    # semua key masih cooling (429/auth/breaker) → jangan tembak
        if self.keys and not any(_st(k)["cooldown_until"] <= now for k in self.keys):
            return None                     # → pakai fallback deterministik siklus ini
        last_err = ""
        # Urutan model: primary pertama, lalu fallback diurutkan oleh kesehatan
        # (success rate) descending — prefer model yang sedang sehat di key ini.
        # Skor PER (key,model) — 1 key overload di model A tak menurunkan model A di key lain.
        def _rank_key_model(k: str, m: str) -> float:
            return _model_health_score(k, m)

        # Untuk pemilihan model fallback, pakai skor rata-rata cross-key > aggregat global
        primary = self.models[0]
        if len(self.models) > 1:
            fallbacks = sorted(
                self.models[1:],
                key=lambda m: sum(_rank_key_model(k, m) for k in self.keys) / max(1, len(self.keys)),
                reverse=True,
            )
            ordered_models = [primary] + fallbacks
        else:
            ordered_models = list(self.models)
        for rnd in range(self.rounds):
            any_transient = False
            for model in ordered_models:
                model_down = False
                # Urut key berdasarkan kesehatan model ini di key tersebut (healthiest first).
                healthy_keys = [k for k in _ordered_keys(self.keys)
                                if not _model_dead(k, model)]
                healthy_keys.sort(key=lambda k: _rank_key_model(k, model), reverse=True)
                for key in healthy_keys:
                    ki = self.keys.index(key)
                    try:
                        _throttle(key)       # jeda WAJIB per-key → hormati RPM (per-project)
                        resp = _get_client(key).models.generate_content(model=model, contents=prompt)
                        txt = (resp.text or "").strip()
                        if not txt:
                            last_err = "empty response"
                            any_transient = True
                            continue
                        _mark_ok(key)
                        u = getattr(resp, "usage_metadata", None)
                        pt = int(getattr(u, "prompt_token_count", 0) or 0)
                        ot = int(getattr(u, "candidates_token_count", 0) or 0)
                        tt = int(getattr(u, "total_token_count", 0) or (pt + ot))
                        store.log_gemini_usage(model, purpose, ki, pt, ot, tt, ok=True)
                        _record_model_health(key, model, True)   # catat sukses utk ranking per-key:model
                        _breaker_record_for(key, True)           # sukses → reset fail counter key
                        return txt
                    except Exception as e:  # boundary
                        last_err = str(e)
                        if "client has been closed" in last_err.lower() or "client is closed" in last_err.lower():
                            _clients.pop(key, None)         # buat ulang client siklus berikutnya
                            any_transient = True
                            continue
                        kind = _classify(e)
                        if kind == "request":   # 400 = prompt salah, percuma rotasi
                            store.log_gemini_usage(model, purpose, ki, 0, 0, 0, ok=False, error=last_err[:160])
                            log.warning(f"Gemini {purpose} request invalid: {last_err[:120]}")
                            _breaker_record_for(key, False)
                            return None
                        if kind in ("rate", "rate_day", "auth"):
                            _mark_bad(key, kind, model)
                            _breaker_record_for(key, False)   # 429/auth juga hitung ke breaker key
                            store.log_gemini_usage(model, purpose, ki, 0, 0, 0, ok=False, error=f"{kind}: {last_err[:140]}")
                            any_transient = True
                            continue
                        if kind == "overload":  # 504: server sibuk → cooldown key + ganti model
                            _mark_bad(key, "rate")   # istirahatkan key 60s, coba key lain
                            _record_model_health(key, model, False)  # 504 = model sibuk di key ini
                            model_down = True
                            any_transient = True
                            break
                        if kind == "model":     # model down/unavailable → coba model lain
                            _record_model_health(key, model, False)  # model down di key ini
                            model_down = True
                            any_transient = True
                            break
                        _breaker_record_for(key, False)  # other transien → tetap catat ke breaker key
                        any_transient = True
                if model_down:
                    continue
            if rnd < self.rounds - 1 and any_transient:
                # backoff EKSPONENSIAL (2→4→8…, cap 30s) atau tunggu key pulih, mana lebih lama
                time.sleep(min(max(_next_available_s(self.keys), 2.0 * 2 ** rnd), 30.0))
            else:
                break
        log.warning(f"Gemini {purpose} gagal semua key/model: {last_err[:160]}")
        return None