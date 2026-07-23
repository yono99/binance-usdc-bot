# Dashboard & Lapisan Data (SQLite + React/Vite)

Dokumen ini menjelaskan **dashboard web** (monitoring + kontrol), **lapisan
penyimpanan SQLite**, dan fitur yang diatur dari UI. Ini melengkapi
[README.md](README.md) (ikhtisar) dan [METHODOLOGY.md](METHODOLOGY.md) (asumsi/temuan).

> Ringkas: **FastAPI** (backend + REST API) menyajikan **SPA React/Vite** dan
> membaca/menulis **SQLite** (`logs/bot.db`). Bot forward-test (`forwardtest.py`)
> menulis ke DB yang sama; UI membacanya. Komunikasi via DB, bukan file ad-hoc.

---

## Arsitektur

```
React/Vite SPA (web/dist)  ──REST /api/*──►  FastAPI (bot/dashboard.py)
   monitoring + kontrol                          │  baca/tulis
                                                 ▼
forwardtest.py (bot/forward.py) ──tulis──►   SQLite  logs/bot.db  (mode WAL)
```

- **Backend**: `bot/dashboard.py` (FastAPI). Menyajikan SPA `web/dist` di `/` +
  deep-link fallback (`/trade`, `/agent`, … → `index.html`). Tanpa build → HTML
  monolit lama. Route `/api/*` selalu diprioritaskan.
- **SPA routes (React Router)**: `/` Overview · `/trade` · `/agent` · `/history` ·
  `/settings`. Halaman Agent di React (bukan HTML monolit) — health, keputusan,
  lessons, evolusi, A/B. Endpoint: `/api/decisions`, `/api/lessons`,
  `/api/agent-health`, `/api/evolution`, `/api/ab`, `/api/plan`. Lihat [AGENT.md](AGENT.md).
- **Frontend**: `web/` (React + Vite + TypeScript + react-router). Lihat [web/README.md](web/README.md).
- **Penyimpanan**: `bot/store.py` (SQLite). Tak butuh instalasi — `sqlite3`
  bawaan Python. File dibuat otomatis di `logs/bot.db` (+ `-wal`, `-shm`).

### Menjalankan

```bash
# Produksi (FastAPI menyajikan build React)
cd web && npm install && npm run build
python forwardtest.py --poll 30 --use-store   # terminal 1: bot (paper)
python dashboard.py                            # terminal 2: http://127.0.0.1:8000

# Dev frontend (hot-reload, proxy /api -> :8000)
cd web && npm run dev                          # http://127.0.0.1:5173
```

---

## Lapisan data SQLite (`logs/bot.db`)

File JSONL/RAM lama tak punya `DELETE`/`UPDATE`/query dan hilang saat restart.
Semua dipindah ke SQLite (mode **WAL** → aman bot menulis + dashboard membaca).

| Objek | Lokasi | Isi |
|---|---|---|
| Event trade | tabel `events` | `forward_open`/`forward_close` (dual-write: JSONL audit + SQLite query/hapus) |
| Pengaturan UI | `kv['runtime']` | leverage, bet, saldo, pair, order, fee, dll (pengganti `runtime.json`) |
| Status bot | `kv['status']` | snapshot terkini per siklus (pengganti `status.json`) |
| State hidup | `kv['botstate']` | saldo + posisi terbuka — **durable, tahan restart** |
| Histori news veto | `news_log` | timeline keputusan veto (saat berubah) |
| Histori screening | `screen_log` | sinyal/alasan tak-entry per pair (saat berubah) |
| Token Gemini | `gemini_usage` | prompt/output/total token per panggilan |

> `runtime.json`/`status.json`/`trades.jsonl` lama hanya dipakai untuk **migrasi
> sekali**; setelahnya tak terpakai (aman dihapus). DB di-`.gitignore` (`*.db*`).

**Saldo & posisi tahan restart:** bot memulihkan `botstate` saat start, dan saat
kamu mengubah **Saldo** dari UI, bot menerapkannya tanpa restart (rekonsiliasi di
`bot/forward.py:_apply_settings`). Field "Saldo" di form = **saldo hidup**
(termasuk PnL), bukan setpoint.

---

## Panel Kontrol (diatur dari UI, hot-reload tiap siklus)

Disimpan ke `kv['runtime']`; bot membacanya tiap siklus. Field:

