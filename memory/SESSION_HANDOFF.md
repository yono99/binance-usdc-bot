# Session handoff — baca dulu di sesi Grok CLI baru

> **Tujuan file ini:** supaya konteks **tidak hilang** saat TUI/CLI ditutup.  
> Di-load lewat project rules (`AGENT.md` + `.grok/rules/`).  
> Update baris “Status terakhir” bila posture server berubah.

**Terakhir diisi:** 2026-07-20 ~09:00 UTC

### Status terakhir (2026-07-20 ~09:00 UTC)

- **Root cause UI vs screening “ada posisi / margin habis”:**
  1. **Dua proses `forwardtest`** (PM2 + zombie manual pid lama tanpa `--mode dry`)
     saling timpa `botstate_dry` → open hilang tanpa `forward_close` (ghost journal).
  2. Panel **Posisi Terbuka** dulu dari reconstruct event all-time (ghost), bukan live.
  3. **Log Screening on-change** menempel alasan lama (“sudah ada posisi”) setelah flat.
  4. Status file bisa **lag mid-cycle** (OPEN di botstate, `status:dry` belum rewrite)
     → UI `open_count` 0 palsu sampai bar close berikutnya.
- **Perbaikan di master (commit `91d12ef` + `e343ba9` + docs/restart + key-rotation):**
  - `ecosystem.config.cjs`: bot args `--mode dry` (pin_mode) + single PM2 bot
  - `forwardtest.py`: **file lock** `logs/forwardtest.lock` (tepat 1 instance)
  - `bot/forward.py`: persist state segera setelah OPEN; **status mid-cycle** setelah OPEN;
    screen dedup reset saat open set berubah; mode-switch persist dulu
  - `bot/dashboard.py`: `open_positions` dari `botstate`/`status` (bukan event ghost)
  - `scripts/reconcile_dry_ghosts.py`: tutup ghost paper (`reconcile_state_flat`)
  - **`restart.sh`**: `git pull` lalu stop PM2 → kill orphan → clear lock → `pm2 start`
    ecosystem → verifikasi **tepat 1** `forwardtest`. **Pakai ini**, bukan `pm2 restart` saja
    bila ada risiko zombie.
  - **`bot/gemini_client.py`**: rotasi **LRU murni** (beban merata); limit → parse retry
    delay dari error Google → **SKIP key** s/d cooldown habis (bukan nunggu di key mati).
- **Deploy server setelah pull:**
  ```bash
  cd /root/binance-usdc-bot && git pull && chmod +x restart.sh && ./restart.sh
  ```
- **Jangan** jalankan `python forwardtest.py` manual di server saat PM2 bot online
  (lock exit 2; zombie lama menimpa state).
- **Batas jujur:** paper mikro; KPI proses/risk, bukan profit harian.
  Open paper berubah tiap siklus — cek UI/API, jangan hardcode jumlah di handoff.

### Gemini keys Proxmox (audit + purge 2026-07-20)

- **Sekarang:** `.env` `GEMINI_API_KEYS` = **19 unik sehat** (server only; **tidak** di git).
  Backup: `.env.env.bak_403_*` / `.env.env.bak_403b_*` di `/root/binance-usdc-bot/`.
- **Dibuang (403 project denied)** — 7 key total (2 putaran purge):

  | Putaran | Prefiks | sha16 | Catatan |
  |---|---|---|---|
  | 1 | `AIzaSyBVMy…` | `c6ed6dfaecc364ff` | ex-index #1 |
  | 1 | `AIzaSyARbR…` | `380f399d35ddae05` | ex-index #2 |
  | 1 | `AIzaSyC0Mb…` | `38b1eb0d28f61a31` | ex-index #8 |
  | 2 | `AIzaSyDmtg…` | `8fc506d8d92ca21e` | ketahuan saat rot-test |
  | 2 | `AIzaSyBhkY…` | `f5847f0f25b5b336` | ketahuan saat rot-test |
  | 2 | `AQ.Ab8RN6IH…` | `511b962b5a3669d5` | ketahuan saat rot-test |
  | 2 | `AQ.Ab8RN6Ir…` | `044d4e512a118993` | ketahuan saat rot-test |

- **Uji rotasi (setelah purge + LRU fix `ee1c2e0`):** 19 call → **19/19 sukses**,
  unique key **19/19**, 1 call/key, **0 error 403**, ~39s. Rotasi merata **PASS**.
- **Panel “Per key”** = usage history, bukan inventory; index bisa bergeser setelah purge.
- **Cooldown limit (client):**
  - 429 RPM: parse `retry in Xs` / `retryDelay`; default **60s**; SKIP key.
  - 429 RPD: SKIP **(key,model)** sampai ~**08:00 UTC** (atau delay API ≥5 mnt).
  - 403 generik: **5 menit**; project denied: **6 jam** durable + SKIP.
- **Jalan A:** manager-mode ON → call Gemini hemat; rotasi LRU tetap tiap call.

---

## 1. Kontrak pemilik (jangan dilupakan)

- Ikuti rekomendasi metodologi: **survival + Jalan A**, **bukan** profit tiap hari.
- **7 hari = gerbang proses** (awasi + checklist), **bukan** duduk diam.
- **H30 / L2 maker ritel = TUTUP** (sudah REJECTED langkah 3).
- Jangan buka ulang H24–H32 / v5–v7 “sedikit diubah”.
- Live dilarang tanpa CANDIDATE + paper.
- Tekanan hutang/psikologi **nyata** — tapi bot paper **tidak** diganti jadi “mesin
  cuan darurat”. Jangan scale live / longgarkan risk karena lapar profit.

Dokumen induk:

| File | Isi |
|---|---|
| [PLAN_OPERASIONAL.md](../PLAN_OPERASIONAL.md) | Rencana fase 0–5, risk lock, KPI |
| [CHECKLIST_HARIAN.md](../CHECKLIST_HARIAN.md) | Ritual ~10 menit + log hari 0–7 |
| [TUJUAN.md](../TUJUAN.md) §2.1 | Tujuan operasional aktif |
| [AGENT.md](../AGENT.md) | ReAct / Jalan A / A/B |

---

## 2. Server paper (status 2026-07-19)

| Item | Nilai |
|---|---|
| Host | `192.168.1.107` (SSH key: `~/.ssh/id_ed25519_proxmox`, user `root`) |
| UI | `http://192.168.1.107:8000` |
| Repo remote | `/root/binance-usdc-bot` |
| PM2 | `bot` (`forwardtest.py`) + `dashboard` — **tepat 1 bot** |
| Mode | **`dry`**, `enabled=true` |

### Risk dry (dikunci)

| Field | Nilai |
|---|---:|
| `daily_max_loss_pct` | 5 |
| `daily_max_trades` | 30 |
| `max_open_positions` | 5 |
| `leverage` | 5 |
| `bet_usd` | 4 |
| `max_drawdown_pct` | 20 |

### Agent Jalan A

| Flag | Nilai |
|---|---|
| `agent_manager_mode` | **ON** |
| `agent_ab_shadow` | **ON** |
| `agent_full_auto` / tool_loop | **OFF** |

**Posture efektif:** arah = **RULES** (bukan gemini-trader), planner ON, autonomous ON, A/B shadow ON (ReAct catat, tidak memblokir).

**SL fixed (pemilik):** `agent.allow_move_sl: false` — manage/agent **tidak** boleh
geser SL (no BE / trail / tighten / micro-profit lock). Exit hanya SL/TP/liq asli +
FLAT/manual. Set `true` di config hanya bila ingin kunci profit via BE lagi.

### Sengaja belum

- H28 paper daemon — **belum** start (tunggu ≥7 hari proses stabil)
- L2 collector / H30 profit — **jangan** hidupkan

### Incident yang sudah diperbaiki (jangan diulang)

- 2× `forwardtest` → orphan di-kill; hanya PM2 (+ file lock + `restart.sh`)
- Dashboard zombie `/tmp/run_dash.py` di :8000 → diganti PM2
- UI open vs screening desync (ghost event / status lag) → botstate live + status-on-open
- Duplikat trade history tanpa side/entry → fix `build_trades` + dashboard bersih
- AI decide-cache menembus manager-mode (Jalan A) → 0 entry / flat Gemini palsu
  (fix 2026-07-20: gate `use_gemini_trader` + clear cache)
- `daily_max_loss_pct` drift 50 → dikembalikan 5 (ulang 2026-07-20; cek tiap sesi)
- conf 0.65 + OF ketat + CVD kosong → 0 LONG/SHORT palsu (kalibrasi conf 0.30 / OF fail-open)
- `ReactAgent.decide` tak terima `halving_phase` → crash seluruh `on_cycle` di bar close
- seed `last_closed=index[-2]` → UI "—" / atr null s/d bar 15m berikutnya
- Key Gemini “tak sampai 26” di UI → inventory 26 OK; usage bias health-sort + 403 denied
  (#1/#2/#8) + call hemat Jalan A (fix: LRU murni + skip limit dgn cooldown ter-parse)

---

## 3. Fase sekarang

```
[0] Kontrak     ✅
[1] Survival    ✅ dry lock
[2] Jalan A     ✅ manager + ab_shadow
[3] H28         ⏸ setelah 7 hari checklist
[4] Hipotesis   ⏸ spek ketat saja
[5] Live        🚫
```

**Tugas user harian:** isi [CHECKLIST_HARIAN.md](../CHECKLIST_HARIAN.md) (~10 menit).  
**Tugas agent sesi baru:** baca file ini + plan; **jangan** usulkan OHLCV/L2 edge hunt.

---

## 4. Cara Grok “ingat” setelah CLI close

| Lapisan | Bertahan close? | Cara |
|---|---|---|
| **Chat sesi ini** | ❌ (kecuali `/resume`) | Hilang saat quit tanpa resume |
| **File di repo** (ini + plan) | ✅ + GitHub | **Paling andal** — auto lewat project rules |
| **Grok Memory** (`~/.grok/memory/`) | ✅ bila diaktifkan | Default **OFF** — lihat §5 |
| **Session disk** | ✅ | `/resume` di TUI |

**Jangan andalkan** “model ingat sendiri”. Andalkan **file + GitHub**.

---

## 5. Aktifkan Grok Memory (opsional, disarankan)

Di mesin user (sekali):

```toml
# ~/.grok/config.toml
[memory]
enabled = true
```

Atau tiap launch: `grok --experimental-memory` / env `GROK_MEMORY=1`.

Di TUI, **sebelum close sesi penting**:

```
/flush
```

Opsional:

```
/remember operational plan: survival + Jalan A dry; see PLAN_OPERASIONAL.md and memory/SESSION_HANDOFF.md
```

Cek: `/memory` · sesi lama: `/resume`.

---

## 6. Prompt pembuka sesi baru (copy-paste)

```
Baca memory/SESSION_HANDOFF.md + PLAN_OPERASIONAL.md + CHECKLIST_HARIAN.md.
Lanjutkan operasional Jalan A (jangan usul edge OHLCV/L2).
Status: kita di fase awasi 7 hari; bantu [isi checklist / audit server / H28 hanya jika gerbang lolos].
```
