# Fase 4 — Registri Hipotesis Alpha (2026-07-02)

> Dihasilkan setelah Fase 1–3 menutup ruang prediksi arah: 15+ hipotesis diuji
> jujur (walk-forward OOS + lintas-simbol + cost-stress + Bonferroni), 0 edge.
> Daftar ini HANYA berisi sudut yang belum diuji dengan data yang tersedia.
> Status diperbarui di file ini setiap ada verdict. Detail uji → RESEARCH_LOG.md.

| # | Hipotesis | Prioritas | Status |
|---|---|---|---|
| H24 | Seasonality settlement funding | TINGGI | **DITOLAK** (2026-07-02: OOS −0.19%/rebal, n=1250; gross ≈ 0 — efeknya nihil dua arah) |
| H25 | Carry × momentum double-sort | SEDANG-TINGGI | **DITOLAK** (2026-07-02: OOS −0.54%/rebal n=263, incl. income funding — sudut carry TAMAT) |
| H26 | Reversal syok illikuiditas (Amihud dinamis) | SEDANG-TINGGI | **DITOLAK** (2026-07-02: pilot +0.54% n=76 = artefak; definitif 103×1400d → −0.35%, n=175) |
| H27 | Dislokasi basis lintas-venue cross-sectional | SEDANG | **DITOLAK** (2026-07-02: 76 pair dua-venue, OOS −0.18% n=220) |
| H28 | VRP timing via Deribit DVOL | SEDANG | **DITOLAK di validasi** (lolos awal p_adj=0.036 n=38 → replikasi 1800d: mean −50%, p_adj=0.336; satu-satunya kandidat forward paper-test) |
| H19/H29 | OI crowding-freshness | STRATEGIS | **DITOLAK** (2026-07-02: uji historis 450 hari via arsip metrics; OOS −0.82% n=15; perekam OI dihentikan — arsip Vision permanen) |
| H30 | Spread capture maker (pair spread-lebar) | TINGGI (struktural) | **DITOLAK di langkah 3** (2026-07-02: replay konservatif −7..−11 bps/rt semua pair/offset; edge milik yang punya posisi antrian = MM profesional) |
| H31 | Asimetri downside-beta | RENDAH | **DITOLAK** (2026-07-02: OOS −1.14% n=112 — sesuai ekspektasi) |
| H32 | TSMOM harian per-simbol | RENDAH | **DITOLAK** (2026-07-02: +0.45% p_adj=0.59 — positif-lemah tak signifikan, pola H18) |

---

### H24: Seasonality settlement funding (flow mekanis 00/08/16 UTC)
**Klaim:** Pair ber-funding tinggi tertekan menjelang settlement (long tutup posisi
hindari bayar funding) dan rebound sesudahnya; sebaliknya untuk funding negatif.
**Rasional ekonomi:** Friksi struktural — pembayaran funding adalah event mekanis
terjadwal; yang diprediksi adalah WAKTU flow, arah diberikan tanda funding yang
sudah diketahui sebelum settlement.
**Kenapa belum habis di-arbitrase:** Cadence 3×/hari di alt illikuid — kapasitas
terlalu kecil dan merepotkan untuk fund besar.
**Definisi sinyal (causal):** Di bar 1h yang close-nya `offset` jam sebelum
settlement: skor = −funding_level (rate 8h terakhir yang terbit, ffill causal).
Long funding-paling-negatif, short funding-tertinggi, dollar-neutral. PnL =
return harga + funding yang benar-benar dibebankan (cumf) selama hold.
**Cara uji:** engine baru `bot/settlement.py` (rebalance TEPAT di bar pra-settlement,
bukan grid tetap) + CLI `settlement_alpha.py`; 1h; universe lebar USDT.
**Parameter minimal:** offset {0,1} jam × hold {1,4,8} bar = 6 trial.
**Failure mode:** Efek ~bps mati oleh slippage (nasib H18); atau flow sudah
bergeser sejak funding interval dinamis.

