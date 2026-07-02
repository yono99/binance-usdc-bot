"""Rem-VRP (A/B shadow) — gap DVOL−RV30 sebagai REM regime, bukan sumber alpha.

Latar: H28 (alpha VRP) DITOLAK oleh 4 palang, TAPI gap DVOL−RV adalah indikator
regime stres yang masuk akal (pasar membayar mahal untuk proteksi). Sesuai
filosofi "LLM/regime = rem, bukan gas": dipakai hanya untuk MENAHAN entry saat
regime stres — dan nilainya DIUKUR dulu via shadow, bukan diasumsikan.

Mode (config.yaml `vrp.mode`):
- off     : tidak ada apa-apa.
- shadow  : TIDAK memblokir; setiap posisi distempel {vrp_brake, vrp_gap} saat
            open, dan saat close hasilnya dicatat ke logs/vrp_shadow.jsonl.
            `analyze_shadow` membandingkan R saat brake-on vs brake-off.
- enforce : blokir entry baru saat gap > threshold (naik kelas HANYA bila
            shadow membuktikan nilai: R brake-on lebih buruk, p<0.05).

Fail-open ketat: error fetch (Deribit/exchange) → gap None → brake off.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import numpy as np

from .logger import log

DVOL_URL = ("https://www.deribit.com/api/v2/public/get_volatility_index_data"
            "?currency=BTC&resolution=1D&start_timestamp={t0}&end_timestamp={t1}")
SHADOW_LOG = Path(__file__).resolve().parent.parent / "logs" / "vrp_shadow.jsonl"


def fetch_dvol_last() -> float | None:
    """Close DVOL harian terakhir dari REST publik Deribit. None bila gagal."""
    t1 = int(time.time() * 1000)
    t0 = t1 - 50 * 86400_000
    try:
        with urllib.request.urlopen(DVOL_URL.format(t0=t0, t1=t1), timeout=15) as resp:
            data = json.loads(resp.read())["result"]["data"]
        return float(data[-1][4]) if data else None
    except Exception as e:  # boundary — fail-open
        log.warning(f"VRP: DVOL gagal ({e}) — brake off")
        return None


def compute_gap(dvol_close: float, btc_daily_closes) -> float | None:
    """gap = IV tahunan (DVOL/100) − RV30 tahunan dari close harian BTC (≥31 titik)."""
    c = np.asarray(btc_daily_closes, dtype=float)
    if len(c) < 31 or not np.isfinite(dvol_close):
        return None
    r = c[1:] / c[:-1] - 1.0
    rv30 = float(np.std(r[-30:]) * np.sqrt(365))
    return dvol_close / 100.0 - rv30


class VRPBrake:
    def __init__(self, ex, cfg: dict, fetch_dvol=fetch_dvol_last):
        vcfg = cfg.get("vrp", {}) or {}
        self.mode = str(vcfg.get("mode", "shadow"))          # off|shadow|enforce
        self.threshold = float(vcfg.get("gap_threshold", 0.10))
        self.ttl = 3600                                       # data harian → 1 jam cukup
        self.ex = ex
        self._fetch_dvol = fetch_dvol
        self._cache: tuple[float, bool, float | None] | None = None

    def check(self) -> tuple[bool, float | None]:
        """(regime_stres, gap). regime True = gap > threshold. TIDAK memblokir
        sendiri — pemblokiran keputusan pemanggil (hanya bila mode enforce)."""
        if self.mode == "off":
            return False, None
        if self._cache and time.time() - self._cache[0] < self.ttl:
            return self._cache[1], self._cache[2]
        gap = None
        try:
            dvol = self._fetch_dvol()
            if dvol is not None:
                btc = self.ex.ohlcv("BTC/USDC:USDC", "1d", limit=33)
                gap = compute_gap(dvol, btc["close"].to_numpy()[:-1])  # buang bar berjalan
        except Exception as e:  # boundary — fail-open
            log.warning(f"VRP check gagal ({e}) — brake off")
        brake = bool(gap is not None and gap > self.threshold)
        self._cache = (time.time(), brake, gap)
        log.info(f"VRP gap={'n/a' if gap is None else f'{gap:+.3f}'} "
                 f"brake={'ON' if brake else 'off'} (mode={self.mode})")
        return brake, gap

    def stamp(self) -> dict:
        """Field untuk ditempel ke posisi saat OPEN (pakai state cache terakhir)."""
        if self.mode == "off" or self._cache is None:
            return {}
        return {"vrp_brake": self._cache[1], "vrp_gap": self._cache[2]}


def log_close(symbol: str, pos: dict, r: float, path: Path = SHADOW_LOG,
              mode: str | None = None) -> None:
    """Catat outcome trade yang punya stempel VRP (dipanggil saat close; boundary)."""
    if "vrp_brake" not in pos:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "symbol": symbol, "r": round(float(r), 4),
                                 "vrp_brake": bool(pos["vrp_brake"]),
                                 "vrp_gap": pos.get("vrp_gap")}) + "\n")
    except Exception as e:  # boundary — pencatatan tak boleh ganggu trading
        log.warning(f"VRP shadow log gagal: {e}")


def analyze_shadow(rows: list[dict], *, alpha: float = 0.05) -> dict:
    """Rem berguna bila trade SAAT brake-on lebih buruk daripada brake-off
    (permutation test satu sisi). PURE → mudah diuji."""
    from .ab import _risk_stats
    from .evolve import permutation_pvalue
    on = [float(x["r"]) for x in rows if x.get("vrp_brake")]
    off = [float(x["r"]) for x in rows if not x.get("vrp_brake")]
    base = {"n_on": len(on), "n_off": len(off),
            "exp_r_on": round(float(np.mean(on)), 4) if on else None,
            "exp_r_off": round(float(np.mean(off)), 4) if off else None,
            "risk_on": _risk_stats(on), "risk_off": _risk_stats(off)}
    if not on or not off:
        return {**base, "verdict": "INSUFFICIENT",
                "reason": "butuh trade di kedua regime (brake-on & brake-off)"}
    p = permutation_pvalue(off, on)                     # H0: off ≤ on
    useful = bool(np.mean(on) < np.mean(off) and p < alpha)
    return {**base, "p_value": round(float(p), 4),
            "verdict": "VRP_BRAKE_ADDS_VALUE" if useful else "NOT_PROVEN",
            "reason": ("trade saat regime stres signifikan lebih buruk — layak enforce"
                       if useful else "belum terbukti; tetap shadow")}


def report(path: Path = SHADOW_LOG) -> dict:
    if not path.exists():
        return {"verdict": "NO_DATA", "reason": "belum ada trade ber-stempel VRP"}
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return analyze_shadow(rows)
