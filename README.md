# Binance USDC-M Futures Bot

Bot trading futures USDC-margined di Binance dengan arsitektur 7-layer:
**data → screening → smart rotate → signal engine → risk gate → execution → position management**, plus layer opsional Gemini untuk konfirmasi/veto regime pasar.

Satu basis kode, **tiga mode** lewat satu variabel `MODE`:

| MODE | Arti | Uang |
|---|---|---|
| `dry` | Data pasar **nyata**, order **disimulasi** di memori. Tanpa API key. | — |
| `test` | Binance **Futures Testnet**. API real, saldo palsu. | palsu |
| `live` | Akun Binance asli. | **NYATA** |

---

> **Artefak utama proyek ini = [METHODOLOGY.md](METHODOLOGY.md)** (asumsi, batasan,
> temuan, cara reproduksi). Nilai di sini adalah *sistem pengujian yang jujur & reproducible*,
> bukan klaim profit. Pada data yang dapat di-backtest, **tidak ditemukan edge tradeable** —
> dan itu didokumentasikan, bukan disembunyikan.

## ⚠️ Baca dulu — jujur soal ekspektasi

- **Tidak ada bot yang menjamin "win rate tinggi" atau "profit konsisten tiap hari."** Yang dirancang di sini adalah *survival* (tidak blow-up) + *expectancy positif* lewat risk/reward dan disiplin eksekusi. Akan ada hari/minggu merah — itu normal dan sudah diantisipasi lewat circuit breaker.
- **Gemini bukan mesin sinyal.** LLM lambat & tidak unggul membaca indikator numerik. Di sini Gemini hanya **menilai regime** dan bisa **mem-veto** entry saat pasar chaos. Otak entry tetap rules deterministik.
- **Wajib lulus `test` dulu.** Jalankan di testnet berhari-hari, cek `logs/trades.jsonl`, baru pertimbangkan `live` dengan modal kecil.
- **Risiko 100% milikmu.** Ini perangkat lunak, bukan nasihat keuangan.

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # lalu isi sesuai mode
```

### Mode dry (langsung bisa, tanpa key)
```bash
# .env -> MODE=dry
python run.py --check       # cek koneksi + universe
python run.py --once        # satu tick
python run.py               # loop penuh (simulasi)
```

### Mode test (Binance Futures Testnet)
1. Daftar & buat API key di https://testnet.binancefuture.com
2. `.env` → `MODE=test`, isi `BINANCE_TEST_KEY/SECRET`
3. `python run.py`

### Mode live (uang nyata)
1. API key Binance: **aktifkan Futures saja**, **matikan withdrawal**, **kunci ke IP server**.
2. `.env` → `MODE=live`, isi `BINANCE_LIVE_KEY/SECRET`.
3. Mulai dengan `risk.account_risk_pct` kecil & `leverage` rendah di `config.yaml`.

---

## Cheatsheet — komponen & perintah

| Perintah | Fungsi |
|---|---|
| `python run.py` | Engine mono Python (riset cepat, strategi v1) |
| `python backtest.py --bars 1500` | Backtest expectancy (R, fee+slippage, no-lookahead) |
| `python optimize.py --strategy v4 --bars 3500` | Sweep + walk-forward (verdict OOS) |
| `python forwardtest.py --poll 30 --use-store` | Forward-test paper di data live (diatur dari UI) |
| `python dashboard.py` | Dashboard web + panel kontrol → `http://127.0.0.1:8000` |
| `python l2collect.py` | Collector orderbook L2 (data forward microstructure) |
| `pytest -q` | 59 unit test Python |
| `cd core && cargo test` | 8 unit test Rust (hot-path) |
| `docker compose up -d --build` | Deploy bot + collector + dashboard 24/7 |

**Dokumentasi:** [METHODOLOGY.md](METHODOLOGY.md) (asumsi/batasan/temuan) ·
[DEPLOY.md](DEPLOY.md) (Proxmox/Debian) · `core/README.md` (Rust core).

**Gemini (opsional):** isi `GEMINI_API_KEYS` + `GEMINI_ENABLED=true` di `.env` →
mengaktifkan regime veto & news veto (`config.yaml: gemini.news_veto`). Tanpa key → non-aktif (allow).

