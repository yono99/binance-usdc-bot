# Strategy Research Log

Autonomous strategy research — discover **structurally new** sources of edge, not new
combinations of existing OHLCV indicators. Every hypothesis is judged by **out-of-sample
walk-forward expectancy (exp_R)** only. In-sample positivity means nothing.

**Co-pilot:** Gemini interprets each cycle's OOS results and proposes the next
structurally-different hypothesis (`bot/copilot.py`, run via `optimize.py --copilot`).
Gemini is **advisory only** — the deterministic walk-forward verdict (`copilot.verdict`)
is the sole authority and can never be loosened by the AI.

**Promotion bar (live):** OOS exp_R > +0.05R AND ≥3 positive windows AND consistent across
≥3 symbols. Nothing below this is ever recommended for live trading.

**Baseline:** all prior OHLCV-derived strategies (v1–v4) converge at breakeven
(exp_R ≈ −0.007R OOS). Root cause: bar-resolution price signals are fully arbitraged.

---

## CYCLE 1 — Cross-exchange basis (Binance vs Bybit)

| field | value |
|---|---|
| **Hypothesis** | Price dislocation between Binance USDC-M perp and Bybit USDT linear perp is mean-reverting. When Binance is rich/cheap vs Bybit (\|basis z-score\| large), Binance price converges back → fade the dislocation. |
| **Economic rationale** | Two venues quoting the same underlying should track within arbitrage bounds. Transient supply/demand imbalances on one venue create a spread that arbitrageurs close, implying short-horizon mean-reversion of the spread. A persistent offset (USDC/USDT basis) is absorbed by the rolling mean, so only *deviations* (z-score) are traded. |
| **Implementation** | `bot/altdata.py`: `fetch_bybit_close`, `basis_zscore` (causal, bar-close only, ffill). `bot/strategy_lab.py`: `FeaturesV5` / `decide_v5` / `walk_forward_v5` — **pure** basis mean-reversion, uses **none** of the v1–v4 trend/momentum/structure scores (structurally distinct). Exits remain ATR-based for identical cost accounting. Window: 48 bars (12h). |
| **Backtest** | `python optimize.py --strategy v5 --symbols "BTC/USDC:USDC" "ETH/USDC:USDC" "SOL/USDC:USDC" --bars 5000 --train 1000 --test 300 --min-trades 30` |
| **Data coverage** | Bybit filled 5000/5000 bars; basis_z active ~4985/5000; \|z\|>2 on 320–452 bars per symbol. No lookahead — basis at bar *t* uses only closes available at *t*; entry at open of *t+1*. |

### OOS results (aggregate, all windows × 3 symbols)

| OOS trades | win% | exp_R | PF | maxDD% |
|---:|---:|---:|---:|---:|
| 576 | 46.5 | **−0.123** | 0.80 | 33.3 |

Per-symbol OOS lean: ETH mildly positive on several windows (chosen z2.0/1.5/3.0:
OOS +0.49, +0.45, +0.19, +0.28, +0.42), **but** BTC and SOL net-negative. Per-window
n mostly 8–25 (< 30) → individual windows not statistically meaningful.

### Verdict: **REJECTED**

**Reason:** Aggregate OOS exp_R = −0.123R, **worse** than the −0.007R breakeven baseline,
and fails the cross-symbol consistency bar (only 1/3 symbols leans positive). The
cross-exchange basis on liquid majors is arbitraged away within sub-bar timescales by
HFT; at 15m resolution the residual spread is noise. Thresholding on large \|z\| does not
select reversion — it selects high-volatility dislocation moments that tend to *continue*
(momentum) at this resolution, which is why it underperforms even the zero-alpha baseline.
ETH's positive windows are sporadic and not robust to the same params on BTC/SOL → most
consistent with overfitting/noise, not edge. No Bonferroni concern triggered (single
structural idea, not 10 variants).

**Falsifier (was):** "if \|z\|>N dislocations revert profitably net of costs across symbols."
Falsified — they do not.

### Co-pilot (Gemini) read
Interpretation: PF 0.56–0.80, majority-negative windows, edge "sporadis atau kebetulan."
Overfit risk: **med**. Live trading: **DILARANG** (deterministic gate).
**Proposed next hypothesis → Liquidation cascade detection** (mission priority b):
forced-liquidation spikes overshoot intrinsic value; fade the snap-back after the cascade
subsides. Falsifier: if large liquidations instead *reinforce* the initial trend (no
tradeable snap-back) net of costs.