| Field | Arti | Default |
|---|---|---|
| Status | ON/OFF buka posisi | **OFF** (di-reset tiap startup — lihat [Default startup](#default-startup--off-preventif) di bawah) |
| Teknik | `scalping` (5m) · `swing` (1h) · `auto` (15m autopilot) | auto |
| **Pair** | multi-select + pencarian. **Kosong = screening SEMUA pair USDC** | (kosong = semua) |
| Leverage | x1–125 | 100 |
| Bet / margin | margin per posisi (USD, presisi 0.01) | 12 |
| Saldo | LIVE: dari Binance Futures USDC (read-only) · paper: input manual, hidup mengikuti PnL (float 0.01) | 12 (paper) |
| **Target profit** | **Auto** (engine tentukan per-pair via ATR/volatilitas) / **Manual %** (0.1–100) | Auto |
| **Max posisi terbuka** | slot posisi paralel | 2 |
| **Stop-loss harian %** | circuit breaker: stop buka posisi bila rugi harian ≥ % saldo awal hari (**0 = nonaktif**) | 3.0 |
| **Max trade harian** | circuit breaker: stop setelah N trade hari ini (**0 = nonaktif**) | 20 |
| **Interval screening** | detik per siklus | **900 (15 menit)** |
| **Jenis order** | `limit` (maker) / `market` (taker) | **limit (maker)** |
| **Fee taker %** | fee market order | 0.05 |
| **Fee maker %** | fee limit order | 0.02 |
| **Model Gemini** | model untuk screening regime/news (dropdown + search) — kosong = default | (default) |

### Validasi input (wajib ikut engine)
Tiap nilai di-clamp ke batas wajar oleh engine (mis. target profit ≤ 100%, leverage
1–125, bet ≥ 0.01). Bila kamu memasukkan nilai tak masuk akal (mis. target
`9999999999%`), saat **Simpan** engine mengembalikannya ke batas wajar dan UI
menampilkan peringatan: *"engine menyesuaikan: Target profit % 9999999999 → 100"*.

### Multi-pair
Pilih banyak pair (chip + pencarian dari ~38 pair USDC-M), atau **kosongkan untuk
screening semua pair USDC**. Bot membuka posisi sampai **Max posisi terbuka**.
Tabel "Aktivitas per Pair" menampilkan watchlist screening (bukan posisi terbuka)
dengan **pagination** (10/20/30/100).

### Jenis order & fee
Fee paper otomatis mengikuti jenis order: **limit → maker** (slippage 0, fill di
harga), **market → taker** (+ slippage). Baris Status menampilkan
`Order: limit (fee 0.02%)`.

- **Jalur live** (`bot/execution.py`): `config.yaml: execution.order_type`
  (default `limit`) + `post_only: true` → entry LIMIT post-only (GTX, dijamin
  maker; ditolak bila jadi taker). SL/TP tetap `STOP_MARKET`/`TAKE_PROFIT_MARKET`.
- **Caveat jujur**: order limit/maker **bisa tidak ke-fill** bila harga lari.
  Paper-sim mengisi limit seketika di harga (optimistis) — hasil maker bisa lebih
  bagus dari kenyataan. "USDC tanpa fee" **belum terkonfirmasi** dari API
  (taker 0.05% / maker 0.02%); set Fee maker % sesuai fee/promo akunmu.

---

## Manajemen risiko (selalu aktif)

- **DRAWDOWN LOCK** — gerbang rugi utama (Settings: **Drawdown lock %** =
  `max_drawdown_pct`, default 20). Equity turun ≥ % dari **puncak** → entry
  terkunci sampai tombol **Reset Drawdown Lock** di Status (`POST /api/dd-reset`).
  Kumulatif, tidak reset harian. Per-wallet USDT/USDC.
- **Stop-loss harian** (`daily_max_loss_pct`) — **retired** (selalu 0). Jangan
  diandalkan; pakai drawdown lock.
- **Max trade harian** — tetap di UI; stop buka posisi setelah N trade hari itu
  (0 = off). Disimpan per-mode, hot-reload.
- **Guard korelasi** (`risk.corr_threshold` / `corr_lookback`): blok entry **searah**
  bila korelasi return ≥ threshold (default 0.85) dengan posisi terbuka — mencegah
  banyak alt USDC jadi satu taruhan BTC tersamar saat screening semua pair.

## Pengaturan per-mode

Tiap mode menyimpan **setting sendiri** (terpisah di kv `runtime:<mode>`): `dry`,
`test`, `live` punya leverage/bet/pair/order/dll masing-masing. Ganti **Mode** di UI
→ form otomatis memuat setting milik mode itu (`GET /api/settings?mode=<m>`). Mode
aktif disimpan di kv `active_mode`; bot **pinned** (`--mode dry|live`) membaca
bucket mode itu tiap heartbeat (tak ikut switch UI).

### LIVE vs paper — sumber data

| | Dry / test (paper) | LIVE |
|---|---|---|
| Saldo | Ledger paper (`botstate_*` / status) | **Binance** `fetch_balance` via `BINANCE_LIVE_*` |
| Posisi terbuka | `botstate_*.open` | **Binance** `fetch_positions` (+ sync engine) |
| Open orders | pending simulasi di status | **Binance** `fetch_open_orders` |
| Riwayat trade UI | journal `mode=dry` | journal `mode=live` (bukan full history API) |
| Order entry/exit | simulasi | order nyata + SL/TP exchange |

Dry + LIVE **bisa paralel** (dua proses, lock per-mode). Spek lengkap:
[memory/LIVE_AND_DRY.md](memory/LIVE_AND_DRY.md).

### Default startup = OFF (preventif)

Saat `forwardtest.py` atau `dashboard.py` dijalankan, fungsi
`bot/settings_store.reset_all_enabled()` me-reset `enabled=False` untuk **semua**
mode (dry/test/live) di SQLite. Bot tidak akan membuka posisi sampai kamu
**menyalakan ON dari dashboard** secara sadar.

**Mencegah risiko:** tanpa reset ini, bila user menyalakan ON di sesi sebelumnya
lalu bot di-restart/crash, bot akan langsung aktif trading pakai state lama —
bisa paper jalan diam-diam atau bahkan **live tanpa pengawasan**. Dengan reset
startup, tiap kali aplikasi dinyalakan, user wajib konfirmasi ON.

- **Hanya `enabled` yang di-reset** — field lain (leverage, bet, pair, teknik, dll)
  tetap dari nilai tersimpan (tahan restart).
- **Tidak otomatis ON** walau sebelumnya ON — user harus klik ON di UI tiap startup.
- **Dua titik reset:** `forwardtest.py:main()` dan `dashboard.py:main()` → keduanya
  memanggil `reset_all_enabled()` di awal, sebelum `load_settings()`/`uvicorn.run()`.

## Panel Positions / Open Orders

Panel bergaya bursa dengan dua tab:
- **Positions**: Pair · Arah (LONG/SHORT) · Qty · Margin · Entry · Mark · Liq ·
  **PnL (ROI%)** · tombol Close.
- **Open Orders**: SL & TP tiap posisi sebagai conditional order (Pair · Tipe ·
  Arah · Harga trigger · Qty).

## Panel Entry Confluence Shadow (`/entry-confluence-shadow`)

Mengakses `GET /api/entry-confluence-shadow` → menampilkan statistik gate 3-faktor
(shadow mode — **tidak memblokir** entry).

**Tab Ringkasan:**
| Metric | Arti |
|---|---|
| Total Logged | Jumlah record gate yang terekam |
| Would Enter | Gate lolos (would_enter=1) |
| Actually Entered | Di antaranya, yang benar-benar jadi trade (actually_entered=1) |
| Skip Rate | Persentase gate skip (btc_blocked / structure_fail / no_valid_level) |
| Avg Outcome R | Rata-rata `outcome_r` dari settled trades |
| Win Rate | Persentase `outcome_r > 0` |

**Tab Records (paginated):**
| Kolom | Isi |
|---|---|
| TS | Timestamp gate |
| Symbol | Pair |
| Side | LONG / SHORT |
| Setup | `range_fade` / `scalp_range` / `trend_pullback` / `trend_continuation` |
| BTC Tier | `full` / `reduced` / `blocked` |
| Struct Pass | ✓ / ✗ |
| Loc Quality | `strong` / `secondary` / — |
| Would Enter | ✓ / ✗ |
| Actually Entered | ✓ / ✗ |
| Conviction | `conviction` score (0–1) |
| Outcome R | R-multiple (setelah trade settle) |
| Reason | Alasan gate decision |

Gunakan untuk kalibrasi (jalankan `python ec_calibrate.py` setelah N≥30 settled).

## Mode live (UANG NYATA) — toggle dari UI

Selector **Mode** di Kontrol Bot: `ikut .env` / `dry` / `test` (paper) / **`live`**.
Memilih `live` butuh **konfirmasi ganda** + `BINANCE_LIVE_KEY/SECRET` di `.env`.

Saat live (`bot/forward.py`):
- **Entry order NYATA**: LIMIT post-only (maker) atau market, + **SL/TP sisi-exchange**
  (`STOP_MARKET`/`TAKE_PROFIT_MARKET`, tetap aktif walau bot mati).
- **Rekonsiliasi tiap siklus**: tarik posisi nyata dari Binance, deteksi yang tertutup
  (SL/TP/likuidasi), bersihkan order yatim, saldo = equity Binance nyata.
- **Close manual** → reduceOnly market nyata + cancel order.
- Circuit breaker & guard korelasi tetap aktif.

> ⚠️ **Peringatan keras.** Kode live ini **belum teruji dengan uang nyata** —
> **wajib uji dengan bet sangat kecil dulu**. Deteksi closure mengikuti interval
> screening (perpendek `poll` saat live). PnL harian live dihitung dari delta equity
> (termasuk unrealized — breaker bisa lebih konservatif). API key: **Futures-only,
> withdrawal OFF, IP-locked**. METHODOLOGY: strategi **belum ada edge** — live = risiko penuh.

## Pemantauan Token Gemini

Gemini dipakai dua layer: **regime veto** (`bot/gemini_layer.py`) dan **news veto**
(`bot/news.py`), lewat klien terpusat **`bot/gemini_client.py`** — **smart key
rotation** (port dari project elearning `lib/gemini/key-pool.ts`):

- **Health per-key lintas-panggilan** (module-level): `cooldown_until`, `fails`,
  `last_used`. `ordered_keys()` = key sehat dulu, diurut **LRU** (sebar beban);
  bila semua cooldown → yang tercepat pulih dulu.
- **Cooldown by error**: 429/kuota → 60 dtk; 403/key invalid → 5 menit;
  model down (5xx/404) → coba **model berikutnya**; request(400) → berhenti.
- **Fallback antar-model** (urutan sama dengan elearning):
  `gemini-2.5-flash → gemini-3.5-flash → gemini-3-flash-preview →
  gemini-3.1-flash-lite-preview → gemini-2.5-flash-lite`. Model dipilih dari UI
  (dropdown+search) jadi primary; sisanya tetap fallback.
- **Catat token tiap panggilan** ke `gemini_usage` (prompt/output/total + model +
  index key + status) → panel pemantauan.
- **Cache client per key** (`_clients`, seperti `clientCache` elearning): JANGAN
  buat `genai.Client` baru tiap panggilan — client throwaway di-GC menutup httpx
  bersama SDK → "Cannot send a request, client has been closed" beruntun. Client
  di-reuse; bila kena "closed", di-evict & dibuat ulang siklus berikutnya.

Panel **"Pemantauan Token Gemini"** (auto-refresh 15 dtk): token hari ini · total
token · total panggilan · error · tabel per-model / per-tujuan / per-key /
panggilan terakhir.

Aktifkan: `.env` → `GEMINI_ENABLED=true`, `GEMINI_API_KEYS=key1,key2,...`.

---

## REST API

| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/api/stats` | statistik agregat (equity curve, win%, expectancy, dll) |
| GET | `/api/status` | status bot terkini + aktivitas per-pair |
| GET | `/api/account` | mode, validitas API, saldo, status Gemini |
| GET | `/api/settings` | pengaturan runtime + daftar teknik |
| POST | `/api/settings` | simpan pengaturan (hot-reload) |
| GET | `/api/symbols` | daftar pair USDC-M tersedia (untuk pencarian) |
| GET | `/api/ohlcv?symbol&tf&limit` | candle + EMA/RSI untuk chart |
| GET | `/api/trades` · `/api/trades.csv` | riwayat trade (filter pair/reason/tanggal) + ekspor |
| DELETE | `/api/trades/{id}` | hapus satu trade |
| POST | `/api/trades/clear` | hapus seluruh riwayat |
| GET | `/api/news-log` · `/api/screen-log` | histori news veto / screening |
| GET | `/api/gemini-usage` | pemantauan token Gemini |
| GET | `/api/gemini-models` | daftar model Gemini tersedia (dropdown+search) |
| POST | `/api/close` · `/api/close-all` | tutup posisi (diproses ≤1 siklus) |
| POST | `/api/validate-key` · `/api/notify-test` | validasi API key · test Telegram |
| GET | `/api/entry-confluence-shadow` | statistik & record Entry Confluence Gate shadow |

---

## Deploy

`web/dist` **di-commit** agar dashboard jalan tanpa Node di server (FastAPI
menyajikan build statis). `web/node_modules` di-`.gitignore`. Untuk Docker/Proxmox
lihat [DEPLOY.md](DEPLOY.md); pastikan semua kontainer berbagi volume `logs/` di
host yang sama (SQLite WAL aman untuk filesystem lokal, **bukan** NFS/jaringan).
