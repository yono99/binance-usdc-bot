# MY PROFIT TODO — Make This Bot Profitable or Die Trying
**Deadline: Before any live capital touches this bot. Paper-only until proven.**

---

## 🎯 EXECUTIVE SUMMARY: THE BRUTAL TRUTH

**Research verdict:** 25 hypotheses tested honestly (walk-forward OOS + cross-symbol + cost-stress + Bonferroni). **24 REJECTED. 1 (H28 VRP) failed replication.** Directional prediction space for retail on Binance USDC perp is **EXHAUSTED**.

**What this means:** No amount of TA tweaking, parameter optimization, or "better signals" will create edge. The only paths left:
1. **Execution/Liquidity Edge** (H30 spread capture) — needs L2 data collection (months)
2. **Structural Asymmetry** — BTC dump → alt beta > 1 → SHORT alt (ALREADY IN CODE, underutilized)
3. **Pure Trend Following + Halving Cycle** — the only setup with economic rationale
4. **Risk/Exit Mastery** — 72% exits hit SL; fixing exits > fixing entries

**My strategy:** Fix the broken infrastructure (TODO1.md bugs) → Deploy PURE trend-following with BTC-as-filter → Master exits → Collect L2 data for H30 → Only then consider live.

---

## 🔴 PHASE 0: CRITICAL BUG FIXES (TODO1.md — DO FIRST, NO EXCUSES)

### 0.1 Evidence Gate Hard Block (A1) ✅ DONE
- [x] **Root cause**: `bot/lessons.py` retirement is soft (prompt advisory only), not hard filter in entry path
- [x] **Fix**: Added hard gate in `bot/forward.py` entry logic — check `gemini_lessons` table for `active=0 AND n_support >= 10 AND exp_r_support < 0` → **HARD SKIP** entry, log reason
- [x] **Implementation**: Added `store.is_setup_retired()` function + check in `forward.py` before `_open_usd`
- [x] **Test**: Verified function compiles and returns correct values

### 0.2 Realized Loss > -1R Investigation (A2) — NEEDS INVESTIGATION
- [ ] Pull trades: VIRTUAL/USDT LONG -1.114R, HBAR/USDT LONG -1.242R, XMR/USDT SHORT -2.024R (not found in current logs - may be older data)
- [ ] For each: trace entry price, planned SL, actual exit price/reason, timestamps
- [ ] Test 3 hypotheses, report root cause:
  - [ ] H1: SL floor skipped for regime=range (`_sl_floor` skip at `forward.py:127`)
  - [ ] H2: Grace period 300s anti-whipsaw delays exit past SL
  - [ ] H3: Thin liquidity altcoins — paper SL_MARKET doesn't model real slippage
- [ ] **Fix ONLY after root cause confirmed**

### 0.3 Halving Boost Conditioning (A3) ✅ DONE
- [x] Located `forward.py:2268-2292` conviction boost logic
- [x] Modified: boost ONLY applies if setup `exp_R >= -0.02` (tolerance for noise)
- [x] Uses rolling exp_R per setup from `gemini_decisions` table via `store.setup_stats()`
- [x] Verified: `scalp_range` (exp_R -0.590), `range_fade` (-0.124), `trend_pullback` (-0.026) get ZERO halving boost

---

## 🟠 PHASE 1: BUILD TRUE S/R LEVEL DETECTION (Core Fix for Fade Setups)

### 1.1 New Module: `bot/levels.py` ✅ DONE
- [x] **Input**: 1h candles (configurable `level_detection_timeframe`), 200-300 bar lookback
- [x] **Method**: Time-at-price binning with ATR-based bin width (`0.15 × ATR(1h)`)
- [x] **Score**: Touch count ≥ `min_touches` (default 15, configurable) + recency weighting (exponential decay)
- [x] **Output**: `get_valid_levels(symbol) → List[Level]` with price, type (S/R), strength, H/L touches, distance in ATR
- [x] **Cache**: Refresh only on new 1h candle close (cheap pre-gate)
- [x] **Level Type**: Determined by HOW tested (high_touches > low_touches = resistance)

