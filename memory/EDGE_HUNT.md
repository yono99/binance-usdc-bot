# Edge Hunt Log — open-ended OOS search

> **Tujuan:** cari edge tradeable di luar antrian H24–H32 / H-CYC yang sudah mati.
> **Hakim:** hanya OOS walk-forward + cost RT 0.18% (+ cost×2 stress).
> **Promosi paper:** oos CANDIDATE + lockbox mean>0 + day-EW oos>0 + cost2x oos>0
> (+ excess vs BTC bila relevan) + n≥30 + p_adj<0.05 + train/OOS tanda konsisten.
> **"Tidak ketemu" = hasil valid.** Jangan klaim edge dari in-sample.

**Mulai:** 2026-07-21  
**Panel default:** `data/snap` daily, ~1153×66 (volume lookback, max_alts≈200, min_bars 400)  
**Harness:** `edge_hunt.py` + skrip putaran

---

## Ringkasan eksekutif

| Putaran | Fokus | CANDIDATE promosi | Top OOS+ (jujur status) |
|---|---|---|---|
| A–F (`edge_hunt.py`) | calendar/ToM, XS rev, dump-bounce, compress, session 1h, dispersion | **0** | high_disp_rev ~+0.42% n=116 p_adj~0.19 NOT_PROVEN |
| Deep (`edge_hunt_deep.py`) | pre-reg param + cost×2 | raw CAND: crash_bounce dd8/dd12 h3 | **gugur** di validasi ketat |
| Crash validate | 50/30/20 + day-EW + cluster + excess BTC | **PROMOTED NONE** | dd15_h3 = WATCHLIST (train−) |
| Volspike | range/vol spike fade | **0** (train≤0) | OOS kuat, train fail |
| Round2 H-EH-24..31 | loser/winner 3d, ST rev LS, lowvol, highvol short, BTC MA, resid mom | **0** | highvol_short_h10 +3.7% n=33 NOT_PROVEN |

**Status global: BELUM ADA EDGE PROMOTE_PAPER.**

---

## Putaran A–F — `edge_hunt.py` → `logs/edge_hunt.json`

### A — Calendar / ToM / DoW
- **Best:** `tom_first3_long_ew` OOS +0.16% n=34 p_adj=1.0 → **NOT_PROVEN**
- Mayoritas DoW **REJECTED** (net cost). Saturday residual lean + tapi tak signifikan.

### B — XS short-term reverse
- Walk-forward XS reverse OOS **+0.24%** n=161 p_adj=1.0 (16 trials) → **NOT_PROVEN**
- Residual reverse vs BTC +0.05% n=305 → **NOT_PROVEN**

### C — Long after BTC dump
- Semua hold 1–7 **REJECTED** (bounce tidak bayar cost / continue dump)

### D — Volatility compression breakout
- compress10/20 break hold1–5 semua **REJECTED**

### E — Time-of-day / session (1h majors bila ada)
- Gross US session ~0; **net semua REJECTED** (cost menghabiskan)

### F — Dispersion regime
- High dispersion → XS reverse: OOS **+0.42%** n=116 p_adj~0.19 → **NOT_PROVEN** (terbaik putaran A–F)
- Low dispersion → mom: **NOT_PROVEN**

---

## Deep dive — `edge_hunt_deep.py` → `logs/edge_hunt_deep.json`

Pre-registered single-param + cost×2. Temuan raw:

| id | OOS mean | n | verdict raw |
|---|---:|---:|---|
| crash_bounce_dd8%_h3 | +1.37% | 808 | CANDIDATE (butuh lockbox) |
| crash_bounce_dd12%_h3 | +5.42% | 279 | CANDIDATE (butuh lockbox) |
| hi_disp / xs_rev / mom / ToM | ~0 atau − | — | NOT_PROVEN / REJECTED |
| cost×2 pada arm tipis | − | — | REJECTED |

---

## Crash bounce strict — `edge_hunt_validate_crash.py`

**Promotion rule:** oos CANDIDATE + lockbox>0 + day-EW oos>0 + cost2x oos>0 + excess_vs_btc>0  
**Cuts:** 50% train / 30% OOS / 20% lockbox  
**Hasil: PROMOTED = []**

Pelajaran utama:
- **Mean trade-level positif** di dump-cluster hari besar = bias: banyak alt jatuh bersama, bounce market-wide, **day equal-weight sering negatif**.
- dd8/dd10/dd12 h3: raw OOS+ tapi **day-EW OOS ≤ 0** → cluster risk, **NO promote**.
- **dd15_h3_c1** (dump ≤−15%, hold 3d):
  - OOS +7.36% n=234, lock +2.67% n=68, day-EW OOS +2.79% n=21, excess vs BTC +5.9%
  - **Train mean −0.72%** → tanda inkonsisten → verdict_oos **NOT_PROVEN**
  - day-EW n=21 kecil; lock day-EW **−1.93%**
  - promotion: **WATCHLIST** saja — **bukan** paper candidate
