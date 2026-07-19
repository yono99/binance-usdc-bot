# Rule — operasional aktif (auto-load tiap sesi di repo ini)

Saat bekerja di **binance-usdc-bot**, anggap ini benar sampai file handoff di-update:

1. Baca dulu: `memory/SESSION_HANDOFF.md`, `PLAN_OPERASIONAL.md`, `CHECKLIST_HARIAN.md`.
2. Pemilik mengikuti **survival + Jalan A** — KPI proses/risk, **bukan** profit tiap hari.
3. Paper **dry** di `192.168.1.107`: risk lock (loss 5%, trades 30, pos 5, lev 5); `agent_manager_mode` + `agent_ab_shadow` ON; full-auto/tool-loop OFF.
4. **Jangan** usulkan: buka H30/L2 untuk cuan, 5 hipotesis L2 paralel, varian OHLCV/H24–H32 mati, longgarkan risk karena 1 hari merah, live scale tanpa bukti.
5. **7 hari checklist** = gerbang ke H28 paper; selama itu bantu awasi/audit, bukan ganti strategi.
6. SSH server: key `~/.ssh/id_ed25519_proxmox`, host `192.168.1.107`, repo `/root/binance-usdc-bot`, PM2 `bot`+`dashboard` (tepat 1 bot).
7. Setelah perubahan posture penting: update `memory/SESSION_HANDOFF.md` dan commit/push bila user minta.
