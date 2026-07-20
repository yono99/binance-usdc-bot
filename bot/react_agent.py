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

_BTC_NOTE = (
    "\nDOMINANSI BTC: BTC memimpin pasar; altcoin ber-BETA LEBIH TINGGI — gerak BTC sering "
    "DIPERBESAR & DIPERPANJANG di alt (BTC turun 1 bar → alt bisa turun 2-3 bar). Pakai "
    "'BTC leader' di state: untuk LONG alt HINDARI masuk saat BTC turun; BTC lemah = "
    "konfluensi SHORT alt. Untuk BTC sendiri, abaikan field ini."
)

ACTIONS = ("ENTER_LONG", "ENTER_SHORT", "SKIP", "REDUCE_RISK", "FLAT")
PORTFOLIO_ACTIONS = ("HOLD", "REDUCE_RISK", "FLAT")   # aksi level-portofolio (point 2: otonomi)
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
        # Devil's Advocate (rem adversarial, adaptasi debat Bull/Bear TradingAgents).
        dcfg = gcfg.get("devil_advocate", {})
        self.devil_enabled = bool(dcfg.get("enabled", False))
        self.devil_threshold = float(dcfg.get("veto_threshold", 0.7))
        dmodel = dcfg.get("model")
        self.devil_client = (GeminiClient(settings.gemini_keys, dmodel)
                             if dmodel and dmodel != gcfg.get("model") else self.client)
        # Telemetri kesehatan agen (dibaca panel Agent Health, Phase 6).
        self.calls = 0
        self.fallbacks = 0
        self.devil_calls = 0
        self.devil_vetoes = 0

    # ---------------------- OBSERVE ----------------------
    def observe(self, sig: Signal, *, regime: str | None = None, alt: dict | None = None,
                n_positions: int = 0, max_positions: int = 0, daily_pnl_r: float = 0.0,
                lessons: list | None = None, memory=None, btc_lead: dict | None = None,
                halving_phase: str | None = None) -> dict:
        alt = alt or {}
        return {
            "recent_memory": memory.summary(sig.symbol) if memory is not None else [],
            "btc_lead": btc_lead or {},
            "halving_phase": (halving_phase or "").strip() or "unknown",
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
            f"- BTC leader (1bar/3bar %, dir): {json.dumps(s.get('btc_lead', {}), default=str)}\n"
            f"- Halving phase (macro): {s.get('halving_phase', 'unknown')}\n"
            f"- Recent lessons: {json.dumps(s['recent_lessons'], default=str)}\n"
            f"- Recent memory (this symbol): {json.dumps(s.get('recent_memory', []), default=str)}\n"
        )

    @classmethod
    def _prompt(cls, s: dict) -> str:
        return (
            "You are a trading agent. Analyze this market state and decide.\n"
            "You MANAGE the decision; you do NOT predict the signal (scores are given).\n\n"
            "State:\n" + cls._state_block(s) + _BTC_NOTE +
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
            "State:\n" + cls._state_block(s) + _BTC_NOTE +
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

    # ---------------------- DEVIL'S ADVOCATE (adaptasi debat Bull/Bear TradingAgents) ---------
    @classmethod
    def _devil_prompt(cls, s: dict, out: dict) -> str:
        side = "LONG" if out["action"] == "ENTER_LONG" else "SHORT"
        return (
            "You are the DEVIL'S ADVOCATE on a trading desk. Another agent PROPOSES to "
            f"ENTER {side} on {s['symbol']}. Your ONLY job is to argue AGAINST it — find the "
            "STRONGEST reasons this entry will LOSE. Be skeptical, not agreeable; if the case "
            "against is weak, say so honestly.\n\n"
            f"Proposed entry reasoning: {out.get('reasoning', '')}\n\n"
            "Market state:\n" + cls._state_block(s) + _BTC_NOTE +
            "\nWeigh especially: entering AGAINST BTC lead, price extended from mean, thin/ambiguous "
            "signal score, adverse funding, chasing a move, regime=chaos or range.\n"
            "Respond ONLY with valid JSON:\n"
            '{"strength":0.0,"objections":["strongest reason against","second"],'
            '"recommend":"VETO"}\n'
            "strength = how strong the case AGAINST is (0.0 = no real objection, 1.0 = clearly a bad entry)."
        )

    @staticmethod
    def _parse_devil(text: str | None) -> dict | None:
        if not text:
            return None
        try:
            d = json.loads(text[text.find("{"):text.rfind("}") + 1])
        except Exception:  # boundary — parse gagal → fail-open (kritik diabaikan)
            return None
        try:
            strength = max(0.0, min(float(d.get("strength", 0.0)), 1.0))
        except (TypeError, ValueError):
            return None
        obj = d.get("objections") or []
        if not isinstance(obj, list):
            obj = [str(obj)]
        return {"strength": round(strength, 3),
                "objections": [str(o)[:120] for o in obj][:5],
                "recommend": str(d.get("recommend", "")).upper().strip()}

    def _devil_advocate(self, sig: Signal, state: dict, out: dict) -> dict:
        """Pass adversarial: tantang HANYA aksi ENTER. strength ≥ threshold → batalkan
        jadi SKIP. Gagal/parse-error → fail-open (proceed). Objection dicatat ke key_risks."""
        if not self.devil_enabled or out["action"] not in ("ENTER_LONG", "ENTER_SHORT"):
            return out
        self.devil_calls += 1
        verdict = self._parse_devil(
            self.devil_client.generate(self._devil_prompt(state, out), purpose="devil_advocate"))
        if verdict is None:
            return out                                   # fail-open
        if verdict["objections"]:                        # audit: selalu catat keberatan
            out["key_risks"] = (out["key_risks"] + verdict["objections"])[:6]
        if verdict["strength"] >= self.devil_threshold:
            self.devil_vetoes += 1
            top = verdict["objections"][0] if verdict["objections"] else "objection kuat"
            out["action"] = "SKIP"
            out["reasoning"] = (out["reasoning"] +
                                f" | DEVIL veto ({verdict['strength']:.2f}): {top}").strip(" |")
        else:
            out["reasoning"] = (out["reasoning"] +
                                f" | devil cleared ({verdict['strength']:.2f})").strip(" |")
        return out

    def challenge_gemini(self, symbol: str, side: str, rationale: str,
                         market: dict, alt: dict | None = None) -> dict | None:
        """Devil's Advocate untuk jalur GeminiTrader (Phase 4). Tantang entry Gemini;
        kembalikan verdict {strength, objections} atau None (devil off / gagal → fail-open,
        pemanggil TIDAK menurunkan tier). Pakai-ulang _parse_devil + telemetri devil."""
        if not self.devil_enabled or side not in ("long", "short"):
            return None
        prompt = (
            "You are the DEVIL'S ADVOCATE on a crypto futures desk. Another agent PROPOSES "
            f"to ENTER {side.upper()} on {symbol}. Argue AGAINST it — the STRONGEST reasons "
            "this entry LOSES. Be skeptical, not agreeable; if the case against is weak, say so.\n\n"
            f"Proposed reasoning: {rationale}\n"
            f"Evidence (regime/funding/OI/order-flow/vol): {json.dumps({'market': market, 'alt': alt or {}}, default=str)}\n"
            + _BTC_NOTE +
            "\nWeigh especially: entering against a strong opposing regime, adverse funding, "
            "CVD divergence, elevated realized vol/chaos, chasing an extended move.\n"
            "Respond ONLY with valid JSON:\n"
            '{"strength":0.0,"objections":["strongest reason against","second"],"recommend":"VETO"}\n'
            "strength = how strong the case AGAINST is (0.0 = no real objection, 1.0 = clearly bad).")
        self.devil_calls += 1
        verdict = self._parse_devil(self.devil_client.generate(prompt, purpose="devil_advocate"))
        if verdict is None:
            return None                                  # fail-open
        if verdict["strength"] >= self.devil_threshold:
            self.devil_vetoes += 1
        return verdict

    def decide_with_tools(self, sig: Signal, tools: dict, *, max_iters: int = 4,
                          regime: str | None = None, alt: dict | None = None,
                          n_positions: int = 0, max_positions: int = 0, daily_pnl_r: float = 0.0,
                          lessons: list | None = None, shadow: bool = False, memory=None,
                          btc_lead: dict | None = None, halving_phase: str | None = None) -> Decision:
        """Loop ReAct sejati: nalar → panggil tool → observasi → nalar → aksi final.
        Gagal/parse-error/maxiters → fallback ke decide() single-shot (TAK pernah blokir).
        memory (opsional): observasi tool & keputusan diingat lintas-tick."""
        obs_kwargs = dict(regime=regime, alt=alt, n_positions=n_positions, max_positions=max_positions,
                          daily_pnl_r=daily_pnl_r, lessons=lessons, memory=memory, btc_lead=btc_lead,
                          halving_phase=halving_phase)
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
                out = self._devil_advocate(sig, state, out)   # rem adversarial
                d = self._build(sig, state, scores, out, "LLM_TOOL", shadow, memory)
                self._record(d)
                return d
            name = step.get("tool")                      # PANGGILAN TOOL
            if name in tools:
                obs = tools[name]["fn"](step.get("args") or {})
                transcript.append({"tool": name, "args": step.get("args") or {}, "obs": obs})
                if memory is not None:                   # ingat observasi utk tick berikutnya
                    memory.remember("tool", sig.symbol, {name: obs})
            else:
                transcript.append({"tool": name, "error": "unknown tool"})
        self.fallbacks += 1                              # tak capai aksi → fallback single-shot
        return self.decide(sig, shadow=shadow, **obs_kwargs)

    # ---------------------- ACT + RECORD ----------------------
    def decide(self, sig: Signal, *, regime: str | None = None, alt: dict | None = None,
               n_positions: int = 0, max_positions: int = 0, daily_pnl_r: float = 0.0,
               lessons: list | None = None, shadow: bool = False, memory=None,
               btc_lead: dict | None = None, halving_phase: str | None = None) -> Decision:
        """shadow=True (mode A/B): agen tetap menalar & MENCATAT verdict, tapi eksekusi
        dipaksa mengikuti rules (permits()=True) → bisa bandingkan rules vs rules+ReAct."""
        state = self.observe(sig, regime=regime, alt=alt, n_positions=n_positions,
                             max_positions=max_positions, daily_pnl_r=daily_pnl_r,
                             lessons=lessons, memory=memory, btc_lead=btc_lead,
                             halving_phase=halving_phase)
        scores = {"long": state["long_score"], "short": state["short_score"]}

        if not self.enabled:
            self.fallbacks += 1
            return self._fallback(sig, state, scores, "LLM_DISABLED", shadow, memory)

        self.calls += 1
        out = self.reason(state)
        if out is None:
            self.fallbacks += 1                        # LLM gagal → JANGAN blokir trading
            return self._fallback(sig, state, scores, "LLM_UNAVAILABLE", shadow, memory)

        source = "LLM"
        # SKIP keyakinan-rendah → jangan percaya; serahkan ke veto deterministik lama.
        if out["action"] == "SKIP" and out["confidence"] < self.min_conf_skip:
            source = "VETO_FALLBACK"
            if self._veto_allows(sig) and sig.actionable:
                out["action"] = "ENTER_LONG" if sig.side == "long" else "ENTER_SHORT"
                out["reasoning"] = (out["reasoning"] + " | low-conf SKIP → veto fallback izinkan").strip(" |")
            else:
                out["reasoning"] = (out["reasoning"] + " | low-conf SKIP → veto fallback tahan").strip(" |")

        out = self._devil_advocate(sig, state, out)    # rem adversarial (bisa ubah ENTER→SKIP)
        d = self._build(sig, state, scores, out, source, shadow, memory)
        self._record(d)
        return d

    def _fallback(self, sig: Signal, state: dict, scores: dict, source: str,
                  shadow: bool = False, memory=None) -> Decision:
        """Deterministik: ikuti sinyal rules bila veto lama (fail-open) mengizinkan."""
        if self._veto_allows(sig) and sig.actionable:
            action = "ENTER_LONG" if sig.side == "long" else "ENTER_SHORT"
            reasoning = f"{source}: fallback deterministik mengikuti sinyal rules"
        else:
            action = "SKIP"
            reasoning = f"{source}: fallback — veto/regime menahan entry"
        out = {"action": action, "confidence": 0.0, "reasoning": reasoning,
               "key_risks": [], "lesson_triggered": ""}
        d = self._build(sig, state, scores, out, source, shadow, memory)
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
               shadow: bool = False, memory=None) -> Decision:
        action = out["action"]
        react_action = ""
        if shadow and sig.actionable:
            # Mode A/B: catat verdict agen, TAPI paksa eksekusi ikut rules (ENTER searah sinyal).
            react_action = action
            action = "ENTER_LONG" if sig.side == "long" else "ENTER_SHORT"
        d = Decision(
            id=uuid.uuid4().hex, ts=_utcnow(), symbol=sig.symbol,
            action=action, reasoning=out["reasoning"], confidence=out["confidence"],
            key_risks=out["key_risks"], lesson_triggered=out["lesson_triggered"], source=source,
            signal_scores=scores, react_action=react_action,
            market_state={"price": state["price"], "atr": state["atr"],
                          "funding": state["funding"], "regime": state["regime"]})
        if memory is not None:                       # ingat keputusan lintas-tick
            memory.remember("decision", d.symbol,
                            {"action": react_action or action, "src": source})
        return d

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

    # ---------------------- POINT 2: aksi level-portofolio (otonomi) ----------------------
    def manage_portfolio(self, portfolio: dict, *, daily_pnl_r: float = 0.0,
                         lessons: list | None = None) -> dict:
        """Aksi portofolio otonom: HOLD | REDUCE_RISK | FLAT. HANYA boleh mengurangi risiko
        (di-enforce pemanggil). Fail-safe = HOLD. Dicatat ke decision_log (audit)."""
        out = {"action": "HOLD", "reasoning": "llm off → hold", "confidence": 0.0}
        if self.enabled:
            self.calls += 1
            prompt = (
                "You are an AUTONOMOUS trading agent managing an OPEN PORTFOLIO.\n"
                "You may ONLY reduce risk — never add.\n"
                f"Daily PnL: {round(float(daily_pnl_r), 3)}R\n"
                f"Portfolio: {json.dumps(portfolio, default=str)}\n"
                f"Recent lessons: {json.dumps(lessons or [], default=str)}\n"
                "Actions: HOLD (do nothing), REDUCE_RISK (move stops to breakeven on winners), "
                "FLAT (close everything now — only if regime/news clearly dangerous).\n"
                'Respond ONLY JSON: {"action":"HOLD","reasoning":"one sentence","confidence":0.0}'
            )
            parsed = self._parse_tool_step(self.client.generate(prompt, purpose="portfolio"))
            out = self._sanitize_portfolio(parsed) if parsed is not None else out
        else:
            self.fallbacks += 1
        decision_log.append({
            "ts": _utcnow(), "id": uuid.uuid4().hex, "symbol": "*PORTFOLIO*",
            "action": out["action"], "reasoning": out["reasoning"],
            "confidence": out["confidence"], "key_risks": [], "lesson_triggered": "",
            "source": "LLM" if self.enabled else "LLM_DISABLED", "signal_scores": {},
            "react_action": "", "market_state": {"portfolio": portfolio},
            "outcome": None, "outcome_r": None, "filled_at_close": False,
        }, path=self.log_path)
        return out

    @staticmethod
    def _sanitize_portfolio(data: dict) -> dict:
        action = str(data.get("action", "")).upper().strip()
        if action not in PORTFOLIO_ACTIONS:
            return {"action": "HOLD", "reasoning": "aksi tak valid → hold", "confidence": 0.0}
        try:
            conf = max(0.0, min(float(data.get("confidence", 0.0)), 1.0))
        except (TypeError, ValueError):
            conf = 0.0
        return {"action": action, "reasoning": str(data.get("reasoning", ""))[:200],
                "confidence": round(conf, 3)}

    def health(self) -> dict:
        """Rasio ketersediaan LLM vs fallback (untuk panel Agent Health)."""
        total = self.calls + self.fallbacks
        return {"llm_calls": self.calls, "fallbacks": self.fallbacks,
                "fallback_rate": round(self.fallbacks / total, 3) if total else 0.0,
                "enabled": self.enabled,
                "devil_calls": self.devil_calls, "devil_vetoes": self.devil_vetoes,
                "devil_veto_rate": round(self.devil_vetoes / self.devil_calls, 3) if self.devil_calls else 0.0}
