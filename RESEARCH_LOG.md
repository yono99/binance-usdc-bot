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

---

## Fase 4 — H25 carry × momentum double-sort (2026-07-02)

`carry.carry_returns` extended with an optional momentum gate (symbol eligible
only when residual momentum opposes funding sign — targeting the documented
carry failure mode: shorting a pump still in progress) + `walk_forward_carry_mom`
+ `carry_mom.py` CLI. Key control: in a synthetic market built to mimic the
failure mode, plain carry LOSES while gated carry WINS — so a real-data failure
is a fact about the market, not the engine. 6 carry tests green.

Definitive run: 103 small-caps × 1400d daily, full funding history (income
realized via cumf), grid 4 (mom {5,10} × hold {3,7}, smooth fixed 3), 9 windows,
fee 0.02 + slip 0.05:

OOS mean **−0.5375%**/rebalance over 263 rebalances, win 44.1%, Sharpe −0.127.
Windows split 3+/6−; IS Sharpe mostly negative too.

### Verdict: **REJECTED**
**Falsifier (was):** "conditioning carry on opposed momentum removes the
run-over-by-pump failure and leaves positive net carry." Falsified: even with
the gate AND realized funding income counted, the strategy loses after costs.
The funding premium in small-caps is simply not large enough to survive the
price risk left after gating. Carry angle is now fully exhausted (majors,
small-cap, gated).

---

## Fase 4 — sisa antrian: H31, H32, H27, H28 (2026-07-02)

Engines: `xs_signals.score_downside_beta` (H31), `bot/tsmom.py` + `tsmom_alpha.py`
(H32), `xs_signals.score_venue_basis` + `basis_alpha.py` (H27, Bybit closes via
altdata.fetch_bybit_close, cached `data/snap_bybit`), `vrp_alpha.py` (H28, Deribit
DVOL public API cached `data/snap_dvol_btc_1d.pkl`). All with +/− controls
(downside-beta control needed drift compensation: asymmetric conditional beta
mechanically drags −a·E|rb| unconditional drift). 361 tests green.

| H | Setup | OOS | Verdict |
|---|---|---|---|
| H31 downside-beta | 103×1400d | −1.14%/rebal, n=112, win 42.9% | ❌ (cousin of dead BAB/coskew, as expected) |
| H32 TSMOM 1d | 103×1400d, lb {30,60,90} | +0.45%, n=184, p_adj=0.59 | ❌ weak-positive-insignificant (H18 pattern) |
| H27 venue basis | 76 dual-venue ×1400d | −0.18%, n=220, win 43.6%, IS negatif | ❌ |
| H28 VRP (DVOL gate on −ivol) | 103×1400d | **+2.06%, n=38, win 71%, p_adj=0.036 → LOLOS awal** | lihat bawah |

### H28 — kandidat pertama yang lolos p_adj, lalu gugur di validasi lanjutan
- Run awal (103×1400d): +2.0620%/rebal, semua 8 window positif, p_adj=0.0365. 
- Cost-stress ×2: mean bertahan (+1.78% — efek >> biaya) tapi p_adj=0.083.
- **Replikasi 78×1800d: mean menyusut separuh (+1.0869%), p_adj=0.336; stress ×2:
  p_adj=0.456.** Pola shrinkage klasik artefak (H18/H26 signature), meski tanda
  tetap positif di kedua sampel (win 65–71%).
- Konteks program-level yang jujur: setelah ~20 hipotesis diuji, SATU p_adj=0.036
  pada n=38 kira-kira yang diharapkan dari kebetulan (expected false positive
  @α=0.05 × 20 ≈ 1).

### Verdict H28: **REJECTED** (palang #4 pada replikasi & stress)
Catatan positif satu-satunya di seluruh program: H28 adalah satu-satunya sinyal
dengan OOS positif konsisten di dua universe + tahan biaya secara ekonomis. Bila
suatu saat ingin satu forward paper-test tanpa risiko, ini kandidatnya — tapi
TIDAK memenuhi syarat live berdasarkan aturan program.

## Fase 4 — PENUTUP
Seluruh antrian direksional habis: H24–H28, H31, H32 semua DITOLAK. Bersama
Fase 1–3: **22 hipotesis, 0 lolos empat palang.** Yang tersisa dan masih hidup:
perekam L2 (H30 spread capture — struktural) & perekam OI (H19/H29) — keduanya
menunggu data matang, bukan menunggu ide baru.

---