**Next step:** Cycle 2 — liquidation cascade detection. Need a liquidation data source
(Binance `forceOrders` / aggregated liq feed) at bar resolution; if unavailable
historically, proxy via extreme range + volume + OI-drop bars.

---

## CYCLE 2 — Liquidation cascade fade (proxy OHLCV)

| field | value |
|---|---|
| **Hypothesis** | Forced-liquidation cascades overshoot intrinsic value; price snaps back. Detect a cascade by its OHLCV signature (range ≥ k×ATR + volume ≥ m×SMA + capitulation close at one end of the bar) and **fade** it: capitulation-low → LONG, capitulation-high → SHORT. |
| **Economic rationale** | In leveraged crypto, margin-call/stop-out waves are forced market orders unrelated to fundamental value → transient supply/demand imbalance → opportunistic liquidity should fade the extreme and revert price. |
| **Data reality** | No free *historical* liquidation feed exists (Binance `@forceOrder` is real-time only; Coinglass paywalled). So cascades are **proxied** from OHLCV — honestly a proxy, not a true liq feed. Proxy is fully backtestable over 5000 bars and causal (signature read at bar close, entry next bar open). |
| **Implementation** | `bot/altdata.py: cascade_components` (range_atr, vol_ratio, close_loc). `bot/strategy_lab.py: decide_v6` — pure event fade, uses **none** of v1–v4 scores. Config: `cascade_vol_mult=2.0`, `cascade_close_loc=0.35`, `cascade_vol_lookback=20`. Cascade events: BTC 200, ETH 209, SOL 171 per 5000 bars. |
| **Backtest** | `python optimize.py --strategy v6 --symbols BTC ETH SOL --bars 5000 --train 1000 --test 300 --min-trades 30` |

### OOS results (aggregate, all windows × 3 symbols)

| OOS trades | win% | exp_R | PF | maxDD% |
|---:|---:|---:|---:|---:|
| 170 | 32.4 | **−0.430** | 0.46 | 31.7 |

Every OOS window across BTC/ETH/SOL is negative (no positive-leaning symbol). Worst
result in the program. Only the primary knob (`cascade_k`) was swept (4 values) → no
Bonferroni inflation; the signal simply has negative expectancy.

### Verdict: **REJECTED** (strongly)

**Reason:** exp_R −0.430R, PF 0.46, win 32.4% — far below the −0.007R baseline. The
**falsifier fired**: a 32% win rate on a fade means cascades **continue** in their
direction at 15m resolution rather than revert. Mechanistically, by bar-close the
intrabar snap-back (if any) is already complete; entering next-bar-open either buys
after the bounce or steps into a *continuation* (the move was informative repricing,
not pure overshoot). Fading is the wrong sign.

### Notable secondary finding (NOT a new claim)
The symmetric loss strongly implies cascades **trend-continue** at this resolution. That
is a *different* hypothesis (cascade continuation) on the *same* data source — testing
it would be inverting a failed signal, so under the anti-data-mining rule it is **not**
counted as edge here. Logged as a candidate for a clean, separately-falsified future test
(with its own out-of-sample window), not pursued now.

### Co-pilot (Gemini) read
Overfit risk: **high**; "semua aset menunjukkan expectancy OOS negatif di hampir semua
window… masalahnya pada logika strategi itu sendiri." (Note: it initially re-proposed
cascade because `TESTED_SOURCES` lagged; fixed — v6 now recorded, cascade removed from
backlog.)

**Next step:** Cycle 3 — **funding regime as a PRIMARY signal** (priority c), structurally
different from all prior cycles: when perpetual funding is extreme-positive, longs are
crowded/overpaying → fade short (and vice versa). Funding has long history (8h cadence,
~years) so it is backtestable over the full window, unlike OI.

---

## CYCLE 3 — Funding regime as a PRIMARY signal

| field | value |
|---|---|
| **Hypothesis** | Extreme perpetual funding = crowded positioning that mean-reverts. funding z ≥ +thr → crowded longs overpaying → **fade SHORT**; funding z ≤ −thr → crowded shorts → **fade LONG**. (Distinct from v3, where funding was only a *filter* on OHLCV signals; here it is the sole entry trigger.) |
| **Economic rationale** | Persistently high funding means longs pay shorts to hold — a positioning imbalance that historically precedes long-squeeze reversions. |
| **Implementation** | Reuses `bot/altdata.py: fetch_funding` + `funding_zscore` (8h cadence, ffill, causal). `bot/strategy_lab.py: decide_v7` — pure funding fade, no OHLCV scores. Window: `funding_z_window=30` (~10 days). Funding filled ~4863/5000 bars; \|z\|≥1.0 on ~1.6–1.8k bars. |
| **Backtest** | `python optimize.py --strategy v7 --symbols BTC ETH SOL --bars 5000 --train 1000 --test 300 --min-trades 30` |