---

## Backtest dulu (wajib sebelum live)

```bash
python backtest.py --symbols "BTC/USDC:USDC" --bars 3000 --tf 15m
python backtest.py --bars 5000 --csv trades.csv     # semua whitelist + dump
```

Metrik kunci = **`exp_R`** (expectancy per trade dalam kelipatan risiko, sudah
termasuk fee+slippage, tanpa lookahead). `exp_R > 0` = ada edge.

> Status default saat ini: **`exp_R ≈ -0.19` (BELUM ada edge).** Parameter di
> `config.yaml` adalah titik awal, bukan strategi jadi. Jangan jalankan `live`
> sampai backtest (idealnya walk-forward) menunjukkan expectancy positif yang stabil.

### Sweep + walk-forward (anti-overfit)

```bash
python optimize.py --symbols "BTC/USDC:USDC" --bars 5000 --train 1000 --test 300
```

Memilih parameter terbaik di data **train**, lalu mengujinya di data **test**
yang belum dilihat, lalu menggeser jendela maju. Verdict = expectancy **OOS**.

> Temuan saat ini: in-sample selalu positif tapi **OOS ≈ −0.21R** → klasik
> **overfitting**. Pelajaran: edge harus datang dari *fitur sinyal yang benar*,
> bukan dari tuning angka. Inilah pengaman utama sebelum membuang uang live.

## Collector L2 orderbook (riset microstructure)

Kumpulkan snapshot depth orderbook — data yang **tak tersedia historis** dari exchange,
jadi harus direkam forward (sekali terlewat, hilang).

```bash
python l2collect.py --symbols "BTC/USDC:USDC" --levels 10 --interval 1
```

Output `data/l2/<symbol>_<tanggal>.jsonl.gz` (~50–200 MB/hari/pair). Tiap baris =
snapshot raw (depth N level) **plus** fitur turunan (imbalance, micro-price, spread).

> Catatan rate-limit: REST-poll banyak simbol/cepat bisa kena ban IP (418) — Binance
> menyarankan WebSocket untuk skala (lihat `bot/l2.py`). Untuk beberapa simbol @ ≥1s aman.

## News veto (Gemini, real-time)

Veto entry saat ada berita high-impact (FOMC/CPI, regulasi, hack, delisting). Headline
dari RSS publik (CoinDesk/Cointelegraph, tanpa API key) → Gemini menilai → `{veto, note}`.

```env
GEMINI_ENABLED=true
GEMINI_API_KEYS=key1,key2
```
Lalu di `config.yaml`: `gemini.news_veto: true`. Aktif di forward-test.

> Jujur: ini **real-time, TIDAK bisa di-backtest** (tak ada histori headline berlabel).
> Gunanya menambah **keamanan** (hindari trading saat chaos berita), **bukan** bukti edge.
> Gagal jaringan/Gemini → otomatis *allow* (tak pernah blokir trading karena error infra).

## Forward-test (paper, data LIVE) — langkah sebelum mempertimbangkan uang

```bash
python forwardtest.py --once                 # uji satu siklus
python forwardtest.py --poll 30              # jalan terus (Ctrl+C berhenti)
python forwardtest.py --symbols "BTC/USDC:USDC" "ETH/USDC:USDC"
```

- **Data live nyata, eksekusi paper** (tanpa uang). Akuntansi identik backtest (fee+slippage).
- **Parameter TETAP** selama jalan — TIDAK re-optimize (re-optimize sambil jalan = menipu diri).
- Tiap trade dicatat ke `logs/forward_trades.jsonl` dengan R-multiple; statistik berjalan
  (win%, expectancy R, equity) tercetak tiap siklus.

> Cara pakai jujur: jalankan **berhari-hari/minggu** di beberapa pair. Bila `expectancy R`
> tetap **> 0** pada sampel besar (puluhan+ trade) di data yang belum pernah dilihat,
> barulah ada bukti edge. Bila ~0 atau negatif (seperti backtest), **jangan live.**
> Catatan: Binance Testnet sering tak punya pair USDC & harganya tak realistis, jadi
> paper-on-live-data ini lebih sahih untuk menilai edge daripada order di testnet.

