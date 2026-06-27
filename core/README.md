# core — Rust hot-path engine

Layer berlatensi-rendah dari bot, ditulis Rust (memory-safe, tanpa GC, async `tokio`).
Berbicara dengan layanan Python (screening/sinyal/Gemini) lewat **ZeroMQ pure-Rust**
(tanpa `libzmq` native → `cargo build` mulus di Windows).

## Tanggung jawab

| Layer | Modul | Catatan |
|---|---|---|
| 1 WS ingestion | `ingest.rs` | combined `aggTrade` stream, auto-reconnect |
| 1 Data normalize | `normalize.rs` | ring buffer tick + agregasi OHLCV 1m |
| 5 Risk gate | `risk.rs` | keputusan in-process, sizing, **circuit breaker** harian |
| 6 Execution | `exec.rs` | signed order (HMAC-SHA256), SL/TP, retry+backoff |
| IPC | `ipc.rs` | PUB candle/event, PULL intent |
| Orkestrasi | `main.rs` | dua task: ingest→publish, intent→risk→exec→event |

## Aliran data

```
Binance WS ──► ingest ──► normalize ──► [PUB market 5556] ──► Python (sinyal)
                                                                   │
                          Python ── [PUSH signal 5557] ──► PULL ───┘
                                              │
                                    risk gate ─► execution ─► [PUB event 5558] ──► Python
```

## Build & run

```bash
cargo build --release
cargo run --release          # MODE diambil dari ../.env
cargo test                   # 8 unit test (risk gate + normalizer)
```

### Toolchain di Windows (penting)

Rust default memakai target **MSVC** yang butuh Visual Studio C++ Build Tools.
Jika tidak ingin memasang VS (besar), pakai jalur **GNU + MinGW-w64** (lebih ringan):

```powershell
winget install Rustlang.Rustup
rustup toolchain install stable-x86_64-pc-windows-gnu
winget install BrechtSanders.WinLibs.POSIX.MSVCRT   # gcc + dlltool + ld
# dari folder core/ — pin toolchain gnu untuk direktori ini:
rustup override set stable-x86_64-pc-windows-gnu
cargo build
```

> Repo ini sudah diverifikasi build & lulus `cargo test` (8/8) via jalur GNU di Windows.

`MODE=dry` → konsumsi data publik nyata, order disimulasi (tanpa API key).
`MODE=test` → Binance Futures Testnet. `MODE=live` → uang nyata.

## Kontrak IPC (JSON)

- **market PUB → Python**: `Candle { symbol, open, high, low, close, volume, open_time }`
- **Python PUSH → core**: `SignalIntent { symbol, side:"long|short", confidence, price, atr }`
- **event PUB → Python**: `OrderEvent { symbol, kind:"open|reject|close|error", side, qty, price, sl, tp, note, ts }`

Sisi Python tinggal `connect` SUB ke 5556/5558 dan PUSH ke 5557 (lihat roadmap repo).

## Status

v0.1 — **build sukses & `cargo test` 8/8 hijau** (jalur GNU di Windows).
Semua layer hot-path tersambung. Belum dijalankan end-to-end melawan
Binance live; uji di `dry`/`test` dulu.