- Idio-cluster / continuation short ikut diuji di putaran ini → tidak promote.

**Kesimpulan:** crash-bounce OHLCV = **efek cluster / regime**, bukan edge idio yang survivable.

---

## Volspike fade — `edge_hunt_volspike.py` → `logs/edge_hunt_volspike.json`

| arm | train | OOS | catatan |
|---|---:|---:|---|
| fade h1–h5 | **≤0** | +0.96% … +4.1% | OOS menarik, train gagal |
| follow h3 control | ~0 | **−4.5%** | fade > follow di OOS, tetap tak promote |

**Verdict: NOT_PROVEN** — inkonsistensi train/OOS (regime shift). Jangan wire.

---

## Round 2 — `edge_hunt_round2.py` → `logs/edge_hunt_round2.json`

H-EH-24…31, panel 1153×66, cut 70%, cost 0.18%. **CANDIDATES = 0.**

| id | OOS mean | n | verdict |
|---|---:|---:|---|
| loser3d_long h1/3/5 | −0.46…−1.57% | ~340 | REJECTED |
| winner3d_short h1/3/5 | +0.30…+2.08% | ~340 | NOT_PROVEN (p) |
| st_rev_ls h1/3 | − | — | REJECTED |
| st_rev_ls h5 | +0.44% | 68 | NOT_PROVEN |
| lowvol_premium h5/10 | +0.35…+0.71% | 33–68 | NOT_PROVEN |
| highvol_short h5/10 | +1.89…+3.72% | 33–68 | NOT_PROVEN (n/p) |
| btc_ma50/200 long 1d | − | — | REJECTED (net cost) |
| resid_mom20_ls | − / +0.29% | — | REJECTED / NOT_PROVEN |
| st_rev_quiet_btc | ~0 | — | REJECTED / NOT_PROVEN |

**Sinyal arah (bukan edge):** winner-fade & high-vol short lean + di OOS; loser-bounce lean −.  
Butuh n lebih besar + lockbox + Bonferroni ketat sebelum promote — belum.

---

## Round 3 — DVOL / regime (non-OHLCV gate) — `edge_hunt_round3.py`

`logs/edge_hunt_round3.json` · panel 1153×66 · cut 2025-07-21 · **CANDIDATES = 0**

| family | best OOS lean | train | verdict |
|---|---|---|---|
| dvol_hi short alts h5 | +2.06% n=109 | **−2.77%** | NOT_PROVEN (regime) |
| ivrv_hi short alts h5 | +2.13% n=95 | **−0.80%** | NOT_PROVEN |
| hicorr short alts h3 | +1.48% n=78 | **−0.90%** | NOT_PROVEN |
| dvolgate highvol short h5 | +2.78% n=25 | **−2.57%** | NOT_PROVEN |
| euphoria BTC+5/7 short alts | + lean | n OOS 1–5 | INCONCLUSIVE |
| lowcorr ST rev LS | ~0 / +0.57% | ≤0 | REJECTED / NOT_PROVEN |
| dump+dvolhi short alts | negative OOS | — | REJECTED |
| dvol_up short hibeta | n=0 | — | INCONCLUSIVE (beta panel) |
| ivrv_hi **long BTC** | negative | — | REJECTED |

**Pelajaran R3:** short-alts di era markdown OOS terlihat “edge”, train bull **selalu negatif**.
Itu **regime fit**, bukan edge. Hakim `train_mean>0` sudah benar menolak.

---

## Round 4 — dollar-neutral / classic factors — `edge_hunt_round4.py`

`logs/edge_hunt_round4.json` · **CANDIDATES = 0**, soft_promote = []

| id | train | OOS | lock | verdict |
|---|---:|---:|---:|---|
| mom12_1_ls h10/21 | − | +0.19…+0.73% | + | NOT_PROVEN (train−) |
| resid5_rev_ls h1/3 | − | + kecil | + kecil | NOT_PROVEN |
| resid5_rev_ls h5 | − | − | − | REJECTED |
| ratio_z_rev_ls h1–5 | − | +0.05…+0.38% | + | NOT_PROVEN (train−) |
| range_exp_fade h1 | − | − | − | REJECTED |
| range_exp_fade h3 | − | +1.05% | ~0 | NOT_PROVEN |
| long_resid_loser20 | **+** | **−1.9…−3.5%** | − | REJECTED (IS trap) |
| weekend long/short EW | − / − | − / ~0 | − / ~0 | REJECTED / NOT_PROVEN |
| volshock | — | — | — | INCONCLUSIVE (API skip) |

