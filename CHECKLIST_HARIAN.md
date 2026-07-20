# Checklist harian — Survival + Jalan A

> **Mulai:** 2026-07-19 · **Durasi fase:** 7 hari proses · **Mode:** `dry` (paper)  
> **Waktu:** ~10 menit · **KPI:** proses & risk — **bukan** “hari ini hijau?”  
> Rencana induk: [PLAN_OPERASIONAL.md](PLAN_OPERASIONAL.md)

Isi **satu baris log** di §5 setiap hari. Centang `[x]` di salinan pribadi / notes;  
file ini di repo = **template** (boleh di-fork ke notes lokal agar git tetap bersih).

---

## 0. Target posture (jangan digeser impulsif)

| Item | Harus |
|---|---|
| Mode aktif | `dry` |
| Bot proses | **tepat 1** `forwardtest` (PM2 `bot`) |
| `daily_max_loss_pct` | **5** |
| `daily_max_trades` | **30** |
| `max_open_positions` | **5** |
| `leverage` | **5** |
| `agent_manager_mode` | **OFF** (sejak 2026-07-20; cegah agent_flat massal) |
| `agent_ab_shadow` | **ON** |
| `agent_full_auto` / tool-loop / autonomous | **OFF** |
| H30 / L2 “cari edge” | **jangan hidupkan** |

UI: `http://192.168.1.107:8000` · Agent: `/agent`

---

## 1. Cek cepat (urut, ~10 menit)

### A. Mesin hidup

- [ ] PM2: `bot` + `dashboard` **online**
- [ ] Hanya **satu** proses bot (bukan dua `forwardtest`)
- [ ] Dashboard `:8000` merespons (buka UI / refresh)

```bash
# di server (opsional)
pm2 list
ps aux | grep forwardtest | grep -v grep   # harus tepat 1
# setelah git pull / restart penuh:
# cd /root/binance-usdc-bot && git pull && ./restart.sh
```

### B. Risk lock masih utuh

- [ ] Daily loss limit masih **5%** (tidak dilonggarkan)
- [ ] Max trade harian masih **30**
- [ ] Max posisi **5**, leverage **5**
- [ ] Bila circuit harian kena: **stop entry dihormati** (tidak “paksa trade”)

### C. Agent posture

- [ ] Manager-mode **OFF** (jangan nyalakan ulang tanpa baca audit agent_flat)
- [ ] A/B shadow **ON**
- [ ] Full-auto / tool-loop / autonomous **OFF**
- [ ] (Opsional) Ada jejak keputusan di log / halaman Agent

### D. Health trading (jujur, tanpa panik)

- [ ] Catat: trade hari ini ≈ ___ / max 30
- [ ] Catat: PnL paper hari ini (boleh merah) ___
- [ ] Catat: posisi open sekarang ___
- [ ] Tidak ada error berulang di log (crash loop, bind port, DB lock)

### E. Larangan hari ini (centang = patuh)

- [ ] **Tidak** ubah strategy / indikator karena 1 hari merah/hijau
- [ ] **Tidak** longgarkan risk “sementara”
- [ ] **Tidak** nyalakan manager-mode / autonomous tanpa alasan tertulis (audit flat)
- [ ] **Tidak** start H28 / L2 collector untuk “cari cuan”
- [ ] **Tidak** hidupkan live

---

## 2. Bila ada yang salah (triage 2 menit)

| Gejala | Tindakan |
|---|---|
| 2 proses `forwardtest` | `./restart.sh` (kill orphan + 1 PM2 bot) — **jangan** biarkan manual + PM2 |
| UI open 0 tapi screening “ada posisi” | Cek `ps` 1 bot; refresh; bila status lag, tunggu 1 siklus / cek botstate |
| Dashboard mati / port 8000 aneh | `./restart.sh` atau `pm2 restart dashboard`; cek zombie non-PM2 |
| Manager/A-B tiba-tiba OFF | Nyalakan lagi; catat di log §5 “siapa/apa yang matikan” |
| Risk longgar dari UI | Kembalikan ke §0; catat “hampir melanggar” |
| Bot crash loop | `pm2 logs bot --lines 50`; **jangan** longgarkan risk sebagai “fix” |

---

## 3. Yang **bukan** tugas harian

- Optimasi parameter / grid strategy  
- Baca chart cari setup baru  
- Bandingkan “kok kemarin lebih cuan”  
- Push fitur edge baru  

Mingguan saja: exp_R, DD, A/B `reduces_risk` — lihat [PLAN_OPERASIONAL.md](PLAN_OPERASIONAL.md) §4.

---

## 4. Gerbang hari ke-7 (sebelum bahas H28)

Centang **semua** sebelum minta lanjut Fase 3:

- [ ] ≥7 entri log harian (§5) terisi  
- [ ] Risk lock §0 tidak diubah “karena emosional”  
- [ ] Manager + A/B tetap ON hampir sepanjang minggu  
- [ ] Selalu 1 bot (tidak double process berulang)  
- [ ] Siap terima: exp_R paper boleh flat/negatif  

**Lolos proses** → boleh bahas start H28 paper (param beku).  
**Gagal proses** → ulangi 7 hari; jangan scale / jangan live.

---

## 5. Log harian (isi 1 baris / hari)

Salin ke notes pribadi (disarankan) atau append di bawah jika kamu memang ingin commit log:

| Hari | Tanggal | 1 bot? | Risk OK? | Mgr+AB? | #trade | PnL paper | Catatan (1 kalimat) | Patuh? |
|---:|---|:---:|:---:|:---:|---:|---:|---|:---:|
| 0 | 2026-07-19 | ✅ | ✅ | ✅ | — | — | Checklist dibuat; posture AB aktif | ✅ |
| 1 | | | | | | | | |
| 2 | | | | | | | | |
| 3 | | | | | | | | |
| 4 | | | | | | | | |
| 5 | | | | | | | | |
| 6 | | | | | | | | |
| 7 | | | | | | | | |

**Cara isi cepat**

- `1 bot?` / `Risk OK?` / `Mgr+AB?` / `Patuh?` → ✅ atau ❌  
- `#trade` → jumlah trade closed hari itu (perkiraan dari UI Riwayat / stats)  
- `PnL paper` → angka atau “merah/hijau kecil” — **bukan** skor keberhasilan  
- `Catatan` → mis. “circuit OK”, “dashboard restart”, “hampir longgarkan loss — urung”

---

## 6. Ritual 60 detik (kalau sangat sibuk)

1. Buka dashboard → bot ON, mode dry  
2. Settings: loss 5 / trades 30 / pos 5 / lev 5  
3. Agent: manager ON, A/B ON  
4. Tulis 1 baris di tabel §5  
5. Tutup — **selesai**

---

## Setelah tutup Grok CLI

Chat sesi **tidak** otomatis diingat. Agar sesi berikutnya sadar plan ini:

1. File repo (sudah ada): `memory/SESSION_HANDOFF.md` + plan ini — **paling andal**
2. Opsional: aktifkan Grok Memory + `/flush` sebelum quit (lihat handoff §5)
3. Atau `/resume` sesi yang sama

## Changelog

| Tanggal | |
|---|---|
| 2026-07-19 | Checklist dibuat; Day 0 = posture AB sudah diterapkan di server |
| 2026-07-19 | Catatan persistensi sesi CLI + link SESSION_HANDOFF |
