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

# 3) Hapus lock basi (proses sudah mati) — per-mode + legacy global
rm -f logs/forwardtest.lock logs/forwardtest_dry.lock logs/forwardtest_test.lock logs/forwardtest_live.lock

# 4) Start ulang dari ecosystem (default: bot dry + dashboard; bot-live opsional di cjs)
if [ ! -f ecosystem.config.cjs ]; then
  echo "ERROR: ecosystem.config.cjs tidak ada" >&2
  exit 1
fi
pm2 delete bot bot-live dashboard 2>/dev/null || true
pm2 start ecosystem.config.cjs
pm2 save

sleep 5
echo "--- pm2 list ---"
pm2 list

echo "--- forwardtest processes (1 per mode; dry+live paralel OK) ---"
n=$(ps aux | grep -F 'forwardtest.py' | grep -v grep | wc -l | tr -d ' ')
ps aux | grep -F 'forwardtest.py' | grep -v grep || true
n_dry=$(ps aux | grep -F 'forwardtest.py' | grep -F -- '--mode dry' | grep -v grep | wc -l | tr -d ' ')
n_live=$(ps aux | grep -F 'forwardtest.py' | grep -F -- '--mode live' | grep -v grep | wc -l | tr -d ' ')
echo "count total=$n dry=$n_dry live=$n_live"
if [ "$n_dry" -gt 1 ] || [ "$n_live" -gt 1 ]; then
  echo "WARN: lebih dari 1 proses mode yang sama (botstate bentrok)" >&2
elif [ "$n" -eq 0 ]; then
  echo "WARN: 0 forwardtest" >&2
else
  echo "OK: forwardtest count sane (1 per mode)"
fi

echo "--- lock ---"
for f in logs/forwardtest.lock logs/forwardtest_dry.lock logs/forwardtest_live.lock; do
  if [ -f "$f" ]; then
    echo "$f pid=$(cat "$f")"
  fi
done
if [ ! -f logs/forwardtest.lock ] && [ ! -f logs/forwardtest_dry.lock ] && [ ! -f logs/forwardtest_live.lock ]; then
  echo "(lock belum — bot masih seed / belum main loop)"
fi

echo "--- quick API ---"
if command -v curl >/dev/null 2>&1; then
  curl -sS --max-time 5 http://127.0.0.1:8000/api/status 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('open_count',d.get('open_count'),'enabled',d.get('enabled'),'mode',d.get('mode'))" \
    2>/dev/null || echo "(dashboard belum siap — cek pm2 logs dashboard)"
fi

echo "OK: UI http://192.168.1.107:8000  |  JANGAN jalankan forwardtest manual di samping PM2"