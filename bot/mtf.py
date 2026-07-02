"""Gerbang kesepakatan multi-timeframe (Phase 3 kalibrasi) — SHADOW dulu.

Filosofi identik VRP (bot/vrp.py): gate baru lahir sebagai SHADOW — catat
setuju/tak-setuju tiap sinyal, JANGAN blokir apa pun — sampai sampel nyata
membuktikan ia menaikkan kalibrasi. Naik kelas ke enforce = perubahan terpisah.

Base signal v4 sudah pakai SATU HTF (config htf_mult, mis. 15m×4=1h). Gate ini
menambah pembacaan arah di ≥2 timeframe DI LUAR itu (default 15m×8=2h & ×16=4h,
diatur config `mtf.mults`) lalu membandingkan arahnya dengan arah trade.

Config (config.yaml `mtf:`) — sengaja mengikuti pola VRP (cfg, bukan RuntimeSettings):
- mode   : off | shadow (default). enforce dibangun terpisah setelah report positif.
- mults  : daftar pengali base-tf untuk dibaca (default [8, 16]).
- sample : ambang sampel sebelum report dianggap bermakna (default 100).

Fail-open ketat: data HTF kurang / error → arah 0 (unknown) → dihitung 'tak menentang'.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .indicators import ema
from .logger import log

SHADOW_LOG = Path(__file__).resolve().parent.parent / "logs" / "mtf_shadow.jsonl"


def _tf_minutes(tf: str) -> int:
    unit, n = tf[-1].lower(), int(tf[:-1])
    return n * {"m": 1, "h": 60, "d": 1440}[unit]


def htf_direction(df: pd.DataFrame, base_tf: str, mult: int, cfg: dict) -> int:
    """Arah tren di base_tf×mult tanpa lookahead: sign(EMA_fast − EMA_slow) pada
    bar HTF terakhir yang SUDAH tertutup. 0 bila data kurang. Mirror _htf_dir."""
    minutes = _tf_minutes(base_tf) * mult
    s = cfg["signals"]
    try:
        htf_close = (df["close"].resample(f"{minutes}min", label="right", closed="right")
                     .last().dropna())
        if len(htf_close) < s["ema_slow"] + 2:
            return 0
        ef = ema(htf_close, s["ema_fast"])
        es = ema(htf_close, s["ema_slow"])
        # bar terakhir yang sudah tertutup = shift(1)
        return int(np.sign((ef - es).iloc[-2]))
    except Exception as e:  # boundary — fail-open
        log.warning(f"MTF htf_direction mult={mult} gagal: {e}")
        return 0


class MTFAgree:
    def __init__(self, cfg: dict):
        m = cfg.get("mtf", {}) or {}
        self.mode = str(m.get("mode", "shadow"))          # off | shadow
        self.mults = [int(x) for x in (m.get("mults") or [8, 16])]
        self.sample = int(m.get("sample", 100))
        self.cfg = cfg

    def evaluate(self, df: pd.DataFrame, base_tf: str, side: int) -> dict:
        """side: +1 long / −1 short. Kembalikan ringkasan kesepakatan.
        agree = TIDAK ada TF yang menentang arah (TF unknown=0 dianggap netral)."""
        dirs = {mult: htf_direction(df, base_tf, mult, self.cfg) for mult in self.mults}
        opposed = sum(1 for d in dirs.values() if d != 0 and d != side)
        with_side = sum(1 for d in dirs.values() if d == side)
        return {"mtf_agree": opposed == 0, "mtf_with": with_side,
                "mtf_opposed": opposed, "mtf_total": len(self.mults),
                "mtf_dirs": {str(k): v for k, v in dirs.items()}}

    def stamp(self, df: pd.DataFrame, base_tf: str, side: int) -> dict:
        """Field untuk ditempel ke posisi saat OPEN (shadow). {} bila mode off."""
        if self.mode == "off":
            return {}
        try:
            return self.evaluate(df, base_tf, side)
        except Exception as e:  # boundary — jangan ganggu open
            log.warning(f"MTF stamp gagal: {e}")
            return {}


def log_close(symbol: str, pos: dict, r: float, conviction: float | None,
              mode: str, path: Path = SHADOW_LOG) -> None:
    """Catat outcome trade ber-stempel MTF (dipanggil saat close; boundary)."""
    if "mtf_agree" not in pos:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": time.time(), "symbol": symbol, "mode": mode,
                "r": round(float(r), 4), "win": 1 if r > 0 else 0,
                "conviction": conviction,
                "mtf_agree": bool(pos["mtf_agree"]),
                "mtf_opposed": pos.get("mtf_opposed"),
                "mtf_with": pos.get("mtf_with")}) + "\n")
    except Exception as e:  # boundary — pencatatan tak boleh ganggu trading
        log.warning(f"MTF shadow log gagal: {e}")


def _agg(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0, "win_rate": None, "exp_r": None, "brier": None}
    briers = [(float(x["conviction"]) - x["win"]) ** 2
              for x in rows if x.get("conviction") is not None]
    return {"n": n,
            "win_rate": round(sum(x["win"] for x in rows) / n * 100, 1),
            "exp_r": round(sum(float(x["r"]) for x in rows) / n, 4),
            "brier": round(sum(briers) / len(briers), 4) if briers else None}


def analyze(rows: list[dict]) -> dict:
    """Bandingkan win rate & Brier: sinyal AGREE (tak ada TF menentang) vs DISAGREE."""
    agree = [x for x in rows if x.get("mtf_agree")]
    disagree = [x for x in rows if not x.get("mtf_agree")]
    return {"agree": _agg(agree), "disagree": _agg(disagree), "total": len(rows)}


def report(mode: str | None = None, sample: int = 100, path: Path = SHADOW_LOG) -> dict:
    """Report shadow per-mode. verdict INSUFFICIENT sampai total ≥ sample."""
    if not path.exists():
        return {"verdict": "NO_DATA", "reason": "belum ada trade ber-stempel MTF"}
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if mode:
        rows = [x for x in rows if x.get("mode") == mode]
    res = analyze(rows)
    if res["total"] < sample:
        res["verdict"] = "INSUFFICIENT"
        res["reason"] = f"butuh ≥{sample} sinyal shadow (ada {res['total']})"
    else:
        a, d = res["agree"]["win_rate"], res["disagree"]["win_rate"]
        better = a is not None and d is not None and a > d
        res["verdict"] = "MTF_AGREEMENT_HELPS" if better else "NOT_PROVEN"
        res["reason"] = ("sinyal agree menang lebih sering — layak dipertimbangkan enforce"
                         if better else "belum terbukti; tetap shadow")
    res["mode"] = mode
    return res
