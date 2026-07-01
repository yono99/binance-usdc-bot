# Alpha Research Handoff — Binance USDC Perp Bot (Fase 1 & 2)

> **Untuk model penerus (Fable):** dokumen ini merangkum SELURUH riset alpha yang
> sudah dilakukan, infrastruktur yang dibangun, apa yang sudah diuji & DITOLAK, dan
> apa yang perlu untuk membuat sesuatu yang **lebih baik**. Baca seluruhnya sebelum
> mengusulkan/menguji hipotesis apa pun — supaya tidak mengulang jalan buntu.
>
> **Kesimpulan satu kalimat:** dari 12+ hipotesis diuji jujur (walk-forward OOS +
> lintas-simbol + cost-stress + koreksi multiple-testing), **0 punya edge terbukti**.
> Ruang hipotesis *sederhana* habis. "Lebih baik" butuh sumber edge yang **fundamental
> berbeda** atau **data baru**, bukan varian TA/cross-sectional lain.

---

## 1. Konteks proyek

- **Instrumen:** Binance USDC-margined perpetual futures (~15–40 pair likuid).
- **Fee:** 0% maker/taker (promo USDC). **PENTING:** edge WAJIB tetap positif di
  fee 0.02% + slippage — jangan bangun edge yang mati tanpa promo (sudah terbukti
  jadi jebakan: beberapa "edge" cuma ditopang fee 0%).
- **Slippage:** nyata & besar di alt USDC (order book lebih tipis dari USDT). Ini
  biaya utama, bukan fee.
- **BTC = mother coin:** dominan; alt ber-beta tinggi mengikuti BTC.
- **Akun:** retail kecil, single-user, tanpa kolokasi/latency-ultra → edge HFT TIDAK
  relevan. Cari edge horizon jam–hari.
- **Stack:** Python. Bot live per-simbol (`bot/signals.py::evaluate` → engine ATR
  SL/TP). Riset backtest terpisah (di bawah).
- **Trik data riset:** USDC perp muda (listing 2023–2024). Untuk hipotesis yang butuh
  histori panjang, **riset di USDT perp** (histori 3–5 thn; properti koin ~sama),
  **eksekusi di USDC** (fee 0%). Dipakai untuk semua uji daily.

---

## 2. PALANG DISIPLIN (non-negotiable — ini yang melindungi dari menipu diri)

Setiap hipotesis WAJIB lolos **keempat**, atau DITOLAK:

