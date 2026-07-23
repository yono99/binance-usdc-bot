# Rule — operasional aktif (auto-load tiap sesi di repo ini)

Saat bekerja di **binance-usdc-bot**, anggap ini benar sampai file handoff di-update:

1. Baca dulu (anti konteks penuh): `memory/CONTINUE.md` → `memory/SESSION_HANDOFF.md`
   (scoreboard edge) → `memory/EDGE_HUNT.md` → `PLAN_OPERASIONAL.md` → `CHECKLIST_HARIAN.md`.
2. Pemilik mengikuti **survival + Jalan A** — KPI proses/risk, **bukan** profit tiap hari.
3. **Scoreboard (2026-07-21):** PROMOTE_PAPER=**0** · PROMOTE_FILTER_PAPER=**2** (shadow only) ·
   WATCHLIST=**1** (LINK). Jangan samakan filter dengan edge entry.
4. Paper **dry** di `192.168.1.107`: risk lock (daily loss OFF, DD lock 20%, trades 30, pos 5, lev 5);
   `agent_manager_mode` **OFF**; `agent_ab_shadow` ON; full-auto/tool-loop/autonomous OFF;
   `risk_filter_shadow` ON · **`risk_filter_block` OFF**.
5. **Jangan** usulkan: buka H30/L2 untuk cuan, retread H24–H32, longgarkan risk karena 1 hari merah,
   live tanpa PROMOTE_PAPER, hard-block risk_filter tanpa paper risk A/B, klaim “ada edge” bila entry=0.
6. **7 hari checklist** = gerbang ke H28 paper; selama itu bantu awasi/audit, bukan ganti strategi.
7. SSH server: key `~/.ssh/id_ed25519_proxmox`, host `192.168.1.107`, repo `/root/binance-usdc-bot`,
   PM2 `bot`+`dashboard` (tepat 1 bot).
8. Setelah perubahan posture penting: update `memory/SESSION_HANDOFF.md` + `CONTINUE.md` dan commit/push.
