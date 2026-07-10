# FIX: gemini_exit -EV — Hard Gate + Kill-Switch

## KONTEKS TEMUAN (sudah terbukti dari data live, bukan hipotesis)

Dari `logs/bot.db`, tabel `gemini_decisions`, kolom `context.exit_track_record`
(snapshot 2026-07-09, n=67 trade total):

| exit_reason  | n  | win_rate | exp_R   | sum_R  |
|--------------|----|----------|---------|--------|
| gemini_exit  | 11 | 18.2%    | -0.253  | -2.785 |
| ? (lainnya)  | 38 | 44.7%    | -0.017  | -0.635 |
| sl           | 7  | 57.1%    | -0.003  | -0.023 |
| tp           | 4  | 100.0%   | +0.361  | +1.444 |

**Kesimpulan matematis:** total sum_R semua kategori = -1.999R. `gemini_exit`
SENDIRIAN menyumbang -2.785R — lebih besar dari total kerugian keseluruhan
sistem. Kalau `gemini_exit` tidak pernah dipakai sama sekali, sum_R total
kemungkinan jadi POSITIF (+0.786R). SL native (-0.003, nyaris breakeven) dan
TP native (+0.361, positif) jauh lebih sehat dari exit yang dipicu Gemini.

Prompt di `bot/trader_curriculum.py` SUDAH punya instruksi bahasa natural:
"cara-keluar dgn exp_r NEGATIF (mis. gemini_exit) = perilaku merugikan →
HENTIKAN, biarkan SL/TP jalan" — tapi ini cuma instruksi lunak ke LLM, BUKAN
hard gate di kode. LLM tidak konsisten mematuhinya, terbukti dari data di atas.

## ROOT CAUSE DI KODE (sudah diverifikasi baca langsung)

Di `bot/forward.py`, method `_gemini_manage()` (sekitar baris 407-445):
- Gemini DIKONSULTASI TIAP CYCLE (~1 menit) untuk SETIAP posisi terbuka,
  tanpa gate numerik berbasis progress-ke-TP.
- Satu-satunya proteksi yang ada adalah `_min_hold_s` (grace period BERBASIS
  WAKTU sejak posisi dibuka), BUKAN berbasis seberapa jauh progress ke TP.
- `pos["peak_tp_prog"]` SUDAH di-track (high-watermark progress ke TP), tapi
  variabel ini TIDAK PERNAH dipakai sebagai syarat sebelum memanggil
  `self.gtrader.manage(ctx)` — desain awal "trigger hanya jika progress ≥50%
  lalu reversal" tidak pernah benar-benar dikode sebagai hard rule.

Di method `_apply_manage()` (sekitar baris 447-460):
```python
if action == "exit":
    log.info(f"Gemini EXIT {sym} @ {price:.6f} — {act.get('reason', '')[:80]}")
    self._close_usd(sym, price, "gemini_exit")
```
Begitu Gemini bilang "exit", LANGSUNG dieksekusi tanpa cross-check terhadap
`exit_track_record` historis (yang sebenarnya sudah dihitung dan tersedia
lewat `gemini_trader._exit_track_record()` — cuma dipakai sebagai info di
prompt, tidak pernah dipakai sebagai gate keputusan kode).

## TUGAS: implementasikan DUA lapis pertahanan

### Lapis 1 — Hard gate progress SEBELUM memanggil Gemini sama sekali

Di `_gemini_manage()`, tepat SEBELUM baris yang memanggil
`act = self.gtrader.manage(ctx)`, tambahkan gate:

- Update `pos["peak_tp_prog"]` seperti yang sudah ada.
- HANYA lanjut memanggil Gemini exit-review jika:
  1. `peak_tp_prog >= 0.5` (progress PERNAH mencapai ≥50% menuju TP), DAN
  2. `peak_tp_prog - prog >= 0.15` (turun ≥15 percentage-point dari puncaknya
     — reversal signifikan, bukan noise kecil)
- Kalau syarat di atas TIDAK terpenuhi → `return` lebih awal, JANGAN panggil
  Gemini sama sekali siklus ini. SL/TP native tetap jalan seperti biasa
  (proteksi ini di luar fungsi ini, tidak terganggu).
