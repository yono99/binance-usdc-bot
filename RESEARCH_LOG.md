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