## Deploy (Docker) — bot + dashboard 24/7

```bash
docker compose up -d --build      # jalankan bot (forward-test) + dashboard
docker compose logs -f bot        # pantau log bot
# dashboard: http://<host>:8000
docker compose down               # stop
```

- Dua service dari satu image; berbagi volume `./logs` (bot menulis jurnal, dashboard membaca).
- `restart: unless-stopped` → otomatis hidup lagi bila crash/reboot.
- Default `MODE=dry` (forward-test paper, tanpa API key). Ganti via `MODE=test docker compose up -d`.
- Cocok untuk VPS kecil mana saja (lihat catatan deploy: hindari serverless untuk proses always-on).
- **Proxmox / server lokal (Debian, DNS 1.1.1.1): lihat [DEPLOY.md](DEPLOY.md).**

## Panel kontrol di dashboard (atur dari UI)

Dashboard punya **Kontrol Bot**: atur dari browser, bot menerapkannya tiap siklus
(hot-reload via `logs/runtime.json`). Jalankan bot dengan `--use-store`:

```bash
python forwardtest.py --poll 30 --use-store    # baca pengaturan UI
python dashboard.py                            # atur di http://127.0.0.1:8000
```

Yang bisa diatur: **Status ON/OFF**, **Teknik** (`scalping` 5m · `swing` 1h · `auto`
= smart autopilot 15m + regime trend/mean-reversion), **Pair**, **Leverage**, **Bet/margin
(USD)**, **Saldo**, **Target profit %**. Sizing = `bet × leverage`, dengan **simulasi
likuidasi jujur**.

> ⚠️ **PERINGATAN LEVERAGE (ditampilkan juga di UI).** Leverage tinggi = judi, bukan trading.
> Pada **x100**, gerakan melawan **~0.5%** sudah **likuidasi** (modal habis) — dan SL berbasis
> ATR biasanya lebih lebar, jadi posisi kena likuidasi **lebih dulu**. Ini matematika, bukan
> pendapat. "Smart autopilot" **bukan** mesin profit: backtest strategi ini masih **impas**.
> Forward-test ini paper (uang palsu) — tempat aman untuk **melihat sendiri** x100 kena
> likuidasi tanpa kehilangan uang nyata.

## Dashboard monitoring (web)

```bash
python forwardtest.py --poll 30     # terminal 1: bot menulis logs/trades.jsonl
python dashboard.py                  # terminal 2: buka http://127.0.0.1:8000
```

Menampilkan (auto-refresh 10 dtk): kartu **trades / win% / expectancy R / profit factor /
equity / return**, **kurva equity**, **posisi terbuka**, **rincian per-simbol**, dan
**trade terakhir**. Dashboard hanya membaca jurnal (`logs/trades.jsonl`) — aman dijalankan
terpisah dari bot, bahkan di mesin/port berbeda (`--host 0.0.0.0 --port 8080`).

## Konfigurasi (`config.yaml`)

Semua strategi & batas risiko ada di sini — tidak ada angka ajaib di kode.
Yang paling penting untuk keselamatan:

- `risk.account_risk_pct` — risiko per trade (% equity). Mulai 0.3–0.5.
- `risk.daily_max_loss_pct` — **circuit breaker**: berhenti trading hari itu.
- `risk.leverage` — konservatif. Naikkan sangat hati-hati.
- `risk.sl_atr_mult` / `tp_atr_mult` — jarak SL/TP berbasis ATR (RR > 1).
- `rotate.max_open_positions` — slot posisi paralel.

---

## Arsitektur (peta ke modul)

| Layer | Modul |
|---|---|
| 1 Data ingestion | `bot/exchange.py` |
| 2 Screening | `bot/screener.py` |
| 3 Smart rotate | `bot/rotate.py` |
| 4 Signal engine | `bot/signals.py`, `bot/indicators.py` |
| 5 Risk gate | `bot/risk.py` |
| 6 Execution | `bot/execution.py` |
| 7 Position mgmt | `bot/position.py` |
| AI konfirmasi/veto | `bot/gemini_layer.py` |
| Orkestrasi (mono Python) | `bot/engine.py` |