- Buat threshold `MIN_PEAK_TO_ASK = 0.5` dan `REVERSAL_BUFFER = 0.15` sebagai
  konstanta yang mudah di-tune di bagian atas file atau di config, jangan
  hardcode magic number tanpa nama.
- Tambahkan log komentar yang menjelaskan MENGAPA gate ini ada, kutip angka
  exp_R -0.253 dari temuan di atas sebagai bukti, biar developer lain paham
  konteksnya kalau baca kode ini nanti.

Efek: Gemini exit hanya ditanya pada kondisi yang PERSIS sesuai desain awal
(progress signifikan lalu berbalik), bukan setiap menit untuk semua posisi.
Ini juga hemat token/API call.

### Lapis 2 — Kill-switch berbasis bukti empiris SEBELUM eksekusi exit

Di `_apply_manage()`, method yang sama, SEBELUM baris
`self._close_usd(sym, price, "gemini_exit")`:

- Ambil `exit_track_record` terbaru via `self.gtrader._exit_track_record()`
  (fungsi ini sudah ada di `bot/gemini_trader.py` baris ~123).
- Cari entry dengan `reason == "gemini_exit"`.
- Jika entry itu ada, DAN `n >= 10` (sampel cukup), DAN `exp_r < 0`
  (secara empiris merugikan) → BLOKIR eksekusi exit ini. Log warning yang
  menyebutkan angka exp_r dan n yang jadi alasan blokir. JANGAN panggil
  `_close_usd`. Biarkan SL/TP native yang menentukan nasib posisi ini.
- Jika kondisi di atas tidak terpenuhi (misal exp_r sudah membaik jadi
  positif setelah lebih banyak data, atau n masih di bawah 10) → izinkan
  exit seperti biasa (kode existing).
- Ini bertindak sebagai circuit breaker independen dari Lapis 1 — kalau
  Lapis 1 entah bagaimana masih kebobolan, atau data makin menegaskan
  gemini_exit tetap -EV, sistem otomatis berhenti total memakainya tanpa
  perlu deploy manual berulang kali.

## CONSTRAINT PENTING

- JANGAN ubah logic SL/TP native (`_monitor_usd` atau sejenisnya) — itu
  sudah benar dan tidak disentuh, cuma exit-lewat-Gemini yang di-gate.
- JANGAN hapus atau matikan total kemampuan Gemini exit — kalau nanti ada
  bukti baru bahwa exp_r membaik (n bertambah, edge jadi positif), sistem
  harus otomatis bisa pakai lagi tanpa intervensi manual (makanya Lapis 2
  re-check tiap kali, bukan flag on/off statis).
- Tulis ATAU update test yang relevan (kemungkinan ada test existing untuk
  `_gemini_manage`/`_apply_manage` di `tests/test_forward*.py` — cek dulu
  sebelum menulis test baru, ikuti pola/style test yang sudah ada di repo).
  Test minimal yang harus ADA:
  1. Posisi dengan `peak_tp_prog` belum pernah ≥0.5 → Gemini TIDAK dipanggil.
  2. Posisi dengan `peak_tp_prog` ≥0.5 tapi belum reversal ≥15pp →
     Gemini TIDAK dipanggil.
  3. Posisi dengan `peak_tp_prog` ≥0.5 DAN reversal ≥15pp → Gemini
     DIPANGGIL seperti biasa.
  4. Gemini bilang "exit" tapi `exit_track_record["gemini_exit"]` punya
     n≥10 dan exp_r<0 → eksekusi exit DIBLOKIR, log warning muncul.
  5. Gemini bilang "exit" dan exit_track_record gemini_exit belum cukup
     data (n<10) atau exp_r sudah positif → eksekusi exit BERJALAN normal.
- Jangan lupa jalankan test suite penuh setelah perubahan untuk pastikan
  tidak ada regresi di modul lain yang bergantung pada `_gemini_manage`
  atau `_apply_manage`.

## SETELAH SELESAI

Ringkas perubahan: file apa saja yang diubah, baris berapa, dan hasil test
(berapa lolos). Jangan commit/push otomatis — tunggu konfirmasi eksplisit
dari saya dulu sebelum commit, karena ini menyentuh jalur eksekusi trading
live.