## Fase 5 — TEROBOSAN DATA: arsip Binance Vision membuka H30 & H19 hari ini (2026-07-02)

Penemuan: `data.binance.vision` menyimpan historis yang tak ada di REST API —
`aggTrades` (setiap eksekusi + flag maker side, tersedia utk pair USDC) dan
`metrics` (OI 5-menit sejak ~2021; batas 30 hari hanya di REST). Kedua gerbang
"tunggu data" terbuka LEBIH AWAL dengan data yang LEBIH BAIK (fill nyata, bukan
proxy snapshot). Infrastruktur: `bot/vision.py` (downloader+parser, cache),
`bot/aggresearch.py` (effective spread + adverse selection dari fill nyata),
`h30_hist.py`, `h19_hist.py`. Kolektor L2/OI forward tetap jalan (bahan langkah 3).

### H30 langkah 1-2 pada 91 HARI fill maker NYATA (Apr–Jun 2026, 7.4 jt trade)

| pair | eff-spread med | adverse | EDGE kotor |
|---|--:|--:|--:|
| **FIL** | 5.59 bps | **−0.50** | **+3.30 bps** ✅ |
| NEAR | 3.68 | +0.27 | +1.58 |
| CRV | 3.09 | +0.80 | +0.75 |
| NEO | 3.10 | +0.90 | +0.65 |
| BOME | 3.12 | +1.82 | −0.26 |
| PNUT | 3.66 | +3.14 | −1.32 |

**Verdict gerbang: PROCEED_TO_SIM** — pertama kalinya dalam 22+ hipotesis sebuah
gerbang pra-registrasi LOLOS di data yang cukup (91 hari ≥ 28).

Kejujuran yang melekat:
1. Ini BATAS ATAS: fill orang lain, posisi antrian kita tak terukur.
2. Lolos TIPIS (3.30 vs ambang 3.0) dan hanya 1 pair dari 6.
3. Effective spread (3–5.6 bps) << quoted spread live (9–15 bps): trade nyata
   terjadi jauh di dalam spread — quoted spread menipu, effective yang jujur.
4. FIL adverse NEGATIF (harga rata-rata bergerak MENGUNTUNGKAN maker pasca-fill)
   — sinyal mean-reversion mikro; menarik tapi butuh konfirmasi langkah 3.

**Langkah 3 (berikutnya, pra-registrasi):** simulasi replay KONSERVATIF — quote
dianggap terisi hanya bila harga MENEMBUS level (bukan menyentuh), inventori
dibatasi, biaya taker utk unwind darurat. Sumber: aggTrades (fill-through nyata)
+ snapshot L2 forward yang terus terkumpul. Lolos → langkah 4 paper-quote mikro.

### H19 — uji historis penuh BERJALAN (450 hari × 30 small-cap, grid 4 pra-reg)
Hasil menyusul di entri berikut.

### H19 OI crowding-freshness — uji historis penuh: REJECTED (2026-07-02)

Run 1 (gate |funding|>0.05%/8h, pra-set): **n=0** — gagal mekanis, strategi tak
pernah aktif (funding default 0.01%; diagnostik: hanya 0.1% bar punya ≥8 simbol
aktif di gate itu). Gate diturunkan SEKALI ke 0.03%/8h berdasarkan DIAGNOSTIK
COVERAGE (dipilih sebelum melihat return — bukan dari PnL; tercatat di output).

Run 2 (450 hari × 30 mid/small-cap, OI 5-menit arsip, grid 4 pra-reg):
OOS mean **−0.8155%**/rebalance, n=15, win 53.3%, Sharpe −0.192, p_adj=1.0.
Point estimate NEGATIF → menambah power tak akan menyelamatkan (beda dgn kasus
H26 yang pilot-positif). Konsep "crowding segar di funding ekstrem" juga
inheren-jarang: bahkan di gate 0.01%/8h hanya 23% bar punya ≥8 simbol aktif.

**Verdict: REJECTED.** Konsekuensi operasional: perekam OI forward (`oicollect.py`)
REDUNDAN — arsip metrics Binance Vision menyediakan OI 5-menit permanen (terbit
H+1). Daemon dihentikan & dikeluarkan dari launcher; data yang sudah terekam
disimpan. H29 (strategi "rekam dulu") selesai tugasnya lebih cepat dari rencana.

## Skor akhir program direksional + struktural per 2026-07-02
24 hipotesis diuji jujur. 23 DITOLAK. 1 (H30 spread capture) LOLOS langkah 1-2
di 91 hari fill nyata → antre langkah 3 (simulasi replay konservatif).

