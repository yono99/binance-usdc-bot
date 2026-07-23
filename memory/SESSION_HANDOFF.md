# Session handoff — baca dulu di sesi Grok CLI baru

> **Tujuan file ini:** supaya konteks **tidak hilang** saat TUI/CLI ditutup.  
> Di-load lewat project rules (`AGENT.md` + `.grok/rules/`).  
> Update baris “Status terakhir” bila posture server berubah.

**Terakhir diisi:** 2026-07-21 (forward mixin split + research/ + ARCHITECTURE kanonik)

### Scoreboard edge (jawab “sudah dapat berapa edge?”)

| Kelas | Jumlah | Arti |
|---|---:|---|
| **PROMOTE_PAPER** (edge entry, boleh paper-size) | **0** | Belum ada. Jangan klaim profit edge. |
| **PROMOTE_FILTER_PAPER** (meta risk overlay) | **2** | Bukan entry alpha — hanya kandidat ↓DD |
| **WATCHLIST** (arah OOS+ tapi gagal bar penuh) | **1** | LINK residual-z fade vs BTC (p_adj gagal) |
| **CANDIDATE (ilmu pemilik)** | **CE-STANCE** | Stance/size long di dump/markdown/unlock — **bukan** PROMOTE |
| Arms OOS diuji (A–R10 + risk harness) | ~300+ | “Tidak ketemu entry” = hasil valid |

**Dua filter (bukan edge entry):**
1. `skip_breadth_lo` — skip entry saat breadth (% alt > SMA50) di bottom 30% lookback 100d  
2. `skip_corr_or_volhi` — skip saat avg corr tinggi **atau** BTC vol20 tinggi (kuartil atas)

**Wire runtime (shadow only):** `bot/risk_filter.py` · `ForwardTester._refresh_risk_filter`  
- `agent.risk_filter_shadow: true` → log `RISK_FILTER_SHADOW` + stamp open  
- **`agent.risk_filter_block: false`** → **tidak** hard-block (wajib sampai paper risk A/B)  
- Panel: `data/risk_filter_panel.npz` (+ snap bila unpickle OK)  
- Dry live smoke: `allow=False`, reasons=`breadth_lo` (breadth≈0.025) — **shadow saja**

**ReAct A/B dry (awal):** n kecil (~7) → exp_R **NOT_PROVEN**; indikasi reduces_risk underpowered.

Detail angka: [EDGE_HUNT.md](EDGE_HUNT.md) · [RESEARCH_LOG.md](../RESEARCH_LOG.md) ·  
`logs/edge_hunt_validate_risk_filter.json`

### Lanjut sesi baru (baca 60 detik — anti konteks penuh)

Bila chat/TUI penuh atau sesi baru: **jangan** andalkan transcript. Baca urutan ini:

| # | File | Untuk apa |
|---|---|---|
| 0 | **[CONTINUE.md](CONTINUE.md)** | 1 halaman fakta terkunci |
| 1 | **file ini** (scoreboard + § Status) | edge count + posture server |
| 2 | **[EDGE_HUNT.md](EDGE_HUNT.md)** | scoreboard hunt + antrian |
| 3 | [PLAN_OPERASIONAL.md](../PLAN_OPERASIONAL.md) | survival + Jalan A |
| 4 | [RESEARCH_LOG.md](../RESEARCH_LOG.md) (tail) | detail putaran |

**Git tip (master):** `9e37df6` (handoff tip) · `b8e924f` (npz panel) · `c67d34c` (shadow wire) ·  
sebelumnya R7–R10 di tree yang sama. `git pull` dulu di server.

**Perintah lanjut yang disarankan (pilih satu jalur, jangan OHLCV retread):**
```text
# A) Candidate CE-STANCE: mode=shadow di dry → log CANDIDATE_EDGE_SHADOW (n≥30) → baru size dry
# B) Kumpulkan risk_filter SHADOW di dry — A/B risk maxDD/std/worst (bukan exp_R entry)
# C) Shadow LINK residual (WATCHLIST only) — log only, no sizing
# D) Scaffold funding/OI panel hist bila cache ada — konstruk ≠ H15/H24/H25
```

