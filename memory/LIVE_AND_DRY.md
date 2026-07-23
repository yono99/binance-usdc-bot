# LIVE + Dry/Paper — paralel & sumber data Binance

> **Status:** didukung (lock per-mode sejak 2026-07-23).  
> **Kontrak:** live = uang nyata; dry = paper. Jangan samakan KPI.

---

## 1. Apakah dry + LIVE bisa aktif bersamaan?

**Ya — dua proses terpisah, mode berbeda.**

| | Dry (paper) | LIVE |
|---|---|---|
| Proses | `forwardtest.py --mode dry` | `forwardtest.py --mode live` |
| PM2 (default) | app `bot` | app `bot-live` (opsional, di-comment di `ecosystem.config.cjs`) |
| Lock file | `logs/forwardtest_dry.lock` | `logs/forwardtest_live.lock` |
| State SQLite | `botstate_dry` | `botstate_live` |
| Status UI | `status:dry` | `status:live` |
| Settings | `runtime:dry` | `runtime:live` |
| Journal | `trades_dry.jsonl` + events `mode=dry` | `trades_live.jsonl` + events `mode=live` |
| Order | disimulasi | **Binance Futures nyata** |

### Aturan keras

1. **Tepat 1 proses per mode** — dua dry atau dua live → botstate bentrok (ghost open).  
2. Dry + live paralel = **OK** (lock & state terpisah).  
3. Satu proses **tanpa** `--mode` memakai lock global `logs/forwardtest.lock` (legacy).  
4. Dashboard **satu** (`:8000`) — ganti **Mode** di UI untuk melihat bucket dry vs live.  
5. Live butuh `.env`: `BINANCE_LIVE_KEY` + `BINANCE_LIVE_SECRET` (Futures, withdrawal OFF, ideal IP-lock).  
6. `SKIP_ENABLED_RESET=1` di PM2 agar proses kedua **tidak** mematikan `enabled` proses pertama.  
7. Live **bukan** auto-promote edge — lihat [LIVE_MICRO_CHECKLIST.md](LIVE_MICRO_CHECKLIST.md), PROMOTE_PAPER=0.

### Cara menyalakan dual (server)

```bash
# 1) Pastikan key live di /root/binance-usdc-bot/.env
# 2) Uncomment blok `bot-live` di ecosystem.config.cjs
# 3) Restart
cd /root/binance-usdc-bot && git pull && ./restart.sh
pm2 list   # bot (dry) + bot-live + dashboard
```

Atau manual (lock per-mode):

```bash
# paper (sudah lewat PM2 bot)
python forwardtest.py --poll 30 --use-store --mode live   # proses kedua
```

UI: `http://<host>:8000` → Mode **dry** / **live** → Settings & Status mengikuti bucket itu.

---

## 2. Sumber data mode LIVE (apa dari Binance, apa dari bot)

Kredensial: `Settings.credentials()` / `RuntimeSettings.credentials()` →  
`BINANCE_LIVE_KEY` + `BINANCE_LIVE_SECRET` dari **`.env`** (bukan dari form UI).

Client: `ccxt.binanceusdm` di `bot/exchange.py` (`Exchange`).

| Data | Sumber LIVE | Endpoint / path | Catatan jujur |
|---|---|---|---|
| **Saldo USDT/USDC** | **Binance** `fetch_balance` | `Exchange.balances()` · `_live_reconcile` tiap siklus · `fetch_live_balances()` saat save settings live · `/api/account` · `/api/live-balance` | Bukan angka form; form balance read-only |
| **Posisi terbuka** | **Binance** `fetch_positions` | `Exchange.positions()` · `_sync_live_positions` (mode switch) · `_live_reconcile` (deteksi close) · `/api/positions` `source=binance` | Engine `self.open` di-sync; dashboard LIVE = API Binance |
| **Entry / fill** | **Binance** order fill + cache engine | Open live → order exchange; limit filled → reconcile; entry price dari exchange | Paper: simulasi lokal |
| **Open orders (LIMIT/SL/TP)** | **Binance** `fetch_open_orders` | `Exchange.open_orders()` · `/api/open-orders` | Dry: `pending_orders` dari status kv |
| **Mark / ticker** | **Binance** publik | `fetch_ticker` / OHLCV | Sama dry & live (data market) |
| **PnL harian UI** | **Δ equity Binance** vs day-start | `_day_pnl_* = balance - day_start` di reconcile | Bukan paper ledger |
| **Riwayat trade (History UI)** | **Journal bot** (bukan full history API Binance) | events SQLite `mode=live` · `trades_live.jsonl` · `build_trades()` · `/api/trades` | Hanya trade yang bot buka/tutup (atau close terdeteksi reconcile). **Bukan** impor seluruh riwayat akun Binance |
| **Settings (lev, bet, pair…)** | SQLite `runtime:live` | Settings UI per-mode | Saldo **tidak** di-overwrite dari form di live |

### Alur LIVE tiap siklus (ringkas)

```
on_cycle
  → _live_reconcile()
       fetch_positions  → deteksi close / fill pending
       fetch_balance    → balance_usdt / balance_usdc
  → sinyal / gerbang / open (create_order nyata bila enabled)
  → _write_status → status:live + botstate_live
```

### Mode switch ke LIVE (satu proses, bukan dual)

Bila **satu** proses ganti mode UI ke live (tanpa pin):

1. Cek key `.env`  
2. `Exchange` baru + `set_journal_mode("live")` + `botstate_live`  
3. Saldo = `fetch_balance`  
4. `_sync_live_positions()` tarik posisi exchange  
5. Peak DD di-reset ke saldo sekarang  

Proses paper **pinned** (`--mode dry`) **tidak** ikut switch — itulah pola dual PM2.

---

## 3. Apa yang BELUM / batasan

| Item | Status |
|---|---|
| Impor penuh user trades / income history Binance ke History | **Tidak** — History = journal bot |
| Posisi dibuka manual di Binance (tanpa bot) | Muncul di `/api/positions` LIVE; engine `self.open` penuh hanya lewat sync (mode switch / open bot) |
| Satu proses trading dry **dan** live sekaligus | **Tidak** — satu proses = satu mode; pakai **dua** proses |
| Testnet futures | Deprecated; `test` = paper di data live |

---

## 4. Referensi kode

| Modul | Peran |
|---|---|
| `forwardtest.py` | Lock per-mode · `--mode` pin |
| `ecosystem.config.cjs` | `bot` dry; `bot-live` opsional |
| `bot/exchange.py` | balances, positions, open_orders, orders |
| `bot/forward_close.py` | `_live_reconcile` |
| `bot/forward_status.py` | `_switch_mode`, `_sync_live_positions` |
| `bot/settings_store.py` | `fetch_live_balances`, credentials live |
| `bot/dashboard.py` | `/api/account`, `/api/positions`, `/api/open-orders`, `/api/trades` |
| `bot/logger.py` | `set_journal_mode` → `trades_<mode>.jsonl` |

---

## 5. Checklist singkat sebelum LIVE paralel

- [ ] `BINANCE_LIVE_KEY/SECRET` di `.env` server  
- [ ] Dry paper tetap 1 proses (`bot`)  
- [ ] Live size mikro / checklist [LIVE_MICRO_CHECKLIST.md](LIVE_MICRO_CHECKLIST.md)  
- [ ] `bot-live` di-uncomment atau proses `--mode live` manual  
- [ ] UI Mode=live → saldo & posisi dari Binance  
- [ ] Drawdown lock % diset di Settings bucket **live** (terpisah dari dry)  
- [ ] Jangan 2× proses mode sama  

---

*Update bila sumber data atau lock berubah.*
