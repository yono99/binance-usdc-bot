# Sistem Riset Strategi + Gemini Co-Pilot

Dokumen ini menjelaskan **mesin riset strategi otonom** dan **Gemini sebagai
co-pilot riset**. Tujuannya: menemukan **sumber edge yang struktural baru** —
bukan kombinasi baru indikator OHLCV lama — dan menilainya secara jujur lewat
walk-forward out-of-sample (OOS).

> **Prinsip tunggal:** OOS walk-forward adalah **satu-satunya hakim**. In-sample
> positif tidak berarti apa-apa. Gemini hanya **menafsirkan & mengusulkan** —
> tidak pernah memutuskan, tidak pernah merekomendasikan live.

Lihat juga: [METHODOLOGY.md](METHODOLOGY.md) (asumsi/biaya/temuan v1–v4) ·
[RESEARCH_LOG.md](RESEARCH_LOG.md) (log tiap siklus hipotesis).

---

## 1. Loop riset (per siklus)

```
HYPOTHESIZE  →  IMPLEMENT  →  BACKTEST (OOS)  →  EVALUATE (deterministik)  →  REPORT
     ▲                                                                          │
     └──────────────────  Gemini co-pilot mengusulkan hipotesis berikut  ◄──────┘
```

1. **HYPOTHESIZE** — pilih sumber sinyal yang **belum** diuji; nyatakan rasional
   ekonomi + apa yang **memfalsifikasinya**. Tiap hipotesis harus struktural berbeda
   (bukan varian EMA/RSI/MACD).
2. **IMPLEMENT** — tambah strategi baru ke `bot/strategy_lab.py` (terisolasi, tidak
   mengubah v1–v4); ambil data baru di `bot/altdata.py`.
3. **BACKTEST** — walk-forward via `optimize.py` (train in-sample → test OOS → geser).
4. **EVALUATE** — verdict **deterministik** (`bot/copilot.py: verdict()`):
   - **REJECTED** bila OOS exp_R ≤ 0.
   - **WEAK** bila positif tapi gagal salah satu gerbang.
   - **CANDIDATE** hanya bila SEMUA terpenuhi: exp_R > 0.05R · n ≥ 30 · ≥3 window positif ·
     konsisten ≥3 simbol · **DAN lolos signifikansi statistik** — bootstrap blok satu-sisi
     (H0: E[R]≤0) dengan **koreksi Bonferroni atas jumlah trial kumulatif**, plus
     **effective-n ≥ 30** (mengoreksi trade berkorelasi). Lihat `bot/stats.py`.
5. **REPORT** — tulis temuan ke `RESEARCH_LOG.md` (hipotesis | rasional | hasil OOS |
   verdict | langkah berikut). "Tidak ditemukan" = hasil valid & penting.

### Ambang promosi ke live (tidak bisa dilonggarkan)
CANDIDATE = `exp_R > +0.05R` **DAN** n≥30 **DAN** ≥3 window positif **DAN** ≥3 simbol konsisten
**DAN** lolos signifikansi (bootstrap Bonferroni + effective-n≥30) **DAN** parameter stabil ≥50%.
Bahkan CANDIDATE **tidak** langsung live — wajib lewat cost-stress, lockbox, lalu paper (§7).
Semua di-enforce di **kode** (`copilot.verdict` + `bot/stats.py`), bukan dipercayakan ke AI.

---

## 2. Gemini sebagai Co-Pilot (`bot/copilot.py`)

Gemini berperan sebagai **partner riset**, memakai `GeminiClient` yang sama (rotasi
banyak API key, fallback antar-model) dengan layer veto/news. Dua tugasnya:

1. **Menafsirkan** hasil OOS dalam bahasa natural — kenapa menang/kalah, divergensi
   antar-simbol, tanda overfit (IS bagus tapi OOS jelek, atau n<30 per window).
2. **Mengusulkan** SATU hipotesis berikutnya yang **struktural berbeda** dari yang
   sudah diuji, lengkap rasional ekonomi + pemfalsifikasi.

### Guardrail (di-enforce di kode, BUKAN dipercayakan ke AI)

