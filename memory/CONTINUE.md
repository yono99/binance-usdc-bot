# CONTINUE — bootstrap 1 halaman (anti konteks penuh)

> Sesi baru / konteks penuh: baca file ini dulu, lalu SESSION_HANDOFF + EDGE_HUNT_LOOP.

## Scoreboard jujur

| | |
|---|---|
| PROMOTE_PAPER (entry edge) | **0** |
| PROMOTE_FILTER_PAPER | **2** shadow |
| CE-STANCE (ilmu pemilik) | dual-track dry⇄live 1:1 · `risk_ack` |
| Edge hunt (2026-07-24) | **Full-engine BT:** rules 1d OOS +0.17R · +G2 filter +0.23R · book PASS · [BACKTEST_FULL_ENGINE](../research/BACKTEST_FULL_ENGINE.md) |
| Trade review | SQLite post-mortem **di bawah** pondasi — bukan auto-edge |

## Fakta terkunci

| Item | Nilai |
|---|---|
| Pondasi | Ilmu siklus → CE size-down long; **tidak** auto-short dump |
| CE config | `mode:size` · allow_live+risk_ack · stop −5R |
| Dry server | `192.168.1.107` · `./restart.sh` · 1× forwardtest dry |
| Risk rugi | daily loss **OFF**; gerbang = **DRAWDOWN LOCK** (default 20) |
| Snap all-time (server) | end **2026-07-24** · ok download 513 · files_1d≈760 |
| Snap lokal Windows | end **2026-07-01** (stale) — hunt di server atau re-download |
| Hakim edge | OOS + cost 0.18% + p_adj · spek [EDGE_HUNT_LOOP.md](EDGE_HUNT_LOOP.md) |
| State mesin | [EDGE_HUNT_STATE.json](EDGE_HUNT_STATE.json) |

## Hierarki belajar (jangan dibalik)

```
HARD risk → PONDASI (ilmu Anda / CE) → trade_reviews → soft prompt → edge kandidat terpisah
```

## Jangan

- Auto-edge dari 1 loss · short dump · longgarkan risk · matikan dry
- Klaim PROMOTE_PAPER dari CE/review/A–F all-time (0 kandidat)
- Retread H24–H32 / crash-bounce / A–F tanpa novelty

## Lanjut (rekomendasi aktif)

1. **G2 paper book** terpisah — `g2_book_runner.py` (+ PM2 `g2-book` cron 02:00 UTC)  
2. **Path B** tetap shadow di bot rules — `block=false`  
3. Dry survival + CE + risk_filter · **jangan** live G2  
4. Refresh snap majors 1d bila stale (supaya rank/book akurat)  

## Perintah

```bash
export PYTHONPATH=/root/binance-usdc-bot
python research/g2_book_runner.py --once
python research/g2_book_runner.py --report
python research/g2_entry_ab_report.py --mode dry
# bot rules (terpisah)
./restart.sh
```