### OOS results (aggregate, all windows × 3 symbols)

| OOS trades | win% | exp_R | PF | maxDD% |
|---:|---:|---:|---:|---:|
| 618 | 45.1 | **−0.116** | 0.82 | 36.8 |

A handful of windows spiked positive (ETH +0.90, +0.71; SOL +0.86) but on n=3–5 → noise.
Several windows show `−inf` (0 OOS trades): the train-selected threshold had ≥30 trades
in-sample but none crossed it in that 300-bar OOS window — a genuine walk-forward outcome,
those windows contribute nothing to the aggregate.

### Verdict: **REJECTED**

**Reason:** exp_R −0.116R, PF 0.82, win 45.1% — below the −0.007R baseline and
inconsistent across symbols. Extreme funding alone is **not a timing signal**: funding can
stay extreme or intensify while a trend persists, so fading it catches falling knives. This
is precisely why the production filter (v3) uses funding to *block crowd-aligned entries*
rather than to *trigger* counter-trend ones. **Caveat:** the level-triggered signal produces
correlated trades (many bars share one funding regime), so per-window n overstates
independent observations — but the negative aggregate is unambiguous.

**Falsifier (was):** "extreme funding reverts profitably net of costs." Falsified.

### Co-pilot (Gemini) read
With `TESTED_SOURCES` now current (v6/v7 recorded), the co-pilot's next structurally-new
proposal is **options-flow proxy** (Deribit DVOL/skew) — implied-vol/skew as a forward-
looking risk signal not derivable from spot OHLCV.

**Next step:** Cycle 4 — **options flow proxy** (priority d): Deribit `DVOL` index and/or
25-delta risk-reversal (skew) via public API. Economic basis: options markets price
forward risk and dealer hedging flow; extreme skew/vol may lead spot. Open question to
resolve first: historical depth of Deribit's public DVOL/skew endpoints at 15m alignment.

---

## Summary so far (3 cycles, structural sources beyond OHLCV)

| Cycle | Source (structurally distinct) | OOS exp_R | Verdict |
|---|---|---:|---|
| 1 | Cross-exchange basis (Binance vs Bybit) | −0.123 | REJECTED |
| 2 | Liquidation cascade fade (OHLCV proxy) | −0.430 | REJECTED |
| 3 | Funding regime as primary signal | −0.116 | REJECTED |

Baseline (v1–v4 OHLCV, best v4): −0.007R. **No structural source tested so far beats the
breakeven baseline; two are materially worse.** Consistent with the program thesis: tradeable
directional edge is unlikely to live at 15m bar resolution on liquid majors. Cascade results
add a positive-knowledge byproduct: at this resolution, large-volatility events **continue**
(momentum) rather than revert.

---

## Fase 3 — H13 sector lead-lag & H14 listing-age (2026-07-02)

New infrastructure on `feat/alpha-research-phase3`: `bot/sector.py` (rolling-correlation
greedy clustering, per-cluster leader by dollar-volume, follower score = leader trailing
return, plugged into the generic score engine) and `bot/lifecycle.py` + `lifecycle.py` CLI
(cohort walk-forward over the *listing-date* axis: age-window + direction selected on the
earliest-listed cohort, tested once on the latest-listed cohort; guards for listing-date
dispersion and left-censored history). 10 new tests with positive/negative controls; 353 green.

### H13 sector lead-lag — majors-only run
`xs_alpha.py --hypothesis sector --tf 1d`, 27 USDT majors × 1984 bars, fee 0.02 + slip 0.05:
OOS mean **+0.2366%**/rebalance over 114 rebalances, win 50.9%, Sharpe 0.043, **p_adj=1.000**
over 4 trials → **REJECTED** on this universe. Caveat: 27 BTC-correlated majors barely form
distinct narrative clusters; a definitive test needs a wide small-cap universe (meme/AI/L1).

### H14 listing-age lifecycle — full-universe definitive run
`lifecycle.py --settle USDT --bars 3000`, **760 symbols**, listing span 2338 days (dispersion
guard: feasible), cost/trade 0.14%. Train cohort (n=456) picked **SHORT ages 1–8 days**
(train mean −1.91%/trade — new listings historically dropped in week one). Latest-listed
test cohort (n=299): **−0.5529%**/trade after direction and costs → the raw effect *flipped
sign* in recent cohorts. Verdict: **REJECTED** — the week-one fade is regime-dependent, not
a durable structural mispricing.