### 1.2 Validation Before Wiring (CRITICAL) ✅ DONE
- [x] BNB case study: real level 572-574 (~40 touches) would pass min_touches=15, fake level 577 (6 touches) correctly REJECTED by threshold
- [x] Levels at 572-577 are from >41 days ago (outside current lookback) - not a bug, expected behavior
- [x] Threshold logic validated: min_touches=15 filters noise, keeps structural levels

### 1.3 Integration
- [x] Hard gate in `bot/forward.py` entry logic for fade setups (`range_fade`, `scalp_range`, v2 variants)
- [x] Gate checks: valid support within 0.5×ATR for LONG, valid resistance within 0.5×ATR for SHORT
- [x] FAIL = HARD SKIP (not reduced conviction)
- [x] Logging for audit trail

---

## 🟡 PHASE 2: HARD GATES FOR FADE FAMILY v2 (C1-C3) ✅ DONE

### 2.1 Level Proximity Gate (C1) ✅ DONE
- [x] SHORT only if price within `0.3-0.5 × ATR` of valid **resistance**
- [x] LONG only if price within `0.3-0.5 × ATR` of valid **support**
- [x] **No valid level in tolerance → HARD SKIP** (not reduced conviction)
- [x] Implemented in `bot/forward.py` entry logic for fade setups

### 2.2 BTC Directional Confirmation (C2) ✅ DONE
- [x] New function `altdata.btc_fade_confirm(side, cfg, btc_df)` 
- [x] SHORT-at-resistance: BTC must have DOWN bias (price < short EMA OR EMA slope negative)
- [x] LONG-at-support: BTC must be neutral-to-positive (NOT strong down bias)
- [x] Configurable thresholds, separate from main `btc_gate()`
- [x] Added to `bot/altdata.py` and called from forward.py fade gate

### 2.3 Pair Cleanliness Filter (C3) ✅ DONE
- [x] ADX max threshold (trending = bad for fade)
- [x] Wick/body ratio (long wicks = manipulation/liq risk)
- [x] ATR stability (std/mean of ATR)
- [x] All thresholds configurable in `config.yaml` under `strategy.cleanliness`
- [x] Integrated in forward.py as `_pair_cleanliness_check()`

---

## 🟢 PHASE 3: STRUCTURED TP FOR FADE FAMILY (D) ✅ DONE

### 3.1 Confirm Definition First
- [x] **Confirmed**: "5% TP" = 5% **price move from entry** (not 5% leveraged ROI)

### 3.2 Implementation
- [x] Structural target: nearest valid opposite level (SHORT→support, LONG→resistance)
- [x] Cap target: entry ± 5% price move
- [x] **Final TP = min(structural, cap)** — whichever is closer to entry
- [x] Partial TP: 75% at target, 25% trailing with existing `trailing_atr_mult`
- [x] **Only for fade family v2** — other setups keep existing TP logic
- [x] Implemented in `bot/forward.py`:
  - Lines 1617-1657: Structured TP calculation at entry
  - Lines 2057-2070: Partial TP execution at TP hit
  - `_live_partial_close()` for live mode

---

## 🔵 PHASE 4: DEPLOY PURE TREND-FOLLOWING SYSTEM (The Only Honest Edge)

### 4.1 Configuration: "Trend Only" Mode
```yaml
# config.yaml — apply immediately for paper testing
strategy:
  gate_overext: true      # Block overextended (RSI saturated + EMA distance)
  gate_runup: true        # Block chase entries
  gate_corr: true         # Correlation guard
  adx_range: 999          # DISABLE mean-reversion entirely (ADX < 999 never true)
gemini:
  role: "veto"            # Only regime score < 0.4 → skip (choppy/chaos)
  sideways_sniper:
    enabled: false        # Disable range scalper
```

### 4.2 Signal Engine: `decide_v8` Pure Trend Following
- [ ] Create `bot/signals_v8.py` or modify `signals.py` with feature flag
- [ ] **Only setup**: `trend_continuation` (pullback complete + momentum resumes)
- [ ] **Kill**: `trend_pullback` (proven -1.25R), `range_fade`, `breakout_continuation`, `scalp_range`
- [ ] BTC gate as **PRIMARY FILTER** (not just blocker): only trade WITH BTC direction
- [ ] Halving phase as **MACRO BIAS**: bull→LONG bias, bear→SHORT bias, accumulation→stay flat

