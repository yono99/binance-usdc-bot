// Phase 5 — PM2 process manager (pengganti nohup/&).
// Default: bot paper (dry) + dashboard :8000, auto-restart & auto-start boot.
//
// Dual dry + live (opsional):
//   - Lock PER MODE: logs/forwardtest_dry.lock + logs/forwardtest_live.lock
//   - Uncomment app `bot-live` di bawah (butuh BINANCE_LIVE_KEY/SECRET di .env)
//   - UI: ganti Mode dry|live untuk melihat status/settings bucket masing-masing
//   - JANGAN dua proses mode SAMA (2× dry atau 2× live) — botstate bentrok
//
// Detail: memory/LIVE_AND_DRY.md · DEPLOY.md
//
// Pakai:
//   pm2 start ecosystem.config.cjs
//   pm2 save && pm2 startup
//   pm2 logs bot | pm2 status

const PY = "/root/binance-usdc-bot/venv/bin/python";  // Linux server (venv)

module.exports = {
  apps: [
    {
      name: "bot",
      script: "forwardtest.py",
      interpreter: PY,
      // --mode dry → pin_mode=True: proses paper tak ikut switch UI ke live
      args: "--poll 30 --use-store --mode dry",
      cwd: __dirname,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 10,
      time: true,
      env: { MODE: "dry", SKIP_ENABLED_RESET: "1" },
    },
    // ── OPSIONAL: LIVE paralel dengan dry (UANG NYATA) ─────────────────────
    // Uncomment HANYA setelah checklist live + BINANCE_LIVE_* di .env.
    // pm2 start ecosystem.config.cjs  →  akan start bot + bot-live + dashboard
    // {
    //   name: "bot-live",
    //   script: "forwardtest.py",
    //   interpreter: PY,
    //   args: "--poll 30 --use-store --mode live",
    //   cwd: __dirname,
    //   autorestart: true,
    //   restart_delay: 5000,
    //   max_restarts: 10,
    //   time: true,
    //   env: { MODE: "live", SKIP_ENABLED_RESET: "1" },
    // },
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
      env: { SKIP_ENABLED_RESET: "1" },
    },
    // G2 paper BOOK (Path A) — terpisah dari bot rules; BUKAN order Binance.
    // cron 02:00 UTC: settle hold=10 bila jatuh tempo + buka buku baru.
    {
      name: "g2-book",
      script: "research/g2_book_runner.py",
      interpreter: PY,
      args: "--once",
      cwd: __dirname,
      autorestart: false,
      cron_restart: "0 2 * * *",
      time: true,
      env: { PYTHONPATH: __dirname },
    },
  ],
};
