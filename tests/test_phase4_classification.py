"""Phase 4: prompt evidence-based, sanitize regime, penurunan tier via Devil's Advocate."""
from bot.config import Settings
from bot.gemini_trader import GeminiTrader
from bot.react_agent import ReactAgent
from bot.settings_store import RuntimeSettings
from bot.trader_curriculum import DECISION_MODULES, curriculum_prompt


# ---------- penurunan tier (Phase 4 <-> Phase 2 tiers) ----------

def test_downgrade_conf_one_tier():
    s = RuntimeSettings()                              # full 0.75, min 0.30, reduced 0.5x
    assert s.downgrade_conf(0.90) == s.conf_min        # full → reduced (tepat ambang)
    assert s.conf_size_mult(s.downgrade_conf(0.90)) == s.conf_reduced_mult
    assert s.downgrade_conf(0.60) == 0.0               # reduced → abstain
    assert s.conf_size_mult(s.downgrade_conf(0.60)) is None
    assert s.downgrade_conf(0.40) == 0.0               # reduced (0.30≤0.40<0.75) → abstain
    assert s.downgrade_conf(0.20) == 0.20              # sudah abstain → tetap


# ---------- prompt keputusan = evidence-based (buang hafalan pola harga) ----------

def test_decision_modules_drop_price_pattern_hafalan():
    assert "chart_patterns" not in DECISION_MODULES
    assert "candlesticks" not in DECISION_MODULES
    assert "indicators" not in DECISION_MODULES
    p = curriculum_prompt(modules=DECISION_MODULES)
    assert "POLA CANDLE" not in p and "POLA CHART" not in p
    assert "PROSES KEPUTUSAN" in p                     # inti evidence/proses tetap
    assert "regime_classification" in p                # kontrak klasifikasi


# ---------- sanitize: regime_classification echo (opsional, fail-closed utuh) ----------

def _trader(cfg):
    return GeminiTrader(Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False), cfg)


def test_sanitize_regime_echo(cfg):
    t = _trader(cfg)
    out = t._sanitize({"setup": "trend_pullback", "side": "long", "conviction": 0.7,
                       "sl": 98.0, "tp": 105.0, "regime_classification": "trend"})
    assert out["regime_classification"] == "trend"
    bad = t._sanitize({"setup": "trend_pullback", "side": "long", "conviction": 0.7,
                       "sl": 98.0, "regime_classification": "bogus"})
    assert bad["regime_classification"] is None        # label asing → None, tak fatal
    assert bad["side"] == "long"                        # tetap actionable


# ---------- Devil's Advocate untuk jalur Gemini ----------

class _FakeClient:
    def __init__(self, text):
        self.text = text
    def generate(self, prompt, purpose=""):
        return self.text


def _react(cfg, devil_text):
    s = Settings(mode="dry", raw=cfg, gemini_keys=[], gemini_enabled=False)
    ra = ReactAgent(s, cfg)
    ra.devil_enabled = True
    ra.devil_threshold = 0.7
    ra.devil_client = _FakeClient(devil_text)
    return ra


def test_challenge_gemini_strong_objection(cfg):
    ra = _react(cfg, '{"strength":0.85,"objections":["against BTC lead"],"recommend":"VETO"}')
    v = ra.challenge_gemini("BTC/USDC:USDC", "long", "kuat", {"regime": "trend"})
    assert v["strength"] == 0.85
    assert ra.devil_calls == 1 and ra.devil_vetoes == 1


def test_challenge_gemini_weak_no_veto(cfg):
    ra = _react(cfg, '{"strength":0.2,"objections":[],"recommend":""}')
    v = ra.challenge_gemini("BTC/USDC:USDC", "short", "x", {"regime": "range"})
    assert v["strength"] == 0.2
    assert ra.devil_calls == 1 and ra.devil_vetoes == 0


def test_challenge_gemini_disabled_or_flat_returns_none(cfg):
    ra = _react(cfg, '{"strength":0.9}')
    ra.devil_enabled = False
    assert ra.challenge_gemini("BTC/USDC:USDC", "long", "x", {}) is None
    ra.devil_enabled = True
    assert ra.challenge_gemini("BTC/USDC:USDC", "flat", "x", {}) is None   # flat tak ditantang


def test_challenge_gemini_parse_fail_open(cfg):
    ra = _react(cfg, "not json at all")
    assert ra.challenge_gemini("BTC/USDC:USDC", "long", "x", {}) is None   # gagal → fail-open