### H25: Carry × momentum double-sort
**Klaim:** Short funding-tinggi HANYA saat residual-momentum sudah negatif (pump
selesai) memberi carry bersih yang gagal didapat carry polos.
**Rasional ekonomi:** Menarget failure mode carry terdokumentasi (short funding
tinggi kelindas pump); risk premium funding disaring dari crowding.
**Definisi sinyal:** skor = −funding_level bila sign(Σresidual_return 5–10 hr)
berlawanan sign(funding), else NaN.
**Cara uji:** builder 1 fungsi di `xs_signals.py` → `walk_forward_scores`.
**Parameter minimal:** lookback {5,10} hr × hold {3,7} hr = 4 trial.
**Failure mode:** interseksi 2 sinyal mati biasanya mati; masking → n kecil.

### H26: Reversal syok illikuiditas (Amihud dinamis)
**Klaim:** Pair yang Amihud-nya melonjak vs baseline sendiri (syok likuiditas)
overshoot lalu revert beberapa hari.
**Rasional ekonomi:** Mispricing likuiditas temporer; premi penyedia likuiditas.
Beda dari Amihud statis yang gugur di combiner: ini SYOK, bukan level.
**Definisi sinyal:** ratio = Amihud(5d)/Amihud(60d); skor = −sign(ret 3d) × ratio,
aktif hanya bila ratio > ambang train.
**Cara uji:** builder di `xs_signals.py` → `walk_forward_scores` 1d small-cap.
**Parameter minimal:** window syok {3,5} × hold {3,5} = 4 trial.
**Failure mode:** slippage maksimal justru saat sinyal aktif (cost-stress ×2 wajib);
syok bisa = berita fundamental (reversal tak datang).

### H27: Dislokasi basis lintas-venue cross-sectional
**Klaim:** Pair yang premium Binance-nya melebar relatif vs Bybit (crowding lokal)
underperform pair diskon beberapa hari ke depan.
**Rasional ekonomi:** Segmentasi venue; kapital arb terkonsentrasi di majors.
**Definisi sinyal:** skor = −z-score[(close_BNB/close_Bybit − 1) vs rolling 30d].
Beda dari v5 yang ditolak: v5 = time-series per-simbol @15m; ini relatif antar-pair @1d.
**Parameter minimal:** z-window {30d} × hold {2,5} = 2 trial.
**Failure mode:** basis dari close = proxy kasar (beda timestamp) → noise > sinyal.

### H28: VRP timing via Deribit DVOL
**Klaim:** Saat gap DVOL−RV30(BTC) di kuartil atas, basket short-ivol/long-ivol
menghasilkan premi vol yang di waktu normal tidak ada.
**Rasional ekonomi:** Volatility risk premium klasik.
**Definisi sinyal:** conditioner DVOL−RV30 > Q75-train → aktifkan skor −ivol (sudah ada).
**Parameter minimal:** kuartil {75} tetap × hold {5,10} = 2 trial.
**Failure mode:** ivol polos sudah DITOLAK (−0.26%) — beban pembuktian di conditioner.

### H29: OI crowding-freshness (data-locked → PEREKAM MENYALA)
**Klaim:** Funding tinggi + OI naik cepat (crowding SEGAR) memprediksi squeeze
lebih baik dari funding saja.
**Status:** OI historis Binance hanya 30 hari → tak bisa diuji jujur SEKARANG.
Perekam `oicollect.py` dinyalakan 2026-07-02 (poll 1 jam, semua perp USDT+USDC,
output `data/oi/`). Uji layak setelah ≥6 bulan data.

### H30: Spread capture maker di pair USDC spread-lebar (STRUKTURAL)
**Klaim:** Quoting di pair spread 9–15 bps (CRV/BOME/FIL/NEAR/NEO/PNUT, terukur
2026-07-02) memberi edge kotor per round-trip melebihi adverse selection di fee maker 0.
**Rasional ekonomi:** BUKAN prediksi — premi penyedia likuiditas yang terlihat
langsung di spread. Sumber edge fundamental berbeda (rekomendasi handoff).
**Status:** menunggu data L2 (`l2collect.py` menyala 2026-07-02, 8 pair @2s).
Riset layak setelah ≥4–8 minggu. Wajib forward-test mikro sebelum dipercaya
(simulasi fill dari snapshot 2s rawan optimis).

### H31: Asimetri downside-beta — RENDAH
skor = β⁻(60d) − β⁺(60d). Sepupu BAB & coskew yang dua-duanya gugur; diuji hanya
karena murah (1 fungsi). Ekspektasi jujur: gugur.

