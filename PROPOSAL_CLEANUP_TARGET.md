# Proposal — Item 7 (Safe-delete data) & Item 8 (Target profit harian)

Status: **DRAFT untuk diskusi**. Belum ada kode diubah. Tolong review & beri keputusan.

---

## Item 7 — Safe-delete data yang tidak dibutuhkan

### Latar belakang
Repo menyimpan banyak data historis yang tumbuh tanpa batas:
- `logs/bot.log` (~1.6 MB saat ini, tumbuh terus), `local_bot.log`, `dashboard.log`, `forwardtest.log` dst.
- `data/market.db` (SQLite) — tabel `gemini_decisions`, `gemini_usage`, `events`, `screen_log`, `flat_shadow`, `calibration_log`, `news_log`, `kv`.
- `data/l2/`, `data/oi/`, `data/snap/` — snapshot market per timestamp.
- `data/h28_forward/`, `data/h28_live/` — hasil paper-test & backtest rolling.

### Prinsip yang diusulkan
1. **Tidak ada hapus otomatis diam-diam.** Setiap penghapusan mencatat apa yang dihapus, kapan, berapa baris/byte, dan ke mana backup-nya.
2. **Retensi berbasis umur (`retention days`) + ukuran (`cap`)** — dikonfigurasi di `config.yaml`, default konservatif. Bisa 0 = tidak pernah hapus.
3. **Dry-run default**: command safe-delete pertama kali cetak rencana tanpa eksekusi, baru dijalankan setelah konfirmasi atau flag `--apply`.
4. **Backup sebelum hapus**: baris yang akan dihapus di-dump ke `data/_trash/<table>_<date>.csv.gz` sekali, sehingga bisa diaudit/dipulihkan.
5. **Hapus-context eksplisit**: bila sebuah tabel terbukti sudah TIDAK terpakai oleh kode mana pun (dideteksi via cakar-referensi via grep), baru masuk daftar "kandidat hapus permanen" — dan ini HARUS didiskusikan dulu (lihat "Yang harus didiskusikan dulu").

### Yang diusulkan untuk safe-delete otomatis (kategori A — aman)
- `screen_log` & `events` lebih tua dari `log_retention_days` (default 90 hari).
- `gemini_usage` per_key/per_model agregat lebih tua dari 365 hari.
- `logs/*.log` dirotasi (file > 50 MB dipotong → `.1`, `.2`; max 3 generasi).
- `data/l2/`, `data/oi/`, `data/snap/` file ts lebih tua dari ttl (default 14 hari), KECUALI yang masih dirujuk oleh h28/snapshot yang active.

### Yang harus didiskusikan dulu sebelum dihapus (kategori B — nontrivial)
- `flat_shadow` settled lebih tua dari N hari — ini dipakai evidence-gate untuk confidence model. Hapus terlalu agresif menipiskan training/calibration.
- `calibration_log`, `gemini_lessons`, `gemini_reflections` — belajar AI terakumulasi; hapus = lupa.
- `data/h28_live/`, `data/h28_forward/` hasil rolling — apakah masih dipakai untuk monitor degradasi?

### Pertanyaan ke Anda
1. Ya/tidak: berapa lama retensi default (60 / 90 / 180 hari)? 
2. Apakah mau backup `.csv.gz` sebelum hapus, atau hapus langsung (hemat disk)?
3. Pakai `--apply` eksplisit vs hapus langsung? Saya usulkan `--apply` dan jalankan lewat systemd timer (bisa cron, bisa manual).
4. Ada tabel/data yang sudah pasti tidak terpakai dan mau langsung saya hapus permanen sekarang?

### Implementasi (kalau disetuju)
- File baru `cleanup.py` — fungsi `safe_delete(dry_run=True)` yang iterasi kategori A, cetak tabel "akan hapus N baris / M byte".
- Bagian `--apply` backup ke `data/_trash/` lalu `DELETE ... WHERE ts < ?`.
- Scheduler opsional lewat systemd/jakarta-timer.

---

## Item 8 — Target profit tiap hari

### Latar belakang & catatan kritis
Hari ini kode punya **circuit breaker downside** (`bot.log` terlihat: `CIRCUIT BREAKER: PnL harian -31.00 <= -30.00. STOP.`) — yaitu stop bila rugi harian melewati batas. Tapi belum ada **target profit harian eksplisit yang menurunkan/menutup aktivitas**.

### Peringatan desain
Target profit harian yang naive bisa **merusak** (over-trading dipaksa, atau berhenti terlalu cepat & kehilangan trend). Proposal disusun dengan sikap konservatif.

### Yang diusulkan
1. **Bukan hard-stop, melainkan "mode lenient setelah target"**: target harian capai → bot **mengurangi agresivitas** (menurunkan `max_new_trades`, `max_exposure_frac`), bukan berhenti total. Trailing yang sudah terbuka tetap ditutup normal.
2. **Dua ambang**:
   - `daily_target_r` = ambang profit harian (default mis. +3R atau +5%) → mode "protect" (no new entries, atau hingga 1 entry saja). 
   - `daily_lockdown_r` sudah ada (circuit breaker downside) tetap.
3. **Reset per hari kalender** — bukan per siklus LLM, agar tidak mudah terpicu fluktuansi singkat. PnL harian dihitung dari settled `outcome_r` hari itu + floating PnL posisi open.
4. **Dapat dimatikan** lewat `config.yaml` (`daily_target_enabled: false`) — default **off** dulu agar tidak mengganggu trading selama evaluasi.
5. **Logging transparan**: setiap transisi `normal → protect` dicatat (`DAILY TARGET tercapai +X R → mode protect`).

### Risiko yang harus Anda pertimbangkan
- Berhenti setelah target jadi **negatif expected value** bila market sedang trend kuat satu arah (miss alpha besar). Mode "protect" bukan stop total mengurangi ini.
- Ambang harus disesuaikan volatilitas (R target statis bisa salah tuning). Usul: ekspos sebagai R, bukan $, supaya mandiri terhadap ukuran balance.

### Pertanyaan ke Anda
1. Satuan target: dalam R (mis. +3R) atau persen balance (mis. +5%)? Saya rekomendasi R.
2. Reaksi capai target: (a) **berhenti entry baru total**, (b) **kurangi agresivitas** (rekomendasi), (c) buka posisi protectif short hedge? 
3. Default `daily_target_enabled`: mau on atau off dulu?
4. Ambang konkret (+3R / +2R / +5%)?

### Implementasi (kalau disetuju)
- Tambah field di `config.yaml` di section baru `daily_target:`.
- Logika di `forward.py` dekat circuit-breaker harmonic (karena sudah ada menejer PnL harian).
- Tidak menyentuh settle/store selain membaca `sum_r` harian.

---

**Tolong jawab pertanyaan di tiap section, atau tandai "skip" bila mau ditunda.**
Setelah disetuju, saya akan masukkan ke TaskList & kerjakan dengan plan mode terpisah.
