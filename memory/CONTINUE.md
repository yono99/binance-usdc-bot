# CONTINUE — bootstrap 1 halaman (anti konteks penuh)

> Sesi baru / konteks penuh: baca file ini dulu, lalu SESSION_HANDOFF.

## Scoreboard jujur

| | |
|---|---|
| PROMOTE_PAPER (entry edge) | **0** |
| PROMOTE_FILTER_PAPER | **2** shadow |
| CE-STANCE (ilmu pemilik) | dual-track dry⇄live 1:1 · `risk_ack` |
| Trade review (baru) | SQLite post-mortem **di bawah** pondasi — bukan auto-edge |
| Arch (2026-07-21) | `ARCHITECTURE.md` · `ENTRY_PIPELINE.md` · `research/` · forward mixins |

## Fakta terkunci

| Item | Nilai |
|---|---|
| Pondasi | Ilmu siklus → CE size-down long; **tidak** auto-short dump |
| CE config | `mode:size` · allow_live+risk_ack · stop −5R |
| Review | `trade_reviews` di bot.db · `bot/trade_review.py` · [TRADE_REVIEW.md](TRADE_REVIEW.md) |
| Inject | Hanya injectable & !conflicts_foundation → prompt soft |
| Dry server | `192.168.1.107` · `./restart.sh` · 1× forwardtest dry |
| Hakim CE | `python ce_report.py` |

## Hierarki belajar (jangan dibalik)

```
HARD risk → PONDASI (ilmu Anda / CE) → trade_reviews → soft prompt → edge kandidat terpisah
```

## Jangan

- Auto-edge dari 1 loss · short dump · longgarkan risk · matikan dry
- Inject lesson yang `conflicts_foundation`
- Klaim PROMOTE_PAPER dari CE atau review

## Lanjut

1. Dry jalan + review terisi tiap close  
2. Live mikro: [LIVE_MICRO_CHECKLIST.md](LIVE_MICRO_CHECKLIST.md)  
3. `python ce_report.py` · `trade_review_stats('dry')`  

## Perintah

```bash
git pull && ./restart.sh
python ce_report.py
python -c "from bot.store import trade_review_stats; print(trade_review_stats('dry'))"
```