### H32: TSMOM harian per-simbol — RENDAH
sign(return 60d) + sizing 1/RV via `optimize.py` 1d. Kategori TA per-simbol yang
handoff larang; satu-satunya pembeda = horizon 1d belum diuji formal. Hampir pasti gugur.

---

## Urutan eksekusi
1. **H24** — uji sekarang (engine + kontrol + data nyata).
2. **H29 + H30** — perekam menyala hari ini; nol keputusan sampai data cukup.
3. H26 → H25 → H27 → H28 → H31 → H32, masing-masing bunuh-cepat.

Aturan tetap: verdict hanya dari 4 palang; hasil apa pun dicatat di RESEARCH_LOG.md.

---

# PROGRAM AKTIF & PRA-REGISTRASI FASE 5 (ditulis 2026-07-02, SEBELUM data ada)

> Bagian ini adalah kontrak dengan diri sendiri: kriteria evaluasi ditetapkan
> SEKARANG, sebelum satu pun titik data forward terkumpul. Menggeser palang
> setelah melihat data = membatalkan seluruh nilai uji. Tiga program berjalan
> otomatis (Scheduled Task `BinanceBot_Collectors`, launcher `start_collectors.ps1`,
> dedupe via `logs/*.pid`).

## A. H28 forward paper-test (daemon `h28_forward.py`, mulai 2026-07-02)

**Setup beku:** gate gap DVOL−RV30 > 0.10; basket −ivol q0.3 dollar-neutral
(universe beku `h28_universe.txt`, 103 simbol); hold 10 hari; biaya 0.28%/siklus;
output `data/h28_forward/trades.jsonl`.

**Kriteria evaluasi PRA-REGISTRASI:**
- Evaluasi PERTAMA hanya setelah **≥15 siklus tertutup** (perkiraan: 6–12 bulan).
  Dilarang menilai sebelum itu; dilarang menghentikan lebih awal karena hasil
  buruk ATAU baik (keduanya bias).
- LOLOS bila: mean pnl_net > 0 DAN t-test satu-sisi p < 0.05 (INI SATU TRIAL —
  tanpa koreksi, karena tak ada grid yang dipilih; semua parameter beku).
- Bila LOLOS → naik ke paper-test tahap 2 dgn sizing realistis & slippage terukur
  dari data L2. Bila GAGAL → H28 mati permanen, jangan revisit.
- Larangan keras: mengubah gate/hold/universe di tengah jalan = test batal.

## B. H30 spread capture maker (data L2 via `l2collect.py`, mulai 2026-07-02)

**Data:** 8 pair (CRV/BOME/FIL/NEAR/NEO/PNUT = spread 9–15bps; BTC/ETH = baseline),
10 level, 2 detik, `data/l2/*.jsonl.gz`.

**Riset layak setelah ≥4 minggu data.** Rencana uji (pra-registrasi):
1. **Ukur dulu, jangan simulasi dulu:** distribusi spread per jam-hari, half-life
   spread, frekuensi mid-cross (proxy fill), ukuran adverse move pasca-fill-proxy.
2. Estimasi edge kotor per round-trip quoting 1 level: spread/2 − adverse
   selection terukur. Bila estimasi KOTOR < 3bps di pair terbaik → H30 mati tanpa
   perlu simulasi (bunuh cepat).
3. Bila lolos (2): simulasi replay KONSERVATIF (fill hanya bila harga MENEMBUS
   level quote, bukan menyentuh; antrian diasumsikan terburuk).
4. Bila lolos (3): forward paper-quote → baru bicara uang recehan.
**Failure mode yang diwaspadai:** snapshot 2s melewatkan dinamika antrian →
semua estimasi fill WAJIB konservatif; hasil optimis = curiga bug dulu.

## C. H19 OI crowding-freshness (data via `oicollect.py`, mulai 2026-07-02)

**Data:** OI semua perp USDT+USDC per jam, `data/oi/*.jsonl.gz`.
**Riset layak setelah ≥6 bulan.** Uji: skor = −sign(funding) × ΔOI%(3d), aktif
hanya di |funding| ekstrem; engine `walk_forward_scores` yang sudah ada; grid
maks 4 trial; palang 4 standar. Sebelum 6 bulan: JANGAN diintip untuk "preview".

## D. Aturan untuk pengembang hipotesis berikutnya