### Verdict summary
Both remaining orthogonal directional angles from the handoff are now tested. H14 is
definitively dead (760 symbols, honest cohort OOS). H13 is dead on majors; the only open
directional question is H13 on a wide small-cap universe. After that, the honest remaining
options are execution/liquidity edges or stopping.

### H13 sector lead-lag — small-cap definitive run (2026-07-02)

Universe: **208 small-caps** (from 760 cached; ≥800 daily bars, top-20 dollar-volume
majors removed, median dvol floor 100k USDT/day, BTC kept for panel requirement),
panel 208×800 daily, fee 0.02 + slip 0.05, train 300 / test 120, grid 4.

- **Forward (follower follows leader):** OOS **−1.0058%**/rebalance over 45 rebalances,
  win 40.0%, Sharpe −0.272. IS Sharpe negative in *all three* windows — no in-sample
  signal to begin with. **REJECTED.**
- **Reverse (follower fades leader), post-hoc check** (effective trials 8, not 4):
  OOS **−0.2211%**, win 45.5%. **REJECTED.**

### Phase 3 closing verdict
H13 and H14 — the last two untested orthogonal directional angles from the handoff —
are both dead on definitive universes. The directional-prediction hypothesis space
(per-symbol TA, cross-sectional, stat-arb, lifecycle, sector rotation) is now
**exhausted**: 15+ hypotheses honestly tested, 0 edges. Remaining honest options:
(1) execution/liquidity structural edges (maker rebate, spread capture, TWAP in
illiquid pairs), which require a different research program (forward L2 collection
is already scaffolded in `l2collect.py`); or (2) stop. Do not go live with anything
that failed the four bars.

---

## Fase 4 — H24 funding-settlement seasonality (2026-07-02)

New engine `bot/settlement.py` (rebalances exactly at pre-settlement bars, PnL =
price + actually-charged funding via cumf) + `settlement_alpha.py` CLI. 4 tests
incl. funding-income sign control; 357 green.

Definitive run: 60 most-liquid USDT perps × 17520 hourly bars (2y) + full funding
history, fee 0.02 + slip 0.03, grid 6 (offset {0,1} × hold {1,4,8}), 5 windows.

Result: OOS mean **−0.1917%**/rebalance over **1250** rebalances (very well-powered),
win 33.6%, Sharpe −0.303. IS Sharpe negative in *all* windows. Decomposition: cost
is 0.20%/rebalance → gross PnL ≈ **+0.008% ≈ zero**. There is no settlement-timed
flow effect at 1h granularity in either direction — the raw effect is nil, so the
reverse trade is equally dead (flipping zero gross still loses the cost).

### Verdict: **REJECTED**
**Falsifier (was):** "funding-payers' position-closing creates predictable pre/post
settlement drift, direction given by funding sign." Falsified cleanly: gross effect
is zero, not merely cost-eaten. Mechanical-schedule rationale was sound; the market
prices it. Next in queue per RESEARCH_HYPOTHESES_PHASE4.md: H26 illiquidity-shock
reversal, then H25 carry×momentum double-sort.

---

## Fase 4 — H26 illiquidity-shock reversal (2026-07-02)

Builder `xs_signals.score_illiq_shock` (dynamic Amihud: ratio short/long-window
illiquidity, structural threshold 1.5 declared upfront, fade sign of last-3d move;
non-shocked = NaN). 3 new tests incl. shock-market positive/negative controls.

- **Pilot** (208 small-caps × 800d): OOS **+0.5443%**/rebalance, positive in all
  3 windows, but n=76 → p_adj=0.737. Underpowered, not tuned further.
- **Definitive** (103 small-caps × 1400d, same grid, trials counted 8 cumulative):
  OOS **−0.3542%** over 175 rebalances, win 49.7%, windows split 4+/4−.

The pilot's +0.54% was a small-sample artifact — textbook trap #2 (skew/carry died
the same way in Phase 2). With 2.3× the OOS sample the effect flips sign.

### Verdict: **REJECTED**
**Falsifier (was):** "sudden liquidity withdrawal causes overshoot that reverts
within days, net of stressed costs." Falsified on the larger sample; the apparent
edge does not survive more data, let alone cost-stress ×2.
