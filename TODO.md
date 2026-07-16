# PROMPT UNTUK MINIMAX — Refactor Balance Split, Isolated Margin, Multi-Timeframe Chart, Per-Mode Profile

## KONTEKS PROYEK (baca dulu sebelum mulai)

Ini adalah proyek `binance-usdc-bot` — bot trading algoritmik untuk Binance USDC-M/USDT-M Perpetual Futures.
Arsitektur lengkap (7 layer: market data → screener → rotator → signal engine → risk gate → executor →
position manager, plus Gemini ReAct agent layer dan dashboard FastAPI) sudah terdokumentasi di file
`ARSITEKTUR_LENGKAP.md` (60+ file Python, ~15.000 baris). **Baca file itu dulu secara utuh sebelum menyentuh kode.**

Prinsip non-negotiable yang HARUS dipatuhi selama refactor ini:

1. **JANGAN ubah logika strategi/edge yang sudah ada** (signal engine, risk gate, BTC gate, sideways sniper,
   Gemini layer, research pipeline). Refactor ini scope-nya HANYA infrastruktur: data balance, margin mode,
   chart, dan pemisahan profile per-mode.
2. Semua mode (`dry`, `test`, `live`) tetap harus berjalan independen seperti sekarang — jangan gabungkan state-nya.
3. Jangan hapus kompatibilitas `use_store=False` (hardcode config.yaml) kalau masih dipakai; cek dulu apakah
   masih relevan sebelum dihapus.
4. Setiap perubahan skema database (SQLite `logs/bot.db`) harus disertai migration script yang aman
   (tidak menghapus data historis `gemini_decisions`, `events`, dll).
5. Buat **TODO plan tertulis dulu** (breakdown task + file yang akan disentuh + urutan eksekusi) sebelum mulai
   coding. Tunjukkan plan itu dulu, baru eksekusi bertahap per bagian (jangan big-bang commit semua sekaligus).

---

## LATAR BELAKANG TEKNIS: BINANCE MARGIN MODE (referensi API resmi)

Karena refactor ini menyentuh margin mode, ini beberapa fakta API Binance USDⓈ-M Futures yang perlu dipegang:

- Ganti margin type per simbol: `POST /fapi/v1/marginType` dengan parameter `symbol` dan `marginType`
  (`ISOLATED` atau `CROSSED`).
- Cek margin type & leverage saat ini sebelum mengubah: `GET /fapi/v1/symbolConfig` (menghindari error
  `-4046 "No need to change margin type"` kalau sudah isolated).
- **PENTING**: margin type **tidak bisa diubah selama ada posisi terbuka** di simbol tersebut, dan leverage
  juga tidak bisa dikurangi saat posisi isolated masih terbuka (`-4161`). Maka margin type harus di-set
  **sebelum** entry, bukan sesudahnya, dan harus idempotent (skip kalau sudah sesuai).
- Info posisi lengkap (termasuk `isolatedWallet`, `isolatedMargin`, `liquidationPrice`, `unRealizedProfit`,
  `marginType` per simbol): `GET /fapi/v2/positionRisk` (atau v3).
- Open orders per simbol atau semua: `GET /fapi/v1/openOrders`.
- Saldo wallet per asset (termasuk USDT dan USDC terpisah dalam satu account USDⓈ-M): `GET /fapi/v2/balance`
  atau `GET /fapi/v3/account`.
- Real-time update posisi & balance tanpa polling: User Data Stream (`listenKey`) dengan event
  `ACCOUNT_UPDATE` — ini yang dipakai untuk live/test agar dashboard tidak perlu polling REST terus-menerus.

Isolated vs Cross (untuk konteks, developer MiniMax perlu paham *kenapa* user minta ini):
- **Cross margin**: seluruh saldo wallet jadi collateral bersama untuk semua posisi. Kalau satu posisi rugi
  besar, bisa "menarik" margin dari saldo lain dan memperpanjang waktu sebelum liquidation (margin call
  melebar ke seluruh akun).