| Guardrail | Mekanisme |
|---|---|
| Verdict adalah otoritas tunggal | `verdict()` menghitung CANDIDATE/WEAK/REJECTED dari metrik OOS; Gemini **tidak** bisa mengubahnya. |
| Signifikansi statistik | CANDIDATE wajib lolos bootstrap blok (Bonferroni atas trial kumulatif) + effective-n ≥ 30 — bukan sekadar tanda exp_R>0.05. Pertahanan inti melawan multiple-testing (`bot/stats.py`, `tests/test_stats.py`). |
| Stabilitas parameter | CANDIDATE wajib parameter MODAL konsisten ≥50% antar-window; loncat-loncat = overfit → WEAK. |
| Reproducibility & lockbox | `--snapshot-dir` (OHLCV bit-for-bit + hash) & `--holdout-frac`/`--lockbox` (ekor histori tak tersentuh untuk ujian final) — `bot/dataset.py`. |
| Cost-stress | `--stress-mult 2` melipatgandakan fee+slippage; edge yang nyata harus bertahan. |
| Larangan live | Bila verdict ≠ CANDIDATE, output distempel `Live trading: DILARANG` apa pun kata Gemini. |
| Fail-open | Tanpa `GEMINI_ENABLED`/keys, co-pilot tetap memberi interpretasi deterministik + usul sumber belum-teruji dari registry (degradasi anggun). |
| Anti salah-ulang (registry) | `bot/registry.py` = **sumber kebenaran tunggal**, dicatat OTOMATIS tiap walk-forward. Gemini hanya boleh memilih `next_source_tag` dari **tag yang belum diuji**; usul yang menyentuh sumber sudah-teruji **ditolak & diganti otomatis** (`is_duplicate`, deterministik — bukan NLP). Ini menutup bug "Gemini mengusulkan ulang cascade". |
| Anti-leakage | `tests/test_no_lookahead.py`: racuni bar masa depan → assert output masa lalu tak berubah; menangkap lookahead/kebocoran di semua transform & sinyal. |
| Output terstruktur | Gemini wajib balas JSON; parse gagal → fallback, tidak pernah memblokir. |

### Output co-pilot
`StrategyCopilot.advise(cycle)` mengembalikan dict:
`verdict`, `verdict_reason` (deterministik), `interpretation`, `overfit_risk`,
`next_hypothesis`, `economic_rationale`, `falsifier`, dan `live_trading` (bila terkunci).

---

## 3. Cara pakai

### Jalankan satu siklus dengan co-pilot
```bash
# aktifkan Gemini dulu (.env): GEMINI_ENABLED=true, GEMINI_API_KEYS=key1,key2
python optimize.py --strategy v5 \
  --symbols "BTC/USDC:USDC" "ETH/USDC:USDC" "SOL/USDC:USDC" \
  --bars 5000 --train 1000 --test 300 --min-trades 30 --copilot
```
Tanpa `--copilot` → hanya tabel walk-forward biasa. Tanpa Gemini key → co-pilot tetap
jalan dengan interpretasi deterministik (tanpa narasi natural-language).

### Argumen relevan
| Arg | Arti |
|---|---|
| `--strategy v5` | strategi cross-exchange basis (lihat §5) |
| `--basis-z 1.5 2.0 2.5 3.0` | grid ambang \|z-score\| basis untuk entry (v5) |
| `--copilot` | aktifkan Gemini co-pilot (tafsir + usul hipotesis) |
| `--min-trades 30` | minimum trade/window agar statistik bermakna |
| `--snapshot-dir DIR` | simpan/﻿muat OHLCV persis (reproducible bit-for-bit) |
| `--holdout-frac 0.2` | sisihkan 20% ekor histori sebagai LOCKBOX (tak dipakai riset) |
| `--lockbox` | UJIAN FINAL di segmen lockbox — pakai SEKALI saja |
| `--stress-mult 2` | kalikan fee & slippage (cost-stress); CANDIDATE wajib bertahan |

---

## 4. Menambah siklus/strategi baru

Pola ekstensi (mirror v2–v5; tidak mengubah versi lama):

1. **Data** → `bot/altdata.py`: fungsi fetch + transform **causal** (hanya data yang
   tersedia saat bar close; ffill nilai jarang). Gagal/kosong → kembalikan netral
   (fitur auto-nonaktif).
