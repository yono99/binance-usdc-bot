# CONTINUE — bootstrap 1 halaman (anti konteks penuh)

> Buka file ini dulu di **sesi Grok CLI baru** bila chat penuh / TUI restart.
> Detail: [SESSION_HANDOFF.md](SESSION_HANDOFF.md) · [CANDIDATE_EDGE.md](CANDIDATE_EDGE.md) ·
> [LIVE_MICRO_CHECKLIST.md](LIVE_MICRO_CHECKLIST.md).

## Jawab cepat: sudah dapat berapa edge?

| | |
|---|---|
| **Edge entry (PROMOTE_PAPER)** | **0** |
| **Risk filter (PROMOTE_FILTER_PAPER)** | **2** — shadow only |
| **WATCHLIST** | **1** — LINK residual-z |
| **Candidate (ilmu pemilik)** | **CE-STANCE dual-track** dry⇄live 1:1 · `risk_ack` ON |

## Fakta terkunci (2026-07-21)

| Item | Nilai |
|---|---|
| Pondasi | Ilmu pemilik → size-down long (dump/markdown/unlock) |
| Config CE | `mode: size` · `allow_live: true` · `risk_ack: true` · `stop_loss_r_live: -5` |
| Dry | Server `192.168.1.107` · risk 5/30/5/5 · manager OFF · ab_shadow ON |
| Live | Checklist [LIVE_MICRO_CHECKLIST.md](LIVE_MICRO_CHECKLIST.md) — bet/loss **lebih ketat** |
| Hakim | `python ce_report.py` (non-mutating) |
| Stop live | `logs/ce_live_state.json` · reset sadar via `reset_live_stop()` |

## Jangan

- Full live size / longgarkan risk karena “ilmu benar”
- Matikan dry · auto-short dump · klaim PROMOTE_PAPER dari CE
- Reset stop berulang tanpa review
- `risk_filter_block` tanpa bukti

## Lanjut

1. Dry: `git pull && ./restart.sh` — CE size-down aktif di paper.  
2. Live: ikuti LIVE_MICRO_CHECKLIST (risk mikro + enabled sadar).  
3. Mingguan: `python ce_report.py`.

## Perintah

```bash
git pull && ./restart.sh          # dry server
python ce_report.py               # dual report
python ce_report.py --mode live
```