- **Isolated margin**: margin dibatasi hanya sejumlah yang dialokasikan ke posisi itu. Kalau kena liquidation,
  kerugian terbatas ke margin yang dialokasikan saja, tidak menyeret saldo/posisi lain.

---

## BAGIAN 1 — Chart Harga per Pair: Timeframe Lengkap + Live

**Kondisi sekarang**: Chart harga per pair diambil dari `bot/chartstore.py` (`data/market.db`), dibaca via
`/api/candles`, overlay EMA/RSI. Sumbernya sudah Binance (via ccxt/exchange wrapper), tapi timeframe yang
tersedia terbatas (kemungkinan hanya intraday: 15m/1h/4h — cek kode aktual untuk konfirmasi timeframe apa saja
yang sudah ada).

**Yang diminta:**
1. Tambahkan timeframe **mingguan (1w)** dan **bulanan (1M)** ke chart per pair, sumber data tetap dari Binance
   (endpoint `GET /fapi/v1/klines` dengan `interval=1w` / `interval=1M`), konsisten dengan sumber data yang
   sudah dipakai untuk timeframe lain (jangan pakai provider lain).
2. Untuk timeframe besar (1w/1M), ambil histori **sebanyak mungkin** yang tersedia dari Binance Vision/klines
   endpoint (limit maksimum per request adalah 1500 candle — kalau butuh lebih, lakukan pagination
   backward menggunakan parameter `startTime`/`endTime` sampai data listing awal pair tersebut).
3. Chart update **live** menggunakan SSE (`EventHub` yang sudah ada di `bot/dashboard.py` sudah pakai
   WebSocket/SSE untuk status/trade/order/balance/candle) — perluas event `candle` supaya juga meng-cover
   timeframe baru ini, atau tambahkan channel SSE terpisah kalau volume data 1w/1M terlalu jarang berubah
   untuk digabung ke channel yang sama (candle close 1w/1M jarang, jadi cukup push saat candle baru close,
   tidak perlu tick-by-tick).
4. Pastikan `chart_ingest.py` (isi chartstore) di-extend agar backfill juga menyimpan timeframe 1w/1M ke
   `data/market.db`, bukan cuma fetch on-demand tanpa cache (biar tidak re-fetch histori besar tiap load chart).
5. Cek dulu apakah `chartstore.py` skemanya per-timeframe-per-table atau satu tabel dengan kolom interval —
   sesuaikan penambahan timeframe baru dengan skema yang sudah ada, jangan bikin skema paralel baru.

---

## BAGIAN 2 — Saldo Terpisah USDT vs USDC (Mode `dry` & `test`)

**Kondisi sekarang**: Kemungkinan ada satu field `balance_usd` (saldo total) di `RuntimeSettings` per mode
(lihat `bot/settings_store.py`). Ini yang perlu diubah.

**Yang diminta:**
1. **Hapus konsep "total saldo" tunggal.** Pecah jadi dua field independen: `balance_usdt` dan `balance_usdc`.
2. Kedua saldo ini **bisa di-input manual** oleh user lewat UI kontrol bot (bagian yang sekarang mengatur
   `balance_usd`), untuk mode `dry` dan `test` (paper trading — saldo simulasi).
3. Logika pemotongan saldo saat entry:
   - Pair dengan quote `USDT` (mis. `BTC/USDT`) → margin diambil/dipotong dari `balance_usdt`.
   - Pair dengan quote `USDC` (mis. `BTC/USDC`) → margin diambil/dipotong dari `balance_usdc`.
   - Saat posisi close (profit/rugi), kembalikan/kurangi ke wallet yang sesuai (USDT atau USDC), jangan
     campur ke satu pool.
4. Terapkan hal ini di semua tempat yang saat ini merujuk saldo tunggal: sizing (`account_risk_pct` dari
   `bot/risk.py` — pastikan basis perhitungan risk % memakai saldo wallet yang sesuai quote pair, bukan
   total gabungan), `max_portfolio_exposure_pct` (apakah dihitung per-wallet atau gabungan — putuskan dan
   dokumentasikan, karena Binance sendiri men-treat USDT-M dan USDC sebagai wallet terpisah tapi dalam satu
   account USDⓈ-M), dan tampilan dashboard (`/api/stats`, `/api/status`).
