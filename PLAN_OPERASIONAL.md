# Rencana Operasional — Survival + Jalan A (disiplin)

> **Status:** AKTIF sejak 2026-07-19.  
> **Kontrak pemilik:** mengikuti rekomendasi metodologi (bukan “profit tiap hari”).  
> **Mesin acuan:** `192.168.1.107` (PM2: `bot` + `dashboard`), mode **`dry`**.

Dokumen ini mengunci **tujuan baru operasional** setelah program riset arah
(22+ hipotesis) **tidak menemukan edge tradeable**. Nilai proyek tetap:
sistem uji jujur + survival + disiplin — lihat [TUJUAN.md](TUJUAN.md),
[METHODOLOGY.md](METHODOLOGY.md), [AGENT.md](AGENT.md).

---

## 1. Apa yang berubah (dan apa yang tidak)

| Tetap | Baru (operasional) |
|---|---|
| OOS = hakim; 0 klaim profit palsu | Target harian = **proses & risk**, bukan $ hijau |
| LLM ≠ peramal arah | **Jalan A:** agent = **manajer disiplin** |
| Live dilarang tanpa bukti | Paper dry dengan **rem ketat** |
| H30 maker ritel **DITOLAK** langkah 3 | **Jangan** buka ulang L2 “cari edge” |

**Bukan tujuan:** win rate tinggi, cuan konsisten tiap hari, 5 hipotesis L2 paralel.

**Adalah tujuan:**

1. **Survival** — tidak blow-up; hormati circuit harian & kill-switch DD.  
2. **Disiplin terukur** — A/B shadow: apakah ReAct/manajer **mengurangi risiko**.  
3. **Satu jalur riset sabar** — H28 paper param beku (belum diwajibkan start di fase ini).  
4. **Hipotesis baru hanya spek ketat** — bukan varian OHLCV/H24–H32 yang sudah mati.

---

## 2. Peta fase

```
[0] KONTRAK MENTAL     ✅ 2026-07-19
[1] SURVIVAL LOCK      ✅ diterapkan dry (lihat §3)
[2] JALAN A + A/B      ✅ manager_mode + ab_shadow ON
[3] H28 PAPER          ⏸ menyusul setelah 7 hari proses stabil
[4] HIPOTESIS BARU     ⏸ hanya sumber data/struktural belum diuji
[5] LIVE SCALE         🚫 dilarang tanpa CANDIDATE + paper
```

---

## 3. Posture dry yang dikunci (2026-07-19)

Diterapkan lewat `settings_store` (hot-reload), **hanya mode `dry`**.
Live/test tidak diubah.

### 3.1 Risk (Fase 1)

| Field | Nilai | Alasan |
|---|---:|---|
| `daily_max_loss_pct` | **5** | Circuit harian (sebelumnya 50 = longgar) |
| `daily_max_trades` | **30** | Batas frekuensi (sebelumnya 200) |
| `max_open_positions` | **5** | Slot terbatas (sebelumnya 10) |
| `leverage` | **5** | Paper disiplin (sebelumnya 10) |
| `bet_usd` | 4 | Tetap kecil |
| `max_drawdown_pct` | 20 | Kill-switch kumulatif (tetap) |

### 3.2 Agent — Jalan A (Fase 2)

| Flag | Nilai |
|---|---|
| `agent_manager_mode` | **false** (OFF 2026-07-20: agent_flat −EV vs chart) |
| `agent_ab_shadow` | **true** |
| `agent_full_auto` | false |
| `agent_tool_loop` | false |

**Posture efektif** (`ForwardTester._agent_posture`):

| | |
|---|---|
| Arah entry | **RULES** (`use_gemini_trader=false` meski `technique` di store bisa masih `"gemini"`) |
| Planner | ON (hanya mengetatkan kuota/stance) |
| Autonomous | ON (hanya REDUCE_RISK / FLAT) |
| Tool-loop | OFF (frugal RPD) |
| A/B shadow | ON — ReAct **mencatat** verdict, **tidak memblokir** entry rules |

Detail konsep: [AGENT.md](AGENT.md) (Jalan A, A/B harness, planner).

### 3.3 Proses server (hardening sesi yang sama)

- Orphan `forwardtest` kedua di-kill → **hanya 1 bot PM2**.  
- Dashboard zombie lama di `:8000` sudah diganti proses PM2 bersih (riwayat trade).  
- `data/l2` kosong / collector tidak di PM2 — **sengaja tidak dihidupkan untuk profit**.

---

## 4. KPI (bukan “profit tiap hari”)