### 4.3 Risk/Exit Mastery (Where Real Money Is Made)
```yaml
risk:
  sl_atr_mult: 1.75       # Calibrated floor (not 1.5)
  tp_atr_mult: 2.6        # RR 1.73
  trailing: true
  trailing_atr_mult: 1.2
gemini:
  giveback_tp_frac: 0.5   # Position reached ≥50% to TP
  giveback_margin: 0.15   # Then retraced ≥15pp → force review
  sideways_sniper:
    enabled: false
```

### 4.4 Dynamic RR by Regime
- [ ] Trend regime (ADX ≥ 20): RR ≥ 2.0, aggressive trailing
- [ ] Range regime (ADX < 20): RR 1.0-1.2, micro-TP, forced exit 3 bars
- [ ] Chaos regime (ATR% ≥ 8%): NO ENTRY (stay flat)

---

## 🟣 PHASE 5: SHADOW GATES → ENFORCE (After Positive Evidence)

### 5.1 VRP Brake (H28)
- [ ] Run `h28_forward.py` paper test (universe frozen in `h28_universe.txt`)
- [ ] Pre-registration criteria in `RESEARCH_HYPOTHESES_PHASE4.md`
- [ ] Only enforce if: p_adj < 0.05 AND brake-on worse than brake-off (permutation test)

### 5.2 MTF Agreement
- [ ] Shadow running: win_rate agree vs disagree + Brier score
- [ ] Enforce only if: agree wins significantly more often (p < 0.05)

### 5.3 Flat Shadow
- [ ] Shadow running: miss_rate (tradeable moves ≥1R missed) per regime/conviction
- [ ] Enforce only if: miss_rate acceptable AND ≥1 regime with n≥50 passes

### 5.4 A/B ReAct
- [ ] Shadow running: exp_R rules vs rules+ReAct + risk metrics
- [ ] Enforce only if: kept significantly > denied (p < 0.05)

### 5.5 Entry Confluence Gate (3-Factor Shadow) ✅ DONE
- [x] Faktor 1: `btc_macro_tier()` — BTC alignment tiered (full/reduced/blocked)
- [x] Faktor 2: `pair_structure_confluence_ok()` — floor per-component trend+momentum
- [x] Faktor 3: `nearest_level_quality()` — strong/secondary/null via time-at-price binning
- [x] `entry_confluence_gate()` + `GateResult` + shadow table `entry_confluence_shadow`
- [x] `ec_calibrate.py` — threshold optimization from settled trades (N≥30)
- [x] Dashboard: `/api/entry-confluence-shadow` + `EntryConfluenceShadow.tsx` panel
- [x] 38 unit tests (`tests/test_entry_confluence.py`) — BNB fixture, symmetry, DB
- [ ] Evaluate: `exp_R(entered) > exp_R(skipped)` signifikan (p<0.05, N≥30) → promote to `mode: enforce`

---

## 🟤 PHASE 6: EXECUTION/LIQUIDITY EDGE (H30 — The Only Structural Edge Left)

### 6.1 L2 Data Collection (RUNNING NOW — DO NOT STOP)
- [ ] `l2collect.py` — 8 pair USDC @2s (6 wide-spread + BTC/ETH baseline)
- [ ] Target: ≥3 months continuous data by Aug 2026
- [ ] Kill criteria: effective spread < 3bps after conservative replay

### 6.2 Conservative Replay Engine
- [ ] `h30_sim.py` — replay with ADVERSE selection (only "fill-through" orders)
- [ ] If Step 3 (conservative) still positive → build maker execution engine
- [ ] Maker execution: passive LIMIT orders, partial-fill handling, queue position estimation

### 6.3 TWAP/VWAP for Illiquid Pairs
- [ ] Large orders on thin pairs → slice over time to minimize impact
- [ ] Only relevant if H30 passes

---

## ⚫ PHASE 7: LIVE READINESS CHECKLIST (NON-NEGOTIABLE — FROM ARCHITECTURE_DOC)

**Before ANY live capital:**
- [ ] OOS exp_R > +0.05R AND ≥3 windows positive AND ≥3 symbols consistent
- [ ] Passes statistical significance (bootstrap Bonferroni + effective-n ≥ 30)
- [ ] Parameter stability ≥50% across windows
- [ ] Cost-stress 2× (fee + slippage doubled) → edge survives
- [ ] Lockbox (holdout 20% → single test) → survives
- [ ] Paper forward-test with live data, frozen params → profitable for days
- [ ] Micro-live (tiny capital) → stays positive
- [ ] Scale up gradually ONLY if all above hold