5. Circuit breaker harian (`daily_max_loss_pct`) — putuskan apakah dihitung terhadap `balance_usdt` +
   `balance_usdc` gabungan, atau terpisah per wallet. **Rekomendasi**: hitung terpisah per wallet supaya
   konsisten dengan prinsip "tidak dicampur" di poin 3 — tapi konfirmasikan pemahamanmu ke user sebelum
   implementasi kalau ambigu, jangan asumsi sepihak.

---

## BAGIAN 3 — Komponen Positions & Open Orders

**Yang diminta:**
1. Setiap posisi terbuka **selalu punya minimal 1 open order pendamping**: stop-loss (`STOP_MARKET`
   reduce-only, sudah disebutkan ada di `bot/execution.py` untuk mode live). Pastikan komponen "Positions"
   di dashboard menampilkan relasi ini secara eksplisit — setiap baris posisi punya sub-baris/link ke
   open order SL miliknya, bukan ditampilkan terpisah tanpa keterkaitan.
2. Kalau entry pakai **limit order** (default executor: LIMIT post-only/GTX), maka selama order itu masih
   *pending* (belum fill), akan ada **dua** entry di "Open Orders": (a) limit order pembuka posisi yang
   masih resting, dan (b) begitu limit ter-fill dan posisi terbentuk, SL/TP muncul sebagai open order baru.
   Pastikan dashboard membedakan jenis order ini dengan jelas (label: `ENTRY_PENDING` vs `SL` vs `TP`),
   supaya user tidak bingung melihat 2 open order untuk 1 posisi.
3. Reconcile status ini (`reconcile via fetch_open_orders`, sudah ada di forward tester) harus tetap jadi
   satu-satunya sumber kebenaran (source of truth) untuk status order — jangan bikin state paralel di UI
   yang bisa desync dari hasil reconcile.

---

## BAGIAN 4 — Margin Mode: ISOLATED (bukan Cross)

**Yang diminta:**
1. Set margin type jadi **`ISOLATED`** untuk setiap simbol sebelum entry (di semua mode: dry/test/live —
   untuk dry/test cukup disimulasikan di logika bot, untuk live/test-real harus benar-benar panggil
   `POST /fapi/v1/marginType` ke Binance).
2. Karena margin type tidak bisa diganti saat posisi terbuka (lihat catatan API di atas), logika set margin
   type harus terjadi di tahap **sebelum order dibuka**, dan idempotent — cek dulu via
   `GET /fapi/v1/symbolConfig` apakah simbol itu sudah `ISOLATED`, kalau sudah, skip (hindari error -4046).
3. Simulasi mode `dry`/`test` (paper) tetap harus menghitung liquidation price & margin behavior **seolah-olah
   isolated** — artinya kerugian per posisi dibatasi ke margin yang dialokasikan ke posisi itu saja, tidak
   "meminjam" dari saldo/posisi lain seperti cross margin. Ini penting supaya simulasi paper konsisten
   dengan perilaku live nantinya.
4. Cek juga **position mode** (One-way vs Hedge) via `GET /fapi/v1/positionMode` — dokumentasikan asumsi
   yang dipakai bot sekarang (kemungkinan One-way berdasarkan arsitektur single-slot per pair), dan pastikan
   isolated margin bekerja konsisten dengan mode ini.

---

## BAGIAN 5 — Mode LIVE: Ambil Semua Data Langsung dari Binance

**Yang diminta, khusus mode `live`:**
1. Saldo USDT dan USDC di komponen "Positions"/dashboard **diambil langsung dari Binance**
   (`GET /fapi/v2/balance` atau `/fapi/v3/account`), bukan dari input manual seperti mode dry/test.
