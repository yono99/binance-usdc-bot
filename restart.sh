#!/usr/bin/env bash
# Restart bot + dashboard setelah git pull (PM2).
# WAJIB: tepat SATU proses forwardtest. Zombie manual menimpa botstate (insiden 2026-07-20).
#
# Pakai:
#   cd /root/binance-usdc-bot && git pull && ./restart.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "== binance-usdc-bot restart =="
echo "cwd: $(pwd)"
echo "git: $(git rev-parse --short HEAD 2>/dev/null || echo '?') — $(git log -1 --oneline 2>/dev/null || true)"

# 1) Stop PM2 apps dulu agar lock file dilepas
pm2 stop bot dashboard 2>/dev/null || true
sleep 1

# 2) Bersihkan orphan forwardtest / dashboard di luar PM2
pkill -f 'python.*forwardtest.py' 2>/dev/null || true
if command -v fuser >/dev/null 2>&1; then
  fuser -k 8000/tcp 2>/dev/null || true
fi
sleep 1

# 3) Hapus lock basi (proses sudah mati)
rm -f logs/forwardtest.lock

# 4) Start ulang dari ecosystem (args --mode dry + lock)
if [ ! -f ecosystem.config.cjs ]; then
  echo "ERROR: ecosystem.config.cjs tidak ada" >&2
  exit 1
fi
pm2 delete bot dashboard 2>/dev/null || true
pm2 start ecosystem.config.cjs
pm2 save

sleep 5
echo "--- pm2 list ---"
pm2 list

echo "--- forwardtest processes (harus tepat 1) ---"
n=$(ps aux | grep -F 'forwardtest.py' | grep -v grep | wc -l | tr -d ' ')
ps aux | grep -F 'forwardtest.py' | grep -v grep || true
if [ "$n" != "1" ]; then
  echo "WARN: expected 1 forwardtest process, got $n" >&2
else
  echo "OK: exactly 1 forwardtest"
fi

echo "--- lock ---"
if [ -f logs/forwardtest.lock ]; then
  echo "pid=$(cat logs/forwardtest.lock)"
else
  echo "(lock belum — bot masih seed / belum main loop)"
fi

echo "--- quick API ---"
if command -v curl >/dev/null 2>&1; then
  curl -sS --max-time 5 http://127.0.0.1:8000/api/status 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('open_count',d.get('open_count'),'enabled',d.get('enabled'),'mode',d.get('mode'))" \
    2>/dev/null || echo "(dashboard belum siap — cek pm2 logs dashboard)"
fi

echo "OK: UI http://192.168.1.107:8000  |  JANGAN jalankan forwardtest manual di samping PM2"