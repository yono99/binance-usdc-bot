# Audit Full Strategy Engine + Roadmap (termasuk solidifikasi G2)

> **Tujuan pemilik:** edge **sinyal entry** · audit mesin penuh · kembangkan & padatkan G2.  
> **Bukan** klaim “sudah ada edge siap scale.”  
> **Dasar:** kode runtime (`ForwardTester` + mixins), [ARCHITECTURE.md](../ARCHITECTURE.md),  
> [ENTRY_PIPELINE.md](../ENTRY_PIPELINE.md), riset multi-family v2, G2 shadow/entry.  
> **Tanggal audit:** 2026-07-24

---

## 0. Ringkas eksekutif

| Pertanyaan | Jawaban audit |
|---|---|
| Apakah repo punya **full strategy engine**? | **Ya (ops + eksekusi)**; **parsial (alpha entry)** |
| Apa yang solid? | Risk HARD, dual dry/live, fail-open LLM, journal, CE pondasi, riset terpisah |
| Apa yang lemah? | Sumber arah OHLCV belum PROMOTE; banyak overlay shadow; G2 entry ≠ G2 research 1:1 |
| G2 sekarang | Formal bagus di **LS book**; runtime = **overlay** · shadow ON · block OFF |
| Arah pengembangan | (1) solidkan G2 sebagai **modul entry** berjenjang · (2) rapikan pipeline · (3) jangan campur 10 overlay tanpa A/B |

**Satu kalimat:**  
Mesin **menjalankan dan menahan risiko** sudah matang; mesin **menemukan arah +EV** masih lab — G2 adalah kandidat terbaik untuk dipadatkan jadi jalur entry, dengan jembatan research→runtime yang lebih setia.

---

## 1. Peta full strategy engine (as-is)

### 1.1 Lapisan (dari data ke close)

