# CONTINUE — bootstrap 1 halaman (anti konteks penuh)

> Buka file ini dulu di **sesi Grok CLI baru** bila chat penuh / TUI restart.
> Detail di [EDGE_HUNT.md](EDGE_HUNT.md) · [SESSION_HANDOFF.md](SESSION_HANDOFF.md).

## Fakta terkunci (2026-07-21)

| Item | Nilai |
|---|---|
| Git | `bec33c1` on `master` (pushed) |
| Edge hunt | ~300 arms A–R10 |
| **PROMOTE_PAPER** | **0** |
| WATCHLIST | LINK residual-z fade vs BTC (OOS+ lock+ cost2x+; **p_adj fail**) |
| Posture paper | dry · risk 5/30/5/5 · manager **OFF** · ab_shadow **ON** |
| Server | `192.168.1.107` · PM2 bot+dashboard · `./restart.sh` |

## Jangan

- Wire entry baru / live / longgarkan risk
- Retread H24–H32, H-CYC short unlock, crash-bounce pure, short-alts markdown-only
- Re-tune threshold LINK supaya “lolos p” (overfit)
- Klaim edge dari train+ atau OOS+ tanpa full promotion rule

## Lanjut (pilih 1)

1. **Risk-filter A/B** di paper dry — metrik maxDD / vol / worst R (Jalan A), bukan exp_R.
2. **Alt-data:** funding/OI/L2 panel hist **hanya** jika coverage OOS cukup; konstruk ≠ H15/24/25.
3. **Shadow LINK residual** — log decision only, no size; kumpulkan n.

## Perintah cepat

```bash
git pull
# baca:
#   memory/CONTINUE.md
#   memory/EDGE_HUNT.md   (scoreboard + antrian)
#   memory/SESSION_HANDOFF.md
```

## Promotion rule (ingat)

`oos CANDIDATE` + train mean>0 + lockbox>0 + day-EW oos>0 + cost×2 oos>0  
(+ excess vs BTC bila relevan) + n≥30 + p_adj<0.05 → baru **PROMOTE_PAPER**.
