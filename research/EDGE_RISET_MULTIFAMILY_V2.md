# Multi-family v2 — deepthink (kompetitif, beda struktur)

> Respons: “pikirkan ide lagi agar lebih kompetitif.”  
> Bukan remix ADX/SL dari F1–F6.  
> Harness: `research/edge_hunt_multifamily_v2.py`  
> Log: `logs/edge_hunt_multifamily_v2.json`  
> **2026-07-24**

---

## 0. Deepthink — gap F1–F6 & ide baru

| Gap F1–F6 | Ide v2 | Cerita ekonomi |
|---|---|---|
| Hampir semua single-name pullback/breakout | **G1** BTC lead → alt catch-up | Risk-on lag / beta delay |
| Tidak ada ranking quality | **G2** quality momentum LS (ret/vol) | XS factor klasik, dollar-neutral |
| Breakout tanpa squeeze | **G3** squeeze + break + BTC regime | Vol compression → expansion |
| F4 = residual **fade** mati | **G4** residual **follow** (continuation) | Relative strength berlanjut |
| Tidak ada exhaustion | **G5** vertical move fade | Overextension 1–2d |
| LINK pair only (lama) | **G6** multi-pair resid z (ETH/SOL/BNB/XRP/AVAX/LINK vs BTC) | Pairs book |
| F3 broad curiga meme | **G7** short_pb_bear di **pure majors** | Bunuh / sahkan F3 |

Universe G1–G6: **28 pure majors** (allowlist, no `1000*`).  
Bar: 50/30/20 · cost 0.18% · cost×2 · train>0 · OOS CANDIDATE · lock>0.

---

## 1. Scoreboard v2

| Family | Formal promote | Verdict ringkas |
|---|---|---|
| **G1** BTC lead-lag | 0 | train+ OOS tipis · lock sering − · NOT_PROVEN |
| **G2** quality mom LS | **3 arms** | **CANDIDATE + lock tipis+ + cost2x+** |
| **G3** squeeze break | 0 | bullL OOS CAND tapi **lock −** |
| **G4** resid mom follow | 0 | OOS CAND kuat · **lock −** → NO |
| **G5** vertical fade | 0 | **REJECTED** |
| **G6** pair resid | 0 | **REJECTED** |
| **G7** pure majors short pb bear | 0 | **REJECTED** (OOS −) → **F3 tidak survive majors** |

```
ENTRY PROMOTED (script):
  G2_qmom_h10_q0.3
  G2_qmom_h10_q0.2
  G2_qmom_h5_q0.3
```

---

## 2. Detail G2 — quality momentum (pemenang kompetisi v2)

**Konstruk:** tiap hari skor = `mean_ret_20 / (std_ret_20 + ε)`  
Long top quantile, short bottom quantile, hold H, cost RT 0.18% pada LS.

| Arm | train mean | OOS mean | n OOS | lock mean | cost2x OOS | p_adj | promo |
|---|---:|---:|---:|---:|---:|---:|---|
| h10 q0.3 | +0.0175 | **+0.0123** | 660 | **+0.00094** | +0.0105 | ~0 | **PROMOTE_PAPER** |
| h10 q0.2 | +0.0214 | **+0.0102** | 660 | **+0.00063** | +0.0084 | ~0 | **PROMOTE_PAPER** |
| h5 q0.3 | +0.0093 | **+0.0062** | 662 | **+0.0025** | +0.0044 | ~0.001 | **PROMOTE_PAPER** |
| h5 q0.2 | +0.0096 | +0.0044 | 662 | +0.0045 | +0.0026 | 0.11 | NOT_PROVEN |

### Yang kuat
- **n sangat besar** (~660 OOS) → bukan sample tipis F3  
- Train / OOS **searah +**  
- **p_adj sangat kecil** (bukan borderline 0.04)  
- Cost×2 OOS tetap +  
- Universe **pure majors** (bukan meme broad)

### Yang menahan wire (operasional)

1. **Lockbox mean sangat tipis** (0.06–0.25% per rebalance period) — lolos bar `>0` tapi **economic margin kecil**.  
2. LS = dua sisi; model cost 0.18% **sekali** per day-book mungkin **understate** (ideal: ~2 leg). Stress cost×2 membantu, belum full microstructure.  
3. Multi-family v1+v2 = banyak uji global — formal family-Bonferroni OK, **klaim proyek tetap hati-hati**.  
4. Belum paper forward param beku.

**Label operasional:**

```
FORMAL_CANDIDATES = G2 quality_mom (3 arms)
BEST_OPERATIONAL_FOCUS = G2_qmom_h10_q0.3  (OOS + lock + n)
OPERATIONAL_STATUS = PAPER_SHADOW_CANDIDATE (bukan live, bukan full size)
WIRE_FORWARDTEST = NO (until paper shadow n sufficient)
```

---

## 3. G7 vs F3 — putusan tegas

| | F3 broad | G7 pure majors |
|---|---|---|
| Short pb bear adx22 sl1.5 rr1.5 | formal CAND (HOLD) | **OOS −0.064 REJECT** |
| rr2.5 | lean | **OOS − REJECT** |

**Kesimpulan:** edge short-pullback-bear **tidak** ada di pure majors.  
F3 formal = **small-cap/meme regime artifact**.  
**Operasional: RETIRE F3 sebagai entry kandidat. Jangan wire.**

---

## 4. Lean lain (kompetitif, gagal promote)

| Arm | OOS | lock | kenapa NO |
|---|---:|---:|---|
| G4 resid mom h10 | +0.039 | **−0.016** | lock gagal |
| G3 squeeze bull h5 | +0.033 | **−0.017** | lock gagal |
| G4 h5 | +0.014 | **−** | lock gagal |
| G1 lead-lag | +0.00x | **−** | tipis |

Pola: beberapa OOS “CANDIDATE” **hancur di lockbox** — hakim 50/30/20 bekerja.

---

## 5. Portofolio kompetitif (setelah deepthink)

| Tier | Isi | Aksi |
|---|---|---|
| **A — Formal + n besar** | **G2 quality mom LS** (h10 q0.3 / q0.2 / h5 q0.3) | Paper **shadow** only; param beku; ukur 4–8 minggu |
| **B — Lean bull struktur** | G3 squeeze bull, G4 resid follow | HOLD; jangan promote sampai lock+ |
| **C — Retired** | F3 short bear broad, G5 fade, G6 pairs, G7 short majors | **Jangan** retread |
| **D — Ops** | risk_filter shadow + CE + dry survival | Tetap jalan terpisah |

---

## 6. Langkah berikutnya (masuk akal)

1. ~~**Bekukan G2** `h10_q0.3`~~ ✅  
2. ~~Paper shadow~~ ✅ `research/g2_quality_mom_shadow.py` · [G2_SHADOW.md](G2_SHADOW.md)  
   - Backfill 2348 settled · OOS mean R ≈ **+0.56%** n=705 · lock ≈ **+0.06%** · health `OK_POSITIVE_OOS_LOCK` · **wire=false**  
3. Periodic: `python research/g2_quality_mom_shadow.py --once`  
4. **Jangan** buka grid baru di quality mom.  
5. Live: **tetap dilarang** sampai paper + n + human review.

---

## 7. Satu kalimat

> Deepthink v2 mengalahkan “satu family tipis”: **quality momentum LS di pure majors** lolos bar formal dengan n besar; **short-bear F3 mati di majors**; wire ditahan karena lock tipis + cost model — **kandidat paper-shadow kompetitif, bukan mesin cuan**.
