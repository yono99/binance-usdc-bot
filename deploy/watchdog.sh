#!/usr/bin/env bash
# Watchdog: restart bot bila snapshot dashboard BEKU (hang yang tak terdeteksi
# supervisor — PM2/systemd hanya restart saat proses EXIT, buta terhadap loop macet).
# Dipanggil cron tiap 5 menit. Cek healthcheck.py; bila basi > MAX menit → restart.
#
# Pasang cron (di server, sekali saja):
#   ( crontab -l 2>/dev/null | grep -v watchdog.sh; \
#     echo "*/5 * * * * /bin/bash $HOME/binance-usdc-bot/deploy/watchdog.sh" ) | crontab -
set -u

cd "$(dirname "$0")/.." || exit 1
PY="./venv/bin/python"; [ -x "$PY" ] || PY="python3"
MAX_MIN="${WATCHDOG_MAX_MIN:-30}"
LOG="logs/watchdog.log"

# Sehat → diam (exit 0). Cek localhost: server memeriksa dirinya sendiri.
if "$PY" healthcheck.py --url http://127.0.0.1:8000 --max-min "$MAX_MIN" >/dev/null 2>&1; then
    exit 0
fi

echo "$(date -u +%FT%TZ) SNAPSHOT BASI (> ${MAX_MIN}m) — restart bot" >> "$LOG"
pm2 restart bot                   >/dev/null 2>&1 \
    || systemctl restart usdc-bot >/dev/null 2>&1 \
    || docker compose restart bot >/dev/null 2>&1 \
    || echo "$(date -u +%FT%TZ) RESTART GAGAL — pm2/systemd/docker tak ada" >> "$LOG"