**Pelajaran R4:** satu-satunya train+ (long residual loser) **jelek di OOS** — pola overfit klasik.
Arm netral lain OOS+/lock+ tapi train− = regime markdown, sama seperti R3.

---

## Round 5 — lead-lag / liquidity / down-beta / streak — `edge_hunt_round5.py`

`logs/edge_hunt_round5.json` · **CANDIDATES = 0**  
Satu arm **train+ & OOS+**: `low_idiovol_ls_h10` train +0.41% oos +0.94% n=33 → **NOT_PROVEN** (p/n)

Strict `edge_hunt_validate_idiovol.py` (50/30/20 + cost×2):

| arm | train | OOS | lock | verdict |
|---|---:|---:|---:|---|
| low_idiovol h10 c1 | +0.34% | +1.21% n=34 | +0.16% | NOT_PROVEN (p_adj / trials) |
| low_idiovol h10 c2 | +0.16% | +1.03% | **−0.02%** | lock cost×2 fail |
| low_idiovol h15 c1 | +0.48% | +1.29% n=23 | +1.38% | NOT_PROVEN n/p |
| others | mixed | | | NO |

**PROMOTED: NONE.** Lottery/idvol premium lean ada tapi tak lolos bar.

BTC lead long alts, streak fade/continue, activity premium: **REJECTED** atau OOS+/train−.

---

## Round 6 — breadth / dom-pressure / unlock-filter — `edge_hunt_round6.py`

`logs/edge_hunt_round6.json` · **CANDIDATES = 0**  
Train+ & OOS+ leans: `breadth_lo_rev_ls_h3`, `dom_pressure_short_alts_h1`, `nounlock_st_rev_ls_h3`

Strict `edge_hunt_validate_r6.py`:

| arm | train | OOS (50%) | lock | verdict |
|---|---:|---:|---:|---|
| breadth_lo_rev h3 | +0.63% | **−0.03%** | +0.28% | REJECTED (OOS flip) |
| dom_pressure short h1 | − | +0.25% | − | NOT_PROVEN |
| nounlock ST rev h3 | +0.33% | n=6 only | + | INCONCLUSIVE |

**PROMOTED: NONE.** 70/30 lean hilang di 50/30 lockbox — classic split artifact.

---

## Scoreboard global (2026-07-21)

| Putaran | Arms diuji (approx) | PROMOTE_PAPER |
|---|---:|---|
| A–F | ~46 | 0 |
| Deep + crash validate | ~40 | 0 (dd15 WATCHLIST) |
| Volspike | 11 | 0 |
| R2 | 19 | 0 |
| R3 DVOL/regime | 26 | 0 |
| R4 neutral/RV | 15 | 0 |
| R5 lead/liq/beta | 26 | 0 |
| R6 breadth/unlock | 23 | 0 |
| **Total** | **~200+** | **0** |

**Honest status:** belum ada edge paper-eligible di panel daily snap.  
Pola berulang: OOS markdown short-alts / highvol short **train−**; train+ sering **OOS−** atau n/p gagal.

---

## Antrian berikutnya

1. ~~R3–R6~~ done, 0 promote
2. **True dollar volume** dari OHLCV snap (bukan activity proxy)
3. **Multi-day non-overlap portfolio** sim (capacity / turnover)
4. **Funding hist** hanya jika cache panel ada + konstruk beda H15/24/25
5. **1h majors** microstructure proxy (bukan session long yang sudah D)
6. **Meta:** ensemble *filter* (bukan entry) — block entries only; ukur risk A/B bukan exp_R
7. Stop pure OHLCV retread; prioritaskan data non-price bila tersedia

---

## Artefak

| File | Isi |
|---|---|
| `edge_hunt.py` | harness load_daily, pack, verdict_arm, rounds A–F |
| `edge_hunt_deep.py` | deep single-param |
| `edge_hunt_validate_crash.py` | promotion ketat crash-bounce |
| `edge_hunt_volspike.py` | volspike fade |
| `edge_hunt_round2.py` … `round6.py` | H-EH-24…65 |
| `edge_hunt_validate_idiovol.py` / `validate_r6.py` | strict promotion |
| `logs/edge_hunt*.json` | hasil numerik (di-commit via gitignore exception) |

---

*Update file ini tiap putaran. Klaim edge hanya lewat baris PROMOTE_PAPER.*
