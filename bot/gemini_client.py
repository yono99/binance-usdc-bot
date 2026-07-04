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

import os
import threading
import time

from .logger import log
from . import store

try:
    from google import genai
except Exception:  # SDK belum terpasang
    genai = None

# Daftar model + urutan fallback — DISAMAKAN dengan elearning (lib/gemini/client.ts).
# Model 3.x dicoba dulu; bila tak tersedia utk key → error 'model' → fallback ke 2.5.
FALLBACK_MODELS = [
    "gemini-2.5-flash",              # utama
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-lite",         # last resort
]

COOLDOWN_RATE = 60.0        # 429 / kuota habis → istirahatkan 60 dtk
COOLDOWN_AUTH = 5 * 60.0    # 403 / key invalid → 5 menit

# THROTTLE global: jeda WAJIB antar-request ke Gemini (batas RPM, bukan token).
# Free tier ~10 RPM → butuh ≥6 dtk antar panggilan. Module-level → SEMUA layer
# (trader/news/planner/devil) berbagi antrean → tak pernah tembak >1 req per interval.
# Knob: set env GEMINI_MIN_INTERVAL_S (mis. "0" utk paid tier RPM tinggi).
_MIN_INTERVAL = float(os.getenv("GEMINI_MIN_INTERVAL_S", "6.5"))
_throttle_lock = threading.Lock()
_last_call = {"ts": 0.0}


def _throttle() -> None:
    """Blok sampai ≥_MIN_INTERVAL detik berlalu sejak panggilan Gemini terakhir."""
    with _throttle_lock:
        wait = _MIN_INTERVAL - (time.time() - _last_call["ts"])
        if wait > 0:
            time.sleep(wait)
        _last_call["ts"] = time.time()

# State per-key LINTAS panggilan & instance (module-level, seperti elearning `states`).
_states: dict[str, dict] = {}
# Cache client per key (seperti elearning `clientCache`) — JANGAN buat Client baru
# tiap panggilan: httpx-nya bisa ketutup ("Cannot send a request, client closed").
_clients: dict = {}


def _st(key: str) -> dict:
    return _states.setdefault(key, {"cooldown_until": 0.0, "fails": 0, "last_used": 0.0})


# Circuit-breaker GLOBAL: hentikan panggil Gemini saat gagal beruntun (anti-spiral 429).
# Module-level → SEMUA layer (news/react/planner/trader/dst) ikut mundur bersama.
_breaker = {"fails": 0, "open_until": 0.0}
BREAKER_FAILS = 5          # kegagalan penuh beruntun sebelum breaker TERBUKA
BREAKER_COOLDOWN = 60.0    # detik breaker terbuka (skip semua panggilan → fallback deterministik)


def _breaker_open() -> bool:
    return time.time() < _breaker["open_until"]


def _breaker_record(ok: bool) -> None:
    if ok:
        _breaker["fails"] = 0
        return
    _breaker["fails"] += 1
    if _breaker["fails"] >= BREAKER_FAILS:
        _breaker["open_until"] = time.time() + BREAKER_COOLDOWN
        _breaker["fails"] = 0
        log.warning(f"Gemini circuit-breaker TERBUKA {BREAKER_COOLDOWN:.0f}s — "
                    "hentikan panggilan (anti-spiral 429); pakai fallback deterministik.")


def _get_client(key: str):
    c = _clients.get(key)
    if c is None:
        c = genai.Client(api_key=key)
        _clients[key] = c
    return c


def _classify(err: Exception) -> str:
    """Gemini error → jenis tindakan: rate | auth | model | request | other."""
    msg = str(err).lower()
    status = getattr(err, "status_code", None) or getattr(err, "code", None)
    if "api key not valid" in msg or "api_key_invalid" in msg or status == 403 or "permission_denied" in msg:
        return "auth"
    if status == 429 or "quota" in msg or "exhausted" in msg or "resource_exhausted" in msg or "rate limit" in msg:
        return "rate"
    if status in (500, 502, 503, 504, 404) or "overloaded" in msg or "unavailable" in msg or "not found" in msg:
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


def _mark_bad(key: str, kind: str) -> None:
    s = _st(key)
    s["fails"] += 1
    if kind == "rate":
        s["cooldown_until"] = time.time() + COOLDOWN_RATE
    elif kind == "auth":
        s["cooldown_until"] = time.time() + COOLDOWN_AUTH


def _next_available_s(keys: list[str]) -> float:
    now = time.time()
    if any(_st(k)["cooldown_until"] <= now for k in keys):
        return 0.0
    return max(0.0, min(_st(k)["cooldown_until"] for k in keys) - now)


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
        if _breaker_open():                 # breaker terbuka → jangan panggil (putus spiral 429)
            return None
        now = time.time()                   # semua key masih cooling (429) → jangan tembak
        if self.keys and not any(_st(k)["cooldown_until"] <= now for k in self.keys):
            return None                     # → pakai fallback deterministik siklus ini
        last_err = ""
        for rnd in range(self.rounds):
            any_transient = False
            for model in self.models:
                model_down = False
                for key in _ordered_keys(self.keys):
                    ki = self.keys.index(key)
                    try:
                        _throttle()          # jeda WAJIB antar-request → hormati batas RPM
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
                        _breaker_record(True)          # sukses → reset breaker
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
                            return None
                        if kind in ("rate", "auth"):
                            _mark_bad(key, kind)
                            store.log_gemini_usage(model, purpose, ki, 0, 0, 0, ok=False, error=f"{kind}: {last_err[:140]}")
                            any_transient = True
                            continue
                        if kind == "model":     # model down/unavailable → coba model lain
                            model_down = True
                            any_transient = True
                            break
                        any_transient = True
                if model_down:
                    continue
            if rnd < self.rounds - 1 and any_transient:
                # backoff EKSPONENSIAL (2→4→8…, cap 30s) atau tunggu key pulih, mana lebih lama
                time.sleep(min(max(_next_available_s(self.keys), 2.0 * 2 ** rnd), 30.0))
            else:
                break
        log.warning(f"Gemini {purpose} gagal semua key/model: {last_err[:160]}")
        _breaker_record(False)             # gagal penuh → dekati/buka breaker (anti-spiral)
        return None