```
┌─────────────────────────────────────────────────────────────────┐
│ L0 DATA                                                          │
│  exchange OHLCV · snap pkl · alt (funding/OI/CVD) · news        │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ L1 UNIVERSE                                                      │
│  perp discover · prefilter volume · screener ATR/spread          │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ L2 SIGNAL (sumber ARAH)  ← "otak entry"                          │
│  A) rules evaluate / signals_v8                                  │
│  B) Gemini trader (bila technique=gemini & bukan manager override)│
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ L3 HARD RISK (boleh memblokir)                                   │
│  enabled · circuit daily · max trades · max open · DD lock       │
│  news veto · corr conflict · (opsional) risk_filter block        │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ L4 SOFT / SHADOW (ukur dulu; default tak hard-block)             │
│  risk_filter shadow · ReAct ab_shadow · entry_confluence shadow  │
│  G2 entry shadow · planner enforce (ketatkan) · MTF/VRP shadow   │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ L5 SIZE & STANCE                                                 │
│  conf tier · CE-STANCE size-down · bet/lev · SL/TP ATR           │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ L6 EXECUTION                                                     │
│  paper fill model · live limit/pending · fee/slip                │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ L7 LIFECYCLE                                                     │
│  monitor SL/TP · close · decision_log outcome · trade_review     │
│  lessons · CE live stop · equity journal                         │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Apa yang “full” vs “belum full”

| Domain | Status | Catatan |
|---|---|---|
| **Execution engine** | Full | dry + live, lock per-mode, PM2 |
| **Risk engine** | Full (keras) | DD, slots, news, fail-open LLM |
| **Signal engine (alpha)** | **Belum solid** | Rules OHLCV historis impas OOS; PROMOTE_PAPER entry ≈ 0 s/d G2 formal LS |
| **Research → runtime bridge** | Lemah | Edge hunt di `research/` jarang 1:1 dengan gerbang entry |
| **Learning loop** | Ada, di bawah pondasi | trade_review + lessons; bukan auto-edge |
| **G2** | Hybrid | Research LS solid-ish · runtime overlay baru · belum A/B entry |

---

## 2. Audit per lapisan (temuan)

### L0–L1 Data & universe — **BAIK / cukup**

**Plus**
- Snap all-time + volume screen untuk riset  
- Screener runtime selaras likuiditas  
- Dual settle USDT riset / USDC eksekusi disadari  

**Gap**
- Rank quality G2 butuh **panel harian segar** di server; stale snap → rank basi  
- Screener 15m/5m tipis untuk scalp “penuh”  
- Tidak ada pipeline otomatis “setiap hari update snap majors” terikat G2  

**Saran**
1. Job harian: refresh `data/snap` pure majors 1d (bukan 500 pair)  
2. G2 `refresh` gagal → log metrik `g2_stale` di status API  

---

### L2 Signal (arah) — **INTI KELEMAHAN ALPHA**

**Plus**
- Dua jalur: rules deterministik vs Gemini (dengan throttle/cache)  
- Manager-mode bisa matikan Gemini arah (Jalan A)  

**Gap**
1. **Rules v4/v8** = kombinasi OHLCV yang program riset lama sudah **tidak** promote sebagai edge  
2. **Gemini** = penalaran/context, **bukan** sumber edge terbukti OOS  
3. Tidak ada modul “strategy registry” dengan arm_id beku (G2, H28, …) sebagai first-class signal  
4. G2 **tidak** generate entry sendiri — hanya filter  

**Saran pengembangan (prioritas tinggi untuk tujuan edge entry)**

| P | Kerja | Alasan |
|---|---|---|
| P0 | **Strategy registry** | `arm_id`, params frozen, mode shadow\|active\|off |
| P0 | **G2 full-book paper path** | Setia research (LS rebalance 10d) — hakim edge yang sama |
| P1 | **G2 entry overlay A/B** | aligned vs misaligned R (sudah mulai shadow) |
| P2 | Kurangi ketergantungan “rules always on” | Bila G2 book lolos paper, rules jadi secondary |

---

### L3 HARD risk — **SOLID (pertahankan)**

**Plus**
- Circuit, DD lock, slots, news, corr  
- Fail-open LLM (tak pernah blokir karena API mati)  
- Dual dry/live lock  

**Gap**
- Banyak flag di YAML/UI → drift posture (manager, loss%, dll.) — sudah pernah kejadian  

**Saran**
1. **Posture snapshot** tiap deploy di status/API (satu JSON “effective gates”)  
2. Checklist otomatis: 1 bot · g2 shadow · risk_filter block off · CE mode  

---

### L4 Soft overlays — **TERLALU BANYAK / under-measured**

Saat ini paralel: risk_filter, ReAct, EC, G2, planner, VRP, MTF, CE, lessons…

**Risiko**
- Multiple gates → 0 trade atau log bising  
- Sulit atribusi: edge dari mana?  
- Beberapa shadow tidak pernah di-promote/retire formal  

**Saran (disiplin kompetitif)**
1. **Maks 2–3 overlay aktif diukur** per fase (sekarang: risk_filter + **G2** + CE)  
2. Lainnya `off` atau arsip sampai A/B selesai  
3. Tiap overlay wajib: log action · join outcome R · verdict KEEP/BLOCK/RETIRE  

---

### L5–L6 Size & execution — **BAIK untuk paper/mikro**

**Plus**
- CE size-down pondasi  
- SL fixed (allow_move_sl false) — selaras pemilik  
- Live path terpisah  

**Gap untuk G2 full book**
- Bot single-symbol sequential ≠ rebalance basket LS  
- Cost 1 kaki entry rules ≠ 2 kaki LS research  

**Saran**
- Path **paper portfolio** terpisah untuk G2 book (bukan paksakan ke `_open_usd` 1 simbol)  
- Atau: entry overlay tetap 1 simbol, tapi **scoring** pakai rank yang sama dengan book  

---

### L7 Lifecycle & learning — **BAIK, batasi klaim**

**Plus**
- decision_log, trade_review, lessons, CE report  

**Gap**
- Join G2 stamp → outcome R belum satu perintah `g2_entry_report.py`  
- Lessons bisa noise vs pondasi (sudah ada conflicts_foundation)  

**Saran**
1. `python research/g2_entry_ab_report.py` — aligned vs misaligned R dari decision_log + closes  
2. Jangan inject “G2 is edge” ke lessons otomatis  

---

## 3. Audit G2 khusus — “bagus → lebih cocok & solid”

### 3.1 Apa yang sudah bagus

| Aspek | Bukti |
|---|---|
| Cerita ekonomi | Quality = return/risk cross-section — klasik, beda pullback tunggal |
| Universe | Pure majors (bukan meme F3) |
| Statistik formal | n OOS ~660, p_adj ketat, train/OOS+, cost×2+ |
| Stress negatif | F3/G7 short-bear **mati** di majors — hakim jujur |
| Shadow book | Counterfactual jsonl + report |
| Entry overlay | Wire shadow di dry tanpa hard-block |

### 3.2 Ketidakcocokan research ↔ runtime (harus diperbaiki agar “sempurna solid”)

| Research LS book | Runtime overlay sekarang | Risiko |
|---|---|---|
| Buka **bersamaan** long basket + short basket | Hanya cek 1 simbol vs rank | Bukan strategi yang sama |
| Hold **10 hari** rebalance | Rank dipakai **tiap entry rules** (TF 15m) | Lookahead/regime mismatch |
| Skor di **close harian** | Entry di bar rules (sering 15m) | Sinyal “harian” di-trigger “intraday” |
| Cost 0.18% pada **LS day-book** | Paper trade fee path rules | R tidak apple-to-apple |
| Netral dollar | Arah ikut rules (bisa long-only bias) | Edge LS bisa hilang |

**Kesimpulan audit G2:** formal bagus untuk **book**; overlay bagus untuk **eksperimen entry**;  
belum solid sebagai **satu kebenaran strategi** sampai ada path yang meniru research ATAU spek entry yang diuji ulang OOS.

### 3.3 Definisi “G2 solid” (acceptance criteria)

G2 baru boleh disebut **solid untuk entry** jika **semua** terpenuhi:

| # | Kriteria | Status sekarang |
|---|---|---|
| 1 | Params frozen + arm_id di config | ✅ h10 q0.3 |
| 2 | Rank pipeline deterministic + stale detect | ⚠️ TTL 30m; stale snap belum alarm |
| 3 | **Path A:** paper **full book** LS 10d = research | ❌ belum |
| 4 | **Path B:** overlay entry — A/B aligned vs misaligned R, n≥50 misaligned | ❌ baru shadow |
| 5 | Cost model jujur (2-leg bila LS; 1-leg bila overlay) | ⚠️ |
| 6 | Fail-open + tests | ✅ unit dasar |
| 7 | `block=true` hanya setelah Path B lolos | ✅ masih false |
| 8 | Tidak double-count multi-testing retune | ✅ jangan retune |

---

## 4. Roadmap pengembangan (prioritas)

### Fase S0 — Stabilisasi (1–3 hari) ✅ sebagian

- [x] G2 entry module + config shadow  
- [x] Counterfactual book script  
- [ ] Status API: `g2_ranks_asof`, `g2_universe_n`, shadow counts  
- [ ] Job refresh snap majors 1d harian  

### Fase S1 — Dua jalur G2 · **SELESAI ukur 2026-07-24**

Detail angka: [G2_S1_RESULTS.md](G2_S1_RESULTS.md)

| Path | Skrip | Verdict |
|---|---|---|
| **A full book** | `g2_book_paper.py` | **`PATH_A_PASS_PAPER_BOOK`** |
| **B entry A/B** | `g2_entry_ab_report.py` | **`PATH_B_LEAN_POSITIVE`** (Δ+0.40R; mean aligned masih −) |

**Keputusan cabang (terisi):** Path A lolos · Path B lean → G2 = **book engine paper OK** + **filter entry yang membantu relatif**; rules tetap −EV; **`block=false`**; jangan live scale.

### Fase S2 — Rapikan full engine · **paralel**

1. **Strategy registry** (`bot/strategies/` atau `bot/registry_arms.py`)  
   - `g2_qmom`, `rules_v8`, `gemini` — flag shadow/active  
2. **Kurangi overlay** ke set terukur (G2 + risk_filter + CE)  
3. **ENTRY_PIPELINE.md** update: sisipkan G2 di diagram L4  
4. Satu perintah: `python research/engine_audit_report.py` (posture + G2 health + last R)  

### Fase S3 — Hanya jika S1 lolos

- `g2_entry.block: true` **atau** aktifkan Path A sizing mikro paper  
- Live: checklist + size mikro — **bukan** full notional  

---

## 5. Saran “full strategy engine” ideal (target arsitektur)

Bukan rewrite total — **rapikan kontrak antar layer**:

```
research/ (OOS, promote)
     │ arm_id + frozen params + cost model
     ▼