2. Data posisi terbuka diambil dari `GET /fapi/v2/positionRisk` (mencakup `isolatedMargin`,
   `liquidationPrice`, `unRealizedProfit`, `marginType` per simbol) — dan **pastikan filter/tampilkan hanya
   yang `marginType == ISOLATED`** sesuai keputusan di Bagian 4 (kalau ada posisi lama yang masih cross,
   flag secara jelas di UI agar user tahu perlu ditutup/dipindah manual dulu, karena tidak bisa
   auto-convert saat posisi terbuka).
3. Open orders diambil dari `GET /fapi/v1/openOrders`.
4. Gunakan **User Data Stream** (`listenKey` + `ACCOUNT_UPDATE` event) untuk update real-time balance dan
   posisi ke dashboard SSE, bukan polling REST terus-menerus (hemat rate limit, lebih real-time). REST tetap
   dipakai sebagai fallback/reconcile berkala (misal tiap N menit) untuk menghindari drift kalau ada event
   yang terlewat oleh websocket.
5. `_live_reconcile()` yang sudah ada di `bot/forward.py` kemungkinan besar adalah tempat logika ini perlu
   di-extend — cek dulu implementasinya sebelum menambah fungsi baru yang duplikat.

---

## BAGIAN 6 — Per-Mode Profile Separation (Bukan Cuma Drawdown Reset)

**Requirement eksplisit dari user:**
- Reset **Drawdown Lock** (`max_drawdown_pct` kill-switch di `bot/forward.py:_update_drawdown`, saat ini
  di-reset via `POST /api/dd-reset`) **harus per-mode**. Kalau mode `dry` kena hit drawdown lock, reset
  hanya melepas lock untuk `dry` — TIDAK ikut mereset `test` atau `live`. Cek implementasi endpoint
  `/api/dd-reset` sekarang: kemungkinan dia reset global/tanpa parameter mode — ubah supaya wajib menerima
  parameter mode dan hanya menyentuh key kill-switch milik mode itu di SQLite `kv` store.

**Analisis tambahan (hal lain yang WAJIB ikut dipisah per-mode, di luar saldo & drawdown reset):**

1. **Circuit breaker harian** (`daily_max_loss_pct`, `daily_max_trades`, state akumulasi rugi/jumlah trade
   hari ini, dan flag "sudah trip hari ini") — ini state runtime, bukan cuma setting. Pastikan counter-nya
   tersimpan per-mode (bukan hanya settingnya yang sudah per-mode di `RuntimeSettings`, tapi *state
   akumulasinya* juga harus terpisah, kalau belum).
2. **Blacklist & cooldown pair** (`bot/rotate.py`: `cooldown_minutes`, `blacklist_after_sl`) — pair yang
   di-blacklist karena SL beruntun di mode `dry` seharusnya tidak ikut memblokir entry pair yang sama di
   mode `live`.
3. **API credentials** — mode `live` butuh API key/secret asli Binance mainnet, mode `test` idealnya
   testnet key (kalau masih dipakai — dokumen menyebut testnet sudah deprecated, konfirmasi ke user), mode
   `dry` tidak butuh key trading sama sekali (read-only public data cukup). Pastikan tidak ada kebocoran
   credential live dipakai tanpa sengaja di jalur dry/test.
4. **Position mode & margin type per simbol di exchange** — settingan ini live di sisi Binance per API key/
   account, jadi kalau `test` pakai testnet account terpisah dari `live` mainnet account, settingannya
   otomatis independen. Tapi kalau `test` ternyata memakai account real yang sama dengan `live` (paper
   dengan API key sama, cuma order-nya disimulasikan lokal), maka margin type/leverage per simbol **bisa
   collide** — perlu dicek dan didokumentasikan mode mana yang benar-benar hit API Binance vs simulasi lokal.
