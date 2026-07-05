# Akses Log & Pemantauan (untuk manusia & LLM sesi berikut)

> **Untuk LLM:** ADA log runtime. Sebelum menebak, BACA log dulu. Bot menulis JSONL;
> tak perlu bikin API baru — dashboard FastAPI sudah menyajikannya.

## 1. Log lokal (my tools baca langsung)

| File | Isi |
|---|---|
| `logs/trades.jsonl` | event `forward_open` / `forward_close` (pnl_usd, r, reason, **regime**, equity) |
| `logs/trades_dry.jsonl` | sama, jalur `dry` sederhana |
| `logs/decision_log.jsonl` | keputusan agen (izin/veto + alasan) |
| `logs/mtf_shadow.jsonl`, `logs/vrp_shadow.jsonl` | shadow A/B (MTF, VRP) |

**Alat baca cepat:**
```bash
python regime_ev.py            # expectancy per regime·reason·side·conviction·symbol
python ab_report.py            # ringkasan A/B
tail -n 50 logs/trades.jsonl   # mentah
```

## 2. Bot live di Proxmox — API yang SUDAH ADA (jangan bikin baru)

Dashboard = FastAPI di `dashboard.py`, disajikan uvicorn `:8000`
(`deploy/systemd/usdc-dashboard.service`). Endpoint baca:

| Endpoint | Fungsi |
|---|---|
| `GET /api/trades?symbol=&reason=&dfrom=` | riwayat trade (JSON, bisa filter) |
| `GET /api/trades.csv` | idem, CSV |
| `GET /api/stats` | statistik berjalan |
| `GET /api/status` | status bot |
| `GET /api/account` | saldo/posisi |
| `GET /api/gemini-usage` | pemakaian token Gemini |
| `GET /api/news-log`, `/api/screen-log` | log berita & screening |

## 3. Cara LLM/sesi menjangkau log Proxmox

Bot di **`192.168.1.107`**, dashboard **`:8000`** dengar di LAN. **SSH tidak diperlukan.**

**HTTP (jalan sekarang) — cara utama:**
```powershell
.\sync_logs.ps1     # curl /api/stats, /api/trades, /api/trades.csv, /api/gemini-usage -> logs/remote_*
```
Manual: `curl "http://192.168.1.107:8000/api/stats"`. Ubah IP via `.env`:
`DASH_URL=http://192.168.1.107:8000`.

**Batas HTTP:** `/api/trades` = data OLAHAN (bukan jsonl mentah) dan **belum memuat regime**
sampai bot Proxmox di-deploy ulang dengan patch `forward.py`. Untuk `regime_ev.py` (butuh
jsonl mentah): (a) deploy ulang lalu ambil `logs/trades.jsonl` via scp **setelah SSH disetel**,
atau (b) tambah endpoint read-only kecil yang menyajikan file mentah bila diperlukan.

**B. Curl API dari mesin di LAN yang sama:**
```bash
curl "http://<PROXMOX_IP>:8000/api/trades?dfrom=2026-07-01" -o logs/remote_trades.json
curl "http://<PROXMOX_IP>:8000/api/stats"
```

## 4. Keamanan — DITUNDA secara sadar (LAN-only)

Keputusan: semua jalan di **Proxmox lokal, tidak di-share publik** → keamanan bukan
prioritas sekarang, fokus ke produk. Ini pilihan sadar, bukan kelalaian.

Risiko yang diterima: dashboard bind `--host 0.0.0.0` **tanpa auth**, ada
`POST /api/close-all`/`/api/close`. Aman **selama** port `:8000` TIDAK di-forward ke
internet dan LAN tepercaya.

**Saat siap mengeraskan** (mis. sebelum expose ke luar): ubah service ke
`--host 127.0.0.1` + akses via `ssh -L 8000:127.0.0.1:8000 user@<PROXMOX_IP>`, atau
tambah token di endpoint mutasi. Jangan port-forward `:8000` ke publik tanpa ini.
