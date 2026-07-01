"""Planner tipis — agen menetapkan TUJUAN sesi; keputusan per-tick tunduk padanya.

Goal-directed TANPA mengorbankan keselamatan: rencana HANYA bisa MENGETATKAN (lebih
konservatif dari batas manusia), tak pernah melonggarkan. Gagal/Gemini off → rencana
NETRAL (tak ada batasan tambahan) → trading tak pernah terblokir oleh planner.

Rencana = stance + bias + kuota trade sesi + kuota eksposur. Di-enforce DETERMINISTIK
di kode (enforce()), bukan dipercayakan ke AI.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .config import Settings
from .gemini_client import GeminiClient
from .logger import log

STANCES = ("aggressive", "normal", "defensive", "risk_off")
BIASES = ("long", "short", "neutral")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_plan(hard_max_trades: int) -> dict:
    """Rencana netral — tak menambah batasan apa pun (fail-safe)."""
    return {"stance": "normal", "bias": "neutral",
            "max_new_trades": int(hard_max_trades), "max_exposure_frac": 1.0,
            "reasoning": "default netral (planner off / fail-safe)", "ts": _utcnow()}


class SessionPlanner:
    def __init__(self, settings: Settings, cfg: dict):
        gcfg = cfg.get("gemini", {})
        self.client = GeminiClient(settings.gemini_keys, gcfg.get("model", "gemini-2.5-flash"))
        self.enabled = bool(settings.gemini_enabled and self.client.available)

    def make_plan(self, ctx: dict, *, hard_max_trades: int) -> dict:
        """Bentuk rencana sesi dari konteks. Gagal/off → default netral."""
        if not self.enabled:
            return default_plan(hard_max_trades)
        prompt = (
            "You are an autonomous trading agent setting a SESSION PLAN (not a single trade).\n"
            "PRODUCT CONTEXT: this bot supports MINIMAL-CAPITAL traders (from ~$10). A SMALL "
            "balance is NORMAL & expected — do NOT treat it as a reason to stop trading. Capital "
            "is deployed via SMALL MARGIN + LEVERAGE, not by sitting idle. Be defensive only for "
            "genuine market danger (news/regime/drawdown), NOT merely because the balance is small.\n"
            "ADAPT TO GROWTH STAGE: tiny balance (~$10) → stay ACTIVE but avoid ruin (few concurrent "
            "risks); as balance GROWS → you may allow more concurrent trades & wider exposure. Scale "
            "with the account, don't freeze it.\n"
            f"Context: {json.dumps(ctx, default=str)}\n"
            f"Hard cap new trades this session: {hard_max_trades} (you may go LOWER, never higher; "
            "but keep at least 1 unless you choose stance=risk_off).\n"
            "max_exposure_frac is a fraction of balance; keep it high enough that at least one "
            "normal-size position fits (small accounts need most of their balance as margin).\n"
            "Respond ONLY JSON:\n"
            '{"stance":"aggressive|normal|defensive|risk_off","bias":"long|short|neutral",'
            '"max_new_trades":<int <= cap>,"max_exposure_frac":<0..1>,"reasoning":"one sentence"}'
        )
        text = self.client.generate(prompt, purpose="planner")
        if not text:
            return default_plan(hard_max_trades)
        try:
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        except Exception as e:  # boundary
            log.warning(f"planner parse gagal → default: {e}")
            return default_plan(hard_max_trades)
        return self.sanitize(data, hard_max_trades)

    @staticmethod
    def sanitize(data: dict, hard_max_trades: int) -> dict:
        """Validasi & CLAMP ke batas aman — rencana tak pernah melebihi pagar manusia."""
        stance = str(data.get("stance", "")).lower()
        bias = str(data.get("bias", "")).lower()
        try:
            mnt = int(data.get("max_new_trades", hard_max_trades))
        except (TypeError, ValueError):
            mnt = hard_max_trades
        try:
            mef = float(data.get("max_exposure_frac", 1.0))
        except (TypeError, ValueError):
            mef = 1.0
        stance = stance if stance in STANCES else "normal"
        mnt = max(0, min(mnt, int(hard_max_trades)))
        mef = max(0.0, min(mef, 1.0))
        # LANTAI modal-minim: kecuali stance=risk_off (stop eksplisit), jangan cekik akun kecil —
        # sisakan ≥1 trade & eksposur cukup agar minimal 1 posisi muat. risk_off = cara berhenti.
        if stance != "risk_off":
            mnt = max(1, mnt)
            mef = max(0.5, mef)
        return {
            "stance": stance, "bias": bias if bias in BIASES else "neutral",
            "max_new_trades": mnt, "max_exposure_frac": round(mef, 3),
            "reasoning": str(data.get("reasoning", ""))[:200], "ts": _utcnow(),
        }

    @staticmethod
    def enforce(plan: dict, side: str, *, new_trades: int, exposure_frac: float) -> str | None:
        """Kembalikan ALASAN blokir bila entry melanggar rencana, else None.
        HANYA mengetatkan: tak pernah mengizinkan yang tadinya diblokir aturan lain."""
        if not plan:
            return None
        if plan.get("stance") == "risk_off":
            return "plan: risk-off (tak buka posisi baru)"
        bias = plan.get("bias", "neutral")
        if bias == "long" and side == "short":
            return "plan: bias long (tolak short)"
        if bias == "short" and side == "long":
            return "plan: bias short (tolak long)"
        if new_trades >= int(plan.get("max_new_trades", 1_000_000)):
            return "plan: kuota trade sesi tercapai"
        if exposure_frac >= float(plan.get("max_exposure_frac", 1.0)):
            return "plan: kuota eksposur tercapai"
        return None
