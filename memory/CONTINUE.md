# CONTINUE — bootstrap 1 halaman (anti konteks penuh)

> Buka file ini dulu di **sesi Grok CLI baru** bila chat penuh / TUI restart.
> Detail: [SESSION_HANDOFF.md](SESSION_HANDOFF.md) (scoreboard) · [EDGE_HUNT.md](EDGE_HUNT.md).

## Jawab cepat: sudah dapat berapa edge?

| | |
|---|---|
| **Edge entry (PROMOTE_PAPER)** | **0** |
| **Risk filter (PROMOTE_FILTER_PAPER)** | **2** — shadow only, **bukan** edge cuan |
| **WATCHLIST** | **1** — LINK residual-z (p_adj fail) |
| **Candidate (ilmu pemilik)** | **CE-STANCE shadow di dry** — bukan PROMOTE; lihat [CANDIDATE_EDGE.md](CANDIDATE_EDGE.md) |

Jangan bilang “sudah ada edge” bila yang dimaksud entry/profit. Filter/candidate = risk/stance.

## Fakta terkunci (2026-07-21)

| Item | Nilai |
|---|---|
| Git tip | `9e37df6` · filter wire `c67d34c` · npz `b8e924f` |
| Edge hunt | ~300 arms A–R10 |
| **PROMOTE_PAPER** | **0** |
| **PROMOTE_FILTER_PAPER** | `skip_breadth_lo` + `skip_corr_or_volhi` |
| WATCHLIST | LINK residual-z fade vs BTC (p_adj fail) |
| Posture paper | dry · risk 5/30/5/5 · manager **OFF** · ab_shadow **ON** |
| Risk filter | **shadow ON** · **block OFF** · panel live (breadth_lo seen) |
| Server | `192.168.1.107` · PM2 bot+dashboard · `./restart.sh` |
| Modul | `bot/risk_filter.py` · panel `data/risk_filter_panel.npz` |

## Jangan

- Wire entry baru / live / longgarkan risk
- `risk_filter_block: true` tanpa bukti paper (would-deny worse risk)
- Retread H24–H32, H-CYC short unlock, crash-bounce pure, short-alts markdown-only
- Re-tune threshold LINK supaya “lolos p” (overfit)
- Klaim edge dari train+ atau OOS+ tanpa full promotion rule
- Samakan PROMOTE_FILTER dengan PROMOTE_PAPER

## Lanjut (pilih 1)

1. **Candidate edge dry (default):** `cycle_candidate.mode=shadow` → log `CANDIDATE_EDGE_SHADOW`; setelah n≥30 → pertimbangkan `size` di dry saja. Live: `allow_live`+`risk_ack` wajib. Spek: [CANDIDATE_EDGE.md](CANDIDATE_EDGE.md).
2. **Kumpulkan** `RISK_FILTER_SHADOW` → A/B **risk** (maxDD/std/worst).
3. **Alt-data / LINK residual** — log only; jangan re-tune thr.

## Perintah cepat

```bash
git pull
# baca: memory/CONTINUE.md → SESSION_HANDOFF.md → EDGE_HUNT.md
./restart.sh   # di server dry
python ab_report.py   # ReAct A/B; filter via decision_log RISK_FILTER_SHADOW
```

## Promotion rule (ingat)

**Entry:** oos CANDIDATE + train mean>0 + lockbox>0 + day-EW oos>0 + cost×2 oos>0  
(+ excess vs BTC bila relevan) + n≥30 + p_adj<0.05 → **PROMOTE_PAPER**.

**Filter only:** train+oos+lock ↓maxDD, oos worst better, n_kept≥30 → **PROMOTE_FILTER_PAPER**  
→ shadow dulu; block hanya setelah paper risk A/B.