2. **Sinyal** → `bot/strategy_lab.py`:
   - `FeaturesVN` (dataclass) + `precompute_vN(df, cfg, ...)`
   - `decide_vN(fN, g) -> np.ndarray` (side per bar: 1 long, −1 short, 0 skip)
   - `build_grid_vN(...)` + `walk_forward_vN(...)` (reuse `run_walk`)
3. **CLI** → `optimize.py`: tambah `vN` ke `--strategy`, cabang `run_wf`, dan format
   `params_str`.
4. **Registry** → `bot/registry.py`: tambah tag sumber baru ke `KNOWN_SOURCES` bila perlu;
   pencatatan ke registry **otomatis** saat `optimize.py` selesai (tak ada daftar manual).
5. **Log** → `RESEARCH_LOG.md`: catat verdict + alasan, **termasuk yang gagal**.

> **Wajib (hard constraints):** tanpa lookahead; OOS adalah verdict; tiap hipotesis
> struktural berbeda; jangan menggabung sinyal gagal berharap "jadi" bila digabung;
> jangan ubah logika evaluasi walk-forward agar hasil terlihat bagus.

---

## 5. Strategi terdaftar

| Versi | Sumber sinyal | Struktural baru? | Backtestable | Verdict OOS |
|---|---|---|---|---|
| v1 | Trend/momentum/struktur OHLCV | baseline | ya | −0.206R |
| v2 | + HTF + regime + sesi | filter | ya | −0.105R |
| v3 | + funding z-score + OI delta (filter) | filter | ya (OI ≤30 hari) | −0.017R |
| v4 | + order flow/CVD (filter) | filter | ya | −0.007R (impas) |
| **v5** | **Cross-exchange basis (Binance vs Bybit)** | **ya — antar-venue** | ya | **−0.123R (REJECTED)** |
| **v6** | **Liquidation cascade fade (proxy OHLCV)** | **ya — event paksa** | ya | **−0.430R (REJECTED)** |
| **v7** | **Funding regime sebagai sinyal primer** | **ya — positioning** | ya | **−0.116R (REJECTED)** |

> ⚠️ **Catatan penamaan:** "v5" di kode = **cross-exchange basis** (siklus riset 1).
> Eksperimen lama berlabel v5 (*event/volatility guard*) sudah **dibuang** jauh
> sebelumnya karena memperburuk OOS — jangan dirancukan.

### v5 — Cross-exchange basis (Siklus 1)
- **Hipotesis:** dislokasi harga Binance vs Bybit (linear USDT perp) bersifat
  mean-reverting; saat \|z-score basis\| besar, harga Binance konvergen → fade.
- **Implementasi:** `altdata.fetch_bybit_close` + `altdata.basis_zscore` (causal),
  `strategy_lab.decide_v5` (mean-reversion **murni**, tanpa skor OHLCV v1–v4).
- **Hasil:** OOS exp_R = **−0.123R** (PF 0.80, win 46.5%, n=576). **REJECTED** — basis
  pada majors sudah diarbitrase HFT sub-bar. Detail: [RESEARCH_LOG.md](RESEARCH_LOG.md) Siklus 1.

### v6 — Liquidation cascade fade (Siklus 2)
- **Hipotesis:** cascade likuidasi overshoot → snap-back. Deteksi via jejak OHLCV (range
  ≥k×ATR + volume spike + close kapitulasi), lalu **fade**.
- **Implementasi:** `altdata.cascade_components`, `strategy_lab.decide_v6`. *Proxy* OHLCV
  (tak ada feed likuidasi historis gratis) — didokumentasikan jujur.
- **Hasil:** OOS exp_R = **−0.430R** (terburuk). **REJECTED** — fade kalah telak; di 15m
  event volatilitas besar **berlanjut (momentum)**, bukan revert. Detail: Siklus 2.

### v7 — Funding regime sebagai sinyal primer (Siklus 3)
- **Hipotesis:** funding ekstrem = positioning crowded → mean-reversion. funding z ≥ +thr →
  fade SHORT; ≤ −thr → fade LONG. (Beda dari v3 yang memakai funding sebagai *filter*.)
