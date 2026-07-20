# CONTINUE — bootstrap 1 halaman (anti konteks penuh)

> Buka file ini dulu di **sesi Grok CLI baru** bila chat penuh / TUI restart.
> Detail di [EDGE_HUNT.md](EDGE_HUNT.md) · [SESSION_HANDOFF.md](SESSION_HANDOFF.md).

## Fakta terkunci (2026-07-21)

| Item | Nilai |
|---|---|
| Edge hunt | ~300 arms A–R10 |
| **PROMOTE_PAPER** | **0** |
| **PROMOTE_FILTER_PAPER** | breadth_lo + corr/vol (meta only) |
| WATCHLIST | LINK residual-z fade vs BTC (p_adj fail) |
| Posture paper | dry · risk 5/30/5/5 · manager **OFF** · ab_shadow **ON** |
| Risk filter | **shadow ON** · **block OFF** · `bot/risk_filter.py` wired |
| Server | `192.168.1.107` · PM2 bot+dashboard · `./restart.sh` |

## Jangan

- Wire entry baru / live / longgarkan risk
- `risk_filter_block: true` tanpa bukti paper (would-deny worse risk)
- Retread H24–H32, H-CYC short unlock, crash-bounce pure, short-alts markdown-only
- Re-tune threshold LINK supaya “lolos p” (overfit)
- Klaim edge dari train+ atau OOS+ tanpa full promotion rule

## Lanjut (pilih 1)

1. **Kumpulkan** paper dry: `RISK_FILTER_SHADOW` di decision_log + stamp open → A/B risk (maxDD/std/worst), bukan exp_R.
2. **Alt-data:** funding/OI/L2 panel hist **hanya** jika coverage OOS cukup; konstruk ≠ H15/24/25.
3. **Shadow LINK residual** — log decision only, no size; kumpulkan n.

## Perintah cepat

```bash
git pull
# baca:
#   memory/CONTINUE.md
#   memory/EDGE_HUNT.md   (scoreboard + antrian)
#   memory/SESSION_HANDOFF.md
# paper dry after deploy:
./restart.sh
python ab_report.py   # ReAct A/B; risk_filter via decision_log RISK_FILTER_SHADOW
```

## Promotion rule (ingat)

`oos CANDIDATE` + train mean>0 + lockbox>0 + day-EW oos>0 + cost×2 oos>0  
(+ excess vs BTC bila relevan) + n≥30 + p_adj<0.05 → baru **PROMOTE_PAPER**.

Filter only: train+oos+lock ↓maxDD, oos worst better, n_kept≥30 → **PROMOTE_FILTER_PAPER** (shadow, not entry).
