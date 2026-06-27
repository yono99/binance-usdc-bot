#!/usr/bin/env bash
# Pasang service systemd (auto-start saat boot + restart bila crash).
# Deteksi path repo & venv otomatis. Jalankan sebagai root: sudo bash deploy/systemd/install.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")/../.." && pwd)"          # root repo
PY="$DIR/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
mkdir -p "$DIR/logs"

echo "Repo : $DIR"
echo "Python: $PY"

for svc in usdc-dashboard usdc-bot usdc-collector; do
  sed -e "s|__DIR__|$DIR|g" -e "s|__PY__|$PY|g" \
      "$DIR/deploy/systemd/$svc.service" > "/etc/systemd/system/$svc.service"
  echo "  -> /etc/systemd/system/$svc.service"
done

# hentikan proses nohup lama bila ada
pkill -f dashboard.py   2>/dev/null || true
pkill -f forwardtest.py 2>/dev/null || true
pkill -f l2collect.py   2>/dev/null || true

systemctl daemon-reload
systemctl enable --now usdc-dashboard usdc-bot
echo ""
echo "Dashboard & bot aktif + auto-start saat boot."
echo "Collector L2 (opsional): systemctl enable --now usdc-collector"
echo "Status : systemctl status usdc-bot"
echo "Log    : journalctl -u usdc-bot -f"
