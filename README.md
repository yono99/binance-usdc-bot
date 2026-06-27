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
- [x] Strategi v3: + funding rate + open interest — `bot/altdata.py` + `strategy_lab.py`
- [ ] Orderflow/CVD (data tick) + koleksi OI sendiri (>30 hari) untuk lewati impas
- [ ] Validasi sampel lebih besar sebelum menyimpulkan edge

### Lintasan edge (OOS, walk-forward — BTC/ETH/SOL)

| Strategi | exp_R | PF | win% | Catatan |
|---|---|---|---|---|
| v1 trend | −0.206 | 0.71 | 41 | jelas rugi |
| v2 +HTF+regime+sesi | −0.105 | 0.86 | 36 | membaik |
| **v3 +funding+OI** | **−0.017** | **0.97** | **45** | **nyaris impas** |

> Tiap lapisan fitur menggeser hasil ke arah benar, tapi v3 masih **break-even, bukan
> profit** (−0.017R ≈ 0, sampel kecil ~100 trade, batas histori OI 30 hari). **JANGAN
> live.** Edge positif yang andal kemungkinan butuh alpha lebih dalam (orderflow/CVD)
> + sampel lebih besar.
- [ ] Close/exit event dari core → svc (slot release otomatis di mode polyglot)
- [ ] User-data stream (fill realtime) + trailing stop sisi exchange
- [ ] Dashboard PnL + notifikasi Telegram
- [ ] Walk-forward parameter tuning

Status saat ini: **v0.1 — mono Python jalan di `dry`/`test`; stack polyglot tersambung (core perlu `cargo build`).** Belum tervalidasi untuk profit; jangan `live` sebelum lewat testnet.