**Sudah ditolak / jangan ulang tanpa novelty:** H24–H32, H-CYC short unlock, crash-bounce pure,
short-alts markdown-only, 1h majors net cost, re-tune thr LINK, `risk_filter_block` tanpa bukti paper.
**Jangan** auto-short dump/unlock; **jangan** `allow_live`+`risk_ack` sebelum D2 dry lolos.

### Status terakhir (2026-07-23)

- **Equity paper dry (ukur via SSH → journal):** trough dalam **$5.40** (7 Jul) / low **$5.65**
  (12 Jul) → last close **~$22.26** (23 Jul). exp_R **all-time −0.56** (tercemar arch lama);
  **adil open≥20 Jul (ghost+manager fix): exp_R ≈ +0.12 · WR 52% · n=91** — **bukan**
  PROMOTE_PAPER. Detail: [EQUITY_SNAPSHOT.md](EQUITY_SNAPSHOT.md) §0.

- **Dry + LIVE paralel — didukung (lock per-mode):**
  - `forwardtest.py`: lock `logs/forwardtest_<mode>.lock` (bukan 1 global)
  - Dry + live = 2 proses OK; **2× mode sama = dilarang**
  - Spek sumber data LIVE (saldo/posisi/orders dari Binance key `.env`;
    riwayat UI = journal bot): [LIVE_AND_DRY.md](LIVE_AND_DRY.md)
  - PM2: `bot` dry default; `bot-live` opsional di `ecosystem.config.cjs`
  - DRAWDOWN LOCK di Settings UI; daily stop-loss harian retired (0)

### Status sebelumnya (2026-07-21)

- **Refactor arsitektur (poin 1–4, pemilik setuju) — SELESAI:**
  - `ForwardTester` dipecah mixin (perilaku 1:1): `bot/forward_gates.py`,
    `forward_open.py`, `forward_close.py`, `forward_status.py` + shell `forward.py`
  - Entry pipeline: [ENTRY_PIPELINE.md](../ENTRY_PIPELINE.md)
  - Kanonik: [ARCHITECTURE.md](../ARCHITECTURE.md) (arsip patch log tetap di
    `ARCHITECTURE_DOCUMENTATION.md`)
  - Research offline: `research/` (`edge_hunt*`, `cyc*`, `h28*`, `*_alpha`, …)
    — jalankan `python research/edge_hunt.py` dari **repo root**
  - `.gitignore`: `tmp_*.py` / `tmp_*.json` / …
  - **Bukan** behavior change trading; PROMOTE_PAPER tetap 0

- **Trade review SQLite (belajar di bawah pondasi) — AKTIF:**
  - Spek: [TRADE_REVIEW.md](TRADE_REVIEW.md) · `bot/trade_review.py` · tabel `trade_reviews`
  - Tiap close → post-mortem: error_class, lesson IF/THEN, `conflicts_foundation`
  - Inject ke ReAct **hanya** injectable & !conflict (soft); **bukan** auto-edge
  - Hierarki: HARD → **ilmu/CE pondasi** → review → prompt → edge terpisah (CE path)
  - Wire: `ForwardTester._post_mortem_close` + `merge_lessons_for_prompt`
  - Tes: `tests/test_trade_review.py`

- **Candidate edge CE-STANCE — dual-track AKTIF (pemilik setuju 2026-07-21):**
  - Spek: [CANDIDATE_EDGE.md](CANDIDATE_EDGE.md) · checklist: [LIVE_MICRO_CHECKLIST.md](LIVE_MICRO_CHECKLIST.md)
  - **Pondasi** = ilmu pemilik; **bukan** PROMOTE_PAPER
  - Config: `mode: size` · `allow_live: true` · `risk_ack: true` · `stop_loss_r_live: -5`
  - **1:1 aturan** dry & live; live risk lock **lebih ketat** di UI mode=live
  - Stop: `logs/ce_live_state.json` · Hakim: `python ce_report.py`
  - **Bukan** auto-short; **bukan** auto-promote
  - Tes: `tests/test_cycle_candidate.py`