1. Baca RESEARCH_HANDOFF.md + file ini SEBELUM mengusulkan apa pun. 22 hipotesis
   sudah mati — daftar lengkap di tabel atas & RESEARCH_LOG.md. Varian kecil dari
   yang mati = dilarang.
2. Hipotesis baru harus punya: rasional ekonomi + alasan belum ter-arbitrase +
   grid ≤6 trial + failure mode tertulis SEBELUM dieksekusi (format file ini).
3. Infrastruktur yang tersedia: builder 1-fungsi di `xs_signals.py` (cross-
   sectional), `carry.py` (+gerbang), `settlement.py` (event-terjadwal),
   `lifecycle.py` (sumbu listing-date), `statarb.py`, `combiner.py` (+lockbox),
   `tsmom.py`, `sector.py`. Hampir semua ide bisa diuji <1 hari kerja.
4. Sumber edge yang benar-benar belum tersentuh bila A–C nihil: on-chain flows,
   sentimen real-time, event listing/delisting SPOT (bukan usia perp), dan
   likuiditas eksekusi (arah H30). Semua butuh data baru — mulai rekam dulu,
   riset belakangan (pola L2/OI).
5. Prinsip tak berubah: OOS adalah hakim; n kecil = belum ada bukti; positif
   tak signifikan = artefak sampai terbukti sebaliknya; LLM = rem, bukan gas;
   dan TIDAK ADA yang di-live-kan tanpa lolos 4 palang.

---

# PENUTUP PRA-REGISTRASI — JALUR PROMOSI H28 & KONDISI TERMINAL PROGRAM
(dikunci 2026-07-02, SEBELUM satu pun siklus paper H28 tercatat)

Status program saat dikunci: 25 hipotesis diuji, 24 DITOLAK (termasuk H30 di
langkah 3 — replay konservatif −7..−11 bps/rt; edge milik posisi antrian = MM
profesional; JANGAN dibuka kembali dengan infrastruktur retail). Satu-satunya
kandidat hidup: H28, di paper-test parameter-beku.

## Jalur promosi H28 → mesin (setiap naik kelas DIBAYAR BUKTI)

**Tahap 1 — PAPER (berjalan).** `h28_forward.py`, parameter beku (gate 0.10,
hold 10d, q0.3, biaya 0.28%/siklus), universe `h28_universe.txt`.
- Evaluasi HANYA setelah ≥15 siklus tertutup di `data/h28_forward/trades.jsonl`.
- LOLOS bila mean pnl_net > 0 DAN t-test satu-sisi p < 0.05 (satu trial).
- Dilarang: mengubah parameter, berhenti lebih awal (hasil buruk ATAU baik),
  menilai sebelum 15 siklus.

**Tahap 2 — MIKRO-LIVE (hanya bila Tahap 1 LOLOS).**
- Basket dipangkas: 10 kaki teratas + 10 terbawah; total notional ≤ $50;
  leverage ≤ 2; eksekusi limit/maker bila mungkin.
- KILL-SWITCH pra-registrasi: mati PERMANEN bila drawdown kumulatif > 15%
  notional ATAU 6 siklus berturut-turut negatif. Tanpa negosiasi.
- Tujuan tahap ini BUKAN profit — mengukur slippage nyata vs asumsi 0.28%.
- Evaluasi setelah ≥10 siklus mikro: mean net > 0 dgn slippage TERUKUR.

**Tahap 3 — UKURAN NYATA (hanya bila Tahap 2 LOLOS).**
- Naikkan bertahap (2× per 10 siklus positif), plafon sesuai toleransi pemilik.
- Kill-switch tetap; parameter tetap beku; review bulanan mean vs backtest —
  degradasi >50% dari ekspektasi = turun kelas ke Tahap 2.

**Tahap 4 — GAGAL DI TITIK MANA PUN = TERMINAL.**
H28 mati permanen. Tidak ada "coba threshold lain". Berlaku rencana terminal ↓.

## Kondisi terminal program (bila H28 gagal di tahap mana pun)

1. **Tidak ada trading live.** Uang untuk crypto → pasif (DCA BTC/ETH) —
   secara konstruksi mengalahkan bot tanpa-edge (nol minus biaya).
2. **Sistem di-repurpose, bukan dibuang**: bot paper + dashboard + chart store
   = alat monitoring/belajar; repo = portofolio engineering & metodologi riset.
