# PROMPT UNTUK MINIMAX M3 — Fix Evidence-Gate Bug + Redesign Setup Fade (range_fade & scalp_range)

## KONTEKS (baca dulu, jangan skip)

Proyek `binance-usdc-bot`. Bot masih di mode paper/demo (belum live), verdict Gemini Trader saat ini:
**REJECTED — exp_R -0.161, n=118, p_adj=1.000**. Ini bukan alasan buat berhenti, tapi alasan buat perbaiki
akar masalah sebelum lanjut riset lagi. Baca `ARSITEKTUR_LENGKAP.md` dulu untuk paham struktur 7-layer,
Gemini ReAct layer, dan filosofi "LLM = rem, bukan gas".

**Temuan dari review data track record terbaru (per setup):**

| Setup | Trades | Win% | exp_R | Status di Playbook |
|---|---|---|---|---|
| `scalp_range` | 23 | 4.3% | **-0.590** | Lesson eksplisit: "MUTLAK HENTIKAN scalp_range" |
| `range_fade` | 14 | 50.0% | -0.124 | belum ada lesson retire eksplisit |
| `trend_continuation` | 26 | 30.8% | -0.069 | — |
| `trend_pullback` | 40 | 37.5% | -0.026 | Lesson: "Hentikan penggunaan setup ini" |
| `breakout_continuation` | 15 | 60.0% | -0.057 | — |

Tapi di log keputusan terakhir (masih tanggal yang sama), `scalp_range` **tetap dieksekusi** — dan hasilnya
ada beberapa trade dengan **realized R lebih negatif dari -1.0** (contoh: VIRTUAL/USDT LONG -1.114R,
HBAR/USDT LONG -1.242R, XMR/USDT SHORT -2.024R). Ini dua bug terpisah yang harus diperbaiki DULU sebelum
kerja fitur baru:

1. Lesson yang bilang "MUTLAK HENTIKAN" tidak benar-benar memblokir entry setup itu (evidence-gate bocor).
2. ⁠Ada trade yang rugi jauh melebihi 1R yang seharusnya jadi batas kalau SL bekerja normal.

Juga ada kasus studi manual (BNB, chart TradingView terpisah) yang menunjukkan entry `range_fade` terjadi
di level "udara" (touch count cuma 6 bar) padahal level support asli ada di zona lain (touch count ~40 bar).
Ini akar masalah struktural kenapa `range_fade`/`scalp_range` gagal: sistem sekarang mendeteksi "posisi
dalam range" (0-1 dari 20-bar high/low), BUKAN "level S/R yang benar-benar teruji berkali-kali disentuh
harga". Fade di level palsu = kalah.

---

## PRINSIP NON-NEGOTIABLE

1. Bot tetap **paper/dry only** selama pekerjaan ini. Jangan sentuh apa pun yang mengarah ke live-kan trading
   nyata. Checklist 8 poin "sebelum live" di `ARSITEKTUR_LENGKAP.md` bagian 14 tetap berlaku penuh.
2. Jangan ubah setup lain di luar scope (`trend_continuation`, `trend_pullback`, `breakout_continuation`)
   KECUALI untuk perbaikan Bagian A (halving boost conditioning) — itu pun cuma logika boost-nya, bukan
   sinyal dasarnya.
3. Setup versi baru hasil redesign harus **diberi ID baru** (misal `range_fade_v2`, `scalp_range_v2`),
   BUKAN menimpa ID lama. Ini penting supaya track record lama (yang sudah kebukti gagal) tidak tercampur
   dengan hasil setup baru — kita butuh data bersih untuk evaluasi walk-forward berikutnya.
4. Semua perubahan harus tetap lolos lewat pipeline riset yang sudah ada (`bot/optimize.py` walk-forward OOS,
   Bonferroni correction) sebelum dianggap "berhasil". Jangan klaim sukses cuma dari beberapa trade paper.
5. Kalau nemu ambiguitas desain yang berdampak besar (contoh: definisi "5% TP" — lihat Bagian D), **JANGAN
   asumsikan sepihak**, laporkan dulu ke user pilihan yang ada + rekomendasimu, baru lanjut setelah dikonfirmasi.

---

## BAGIAN A — FIX BUG (prioritas, kerjakan dulu sebelum fitur baru)

### A1. Evidence-gate tidak benar-benar memblokir setup yang sudah "MUTLAK HENTIKAN"

**Investigasi:**
- Cek `bot/lessons.py` — bagaimana status "pensiun otomatis" (akurasi < 0.4 setelah ≥10 pemicu) disimpan,
  dan apakah ada fungsi yang benar-benar dipanggil di entry-path (`bot/signals.py` atau `bot/forward.py`)
  untuk **mencegah** setup yang berstatus retired dari menghasilkan entry baru.
