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

Butuh Rust toolchain (belum terpasang di mesin ini):

```bash
# 1. install Rust
#    Windows: unduh & jalankan https://win.rustup.rs   (atau: winget install Rustlang.Rustup)
# 2. dari folder core/
cargo build --release
cargo run --release          # MODE diambil dari ../.env
```

`MODE=dry` → konsumsi data publik nyata, order disimulasi (tanpa API key).
`MODE=test` → Binance Futures Testnet. `MODE=live` → uang nyata.

## Kontrak IPC (JSON)

- **market PUB → Python**: `Candle { symbol, open, high, low, close, volume, open_time }`
- **Python PUSH → core**: `SignalIntent { symbol, side:"long|short", confidence, price, atr }`
- **event PUB → Python**: `OrderEvent { symbol, kind:"open|reject|close|error", side, qty, price, sl, tp, note, ts }`

Sisi Python tinggal `connect` SUB ke 5556/5558 dan PUSH ke 5557 (lihat roadmap repo).

## Status

v0.1 scaffold — semua layer hot-path ada & saling tersambung. Belum di-build
end-to-end (toolchain Rust belum ada saat scaffold). Setelah `cargo build`,
iterasi kecil pada API crate (zeromq/tungstenite) mungkin diperlukan.