3. **Riset baru HANYA untuk sumber data yang belum pernah disentuh** (on-chain,
   sentimen real-time), dengan format registri ini, dan hanya bila risetnya
   sendiri dinikmati. Dilarang: varian dari 25 yang mati, pelonggaran palang
   (p<0.10 dsb.), "live kecil-kecilan biar tahu rasanya".
4. Nilai program = 25+ keputusan TIDAK yang terdokumentasi. Itu bukan
   kegagalan; itu temuan — dan dia menyelamatkan uang nyata setiap hari
   dengan terus berkata tidak.

---

# ADDENDUM PEMILIK (2026-07-02) — LIVE-MIKRO ATAS KEPUTUSAN SADAR PEMILIK

Pemilik memutuskan trading uang nyata SEBELUM ada strategi yang lolos 4 palang,
dengan pernyataan risiko eksplisit ("dengan catatan resiko kesadaran itu semua").
Dicatat transparan — ini KEPUTUSAN PEMILIK, bukan rekomendasi riset. Ekspektasi
matematis yang didokumentasikan program: ≈ nol dikurangi biaya; tujuan nyata =
pengalaman live + data slippage riil.

**A. Bot teknikal LIVE-MIKRO (aktif 2026-07-02).** MODE=live; guardrail dikunci:
bet $2/posisi, leverage 3, max 3 posisi, circuit breaker rugi harian 10%,
max 10 trade/hari, semua rem aktif (news veto, BTC-gate, DA, VRP-shadow).
Eksposur maks ≈ $18 notional. Aturan: guardrail hanya boleh DIKETATKAN.

**B. H28 mikro-live paralel (dibangun; MENUNGGU kill-switch & sizing teruji).**
Override sadar atas urutan Tahap 1→2: basket diciutkan 5+5 kaki × ~$5-10/kaki
(min-notional exchange), total ≤$50. Kill-switch pra-registrasi TETAP berlaku:
DD kumulatif >15% notional ATAU 6 siklus negatif beruntun = mati permanen.
Paper-test Tahap 1 TETAP berjalan paralel (tak tersentuh) — vonis ilmiah tetap
dari 15 siklus paper; jalur live-mikro hanya mengukur slippage lebih awal.

---

# ADDENDUM PEMILIK #2 (2026-07-02) — ATURAN COMPOUNDING BOT UTAMA
(dikunci SEBELUM satu pun trade live tercatat; tujuan pemilik: pertumbuhan aset)

Mesin compounding = `bet_pct` (margin = % saldo, auto-scale saat modal tumbuh).
Compounding memperbesar DUA arah — menyalakannya sebelum expectancy terbukti
positif berarti mempercepat kerugian. Maka:

## Gerbang naik-kelas (bet tetap $2 → bet_pct)
`bet_pct` boleh > 0 HANYA bila SEMUA terpenuhi, diukur dari riwayat LIVE
(terisolasi per mode sejak commit 7d9da76):
1. ≥ 30 trade live TERTUTUP, dan
2. `expectancy_r` > 0 atas seluruh trade live itu (bukan winrate — winrate bisa
   dibeli dgn R:R buruk), dan
3. drawdown lock TIDAK sedang/pernah terpasang dalam 30 trade terakhir.

Cara ukur (satu perintah, mode aktif = live):
    curl -s http://127.0.0.1:8000/api/stats
    → lihat "trades" (≥30?) dan "expectancy_r" (>0?)

## Saat dinyalakan
- Mulai kecil: bet_pct 4–5% (≈ setara $2 di saldo $50 — kontinuitas, bukan lompatan).
- Naik bertahap maks 2× per 30 trade positif berikutnya. Tanpa lompatan.

## Turun-kelas OTOMATIS (tanpa negosiasi)
Kembali ke bet tetap $2 bila SALAH SATU:
- drawdown lock terpasang (kunci 20% dari puncak), ATAU
- expectancy_r atas 30 trade live terakhir < 0.
Naik lagi = ulangi gerbang dari nol (30 trade positif baru).

## Larangan
- Menyalakan bet_pct "karena minggu ini bagus" (n kecil = noise, lihat H26/H28).
- Menaikkan max_drawdown_pct utk "memberi ruang" saat mendekati kunci.
- Menghitung trade paper/test sebagai bukti utk gerbang live (bucket terpisah!).