- Kemungkinan besar temuan: lesson cuma tersimpan sebagai teks advisory yang dibaca Gemini sebagai konteks
  prompt (soft signal), bukan hard filter di kode. Kalau benar, itu akar masalahnya — LLM bisa saja
  "mengabaikan" saran dalam prompt kalau confidence lain cukup tinggi.

**Fix:**
- Tambahkan **hard gate** di level kode (bukan cuma prompt LLM): sebelum entry dieksekusi untuk suatu
  `setup_id`, cek status retirement dari tabel `gemini_lessons` — kalau `accuracy < 0.4 AND triggered_count >= 10`
  dan status = retired, maka **skip entry keras**, apa pun rekomendasi LLM. Log alasan skip untuk audit.
- Retirement harus dievaluasi ulang secara berkala (bukan sekali retired selamanya) — dokumentasikan aturan
  saat ini kalau sudah ada, atau usulkan (misal re-evaluasi tiap N trade baru / tiap minggu) dan konfirmasi
  ke user sebelum dikunci.

### A2. Realized loss melebihi -1R

**Investigasi (buat laporan dulu sebelum fix):**
- Ambil beberapa trade yang keluar dengan R jauh di bawah -1 (contoh: VIRTUAL, HBAR, XMR di atas). Untuk
  masing-masing, trace: entry price actual, planned SL price (dari risk gate saat entry), actual exit
  price/reason, dan timestamp entry vs exit.
- Cek 3 kemungkinan penyebab, laporkan mana yang terbukti:
  1. **SL floor calibration di-skip untuk regime=range** (disebutkan di dokumentasi:
     `_sl_floor(..., skip di regime=range)`) — apakah ini bikin SL efektif lebih longgar dari yang
     dikira/direncanakan risk gate?
  2. **Grace period anti-whipsaw 300 detik** pada Gemini manage exit — apakah ini menahan exit sampai
     harga sudah jauh melebihi level SL yang direncanakan?
  3. **Likuiditas/slippage pair kecil** (VIRTUAL, HBAR, XMR, TURBO, LIT — semua altcoin kecil) — apakah
     order SL_MARKET/simulasi paper tidak memperhitungkan slippage realistis untuk pair thin-liquidity ini?
- Setelah akar masalah ketemu, baru fix. **Jangan langsung ubah `sl_atr_mult`/floor tanpa tau akar
  masalahnya**, karena efeknya beda-beda tergantung penyebab (calibration vs timing vs slippage model).

### A3. `HALVING_BEAR_BOOST` menaikkan conviction tanpa peduli track record setup

**Fix:**
- Cari logika boost di sekitar `bot/forward.py:2085-2109` (conviction boost BTC dump/halving).
- Ubah supaya boost **hanya berlaku untuk setup dengan exp_R historis ≥ 0** (atau di atas threshold yang
  disepakati, misal ≥ -0.02R kalau mau kasih toleransi noise kecil). Setup dengan exp_R negatif signifikan
  (seperti `scalp_range`, `range_fade`, `trend_pullback` di data sekarang) **tidak boleh** dapat boost
  conviction dari macro bias — macro bias harusnya memperkuat edge yang sudah ada, bukan memperbesar
  exposure ke setup yang sudah terbukti gagal.
- Threshold exp_R historis ini harus dihitung dari data live/paper terbaru secara rolling (bukan angka
  statis di config selamanya) — cek dulu apakah sudah ada mekanisme rolling exp_R per setup yang bisa
  dipakai ulang (kemungkinan besar sudah ada karena tabel per-setup di dashboard sudah menghitung ini).

---

## BAGIAN B — Level Validity Score (modul baru: deteksi S/R asli, bukan naive range position)

**Masalah yang diperbaiki**: Structure component sekarang di `bot/signals.py` cuma pakai 20-bar high/low
breakout/breakdown dan `pos_in_range` — tidak membedakan level yang "teruji" (banyak disentuh) dari level
yang cuma "kebetulan lewat".

**Desain modul baru** (sarankan file baru `bot/levels.py`, integrasi ke `signals.py`):

1. **Deteksi di timeframe lebih tinggi dari entry.** Entry tetap di timeframe eksekusi bot yang sekarang
   (default 15m, cek konfirmasi timeframe aktual di kode), tapi deteksi level S/R dihitung dari candle
   **1h (default)** dengan lookback panjang (200-300 candle 1h ≈ 8-12 hari). Timeframe deteksi ini harus
   configurable (`level_detection_timeframe: 1h`).
