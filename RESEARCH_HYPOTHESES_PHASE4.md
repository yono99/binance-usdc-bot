# Fase 4 — Registri Hipotesis Alpha (2026-07-02)

> Dihasilkan setelah Fase 1–3 menutup ruang prediksi arah: 15+ hipotesis diuji
> jujur (walk-forward OOS + lintas-simbol + cost-stress + Bonferroni), 0 edge.
> Daftar ini HANYA berisi sudut yang belum diuji dengan data yang tersedia.
> Status diperbarui di file ini setiap ada verdict. Detail uji → RESEARCH_LOG.md.

| # | Hipotesis | Prioritas | Status |
|---|---|---|---|
| H24 | Seasonality settlement funding | TINGGI | **DITOLAK** (2026-07-02: OOS −0.19%/rebal, n=1250; gross ≈ 0 — efeknya nihil dua arah) |
| H25 | Carry × momentum double-sort | SEDANG-TINGGI | antre |
| H26 | Reversal syok illikuiditas (Amihud dinamis) | SEDANG-TINGGI | antre |
| H27 | Dislokasi basis lintas-venue cross-sectional | SEDANG | antre |
| H28 | VRP timing via Deribit DVOL | SEDANG | antre (butuh fetcher DVOL) |
| H29 | OI crowding-freshness | STRATEGIS | **PEREKAM OI MENYALA** (uji ≥6 bln lagi) |
| H30 | Spread capture maker (pair spread-lebar) | TINGGI (struktural) | menunggu data L2 (≥4–8 minggu) |
| H31 | Asimetri downside-beta | RENDAH | antre (ekspektasi: gugur) |
| H32 | TSMOM harian per-simbol | RENDAH | antre (penutup lubang formal) |

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
