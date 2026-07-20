# Deploy — Docker & Manual

Menjalankan **bot forward-test + dashboard** 24/7. Default `MODE=dry` (paper) →
**tanpa API key, nol risiko uang**. Lihat juga [DASHBOARD.md](DASHBOARD.md) (arsitektur
+ setting UI) dan [METHODOLOGY.md](METHODOLOGY.md).

> Frontend React/Vite sudah **di-build & ikut di repo** (`web/dist`) → dashboard
> menyajikannya tanpa Node. SQLite (`logs/bot.db`) dibuat otomatis. Tak perlu setup tambahan.

---

## TL;DR

```bash
git clone https://github.com/yono99/binance-usdc-bot.git
cd binance-usdc-bot

# --- A. Docker (paling mudah) ---
docker compose up -d --build          # dashboard: http://<host>:8000

# --- B. Manual (Python venv) ---
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                  # MODE=dry default
python forwardtest.py --poll 60 --use-store &   # bot (paper)
python dashboard.py --host 0.0.0.0               # dashboard :8000
```

Prasyarat: **outbound HTTPS** ke Binance (`api.binance.com`, `fapi.binance.com`,
`fstream.binance.com`). DNS `1.1.1.1` membantu resolusi stabil.

---

## A. Deploy dengan Docker (disarankan)

Satu image dipakai 3 service di `docker-compose.yml`: **bot** (forward-test),
**dashboard** (`:8000`), **collector** L2 (opsional).

```bash
# 1. Install Docker (sekali)
curl -fsSL https://get.docker.com | sh

# 2. Clone & jalankan
git clone https://github.com/yono99/binance-usdc-bot.git
cd binance-usdc-bot
docker compose up -d --build

# 3. Akses dashboard
#    http://<IP-host>:8000     (atur teknik/leverage/bet/pair dari UI)

# 4. Pantau
docker compose logs -f bot
docker compose ps
```

**Mode** (default `dry`): override saat up —
```bash
MODE=test docker compose up -d        # paper di data live (sama dgn dry)
```

**Operasional:**
```bash
docker compose restart bot            # restart satu service
docker compose down                   # stop semua
git pull && docker compose up -d --build   # update setelah ada commit baru
```

- **Data** persisten di volume `./logs` (SQLite `bot.db` + jurnal) dan `./data` (L2).
- `restart: unless-stopped` → service hidup lagi setelah crash/reboot. Docker service
  sendiri auto-start saat boot.
- Collector L2 opsional — hapus service `collector` di compose bila tak perlu.

---

## B. Deploy Manual (Python venv + systemd)

Untuk server tanpa Docker, atau yang ingin kontrol penuh.

### 1. Install & siapkan

```bash
sudo apt update && sudo apt -y install git python3 python3-venv python3-pip
git clone https://github.com/yono99/binance-usdc-bot.git
cd binance-usdc-bot

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                  # isi sesuai mode (default MODE=dry)
```

### 2. Jalankan (uji manual)

```bash
# terminal 1 — bot (paper, baca pengaturan dari UI)
python forwardtest.py --poll 60 --use-store

# terminal 2 — dashboard
python dashboard.py --host 0.0.0.0    # http://<host>:8000
```

> Penting: jalankan **tepat satu** proses bot. Dua bot menulis DB yang sama =
> posisi & status saling timpa.

### 3a. Auto-start saat boot (PM2) — alternatif systemd

`ecosystem.config.cjs` mengelola **bot + dashboard** dengan auto-restart & auto-start boot.

```bash
npm install -g pm2                  # butuh Node 18+
pm2 start ecosystem.config.cjs      # jalankan kedua service (bot MODE=dry default)
pm2 save                            # simpan daftar proses
pm2 startup                         # cetak perintah; jalankan utk auto-start saat reboot
```

```bash
pm2 status                          # ringkasan service
pm2 logs bot                        # log realtime
pm2 restart bot dashboard           # restart cepat (bila yakin 0 orphan)
pm2 delete all                      # stop + cabut
```