2. **Time-at-price binning**: bagi rentang harga jadi bin kecil (lebar bin berbasis ATR, misal
   `bin_width = 0.15 × ATR(1h)` atau persentase harga ~0.15-0.25%, pilih salah satu dan buat configurable).
   Hitung berapa banyak candle 1h yang high/low-nya menyentuh tiap bin dalam window lookback.
3. **Level valid** = bin dengan touch count ≥ threshold minimum (default `min_touches: 15`, configurable).
   Beri skor kekuatan level = touch_count, dengan **recency weighting** (touch yang lebih baru dibobot
   lebih tinggi — misal exponential decay berbasis jarak candle dari sekarang) supaya level lama yang sudah
   tidak relevan tidak terus dianggap kuat.
4. Cache hasil deteksi per simbol, **refresh hanya saat candle 1h baru close** (bukan tiap tick/cycle) —
   ini murah secara komputasi, konsisten dengan filosofi "pre-gate murah" yang sudah ada di forward tester.
5. Output: list level (harga, jenis support/resistance, strength score), bisa dipanggil sebagai fungsi
   `get_valid_levels(symbol) -> List[Level]` yang dipanggil dari signal engine.

**Validasi sebelum wiring ke live signal path**: jalankan modul ini secara offline/manual dulu terhadap
histori BNB (atau pair yang jadi kasus studi) untuk konfirmasi bahwa 572-574 (level asli) ke-flag sebagai
valid dan 577 (level palsu) tidak lolos threshold. Tunjukkan hasil ini ke user sebelum lanjut wiring.

---

## BAGIAN C — Hard Gate untuk Setup Fade + BTC Confirmation

