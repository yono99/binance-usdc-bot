"""Phase 1 — ReAct trading agent: OBSERVE → REASON → ACT → RECORD.

Menggantikan veto PASIF Gemini (GeminiLayer.allows) dengan loop penalaran AKTIF
sebagai GERBANG ENTRY. Tetap deterministik-first:

  HARD CONSTRAINTS (misi):
  - Tak ada lookahead — hanya membaca state saat ini.
  - LLM mengelola KEPUTUSAN, bukan memprediksi sinyal (skor tetap dari signals.py).
  - Kegagalan LLM TAK PERNAH memblokir trading → fallback ke veto lama + aturan sinyal.
  - Setiap keputusan dicatat penuh ke logs/decision_log.jsonl (alasan + risiko + skor).

Alur tiap tick (per kandidat sinyal):
  1. OBSERVE  — rakit state pasar (harga, ATR, funding/OI/CVD bila ada, regime,
                skor sinyal, posisi terbuka, PnL harian R, pelajaran terbaru).
  2. REASON   — kirim ke Gemini (JSON terstruktur) → {action, reasoning, confidence,
                key_risks, lesson_triggered}.
  3. ACT      — terjemahkan action menjadi izin BUKA posisi searah sinyal, atau defer
                ke mesin deterministik.
  4. RECORD   — tulis satu baris JSON ke decision_log.jsonl.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import decision_log
from .config import Settings
from .gemini_client import GeminiClient
from .gemini_layer import GeminiLayer
from .logger import log
from .signals import Signal

ACTIONS = ("ENTER_LONG", "ENTER_SHORT", "SKIP", "REDUCE_RISK", "FLAT")
DECISION_LOG = Path("logs/decision_log.jsonl")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Decision:
    id: str
    ts: str
    symbol: str
    action: str
    reasoning: str
    confidence: float
    key_risks: list = field(default_factory=list)
    lesson_triggered: str = ""
    source: str = "LLM"            # LLM | LLM_UNAVAILABLE | LLM_DISABLED | VETO_FALLBACK
    signal_scores: dict = field(default_factory=dict)
    market_state: dict = field(default_factory=dict)
    react_action: str = ""         # A/B shadow: verdict agen saat eksekusi dipaksa rules

    def permits(self, sig: Signal) -> bool:
        """True HANYA bila agen mengizinkan buka posisi SEARAH sinyal aktif.
        SKIP/REDUCE_RISK/FLAT atau arah berlawanan → tak buka posisi (aman)."""
        if sig.side == "long":
            return self.action == "ENTER_LONG"
        if sig.side == "short":
            return self.action == "ENTER_SHORT"
        return False


class ReactAgent:
    def __init__(self, settings: Settings, cfg: dict, veto: GeminiLayer | None = None,
                 log_path: Path | str = DECISION_LOG):
        self.settings = settings
        self.cfg = cfg
        gcfg = cfg.get("gemini", {})
        self.client = GeminiClient(settings.gemini_keys, gcfg.get("model", "gemini-2.5-flash"))
        # Veto lama dipertahankan sebagai FALLBACK deterministik (fail-open).
        self.veto = veto if veto is not None else GeminiLayer(settings, cfg)
        self.enabled = bool(settings.gemini_enabled and self.client.available)
        self.min_conf_skip = float(gcfg.get("react_min_skip_conf", 0.3))
        self.log_path = Path(log_path)
        # Telemetri kesehatan agen (dibaca panel Agent Health, Phase 6).
        self.calls = 0
        self.fallbacks = 0

    # ---------------------- OBSERVE ----------------------
    def observe(self, sig: Signal, *, regime: str | None = None, alt: dict | None = None,
                n_positions: int = 0, max_positions: int = 0, daily_pnl_r: float = 0.0,
                lessons: list | None = None) -> dict:
        alt = alt or {}
        return {
            "symbol": sig.symbol,
            "price": sig.price,
            "atr": sig.atr,
            "atr_pct": round(sig.atr / sig.price * 100, 3) if sig.price else None,
            "funding": alt.get("funding"),
            "oi_change_1h_pct": alt.get("oi_change"),
            "cvd_1h": alt.get("cvd"),
            "regime": regime or getattr(sig, "regime", "unknown"),
            "signal_side": sig.side,
            "signal_confidence": sig.confidence,
            "long_score": getattr(sig, "long_score", None),
            "short_score": getattr(sig, "short_score", None),
            "n_positions": n_positions,
            "max_positions": max_positions,
            "daily_pnl_r": round(float(daily_pnl_r), 3),
            "recent_lessons": (lessons or [])[:5],
        }

    # ---------------------- REASON ----------------------
    def reason(self, state: dict) -> dict | None:
        """Panggil Gemini → dict ter-sanitasi, atau None bila gagal/parse-error."""
        text = self.client.generate(self._prompt(state), purpose="react")
        if not text:
            return None
        try:
            data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        except Exception as e:  # boundary — parse gagal = LLM tak tersedia → fallback
            log.warning(f"react parse gagal: {e}")
            return None
        return self._sanitize(data)

    @staticmethod
    def _sanitize(data: dict) -> dict | None:
        action = str(data.get("action", "")).upper().strip()
        if action not in ACTIONS:
            return None
        try:
            conf = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(conf, 1.0))
        risks = data.get("key_risks") or []
        if not isinstance(risks, list):
            risks = [str(risks)]
        return {
            "action": action,
            "confidence": round(conf, 3),
            "reasoning": str(data.get("reasoning", ""))[:300],
            "key_risks": [str(r)[:120] for r in risks][:5],
            "lesson_triggered": str(data.get("lesson_triggered", ""))[:200],
        }

    @staticmethod
    def _state_block(s: dict) -> str:
        return (
            f"- Symbol: {s['symbol']}\n"
            f"- Price: {s['price']}, ATR: {s['atr']} ({s['atr_pct']}%)\n"
            f"- Funding (8h): {s['funding']}  OI 1h%: {s['oi_change_1h_pct']}  CVD 1h: {s['cvd_1h']}\n"
            f"- Regime: {s['regime']}\n"
            f"- Signal: side={s['signal_side']} conf={s['signal_confidence']} "
            f"long={s['long_score']} short={s['short_score']}\n"
            f"- Open positions: {s['n_positions']}/{s['max_positions']}  Daily PnL: {s['daily_pnl_r']}R\n"
            f"- Recent lessons: {json.dumps(s['recent_lessons'], default=str)}\n"
        )

    @classmethod
    def _prompt(cls, s: dict) -> str:
        return (
            "You are a trading agent. Analyze this market state and decide.\n"
            "You MANAGE the decision; you do NOT predict the signal (scores are given).\n\n"
            "State:\n" + cls._state_block(s) +
            "\nAvailable actions: ENTER_LONG, ENTER_SHORT, SKIP, REDUCE_RISK, FLAT\n"
            "Respond ONLY with valid JSON:\n"
            '{"action":"SKIP","reasoning":"one sentence why","confidence":0.0,'
            '"key_risks":["risk1","risk2"],"lesson_triggered":"id of the lesson that '
            'influenced this (from Recent lessons), or empty string"}'
        )

    # ---------------------- ReAct TOOL-LOOP (point 1: agent otonom) ----------------------
    @classmethod
    def _tool_prompt(cls, s: dict, tools: dict, transcript: list) -> str:
        tool_list = "\n".join(f"  - {n}: {t['desc']}" for n, t in tools.items())
        hist = ""
        if transcript:
            lines = []
            for i, x in enumerate(transcript):
                obs = x.get("obs", x.get("error"))
                lines.append(f"  {i + 1}. {x.get('tool')}({json.dumps(x.get('args', {}), default=str)}) "
                             f"-> {json.dumps(obs, default=str)}")
            hist = "\nObservasi yang sudah kamu kumpulkan:\n" + "\n".join(lines)
        return (
            "You are an AUTONOMOUS trading agent. INVESTIGATE with tools, THEN decide.\n"
            "You manage decisions; signal scores are given (you don't predict them).\n\n"
            "State:\n" + cls._state_block(s) +
            "\nTools (call to gather evidence BEFORE deciding):\n" + tool_list + hist +
            "\n\nReply with EXACTLY ONE JSON object — either:\n"
            '  tool call:      {"tool":"<name>","args":{...}}\n'
            '  final decision: {"action":"ENTER_LONG|ENTER_SHORT|SKIP|REDUCE_RISK|FLAT",'
            '"reasoning":"...","confidence":0.0,"key_risks":[],"lesson_triggered":""}\n'
            "Call a tool only if it would change your decision; otherwise decide now."
        )

    @staticmethod
    def _parse_tool_step(text: str | None) -> dict | None:
        if not text:
            return None
        try:
            return json.loads(text[text.find("{"):text.rfind("}") + 1])
        except Exception:  # boundary
            return None

    def decide_with_tools(self, sig: Signal, tools: dict, *, max_iters: int = 4,
                          regime: str | None = None, alt: dict | None = None,
                          n_positions: int = 0, max_positions: int = 0, daily_pnl_r: float = 0.0,
                          lessons: list | None = None, shadow: bool = False) -> Decision:
        """Loop ReAct sejati: nalar → panggil tool → observasi → nalar → aksi final.
        Gagal/parse-error/maxiters → fallback ke decide() single-shot (TAK pernah blokir)."""
        obs_kwargs = dict(regime=regime, alt=alt, n_positions=n_positions,
                          max_positions=max_positions, daily_pnl_r=daily_pnl_r, lessons=lessons)
        if not self.enabled or not tools:
            return self.decide(sig, shadow=shadow, **obs_kwargs)
        state = self.observe(sig, **obs_kwargs)
        scores = {"long": state["long_score"], "short": state["short_score"]}
        self.calls += 1
        transcript: list = []
        for _ in range(max(1, max_iters)):
            step = self._parse_tool_step(
                self.client.generate(self._tool_prompt(state, tools, transcript), purpose="react_tool"))
            if step is None:
                break
            if step.get("action"):                       # AKSI FINAL
                out = self._sanitize(step)
                if out is None:
                    break
                d = self._build(sig, state, scores, out, "LLM_TOOL", shadow)
                self._record(d)
                return d
            name = step.get("tool")                      # PANGGILAN TOOL
            if name in tools:
                obs = tools[name]["fn"](step.get("args") or {})
                transcript.append({"tool": name, "args": step.get("args") or {}, "obs": obs})
            else:
                transcript.append({"tool": name, "error": "unknown tool"})
        self.fallbacks += 1                              # tak capai aksi → fallback single-shot
        return self.decide(sig, shadow=shadow, **obs_kwargs)

    # ---------------------- ACT + RECORD ----------------------
    def decide(self, sig: Signal, *, regime: str | None = None, alt: dict | None = None,
               n_positions: int = 0, max_positions: int = 0, daily_pnl_r: float = 0.0,
               lessons: list | None = None, shadow: bool = False) -> Decision:
        """shadow=True (mode A/B): agen tetap menalar & MENCATAT verdict, tapi eksekusi
        dipaksa mengikuti rules (permits()=True) → bisa bandingkan rules vs rules+ReAct."""
        state = self.observe(sig, regime=regime, alt=alt, n_positions=n_positions,
                             max_positions=max_positions, daily_pnl_r=daily_pnl_r, lessons=lessons)
        scores = {"long": state["long_score"], "short": state["short_score"]}

        if not self.enabled:
            self.fallbacks += 1
            return self._fallback(sig, state, scores, "LLM_DISABLED", shadow)

        self.calls += 1
        out = self.reason(state)
        if out is None:
            self.fallbacks += 1                        # LLM gagal → JANGAN blokir trading
            return self._fallback(sig, state, scores, "LLM_UNAVAILABLE", shadow)

        source = "LLM"
        # SKIP keyakinan-rendah → jangan percaya; serahkan ke veto deterministik lama.
        if out["action"] == "SKIP" and out["confidence"] < self.min_conf_skip:
            source = "VETO_FALLBACK"
            if self._veto_allows(sig) and sig.actionable:
                out["action"] = "ENTER_LONG" if sig.side == "long" else "ENTER_SHORT"
                out["reasoning"] = (out["reasoning"] + " | low-conf SKIP → veto fallback izinkan").strip(" |")
            else:
                out["reasoning"] = (out["reasoning"] + " | low-conf SKIP → veto fallback tahan").strip(" |")

        d = self._build(sig, state, scores, out, source, shadow)
        self._record(d)
        return d

    def _fallback(self, sig: Signal, state: dict, scores: dict, source: str,
                  shadow: bool = False) -> Decision:
        """Deterministik: ikuti sinyal rules bila veto lama (fail-open) mengizinkan."""
        if self._veto_allows(sig) and sig.actionable:
            action = "ENTER_LONG" if sig.side == "long" else "ENTER_SHORT"
            reasoning = f"{source}: fallback deterministik mengikuti sinyal rules"
        else:
            action = "SKIP"
            reasoning = f"{source}: fallback — veto/regime menahan entry"
        out = {"action": action, "confidence": 0.0, "reasoning": reasoning,
               "key_risks": [], "lesson_triggered": ""}
        d = self._build(sig, state, scores, out, source, shadow)
        self._record(d)
        return d

    def _veto_allows(self, sig: Signal) -> bool:
        try:
            return bool(self.veto.allows(sig.symbol, {
                "price": sig.price, "atr": sig.atr,
                "conf": sig.confidence, "reason": sig.reason}))
        except Exception as e:  # boundary — fail-open: jangan blokir karena error infra
            log.warning(f"veto fallback error, allow: {e}")
            return True

    def _build(self, sig: Signal, state: dict, scores: dict, out: dict, source: str,
               shadow: bool = False) -> Decision:
        action = out["action"]
        react_action = ""
        if shadow and sig.actionable:
            # Mode A/B: catat verdict agen, TAPI paksa eksekusi ikut rules (ENTER searah sinyal).
            react_action = action
            action = "ENTER_LONG" if sig.side == "long" else "ENTER_SHORT"
        return Decision(
            id=uuid.uuid4().hex, ts=_utcnow(), symbol=sig.symbol,
            action=action, reasoning=out["reasoning"], confidence=out["confidence"],
            key_risks=out["key_risks"], lesson_triggered=out["lesson_triggered"], source=source,
            signal_scores=scores, react_action=react_action,
            market_state={"price": state["price"], "atr": state["atr"],
                          "funding": state["funding"], "regime": state["regime"]})

    def _record(self, d: Decision) -> None:
        """Append satu baris keputusan (outcome diisi nanti saat posisi tutup, Phase 2)."""
        decision_log.append({
            "ts": d.ts, "id": d.id, "symbol": d.symbol, "action": d.action,
            "reasoning": d.reasoning, "confidence": d.confidence,
            "key_risks": d.key_risks, "lesson_triggered": d.lesson_triggered,
            "source": d.source, "signal_scores": d.signal_scores,
            "react_action": d.react_action,
            "market_state": d.market_state,
            "outcome": None, "outcome_r": None, "filled_at_close": False,
        }, path=self.log_path)

    def health(self) -> dict:
        """Rasio ketersediaan LLM vs fallback (untuk panel Agent Health)."""
        total = self.calls + self.fallbacks
        return {"llm_calls": self.calls, "fallbacks": self.fallbacks,
                "fallback_rate": round(self.fallbacks / total, 3) if total else 0.0,
                "enabled": self.enabled}