- **Edge hunt open loop SELESAI tahap 1 — ENTRY EDGE = 0:**
  - ~300 arms OOS (A–F, deep, crash, volspike, R2–R10 pairs)
  - **PROMOTE_PAPER = 0** (jujur; jangan wire entry baru / jangan bilang “sudah ada edge”)
  - **PROMOTE_FILTER_PAPER = 2** (breadth_lo + corr/vol) — meta only, shadow di dry
  - **WATCHLIST = 1:** LINK residual-z fade vs BTC — OOS+ lockbox+ cost×2+ tapi **p_adj gagal**
  - Log: [EDGE_HUNT.md](EDGE_HUNT.md) · `RESEARCH_LOG.md` · `logs/edge_hunt*.json`
  - Harness: `research/edge_hunt.py` + `research/edge_hunt_round*.py` + validators
  - **Jangan** retread H24–H32 / crash-bounce murni / short-alts markdown-only
  - Lanjut: kumpulkan shadow risk-filter / alt-data hist / LINK log-only — **bukan** re-tune thr

- **Risk-filter deploy dry SELESAI (2026-07-21):**
  - Modul + gate + tes + docs + `data/risk_filter_panel.npz`
  - Server `192.168.1.107`: `git pull` + `./restart.sh` → **tepat 1** forwardtest dry
  - Filter evaluasi OK (bukan `no_panel`); hard block **OFF**
  - Commits: `c67d34c` → `b8e924f` → tip handoff

- **P2/P3 siklus SELESAI (ukur + inject, bukan hard gate):**
  - Modul: `bot/cycle_regime.py` · riset `research/cyc02_cycle_unlock_altseason.py`
  - Wire: `forward._cycle_context` → ReAct observe + Gemini `build_context`
  - Curriculum: `btc_dominance` / `halving_cycle` = stance only (hapus klaim boost short OOS)
  - Verdict: phase/dom **CONTEXT_ONLY**; unlock **INCONCLUSIVE** (isi `data/unlock_calendar.csv` manual)
  - Snap asof: phase=**markdown**, cal=bear, dom=neutral, DD ATH ~−52%
  - **Jangan** hard gate / auto-short unlock / FLAT dari cycle labels
  - Detail: [CRYPTO_CYCLE_KNOWLEDGE.md](CRYPTO_CYCLE_KNOWLEDGE.md) §4 P2/P3

- **Proxmox paper dry (postur terkunci — cek tiap deploy):**
  risk: daily_loss **OFF (0)** · DD lock **20%** / trades 30 / max_open 5 / lev 5 / bet 4;
  manager **OFF**, ab_shadow **ON**;
  dump_short_boost false; risk_filter_shadow ON / block OFF;
  **cycle_candidate mode=size** (allow_live+risk_ack ON di config — live enforce
  hanya saat proses **mode=live**; dry paper size-down aktif).
  **Cek risk tiap deploy** (sering drift loss %). Live mikro: lihat checklist.

- **P0 H-CYC-01 SELESAI** — beta>1 CONFIRMED; short_weak **REJECT**.
- **P0b SELESAI** — 598 alt frac~65%; block_long hold1 REJECT; boost short OFF.

- **`agent_manager_mode` = OFF** (pemilik setuju setelah audit `agent_flat` vs chart).
  Autonomous/FLAT massal tidak lagi di-force. `agent_ab_shadow` tetap ON.
  Hot-reload via `POST /api/agent-settings` — bot pakai siklus berikutnya (tanpa restart wajib).

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
| `daily_max_loss_pct` | **0** (retired 2026-07-23 — pakai DD lock) |
| `daily_max_trades` | 30 |
| `max_open_positions` | 5 |
| `leverage` | 5 |
| `bet_usd` | 4 |
| `max_drawdown_pct` | **20** (Settings UI; set 25 bila mau) |

### Agent (postur 2026-07-20/21, setelah audit agent_flat + risk-filter)

| Flag | Nilai |
|---|---|
| `agent_manager_mode` | **OFF** (dimatikan 2026-07-20) |
| `agent_ab_shadow` | **ON** |
| `agent_autonomous` / planner / full_auto / tool_loop | **OFF** |
| `risk_filter_shadow` (config.yaml `agent.`) | **ON** (log would-deny) |
| `risk_filter_block` | **OFF** (jangan nyalakan tanpa paper risk A/B) |