bot/strategies/<arm>.py   ← generate Signal{side, conf, horizon, meta}
     │
     ▼
bot/pipeline.py           ← HARD → strategy → overlays → size → exec
     │
     ▼
forward mixins (tetap)    ← open/close/status
```

**Prinsip**
1. **Satu arm = satu file spek + satu implement + satu report**  
2. Research metrics **harus** bisa dihitung ulang dari runtime logs  
3. Overlay tidak boleh diam-diam jadi signal tanpa arm_id  
4. PROMOTE_PAPER / PROMOTE_FILTER / PAPER_SHADOW = status di registry, bukan feeling  

---

## 6. Prioritas kerja konkret (urutan disarankan)

| Urutan | Deliverable | Mengunci apa |
|---:|---|---|
| 1 | `g2_entry_ab_report.py` (aligned vs misaligned dari decision_log) | Path B measurable |
| 2 | `g2_book_paper.py` daemon/skrip rebalance 10d (Path A) | Full engine G2 setia research |
| 3 | Daily snap refresh majors | Rank tidak basi |
| 4 | Status/API G2 fields | Observability |
| 5 | Registry + matikan overlay non-A/B | Signal-to-noise engine |
| 6 | Keputusan block/retire G2 | Solid / stop |

---

## 7. Yang **tidak** disarankan

- Retune `top_q` / lookback karena 1 minggu paper  
- `block=true` hari ini  
- Campur G2 + 5 shadow lain tanpa report  
- Klaim “full strategy edge” hanya dari formal backtest LS  
- Hidupkan lagi short-bear F3  

---

## 8. Jawaban langsung ke pemilik

### “Full strategy engine seperti apa?”
Mesin ujung-ke-ujung: **data → universe → sinyal arah → risk → size → eksekusi → belajar**.  
Repo Anda **sudah full di risk+eksekusi+ops**; **belum full-solid di sinyal entry terbukti**.

### “G2 bagus — biar lebih cocok & sempurna solid?”
1. Akui dua wujud: **book engine** vs **entry filter** — ukur keduanya.  
2. Samakan data (daily close majors), cost, horizon.  
3. Jangan promote block sebelum Path B (atau Path A jika book).  
4. Masukkan G2 ke **registry strategi**, bukan forever “patch di forward.py”.  
5. Matikan noise overlay lain saat G2 diuji.

### “Audit + saran pengembangan?”
Prioritas: **ukur G2 dua jalur → rapikan pipeline → baru scale.**  
Survival dry + CE + risk_filter shadow tetap; **inti R&D entry = G2 solidification.**

---

## 9. Referensi kode & artefak

| Artefak | Isi |
|---|---|
| `bot/forward.py` + mixins | Full runtime engine |
| `bot/g2_entry.py` | Overlay entry G2 |
| `research/g2_quality_mom_shadow.py` | Counterfactual book |
| `research/EDGE_RISET_MULTIFAMILY_V2.md` | Bukti formal G2 |
| `research/G2_ENTRY_ADAPT.md` | Adaptasi entry |
| `ENTRY_PIPELINE.md` | Urutan gerbang (perlu update G2) |

---

*Audit ini = spek kerja, bukan auto-implement semua fase. Fase S1 Path A/B adalah pekerjaan berikutnya yang paling berdampak untuk tujuan edge entry.*