Berlaku untuk `range_fade_v2` dan `scalp_range_v2` (versi baru, ID terpisah dari yang lama sesuai prinsip
non-negotiable #3).

### C1. Level proximity gate
- SHORT hanya boleh entry kalau harga current berada dalam toleransi kecil dari level **resistance valid**
  (dari Bagian B) — default toleransi `0.3-0.5 × ATR`, configurable.
- LONG sama, tapi dari level **support valid**.
- **Tidak ada level valid dalam toleransi → SKIP total**, bukan sekadar turunkan conviction. Ini beda dari
  perilaku sekarang yang tetap entry dengan conviction rendah.

### C2. BTC directional confirmation (lebih ketat dari `btc_gate()` yang sekarang)
- `btc_gate()` yang ada sekarang sifatnya pasif: cuma blok/diskon kalau **lawan arah** BTC yang bergerak
  kuat (threshold dump 0.5%). Untuk setup fade family, buat lapisan tambahan yang **mensyaratkan dukungan
  aktif**, bukan cuma "tidak melawan":
  - SHORT-at-resistance: BTC harus punya bias turun yang jelas (contoh kriteria: harga BTC di bawah
    EMA-pendeknya sendiri, ATAU slope EMA negatif dalam N bar terakhir — pilih salah satu/kombinasi, buat
    configurable, tidak perlu setinggi threshold dump 0.5% yang sudah ada untuk gate lama).
  - LONG-at-support: BTC minimal netral-ke-positif (tidak sedang bias turun kuat).
- Ini layer terpisah dari `btc_gate()` existing — jangan modifikasi gate lama (dipakai setup lain juga),
  buat fungsi baru khusus dipanggil dari path `range_fade_v2`/`scalp_range_v2`.

### C3. Pair cleanliness filter
- Tambahan filter sebelum setup fade family diizinkan entry di suatu pair (bisa di level screener atau
  pre-gate signal, putuskan mana yang lebih pas secara arsitektur — screener.py sudah punya 4 filter keras,
  bisa ditambah atau taruh di signals.py sebagai pre-check khusus fade):
  1. ADX minimum (pair dengan ADX terlalu tinggi = trending kuat, bukan kandidat range/fade yang bagus —
     ini beda dari ADX filter regime yang sudah ada, fokusnya ke "kebersihan" bukan "arah").
  2. Rasio wick/body candle dalam lookback — pair dengan wick panjang berulang (indikasi likuidasi liar/
     manipulasi) di-skip untuk fade family.
  3. Stabilitas ATR — kalau ATR pair meloncat-loncat gak stabil antar-candle (std/mean tinggi), skip.
- Threshold masing-masing filter harus configurable dan didokumentasikan alasan angkanya (kalibrasi dari
  data histori, bukan angka sembarang).

---

## BAGIAN D — TP Terstruktur: min(level struktural, cap 5%)

⚠️ **ASUMSI YANG PERLU DIKONFIRMASI ULANG SEBELUM FINAL**: dalam diskusi, "profit 5%" diasumsikan berarti
**5% pergerakan harga dari entry** (price move), BUKAN 5% ROI ter-leverage akun. Contoh: entry $577 →
TP maksimum sekitar $606 (bukan target ROI 5% dari margin yang notabene bisa dicapai dengan price move
jauh lebih kecil karena ada leverage 3x). Kalau MiniMax menemukan indikasi berbeda saat implementasi
(misal dari existing `target_profit_pct` di `RuntimeSettings` yang mungkin didefinisikan sebagai ROI),
**stop dan laporkan ke user**, jangan asumsikan salah satu tanpa konfirmasi — ini beda jauh secara hasil.

**Desain (dengan asumsi price-move di atas):**
1. Hitung target TP dua cara:
   - **Structural target**: level valid (dari Bagian B) di sisi berlawanan dari entry (untuk SHORT-at-resistance,
     targetnya level support valid terdekat di bawah; untuk LONG-at-support, targetnya level resistance
     valid terdekat di atas).
   - **Cap target**: entry price ± 5% (price move).
2. **TP final = yang lebih dekat ke entry** antara structural target dan cap 5% (whichever tercapai duluan
   secara realistis, sesuai prinsip "jangan greedy melebihi struktur pasar").
3. **Partial TP**: ambil 70-80% ukuran posisi begitu target tercapai, sisanya pakai trailing stop existing
   (`trailing_atr_mult`) untuk menangkap potensi lanjutan tanpa risiko full give-back.
4. Ini menggantikan `tp_atr_mult: 2.6` murni ATR-based **khusus untuk fade family** — setup lain
   (`trend_continuation`, dll) tetap pakai mekanisme TP yang sudah ada, tidak diubah.

---

## URUTAN KERJA (WAJIB IKUTI URUTAN INI, jangan lompat ke fitur baru sebelum bug fix selesai & terverifikasi)

1. **Bagian A1** (fix evidence-gate hard block) → verifikasi dengan cara paksa retire suatu setup dummy,
   pastikan entry benar-benar ke-skip, bukan cuma warning di log.
2. **Bagian A2** (investigasi + fix realized R < -1) → laporkan dulu root cause sebelum coding fix.
3. **Bagian A3** (halving boost conditioning) → verifikasi setup dengan exp_R negatif tidak lagi dapat boost.
4. **Bagian B** (level validity module) → validasi manual dulu pakai kasus BNB sebelum wiring ke live path.
5. **Bagian C** (hard gate + BTC confirmation + pair cleanliness) → wiring ke `range_fade_v2`/`scalp_range_v2`
   dengan ID baru, jalan di paper/dry.
6. **Bagian D** (TP terstruktur) → **konfirmasi definisi 5% dulu ke user** sebelum implementasi final.
7. Kumpulkan sample trade paper secukupnya (idealnya lolos syarat effective-n minimum yang sama dengan
   pipeline riset lain, cek `bot/optimize.py` untuk angka minimum yang sudah dipakai), baru evaluasi
   apakah `range_fade_v2`/`scalp_range_v2` layak lanjut ke walk-forward OOS resmi.

---

## ACCEPTANCE CRITERIA

- [ ] Setup dengan status "retired" di lessons engine **tidak bisa** menghasilkan entry baru — dites dengan
      skenario paksa (bukan cuma baca kode, harus ada bukti test/dry-run).
- [ ] Laporan tertulis root cause untuk trade dengan R < -1 (VIRTUAL, HBAR, XMR, dan sampel lain kalau ada),
      disertai fix yang sesuai root cause tersebut.
- [ ] `HALVING_BEAR_BOOST` (dan boost sejenis) tidak lagi diterapkan ke setup dengan exp_R historis negatif
      signifikan — dites dengan menunjukkan log sebelum/sesudah untuk setup yang exp_R-nya negatif.
- [ ] Modul level validity terbukti (lewat validasi manual kasus BNB atau pair lain) membedakan level asli
      (touch count tinggi) dari level palsu (touch count rendah) sesuai definisi threshold yang disepakati.
- [ ] `range_fade_v2`/`scalp_range_v2` (ID baru, terpisah dari lama) hanya entry saat: (a) dekat level valid,
      (b) BTC confirmation searah, (c) lolos pair cleanliness filter — dan SKIP (bukan reduced conviction)
      kalau salah satu syarat tidak terpenuhi.
- [ ] TP fade family = min(structural, cap harga 5%) dengan partial TP, **definisi 5% sudah dikonfirmasi
      eksplisit oleh user** sebelum dianggap selesai.
- [ ] Track record `range_fade_v2`/`scalp_range_v2` tercatat terpisah dari `range_fade`/`scalp_range` lama
      di semua tabel (`gemini_decisions`, dashboard per-setup table, dll).