### H30 langkah 3 — replay konservatif: REJECTED (2026-07-02)

Simulator `bot/mmsim.py` (kontrol +/− teruji: osilasi menang, gap-staircase
rugi, sentuh≠fill, biaya unwind terverifikasi presisi). Aturan verdict dikunci
SEBELUM run: positif di stress penuh (unwind = half-spread + taker 2bps) pada
KEDUA offset {1.0x, 1.5x} + ≥20 rt/hari.

Hasil di 91 hari × 7.4jt trade: SEMUA pair, SEMUA offset, bahkan TANPA stress
taker: mean **−7.3 s/d −11.4 bps**/round-trip, win 32–41%, ~1000–1900 rt/hari.
REJECTED — bukan marginal.

**Diagnosis ekonomi (pelajaran kunci program):** langkah 2 (+3.30 bps, batas
atas) mengukur adverse atas SEMUA fill maker — mayoritas fill "at-touch" dalam
flow jinak. Langkah 3 hanya mengizinkan fill "tembus" — subset fill dengan
adverse selection TERBURUK by construction. Realitas ada di antara +3.3 dan
−10 bps, dan posisinya ditentukan oleh SATU variabel: posisi antrian. Posisi
antrian = kecepatan + infrastruktur = persis yang tidak dimiliki retail.
Spread capture di pair ini adalah bisnis MM profesional; angka kami
membuktikannya dari dua arah.

## KESIMPULAN PROGRAM (matang, 2026-07-02)
**25 hipotesis diuji jujur lintas Fase 1–5. 24 DITOLAK. 1 (H28) tersisa di
paper-test forward berbiaya nol.** Ruang edge yang bisa diakses retail kecil di
Binance USDC perp — direksional (TA, cross-sectional, carry, stat-arb, event,
lifecycle, sektor, vol-premium, OI) DAN struktural (spread capture) — telah
dieksplorasi sampai habis dengan disiplin penuh. Sesuai handoff opsi #4:
kesimpulan matang = edge tidak tersedia; JANGAN live-kan apa pun. Bot tetap
paper (gratis, teruji, berguna sebagai infrastruktur bila suatu saat muncul
edge dari sumber data yang belum ada). Seluruh nilai kerja ini ada pada
25 keputusan "tidak" yang terdokumentasi — masing-masing menyelamatkan uang nyata.

---

## Studi kalibrasi lantai SL — 1 tahun × 15 pair (2026-07-02)

Keluhan pemilik tervalidasi data: "SL terlalu mepet saat candle besar".
Akar mekanis: (1) ATR(14) Wilder telat bereaksi thd candle raksasa padahal
sinyal momentum menyala tepat sesudahnya; (2) SL usulan Gemini tak punya cek
jarak minimum sama sekali (hanya sisi-benar + dalam-likuidasi).

**Metode** (`bot/slcalib.py` + `sl_calibrate.py`): entry-agnostik, 35.040 bar
15m/pair (1 thn, via chartstore→SQLite), tiap bar = kandidat entry dua arah;
'pemenang' = MFE ≥ 2.5×ATR (TP bot) dalam horizon 16 bar; ukur MAE (gerak
melawan) para pemenang dlm ×ATR. Kuantil-80 = lantai SL yang menyelamatkan
~80% calon pemenang. ±325rb pemenang dianalisis.

**Hasil:** SANGAT konsisten antar 15 pair — q80 1.70–1.98, median **1.78×ATR**;
subset setelah-candle-besar **1.86**; BTC justru paling butuh ruang (1.98).
SL lama 1.5×ATR ≈ q75 → **~25% calon pemenang mati kena SL duluan**.

**Tindakan:** `_sl_floor` default k_atr **1.75×ATR** + k_range 0.5×range candle
tertutup (menangkap kasus candle besar), berlaku utk SL rule-based & Gemini,
tetap dijaga dalam likuidasi. Trade-off jujur: breakeven winrate 37.5%→41%.
Bukti: `data/sl_calibration.json` + kv `sl_calibration` (SQLite).

**Pelengkap (Fix B):** MFE/MAE + exit_reason kini mengalir ke jurnal, decision
log, dan tabel keputusan Gemini; `setup_stats` menghitung sl_hit_rate & avg
MFE-sebelum-SL → refleksi Gemini bisa mendiagnosis "SL kepencet" dgn angka.
