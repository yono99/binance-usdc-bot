// Phase 5 — PM2 process manager (pengganti nohup/&).
// Menjalankan bot forward-test (paper, MODE=dry default) + dashboard :8000, dengan
// auto-restart saat crash & auto-start saat reboot (lihat `pm2 startup` + `pm2 save`).
//
// cwd: __dirname → tak hardcode path; jalan di mana pun repo di-clone.
// interpreter: ./venv/bin/python (Linux server; sesuai DEPLOY.md). Di Windows ganti
//   ke ".\\venv\\Scripts\\python.exe".
//
// Pakai:
//   npm install -g pm2
//   pm2 start ecosystem.config.cjs
//   pm2 save && pm2 startup     # auto-start saat boot (jalankan perintah yang dicetak)
//   pm2 logs bot | pm2 status | pm2 restart bot
//
// PENTING: jalankan TEPAT SATU proses bot (dua bot menulis DB sama → state bentrok).

const PY = "/root/binance-usdc-bot/venv/bin/python";  // Linux server (venv)

module.exports = {
  apps: [
    {
      name: "bot",
      script: "forwardtest.py",
      interpreter: PY,
      args: "--poll 30 --use-store",
      cwd: __dirname,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 10,
      time: true,                 // timestamp di log PM2
      env: { MODE: "dry" },       // paper — nol risiko uang; ganti ke "live" sadar penuh
    },
    {
      name: "dashboard",
      script: "dashboard.py",
      interpreter: PY,
      args: "--host 0.0.0.0 --port 8000",
      cwd: __dirname,
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 10,
      time: true,
    },
  ],
};
