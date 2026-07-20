# Session handoff — baca dulu di sesi Grok CLI baru

> **Tujuan file ini:** supaya konteks **tidak hilang** saat TUI/CLI ditutup.  
> Di-load lewat project rules (`AGENT.md` + `.grok/rules/`).  
> Update baris “Status terakhir” bila posture server berubah.

**Terakhir diisi:** 2026-07-20

### Status terakhir (2026-07-20 ~04:10 UTC)

- Bot PM2 online; dry `enabled=true`; manager + ab_shadow ON; risk dry **loss 5%**
  (sempat drift ke 50 — dikunci lagi).
- **Entry paper HIDUP lagi** (setelah 0 entry sejak `agent_flat` 2026-07-19):
  OPEN ACE LONG, BCH SHORT, ZEC SHORT — `open_count=3`, `day_trades=3`.
  Sinyal LONG/SHORT lain ada tapi ditahan **margin USDT habis** (pool ~$5.79) — normal
  untuk akun mikro, bukan bug gate.
- Root cause 0 entry (berlapis), semua di-deploy server:
  1. AI decide-cache mereplay flat Gemini di manager-mode → clear cache bila non-gemini
  2. `entry_confidence` 0.65 vs skor live p90≈0.37 → preset auto/gemini **0.30**; OF off
  3. `decide_v4` OF fail-open bila CVD/taker kosong
  4. `gate_overext` / `gate_runup` off di paper (ukur frekuensi dulu)
  5. **Crash siklus:** `ReactAgent.decide(...halving_phase=)` TypeError → fix signature
  6. Seed set `last_closed` → evaluasi tertunda 1 bar; seed **tidak** lagi set last_closed
- File: `bot/forward.py`, `bot/react_agent.py`, `bot/settings_store.py`,
  `bot/strategy_lab.py`, `config.yaml`.
- **Batas jujur:** paper ~$10 **bukan** mesin bayar hutang. Disiplin + bukti OOS, bukan
  janji $ harian. Jangan longgarkan risk / buka H30-L2 karena ada 3 entry.

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

### Sengaja belum

- H28 paper daemon — **belum** start (tunggu ≥7 hari proses stabil)
- L2 collector / H30 profit — **jangan** hidupkan

### Incident yang sudah diperbaiki (jangan diulang)

- 2× `forwardtest` → orphan di-kill; hanya PM2
- Dashboard zombie `/tmp/run_dash.py` di :8000 → diganti PM2
- Duplikat trade history tanpa side/entry → fix `build_trades` + dashboard bersih
- AI decide-cache menembus manager-mode (Jalan A) → 0 entry / flat Gemini palsu
  (fix 2026-07-20: gate `use_gemini_trader` + clear cache)
- `daily_max_loss_pct` drift 50 → dikembalikan 5 (ulang 2026-07-20; cek tiap sesi)
- conf 0.65 + OF ketat + CVD kosong → 0 LONG/SHORT palsu (kalibrasi conf 0.30 / OF fail-open)
- `ReactAgent.decide` tak terima `halving_phase` → crash seluruh `on_cycle` di bar close
- seed `last_closed=index[-2]` → UI "—" / atr null s/d bar 15m berikutnya

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