1. **Walk-forward OUT-OF-SAMPLE** — param dipilih di train, diuji di test yang belum
   dilihat. Bukan in-sample. (In-sample yang cantik = jebakan #1; lihat H21.)
2. **Lintas-simbol** — edge harus general di banyak pair, bukan keberuntungan 1
   simbol. (Pelajaran termahal: "edge" v7 di BTC-saja MENGUAP saat multi-simbol.)
3. **Cost-stress** — tetap positif di fee>0 + slippage, idealnya stress ×2.
4. **Signifikansi + koreksi multiple-testing** — Bonferroni atas jumlah kombinasi
   param. "Positif tapi p_adj≥0.05" = DITOLAK (kemungkinan artefak).

Plus: **LOW-DOF** (sedikit param → gampang lolos signifikansi) dan **rasional
ekonomi** (tolak pola murni data-mining).

**Anti-selection-bias:** untuk riset multi-sinyal, ada **LOCKBOX** (segmen akhir data
yang TAK PERNAH disentuh saat seleksi; portfolio final diuji sekali di sana).

---

## 3. Infrastruktur riset yang sudah dibangun (aset permanen, semua teruji)

Semua di branch `feat/alpha-research-phase2`. 343 test hijau. Anti-lookahead
(skor ≤t, PnL >t, rebalance non-overlap).

| File | Fungsi | CLI |
|---|---|---|
| `bot/optimize.py` | walk-forward per-simbol (OHLCV + funding/OI/CVD/basis), exit ATR | `optimize.py --strategy v1..v7` |
| `bot/strategy_lab.py` | strategi v2–v7 (HTF/regime/funding/OI/CVD/basis/cascade) | (via optimize.py) |
| `bot/xsectional.py` | engine CROSS-SECTIONAL: rank universe, long-short dollar-neutral. Mode `--reverse`, `--regime` (dispersi>median-train), **`walk_forward_scores`** (engine skor generik) + `verdict`/`sharpe`/`t_pvalue` (koreksi multiple-testing) | `xsectional.py`, `xs_alpha.py` |
| `bot/carry.py` | funding carry cross-sectional (income funding realized + PnL harga) | `carry.py` |
| `bot/xs_signals.py` | **builder skor** [T×N]: residual-momentum, BTC-lead-lag, ivol, skew, funding-accel, BAB, short-term-reversal, coskew, Amihud, turnover + rolling_beta | (dipakai xs_alpha/combine) |
| `bot/statarb.py` | pairs stat-arb: half-life OU, select_pairs, trade_spread (+ stop-loss spread), walk-forward | `statarb.py` |
| `bot/combiner.py` | **multi-sinyal**: bangun matriks return teraligned, seleksi (block-stability + korelasi<0.3), portfolio (equal/invvol), pipeline train/**LOCKBOX** | `combine.py` |

**Menambah hipotesis cross-sectional baru = tulis 1 fungsi builder skor** di
`xs_signals.py` (kembalikan panel [T×N], skor tinggi=long, causal), lalu jalankan
via `xs_alpha.py` atau masukkan ke `combine.py`. Itu saja.

Contoh perintah (0 token, jalankan sendiri):
```bash
python optimize.py --strategy v7 --tf 15m --bars 12000 --fee 0 --slippage 0.03 --snapshot-dir data/snap
python xs_alpha.py --hypothesis skew --tf 1d --bars 2200 --windows 30 60 --holds 5 10 --snapshot-dir data/snap
python carry.py --tf 1h --symbols <small-caps> --fee 0 --slippage 0.05
python statarb.py --tf 1d --stop-z 4.0 --snapshot-dir data/snap
python combine.py --tf 1d --hold 10 --window 60 --corr-max 0.3 --snapshot-dir data/snap
```
`--snapshot-dir data/snap` = cache OHLCV (gitignored) supaya reproducible & hemat fetch.

---

## 4. SUDAH DIUJI & DITOLAK — JANGAN ULANGI (kecuali dengan setup fundamental berbeda)

### Fase 1 — strategi per-simbol (semua @15m/1h, fee 0 = kondisi terbaik)
| Teknik | Hasil OOS | Verdict |
|---|--:|---|
| v1 trend (EMA/ADX/RSI/MACD) | −0.110R | ❌ |
| v2 HTF+regime+sesi | +0.039R (impas, mati di cost-stress −0.317R) | ❌ |
| v3 funding+OI filter | negatif/impas | ❌ |
| v4 order-flow/CVD | negatif/impas | ❌ |
| v5 cross-exchange basis (Binance-Bybit) | −0.055R | ❌ |
| v6 liquidation cascade fade | −1.054R | ❌ |
| v7 funding regime (primer) | BTC-saja +0.289R → **lintas-simbol −0.028R** | ❌ |

### Fase 2 — cross-sectional & lanjutan (mayoritas USDT daily / 1h)
| Hipotesis | OOS/lockbox | Verdict |
|---|--:|---|
| Cross-sectional momentum (harian & mingguan) | ~0 / gagal signifikansi | ❌ |
| Cross-sectional reversal | −0.13% | ❌ |
| Funding carry (majors) | −0.26% | ❌ |
| Funding carry (small-cap) | +0.27% (n=82) → **+0.005% (n=318)** | ❌ artefak |
| Regime-conditional (dispersi; ADX; tren-BTC) | −0.08% / negatif dari 3 sudut | ❌ |
| H2 BTC lead-lag | −0.18% | ❌ |
| H3 residual momentum (beta-neutral) | +0.026% → −0.46% (data banyak) | ❌ |
| H6 idiosyncratic vol | −0.26% | ❌ |
| H15 funding acceleration | −0.12% | ❌ |
| **H18 skewness** (kandidat "terbaik") | +0.14% (n=156) → **+0.055% win 50.4% Sharpe 0.011** (uji definitif) | ❌ koin |
| H21 stat-arb (pairs) | IS Sharpe +2..+3.6 (**overfit**) → OOS −1.98%/trade; +stop −0.28% | ❌ |
| Combiner (skew/BAB/reversal/coskew/Amihud/turnover) | hanya coskew lolos skrining → lockbox −1.12% | ❌ |

**Tiga jebakan yang membunuh kandidat (ingat ini):**
1. **Overfit in-sample** — H21 & coskew cantik di train (Sharpe +3.6 / +1.73%), runtuh OOS. → OOS/lockbox adalah hakim, BUKAN train.
2. **Artefak sampel-kecil** — skew/carry "positif" di n kecil, menguap di n besar.
3. **Ketergantungan promo 0% fee** — v2/v7 positif di fee 0, negatif di fee realistis.

### Terkunci data (belum bisa diuji jujur)
- OI (open interest): hanya ~30 hari histori Binance → tak bisa walk-forward panjang
  (H5, H19 crowding-freshness).
- CVD/order-flow: perlu konfirmasi histori terkumpul (H17).
- L2 order book: hanya forward, rawan lookahead, sulit validasi historis (H23).

---

## 5. Layer manajemen risiko yang sudah ada (REM, bukan sumber edge)

Penting: keduanya **menjaga** edge, **tidak menciptakan**-nya. Berguna SAAT edge ada.
- **BTC dominance gate** (`bot/altdata.py::btc_gate/btc_gate_side`) — direction-aware:
  blok entri LAWAN arah BTC saat BTC bergerak kuat (long saat dump / short saat pump).
  Searah/gerak kecil = lolos. Dipakai semua teknik (live `signals.evaluate` +
  backtest `run_walk`). Config `btc:` di `config.yaml`.
- **Devil's Advocate** (`bot/react_agent.py`) — pass LLM adversarial menantang tiap
  ENTER (adaptasi debat Bull/Bear TradingAgents). Fail-open. Config `gemini.devil_advocate`.
- **Prinsip LLM:** LLM TIDAK punya alpha prediktif. Perannya = **rem** (hindari trade
  buruk: news veto, regime, exit), BUKAN gas (prediksi arah). Uji A/B sebelum percaya.

---

## 6. Untuk membuat yang LEBIH BAIK — arah yang belum dieksplorasi

Odds tetap kecil (base-rate alpha rendah), tapi ini yang BELUM jadi jalan buntu:

**Belum diuji (butuh engine/data baru, masih dalam ruang cross-sectional):**
- **H13 Sektor/narrative rotation lead-lag** — cluster pair co-move (L1/meme/AI),
  leader memimpin follower. Butuh preprocessing clustering rolling-correlation.
- **H14 Listing-age lifecycle** — pair baru listing (<60–90hr) mispriced (overreaction
  fade / underreaction drift). Unit analisis paling orthogonal (waktu-sejak-listing).
  Butuh metadata `listing_date` per pair. Cek dulu sebaran listing_date (kalau
  terkumpul dalam batch waktu, variance kecil → gagal).
- **Multi-sinyal (combiner) dengan universe/param lain** — infrastruktur SUDAH ada.
  Cari ≥2 sinyal weak-positif TAK-korelasi yang gabungannya positif di lockbox +
  `diversifikasi=YA` + p_adj<0.05. Coba small-cap, hold/window berbeda.

**Sumber edge fundamental berbeda (di luar TA/cross-sectional yang sudah habis):**
- **Eksekusi/likuiditas** — maker rebate, spread capture, TWAP di pair illikuid. Ini
  edge STRUKTURAL (bukan prediksi), lebih mungkin nyata untuk retail kecil.
- **Microstructure L2** — tapi harus mulai collect data L2 forward dulu (berbulan).
- **Data yang belum dimiliki** — on-chain flows, social/sentiment real-time, dsb.

**JANGAN buang waktu pada:** varian TA per-simbol lain, momentum/reversal/carry/vol/
skew/stat-arb cross-sectional lain (semua sudah diuji, ruangnya habis).

---

## 7. Prinsip metodologis (paling berharga — pertahankan)

1. **In-sample cantik ≠ edge.** Selalu OOS. Sharpe train +3.6 bisa jadi OOS negatif.
2. **Uji lintas-simbol.** Edge 1-simbol hampir selalu overfit.
3. **Cost-stress dengan fee>0.** Jangan bergantung promo.
4. **Koreksi multiple-testing.** Makin banyak dicoba, makin tinggi palang signifikansi.
5. **Lockbox untuk seleksi.** Jangan pernah optimasi di segmen ujian final.
6. **Effect size, bukan cuma tanda.** H18 positif tapi Sharpe 0.011 = tak tradeable
   (edge dimakan slippage; butuh ~3500 rebalance untuk signifikan → mustahil).
7. **Bunuh ide cepat & murah.** Mayoritas gagal; nilainya di menolak sebelum uang nyata.
8. **LLM = rem, bukan gas.** Tak ada alpha prediktif dari LLM.

---

## 8. Status git & data

- Branch: **`feat/alpha-research-phase2`**. Commit: BTC-gate+Devil's Advocate+engine
  Fase 2 (`6e16b39`), H21 stat-arb (`b35d381`), combiner (`c142172`). **BELUM di-push**
  (permintaan pemilik — jangan push tanpa izin eksplisit).
- Data: `data/snap/` (gitignored) = cache OHLCV. Fetch butuh koneksi + API di `.env`.
- Test: `tests/test_{btc_gate,devil_advocate,xsectional,carry,xs_signals,statarb,combiner}.py`
  — semua punya kontrol positif/negatif. Jalankan: `python -m pytest -q`.

---

## 9. Rekomendasi untuk penerus

Pilihan jujur, urut:
1. **Multi-sinyal (combiner) dengan universe/param baru** — infrastruktur siap, 0 build.
   Cari kombinasi ≥2 sinyal positif tak-korelasi yang lolos lockbox.
2. **H14 listing-age** atau **H13 sektor-cluster** — butuh build sedang, sudut orthogonal.
3. **Pivot ke edge eksekusi/likuiditas** — kalau prediksi arah buntu (kemungkinan besar),
   ini sumber edge retail yang lebih realistis. Butuh arah kerja berbeda.
4. **Berhenti** — kesimpulan matang bila semua di atas nihil: edge sederhana tak ada
   di pasar ini untuk retail; jangan live-kan apa pun yang tak lolos keempat palang.

**Jangan pernah** live-kan strategi yang gagal signifikansi/OOS demi "mencoba" — itu
mentradingkan koin dengan biaya nyata. Seluruh nilai kerja ini ada pada disiplin itu.
