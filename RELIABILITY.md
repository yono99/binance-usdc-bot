# Keandalan 24/7 — Gemini rate-limit, exit, & watchdog

Runbook untuk menjaga bot hidup terus dan tak beku. Ditulis setelah insiden
2026-07-03: UI tampak "freeze", posisi kena TP tapi tak tertutup.

## Gejala & cara diagnosis cepat

**Gejala:** dashboard hidup (HTTP 200) tapi angka tak berubah; posisi lewat
TP/SL tapi tak close.

**Penyebab pasti = snapshot BEKU.** Bot tak crash — loop utamanya macet. Cek:

```bash
python healthcheck.py                 # dari mana saja; alarm bila ts basi > 30 mnt
# atau langsung:
curl -s http://<host>:8000/api/status | python -c "import sys,json;print(json.load(sys.stdin)['ts'])"
```

Kalau `ts` tak maju padahal `poll_seconds` (default 900 = 15 mnt) sudah lewat →
loop macet. Supervisor (PM2/systemd) **tak bisa mendeteksi ini** — proses masih
hidup, hanya loop-nya nyangkut. Itu sebabnya perlu watchdog (di bawah).

## Akar masalah insiden & perbaikannya

### 1. Exit (SL/TP) tersandera Gemini — `bot/forward.py`

Menutup posisi di TP/SL itu **murni aritmetika** (harga Binance vs level), nol
Gemini. Tapi dulu cek exit dijalankan per-simbol **di dalam loop yang sama**
dengan panggilan Gemini. `generate_content` **tanpa timeout** → satu panggilan
hang membekukan seluruh `on_cycle` → exit SEMUA posisi ikut berhenti.

**Fix:** sapu exit semua posisi terbuka di **awal** siklus, sebelum Gemini
disentuh (`_on_cycle_store`). Exit tak akan pernah tersandera LLM.

### 2. Badai 429 — `bot/gemini_client.py`

Bot membombardir Gemini tanpa jeda → tabrak batas RPM → 429 beruntun → loop
tak selesai. **Fix:** throttle per-key + skip-saat-cooling + backoff eksponensial
(lihat bagian "Smart key rotation").

## Batas Gemini API (free tier, `gemini-2.5-flash`)

**~10 RPM · 250K TPM · 250 RPD** — dan **batas ini per PROJECT, bukan per key.**

| Batas | Reset | Ditangani |
|---|---|---|
| RPM (req/menit) | jendela 1 menit bergulir | 429 → cooldown key 60s |
| RPD (req/hari) | tengah malam Pacific (~08:00 UTC) | 429 "PerDay" → cooldown sampai reset |
| TPM (token/menit) | jendela 1 menit | jarang kena (prompt kecil) |

Dok: <https://ai.google.dev/gemini-api/docs/rate-limits>

## Smart key rotation

Diatur di `bot/gemini_client.py` (module-level, dibagi semua layer:
trader/news/planner/devil's advocate).

- **Throttle PER-KEY** (`_MIN_INTERVAL`, default 6.5s) — key dari project berbeda
  jalan paralel → RPM efektif ~N×.
- **Rotasi LRU** — sebar beban ke key paling lama tak dipakai.
- **Cooldown adaptif** — RPM 429 → 60s (per-key); auth 403 → 5 mnt (per-key);
  **RPD 429 → per-(key,MODEL)** sampai reset harian. RPD habis = kuota harian model
  ITU di key ITU (bukan seluruh key): model fallback di key sama tetap jalan, dan
  sukses fallback **tak menghapus** tanda mati model primary. (Dulu cooldown RPD per-key
  di-reset oleh sukses fallback → primary di-retry tiap keputusan = ribuan 429 sia-sia/hari.)
- **Skip saat semua key cooling** → keputusan deterministik siklus itu (tak ada badai 429).
- **Circuit breaker global** — gagal beruntun 5× → semua layer mundur 60s.

### Konfigurasi

`.env` — **key harus dari project/akun Google BERBEDA** untuk menambah kuota
(satu project = satu kolam RPM/RPD):

```
GEMINI_API_KEYS=key_projectA,key_projectB,key_projectC   # dipisah koma
```

Knob:
- `GEMINI_MIN_INTERVAL_S` (env) — jeda antar-request per key. Set `0` untuk paid tier.
- `gemini.gemini_decide_cap` (`config.yaml`, default 24) — CAP budget keputusan
  Gemini/siklus. Budget dihitung DINAMIS = `min(ceil(simbol/cycles), cap)`,
  `cycles = gemini_decide_seconds // poll_seconds` → semua simbol dapat giliran
  sekali per decide-interval tanpa melampaui cap. Cap jaga wall-clock: `cap ×
  ~2dtk < poll_seconds` agar `_monitor_usd` (SL/TP) tak tertunda. Naikkan bila
  universe besar & poll longgar; turunkan bila siklus mepet.
- `GEMINI_TIMEOUT_S` (env, default 20) — timeout HTTP per panggilan Gemini. Satu
  call hang → error transien → rotasi key, siklus tak beku. Penting krn budget
  dinamis bisa banyak call/siklus.

## Watchdog 24/7

Supervisor buta terhadap hang. Watchdog menutup celah itu.

**Pasang di server (sekali):**
```bash
cd ~/binance-usdc-bot && git pull
( crontab -l 2>/dev/null | grep -v watchdog.sh; \
  echo "*/5 * * * * /bin/bash $HOME/binance-usdc-bot/deploy/watchdog.sh" ) | crontab -
```

Tiap 5 menit cron menjalankan `deploy/watchdog.sh` → `healthcheck.py`. Bila
snapshot basi > 30 menit → restart bot (auto-deteksi pm2/systemd/docker),
dicatat ke `logs/watchdog.log`.

Knob: `WATCHDOG_MAX_MIN` (default 30) di baris cron.

**Tes manual:**
```bash
bash ~/binance-usdc-bot/deploy/watchdog.sh; echo "exit=$?"   # 0 = sehat
```

## Deploy update

Perubahan backend (Python) — **tak perlu build web**:
```bash
cd ~/binance-usdc-bot && git pull && (pm2 restart bot || systemctl restart usdc-bot || docker compose restart bot)
```
Build `web/` (`npm install && npm run build`) hanya bila mengubah UI.