**Kenapa manager OFF:** audit 8× `agent_flat` → sum R **−2.91**, mean **−0.36**; chart
menunjukkan ~3/8 premature cut (WLD/ZEC recovery). Manager memaksa `autonomous=True`
→ FLAT massal; REDUCE_RISK mati (`allow_move_sl=false`). Survival = biarkan SL/TP,
bukan exit LLM massal.

**Posture efektif sekarang:** A/B shadow ON (ReAct catat, tidak tutup posisi);
**tidak** ada force autonomous/FLAT dari manager. Entry mengikuti technique/settings
(bukan override Jalan A manager).

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
[2] Jalan A     ✅ ab_shadow ON; manager OFF; risk_filter SHADOW ON / block OFF
[3] Edge hunt   ✅ tahap 1 done — PROMOTE_PAPER=0; FILTER=2 shadow; WATCHLIST=1
[4] H28         ⏸ setelah 7 hari checklist
[5] Hipotesis   ⏸ spek ketat — jangan retread; alt-data / kumpulkan shadow
[6] Live        🚫
```

**Tugas user harian:** isi [CHECKLIST_HARIAN.md](../CHECKLIST_HARIAN.md) (~10 menit).  
**Tugas agent sesi baru:** baca [CONTINUE.md](CONTINUE.md) + file ini + plan + EDGE_HUNT;  
**ingat scoreboard:** edge entry **0**; filter **2** shadow only;  
**jangan** usulkan OHLCV/L2 retread / hard-block filter / live tanpa promotion.

---

## 7. Backlog — ilmu siklus BTC/alt (pemilik, 2026-07-20)

**Dokumen penuh:** [CRYPTO_CYCLE_KNOWLEDGE.md](CRYPTO_CYCLE_KNOWLEDGE.md)

Ilmu (ringkas): BTC dump ≳2% → alt sering lebih dalam (short alt **setelah** cek
relative weakness vs BTC); unlock supply tanpa kabar bagus → short swing hari–minggu;
halving / BTC.D → sedikit alt ikut; alt-season ≈ BTC sideways + breadth alt hijau;
4 fase (akumulasi → uptrend → distribusi/FOMO short ritel → bear); pendorong:
halving, ETF/institusi, makro.

**Validasi agent:** bagus sebagai **regime + filter + event** (bukan entry AI bebas).
Sebagian sudah stub di kode (`dump_flag`, `dominance_dir`, `_halving_phase`) — masih kasar.

| Prioritas | Kerja | Status |
|---|---|---|
| **P0** | H-CYC-01 short_weak OOS | ✅ REJECT entry; beta OK |
| **P0b** | Universe 598 + block_flag + block_long | ✅ frac~65% stabil; block_long hold1 REJECT; boost short unproven |
| **P1** | Disable `BTC_DUMP_BOOST` ×1.5 short | ✅ `btc.dump_short_boost: false` + gate di forward |
| **P1** | ~~`alt_beta_short` / hard block_long~~ | ❌ ditolak data |
| **P1** | `dominance_dir` / alt-season breadth | ✅ diukur via BTCDOM → CONTEXT_ONLY |
| **P2** | Token unlock / supply → bearish | ✅ n=471 curated; **OOS short7 NOT_PROVEN** (train hijau, OOS −0.31%) |
| **P2** | Fase siklus terukur | ✅ CONTEXT_ONLY (markdown now) |
| **P3** | Inject cycle_context ReAct/Gemini | ✅ stance/SKIP only — no hard gate |

**Larangan:** manager ON, H30/L2, claim short-after-dump / block_long universal, hard gate fase/unlock, FLAT dari cycle labels.

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
Baca memory/CONTINUE.md + memory/SESSION_HANDOFF.md (scoreboard edge dulu) +
PLAN_OPERASIONAL.md + CHECKLIST_HARIAN.md + memory/EDGE_HUNT.md.
Fakta: PROMOTE_PAPER=0; PROMOTE_FILTER_PAPER=2 (shadow only); WATCHLIST=1 LINK.
Lanjutkan Jalan A (manager OFF, ab_shadow ON, risk_filter_block OFF).
Jangan klaim edge entry; jangan hard-block filter; jangan OHLCV/L2 retread.
Status: kumpulkan shadow risk / checklist 7 hari; H28 hanya bila gerbang lolos.
```
