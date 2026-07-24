# Multi-family edge search — kompetitif tapi disiplin

> Respons atas: “satu family saja kurang kompetitif — pikirkan ide masuk akal lagi.”  
> Harness: `research/edge_hunt_multifamily.py`  
> Log: `logs/edge_hunt_multifamily.json`  
> **2026-07-24**

---

## 1. Filosofi (yang Anda minta)

| Bukan | Ya |
|---|---|
| Satu jalur tipis | **Portofolio family** paralel |
| 200 parameter EMA | **Sedikit arms / family**, beda **struktur** |
| Klaim dari 1 lean | Scoreboard + **stress majors vs broad** |
| Auto-wire | Bar ketat + caveat jujur |

**6 family pra-registrasi:**

| ID | Ide | Universe | Arms |
|---|---|---|---:|
| **F1** | Short pullback di **bear** | majors+large | 4 |
| **F2** | Long pullback di **bull** | majors+large | 4 |
| **F3** | Short pullback di **bear** (replikasi) | broad liquid 25 | 2 |
| **F4** | Residual-z fade vs BTC (XS basket) | panel majors | 4 |
| **F5** | Long BB breakout di **bull** | majors+large | 3 |
| **F6** | Meta: skip long EW saat breadth rendah | panel | 1 filter |

Bar entry: train>0 · OOS CANDIDATE (p_adj family) · lockbox>0 · cost×2 OOS>0 · cost 0.18%.

---

## 2. Scoreboard

| Family | Hasil formal | Catatan jujur |
|---|---|---|
| **F1 short_pb_bear_majors** | **0 promote** | OOS tipis / **lockbox −** — gagal di “majors” |
| **F2 long_pb_bull_majors** | **0 promote** | train+OOS+lock+ di 3 arms tapi **p_adj gagal** |
| **F3 short_pb_bear_broad** | **1 formal PROMOTE** (lihat §3) | Sama lean meme/broad; **bukan** majors |
| **F4 resid_fade** | **REJECTED** semua | mean train & OOS − |
| **F5 breakout_bull** | **0 promote** | lean+ OOS/lock, p_adj gagal |
| **F6 breadth skip** | META_ONLY | mean filter agak naik; bukan entry alpha |

```
ENTRY PROMOTED (script formal): F3_short_adx22_sl1.5_rr1.5_t1.0
FILTER PROMOTED: NONE
```

---

## 3. Detail F3 — formal lolos, **promosi operasional DITAHAN**

| Field | A1 (promote formal) | A2 |
|---|---|---|
| params | adx22 sl1.5 rr1.5 touch1.0 short bear | adx22 sl1.5 rr2.5 |
| n total | 185 | 183 |
| train mean R | +0.049 | +0.039 |
| OOS mean R | **+0.293** n=56 | +0.286 n=55 |
| lock mean R | **+0.240** | +0.351 |
| cost×2 OOS | + (lolos bar skrip) | + |
| p_adj | **0.044** (trials=2) | 0.082 |
| Script promo | **PROMOTE_PAPER** | NO |

### Kenapa **tidak** langsung wire / full PROMOTE proyek?

1. **F1 majors gagal lockbox** — edge **tidak** robust di aset lebih “bersih”.  
2. Broad 25 = banyak **meme/small-cap** (sama isu sebelumnya).  
3. Family trials **turun ke 2** (replikasi) → p_adj lebih longgar dari uji 4-arm sebelumnya (p_adj 0.088).  
4. Multiple family diuji paralel → klaim global harus lebih hati-hati.  
5. Kontrak proyek: wire hanya setelah sadar + paper; formal skrip ≠ auto-deploy.

**Label operasional jujur:**

```
FORMAL_CANDIDATE_F3 (script bar)
OPERATIONAL_STATUS = HOLD_NO_WIRE
WATCHLIST_STRONG = short_pb_bear_broad A1
NEXT_GATE = pure majors-only (BTC ETH SOL BNB XRP AVAX LINK … tanpa 1000*)
            dengan arms beku A1; bila lock gagal lagi → RETIRE
```

---

## 4. Lean lain (kompetitif, belum edge)

| Rank | Family | OOS mean R | n | lock | p_adj | status |
|---:|---|---:|---:|---:|---:|---|
| 1 | F3 short broad | +0.29 | 56 | +0.24 | 0.044 | formal CAND / HOLD wire |
| 2 | F3 short broad rr2.5 | +0.29 | 55 | +0.35 | 0.082 | NOT_PROVEN |
| 3 | F5 breakout bull | +0.17 | 179 | +0.40 | 0.23 | lean |
| 4 | F5 breakout | +0.15 | 171 | +0.39 | 0.20 | lean |
| 5 | F2 long bull pb | +0.13 | 162 | +0.13 | 0.38 | lean |

**F2 & F5** = pipeline bull yang **kompetitif** (n besar, lock+) tapi belum signifikan.  
**F4** mati. **F1** = short bear **tidak** jalan di majors.

---

## 5. Pelajaran strategis

1. **Multi-family benar:** short-bear **bukan** universal; long-bull pullback/breakout juga “main” di scoreboard.  
2. **Universe = setengah edge:** broad/meme ≠ majors.  
3. **Satu formal promote di small-cap bear short** = menarik, **bukan** tiket live.  
4. Kompetisi ide ≠ longgarkan bar.

---

## 6. Rencana lanjut (masuk akal, berjenjang)

| # | Aksi | Tujuan |
|---|---|---|
| 1 | **HOLD wire** F3 | Anti self-promote |
| 2 | **Gate A:** A1 beku di pure majors (8–12 coin, no 1000*) | Bunuh / sahkan |
| 3 | **Gate B:** F2/F5 family ketat (≤3 arms) lockbox only | Bull side |
| 4 | Paper dry survival tetap | Bukan diganti F3 |
| 5 | Non-OHLCV nanti | Setelah OHLCV family habis jujur |

---

## 7. Satu kalimat

> Multi-family menemukan **satu kandidat formal** (short pullback bear di broad) dan **banyak lean bull** yang belum signifikan; **majors short gagal** → kompetitif, tapi **belum edge siap trade**; wire ditahan sampai gate majors murni.