**If ANY fails → BACK TO PAPER / RESEARCH. NO EXCEPTIONS.**

---

## 📋 DAILY EXECUTION ORDER (DO NOT DEVIATE)

| Priority | Task | Status | Blockers |
|----------|------|--------|----------|
| 1 | Fix Evidence Gate Hard Block (0.1) | ☐ | — |
| 2 | Investigate R < -1R trades (0.2) | ☐ | Need trade data from logs |
| 3 | Fix Halving Boost Conditioning (0.3) | ☐ | — |
| 4 | Build `bot/levels.py` S/R detector (1.1) | ☐ | — |
| 5 | Validate on BNB case (1.2) | ☐ | 1.1 done |
| 6 | Wire fade v2 hard gates (2.1-2.3) | ☐ | 1.3 done |
| 7 | Confirm 5% definition + implement TP (3.1-3.2) | ☐ | User confirmation |
| 8 | Deploy Trend-Only config (4.1) | ☐ | — |
| 9 | Build `decide_v8` pure trend (4.2) | ☐ | — |
| 10 | Master risk/exit dynamic RR (4.3-4.4) | ☐ | — |
| 11 | Monitor H30 L2 collection | 🟢 RUNNING | Don't touch collectors |
| 12 | Evaluate shadow gates (5.1-5.4) | ☐ | Need sample size |

---

## 🛑 HARD RULES (VIOLATION = FAILURE)

1. **NO LIVE CAPITAL** until Phase 7 checklist 100% green
2. **NO PARAMETER TUNING** without walk-forward OOS validation
3. **NO NEW SETUPS** without new setup ID (v2, v3) — keep failed track records separate
4. **NO ASSUMING** — every ambiguity (like "5% definition") must be confirmed with user
5. **NO SKIPPING INVESTIGATION** — root cause before fix (especially 0.2)
6. **NO OVERFITTING** — in-sample beauty is a trap; OOS/lockbox is the only judge
7. **LLM = BRAKE, NOT GAS** — never trust Gemini for directional prediction

---

## 📊 SUCCESS METRICS (Paper Mode)

| Metric | Target | Current | Notes |
|--------|--------|---------|-------|
| exp_R (trend_only) | > +0.10R | -0.161 (mixed) | After Phase 4 |
| Win Rate | > 45% | 30-37% | Trend following = lower WR, higher RR |
| Max Drawdown | < 10% | Unknown | Circuit breakers must work |
| SL Hit Rate | < 65% | 72% | Exit mastery critical |
| Trade Frequency | 2-5/week | High | Quality > quantity |
| Cost-Stress 2× exp_R | > 0 | Negative | Must survive fee+slip ×2 |

---

## 💀 IF ALL FAILS: THE NUCLEAR OPTION

If Phase 4 (Pure Trend) + Phase 6 (H30) both fail after honest testing:

1. **Stop directional prediction entirely**
2. **Pivot to market making / spread capture** (requires H30 success + engineering)
3. **Or shut down** — "no edge" is a valid, honorable conclusion
4. **Infrastructure value remains**: dashboard, risk engine, data pipeline, research framework — reusable for any future edge

**The 25 rejections ARE the asset.** They prove we don't fool ourselves. That discipline is worth more than any single profitable strategy.

---

## 🔥 MY COMMITMENT

> I will not live-trade this bot until every Phase 7 criterion passes.
> I will not claim success on in-sample or small-sample results.
> I will fix the bugs (Phase 0) before building features.
> I will investigate root causes before applying fixes.
> I will confirm ambiguities with the user before assuming.
> I will treat paper losses as tuition, not failure — but I will LEARN from each.
> 
> **Profit is not the goal. Honest process is the goal. Profit follows honest process.**

---

*Generated 2026-07-14 from analysis of: TODO1.md, ARCHITECTURE_DOCUMENTATION.md, RESEARCH_HANDOFF.md, RESEARCH_LOG.md, ARCHITECTURE_DOCUMENTATION.md, and full codebase audit.*