### Harian (~10 menit)

Checklist siap pakai (centang + log 1 baris): **[CHECKLIST_HARIAN.md](CHECKLIST_HARIAN.md)**.

- Apakah daily loss 5% dihormati (stop entry bila kena)?  
- Jumlah trade ≤ 30?  
- Error bot / double process? (`pm2 list`, satu `forwardtest`)  
- **Bukan** KPI: “hari ini hijau?”

### Mingguan (~1 jam)

- exp_R paper (setelah fee) — **boleh flat/negatif**; catat jujur  
- Max DD, worst R  
- A/B: `reduces_risk`, exp_R kept vs denied (`ab_report.py` / `/api/ab`)  
- decision_log: rasio sumber LLM vs fallback

### 7 hari (gerbang lanjut ke H28)

| Lolos proses | Gagal proses |
|---|---|
| 1 bot, risk lock tidak dilonggarkan impulsif | Longgarkan limit karena merah 1 hari |
| Manager + ab_shadow tetap ON | Matikan agent tanpa data |
| Log lengkap | Force trade / multi-bot |

### 90 hari (sukses realistis)

| Level | Arti |
|---|---|
| **A — Proses** | Survival, rule dihormati, 0 strategi impulsif |
| **B — Disiplin** | A/B menunjukkan risiko turun (`reduces_risk`) meski exp_R `NOT_PROVEN` |
| **C — Edge** | H28 (atau 1 hipotesis spek-ketat) lolos — **jangan dipaksakan** |

---

## 5. Larangan operasional (anti-daftar)

1. Mengejar “profit konsisten tiap hari” sebagai KPI.  
2. Menambah indikator OHLCV / membuka v5–v7 / H24–H32 “sedikit diubah”.  
3. Menguji **banyak** varian L2 sekaligus lalu pilih yang menang (multiple testing).  
4. Membuka ulang **H30 maker ritel** (replay konservatif −7…−11 bps; edge antrian MM).  
5. Auto-tune threshold live diam-diam.  
6. Dua proses bot menulis DB yang sama.  
7. Scale live tanpa CANDIDATE OOS + paper param beku.

---

## 6. Urutan lanjut (setelah dokumen ini)

1. **Jaga AB 7 hari** — kumpulkan A/B + survival metrics.  
2. **H28 paper** — `research/h28_forward.py`, parameter **beku**, evaluasi pertama hanya setelah n siklus cukup (lihat pra-registrasi di `RESEARCH_HYPOTHESES_PHASE4.md`).  
3. **Hipotesis baru (kandidat struktural)** — ilmu siklus BTC/alt pemilik:
   [memory/CRYPTO_CYCLE_KNOWLEDGE.md](memory/CRYPTO_CYCLE_KNOWLEDGE.md)
   (beta dump, relative weakness, unlock, alt-season, 4 fase). **P0 ukur OOS dulu**;
   bukan OHLCV indikator baru / H24–H32 revival.  
4. **Live** — hanya setelah bukti, size micro.

---

## 7. Referensi cepat

| Dokumen | Isi |
|---|---|
| [TUJUAN.md](TUJUAN.md) | Tujuan proyek & peran LLM |
| [METHODOLOGY.md](METHODOLOGY.md) | Cara uji, temuan OOS |
| [AGENT.md](AGENT.md) | ReAct, Jalan A, A/B, planner |
| [memory/CRYPTO_CYCLE_KNOWLEDGE.md](memory/CRYPTO_CYCLE_KNOWLEDGE.md) | Ilmu siklus BTC/alt + backlog P0–P3 |
| [memory/SESSION_HANDOFF.md](memory/SESSION_HANDOFF.md) | Status server + §7 backlog |
| [RESEARCH_LOG.md](RESEARCH_LOG.md) | Log hipotesis & verdict |
| [RESEARCH_HYPOTHESES_PHASE4.md](RESEARCH_HYPOTHESES_PHASE4.md) | H24–H32, H28/H30 pra-reg |
| [DEPLOY.md](DEPLOY.md) | PM2 / server |

---

## 8. Changelog dokumen

| Tanggal | Perubahan |
|---|---|
| 2026-07-20 | Kandidat hipotesis struktural: CRYPTO_CYCLE_KNOWLEDGE (backlog; P0 dulu) |
| 2026-07-20 | agent_manager_mode OFF (audit agent_flat); ab_shadow tetap ON |
| 2026-07-19 | Dokumen dibuat; kontrak pemilik; risk dry + Jalan A diterapkan di server paper |