### Mode polyglot (Rust core + Python svc)

Untuk latency hot-path, layer 1/5/6 dipindah ke Rust (`core/`), sementara
screening/sinyal/Gemini tetap Python (`svc/`), tersambung lewat ZeroMQ:

```
core (Rust)  PUB candle 5556 ─► svc (Python)  ─ sinyal ─► PUSH 5557 ─► core ─ risk+exec ─► PUB event 5558 ─► svc
```

Jalankan **core dulu** (dia yang BIND socket), lalu svc:

```bash
# terminal 1 — Rust core (lihat core/README.md untuk install Rust)
cd core && cargo run --release

# terminal 2 — Python services
python -m svc.run
```

> `bot/engine.py` (mono Python) dan stack polyglot adalah dua jalur terpisah:
> pakai salah satu. Mono untuk riset/backtest cepat; polyglot untuk produksi latency-sensitif.

---

## Roadmap

- [x] Mono Python 7-layer (dry/test/live)
- [x] Rust core hot-path (ingest/normalize/risk/exec) + ZeroMQ IPC — **build + `cargo test` 8/8**
- [x] Jembatan Python `svc/` (SUB candle/event, PUSH intent)
- [x] Unit test: Python **23** (`pytest`) + Rust **8** (`cargo test`)
- [x] Backtester expectancy (R-multiple, fee+slippage, tanpa lookahead) — `backtest.py`
- [x] Sweep + walk-forward (anti-overfit, verdict OOS) — `optimize.py`
- [x] Strategi v2: filter HTF + regime trend/mean-reversion + sesi — `bot/strategy_lab.py`
- [x] Strategi v3: + funding rate + open interest — `bot/altdata.py`
- [x] Strategi v4: + order flow / CVD (taker buy/sell) — `bot/orderflow.py`
- [x] Forward-test paper di data LIVE (parameter tetap, log R-multiple) — `forwardtest.py`
- [x] Dashboard web monitoring (FastAPI, auto-refresh) — `dashboard.py`
- [x] Deploy Docker (bot + dashboard, volume bersama) — `Dockerfile` + `docker-compose.yml`
- [x] Panel kontrol UI: teknik (scalping/swing/auto), leverage, bet, target profit + likuidasi
- [x] News veto via Gemini (real-time, forward-test) — `bot/news.py`
- [x] Collector L2 orderbook (data forward microstructure) — `l2collect.py`
- [x] Dokumen metodologi reproducible — [METHODOLOGY.md](METHODOLOGY.md)
- [ ] Jalankan forward-test/collector berhari-hari → riset microstructure & sampel lebih besar

### Lintasan edge (OOS, walk-forward — BTC/ETH/SOL)

| Strategi | exp_R | PF | win% | Catatan |
|---|---|---|---|---|
| v1 trend | −0.206 | 0.71 | 41 | jelas rugi |
| v2 +HTF+regime+sesi | −0.105 | 0.86 | 36 | membaik |
| v3 +funding+OI | −0.017 | 0.97 | 45 | nyaris impas |
| **v4 +orderflow/CVD** | **−0.007** | **0.99** | 40 | **impas (mentok)** |

> Empat lapisan fitur menggeser hasil dari −0.21R ke ~0, tapi **konvergen di IMPAS,
> BUKAN profit** (PF 0.99). Kenaikan v3→v4 cuma +0.01R = **diminishing returns**:
> data resolusi-bar sudah habis diperas. **JANGAN live** — expectancy ~0 berarti
> tidak menghasilkan uang setelah biaya. Edge positif sejati kemungkinan butuh
> microstructure tick (tak praktis di-backtest) atau gaya strategi berbeda.
- [ ] Close/exit event dari core → svc (slot release otomatis di mode polyglot)
- [ ] User-data stream (fill realtime) + trailing stop sisi exchange
- [ ] Dashboard PnL + notifikasi Telegram
- [ ] Walk-forward parameter tuning

Status saat ini: **v0.1 — mono Python jalan di `dry`/`test`; stack polyglot tersambung (core perlu `cargo build`).** Belum tervalidasi untuk profit; jangan `live` sebelum lewat testnet.