- **Implementasi:** reuse `altdata.fetch_funding`/`funding_zscore`, `strategy_lab.decide_v7`.
- **Hasil:** OOS exp_R = **−0.116R**. **REJECTED** — funding ekstrem bisa bertahan/menguat
  saat tren jalan; fading = nangkap pisau jatuh. Karena itu funding lebih tepat jadi filter.
  Detail: Siklus 3.

---

## 6. Backlog hipotesis (urut prioritas)

Sudah diuji & REJECTED: ~~cross-exchange basis (v5)~~ · ~~liquidation cascade fade (v6)~~ ·
~~funding regime primer (v7)~~. Berikutnya:

1. **Options flow proxy** — mis. Deribit DVOL/skew via API publik (siklus 4). *(usul co-pilot)*
2. **On-chain flow proxy** — inflow/outflow exchange via API publik.
3. **Time-of-day microstructure** — jam UTC dengan imbalance struktural.
4. *(catatan)* **Cascade CONTINUATION** — kebalikan v6 (ikuti, bukan fade). Byproduct
   pengetahuan dari Siklus 2; harus diuji bersih dengan falsifier sendiri (hati-hati
   data-mining: ini membalik sinyal gagal di sumber data yang sama → koreksi Bonferroni).

Status hidup ada di `bot/registry.py` (`KNOWN_SOURCES` + `research_registry.json`),
dicatat otomatis tiap walk-forward. Lihat `registry.untested_sources()` untuk kandidat
berikutnya.

---

## 7. Promosi bertahap ke live (proses non-negotiable)

Verdict CANDIDATE **bukan** izin live — hanya izin lanjut. Urutannya wajib:

```
RISET (walk-forward OOS, --holdout-frac menyisihkan lockbox, --snapshot-dir untuk reproduksi)
  → CANDIDATE (exp_R>0.05 · ≥3 window · ≥3 simbol · SIGNIFIKAN · param STABIL)
    → COST-STRESS (--stress-mult 2): edge bertahan saat biaya 2×?
      → LOCKBOX (--lockbox, sekali): bertahan di data yang tak pernah menyetel parameter?
        → PAPER forward-test data live (parameter TETAP, berhari-hari) — forwardtest.py
          → MICRO-LIVE (modal sangat kecil)
            → naikkan ukuran perlahan bila tetap positif
```

Tiap panah adalah filter; gagal di mana pun → kembali ke flat / riset. Lockbox & paper
memakai **data yang tak pernah memengaruhi pemilihan parameter** → satu-satunya cara jujur
menilai apakah edge nyata atau artefak. Reproduksi dijamin `--snapshot-dir` (hash OHLCV).

---

## 8. Penilaian jujur & batasan sistem co-pilot

**Yang dilakukan dengan baik.**
- Memisahkan **hakim** (walk-forward deterministik) dari **penasihat** (Gemini). Verdict +
  gerbang live ada di kode dan **teruji** (`tests/test_copilot.py`) — AI tak bisa
  melonggarkannya. Ini benar secara arsitektur.
- Mempercepat langkah *generatif* (tafsir + usul hipotesis) tanpa menyentuh integritas
  evaluasi. Fail-open: tanpa key, riset tetap jalan.

**Batasan yang harus diingat (jangan dilebih-lebihkan).**
1. **Gemini tidak menambah alpha.** Ia menyusun narasi & ide, bukan menemukan edge. Tiga
   siklus pertama tetap REJECTED — co-pilot tidak (dan tak seharusnya) mengubah itu.
2. **Risiko salah-arah dari usul AI.** Usul hipotesis bisa terdengar meyakinkan tapi keliru
   (mis. sempat mengusulkan ulang cascade karena daftar tertinggal). Mitigasi: `TESTED_SOURCES`
   wajib di-update tiap siklus; usul AI **tak pernah** auto-diimplementasi tanpa review.
3. **Multiple-testing meluas.** Makin banyak hipotesis diuji, makin besar peluang positif
   *kebetulan*. Disiplin Bonferroni & ambang konsistensi (≥3 window, ≥3 simbol) adalah
   pertahanan utama — bukan jumlah ide yang dihasilkan co-pilot.
4. **Co-pilot bukan pengganti penilaian manusia.** Ia alat percepat dokumentasi & brainstorming
   ber-guardrail; keputusan riset tetap milik manusia.