**Update aman setelah `git pull` (disarankan di Proxmox paper):**

```bash
cd /root/binance-usdc-bot
git pull
chmod +x restart.sh
./restart.sh
```

`restart.sh` melakukan: stop PM2 → kill orphan `forwardtest` / fuser :8000 → hapus
`logs/forwardtest.lock` basi → `pm2 start ecosystem.config.cjs` → cek **tepat 1**
proses + lock + `/api/status`. Ini mencegah insiden 2026-07-20 (zombie manual + PM2
saling timpa `botstate_dry`).

- `interpreter: ./venv/bin/python` (Linux). Pastikan venv sudah dibuat (langkah 1).
- Jalankan **tepat satu** proses bot (sama spt systemd) — dua bot = state DB bentrok.
- **Jangan** `python forwardtest.py` manual di samping PM2 (file lock exit 2; zombie
  menimpa paper open tanpa CLOSE).
- Reboot: `pm2 save` + `pm2 startup` membuat kedua service hidup lagi otomatis.

### 3. Auto-start saat boot (systemd)

```bash
sudo bash deploy/systemd/install.sh   # deteksi path & venv otomatis
```

Memasang & mengaktifkan `usdc-dashboard` + `usdc-bot` (collector opsional:
`sudo systemctl enable --now usdc-collector`).

```bash
systemctl status usdc-bot             # status
journalctl -u usdc-bot -f             # log realtime
sudo systemctl restart usdc-bot usdc-dashboard   # terapkan update setelah git pull
sudo systemctl disable --now usdc-bot # matikan + cabut auto-start
```

### Rebuild frontend (hanya bila mengubah `web/src`)

`web/dist` sudah ikut repo, jadi normal **tak perlu**. Bila mengubah kode UI:
```bash
cd web && npm install && npm run build   # butuh Node 18+
```

---

## Konfigurasi

| Berkas | Isi |
|---|---|
| `.env` | `MODE` (dry/test/live) + API key (`BINANCE_LIVE_KEY/SECRET`) + Gemini (`GEMINI_ENABLED`, `GEMINI_API_KEYS`) |
| `config.yaml` | strategi + batas risiko (`risk.*`, `corr_threshold`, dll) |
| UI (Kontrol Bot) | teknik, leverage, bet, pair, order, fee, model Gemini — **per-mode**, hot-reload |

### Mode live (UANG NYATA)
1. API key Binance: **Futures-only**, **withdrawal OFF**, **IP-locked**.
2. `.env`: `MODE=live` + isi `BINANCE_LIVE_KEY/SECRET` (atau toggle mode `live` dari UI).
3. Mulai dengan **bet sangat kecil**. Saldo live diambil otomatis dari Binance.

> ⚠️ Methodology: strategi **belum ada edge (impas)**. Live = risiko penuh. Order
> nyata + SL/TP sisi-exchange aktif, tapi uji kecil dulu. Lihat [DASHBOARD.md](DASHBOARD.md).

---

## Data & backup

- Semua state di **`logs/bot.db`** (SQLite): trades, setting per-mode, status, state
  hidup, histori news/screening, token Gemini.
- Backup: salin `logs/bot.db` (idealnya saat bot berhenti), atau
  `sqlite3 logs/bot.db ".backup logs/backup.db"`.
- ⚠️ SQLite (mode WAL) aman untuk semua proses **di host yang sama**. **Jangan** taruh
  `logs/` di storage jaringan (NFS/Ceph) bila dibagi antar VM — file-lock tak andal.

---

## Catatan Proxmox (server lokal)

- **LXC unprivileged** + `nesting=1` (Options → Features) untuk Docker; atau pakai **VM**
  (paling mulus untuk Docker). 2 vCPU / 2 GB / 8 GB cukup untuk bot+dashboard.
- DNS `1.1.1.1` di tab DNS CT. Tes: `getent hosts fapi.binance.com`.
- Server lokal bagus untuk **riset & forward-test**. Untuk live latency-kritis,
  pertimbangkan VPS region dekat Binance (mis. Tokyo).