5. **Gemini agent memory** (`gemini_decisions`, `gemini_lessons`, `gemini_reflections`, `calibration_log`,
   `flat_shadow`, `vrp_shadow`, `mtf_shadow`) — pertimbangkan apakah lessons/kalibrasi dari mode `dry`
   boleh "bocor" mempengaruhi keputusan di `live`. Rekomendasi: **pisahkan per-mode** (tambah kolom/prefix
   `mode` di setiap tabel ini kalau belum ada), supaya pelajaran dari eksperimen paper tidak diam-diam
   mengubah perilaku live tanpa validasi eksplisit. Kalau user ingin lessons di-share lintas mode secara
   sengaja, itu harus jadi keputusan eksplisit (opt-in), bukan default.
6. **User Data Stream `listenKey`** — hanya relevan untuk mode yang benar-benar konek ke Binance (live, dan
   test kalau masih pakai real/testnet API). Pastikan listenKey dan koneksi WS terpisah per mode, tidak
   di-share satu koneksi untuk representasi data yang berbeda konteks.
7. **Rate-limit/weight tracking** — kalau live dan test menggunakan API key berbeda, tracking limit request
   idealnya per API key (per mode), bukan digabung, supaya throttle di satu mode tidak salah membatasi mode lain.

Tunjukkan ke user tabel/daftar keputusan final "apa yang di-share vs apa yang dipisah per-mode" sebelum
implementasi, khususnya untuk poin Gemini memory (poin 5) karena ini punya trade-off (riset lintas-mode vs
isolasi keamanan) yang sebaiknya dikonfirmasi eksplisit oleh user, bukan diasumsikan sepihak oleh MiniMax.

---

## TODO PLAN YANG HARUS DIBUAT MINIMAX SEBELUM CODING

1. Audit kode eksisting: petakan semua tempat yang merujuk `balance_usd` (saldo tunggal), `marginType`
   default saat ini, skema `chartstore.py`, skema tabel Gemini di `bot/store.py`, dan implementasi
   `/api/dd-reset`. Laporkan temuan sebelum lanjut (jangan asumsi struktur tanpa verifikasi).
2. Rancang skema migrasi SQLite (kolom baru `mode`/split balance) + script migrasi data lama.
3. Implementasi per bagian di atas, urutan disarankan: Bagian 2 (split saldo) → Bagian 4 (isolated margin) →
   Bagian 3 (positions/open order UI) → Bagian 6 (per-mode profile separation) → Bagian 5 (live data
   real dari Binance) → Bagian 1 (chart timeframe + SSE).
4. Setiap bagian selesai → jalankan dry-run/paper test untuk memverifikasi tidak merusak alur existing
   (entry/exit/circuit breaker/drawdown lock) sebelum lanjut ke bagian berikutnya.
5. Update dokumentasi arsitektur (`ARSITEKTUR_LENGKAP.md`) di akhir untuk mencerminkan perubahan ini.

## ACCEPTANCE CRITERIA

- [ ] Tidak ada lagi field/variabel "total saldo" tunggal; semua path kode memakai `balance_usdt` /
      `balance_usdc` sesuai quote pair.
- [ ] Semua entry order (live/test-real) di-set `ISOLATED` sebelum order dibuka, idempotent, tidak error `-4046`.
- [ ] Dashboard Positions menampilkan keterkaitan posisi ↔ SL/TP open order dengan jelas, termasuk kasus
      dua open order untuk limit-entry yang masih pending.
- [ ] Mode `live`: saldo, posisi, open order semua bersumber dari Binance real-time (REST + user data stream),
      tidak ada input manual untuk data yang seharusnya live.
- [ ] Reset drawdown lock per mode terverifikasi tidak saling mempengaruhi mode lain (test: trip drawdown di
      `dry`, cek `test`/`live` tetap unlocked).
- [ ] Chart per pair punya timeframe 1w dan 1M, data historis maksimal yang tersedia, update live via SSE.
- [ ] Daftar item per-mode-profile (Bagian 6, termasuk 7 poin tambahan) sudah diimplementasi atau
      didiskusikan eksplisit dengan user untuk item yang trade-off-nya perlu keputusan (terutama Gemini memory).