# CONTINUE — bootstrap 1 halaman (anti konteks penuh)

> Buka file ini dulu di **sesi Grok CLI baru** bila chat penuh / TUI restart.
> Detail: [SESSION_HANDOFF.md](SESSION_HANDOFF.md) (scoreboard) · [EDGE_HUNT.md](EDGE_HUNT.md).

## Jawab cepat: sudah dapat berapa edge?

| | |
|---|---|
| **Edge entry (PROMOTE_PAPER)** | **0** |
| **Risk filter (PROMOTE_FILTER_PAPER)** | **2** — shadow only, **bukan** edge cuan |
| **WATCHLIST** | **1** — LINK residual-z (p_adj fail) |
| **Candidate (ilmu pemilik)** | **CE-STANCE** pondasi · dry⇄live **1:1** · bukan PROMOTE — [CANDIDATE_EDGE.md](CANDIDATE_EDGE.md) |

Jangan bilang “sudah ada edge” bila yang dimaksud entry/profit. Filter/candidate = risk/stance.

## Fakta terkunci (2026-07-21)

| Item | Nilai |
|---|---|
| Git tip | `c4ab001` CE-STANCE · filter `c67d34c` · npz `b8e924f` |
| Edge hunt | ~300 arms A–R10 · **PROMOTE_PAPER = 0** |
| **PROMOTE_FILTER_PAPER** | `skip_breadth_lo` + `skip_corr_or_volhi` (shadow) |
| WATCHLIST | LINK residual-z fade vs BTC (p_adj fail) |
| Pondasi | Ilmu pemilik → CE-STANCE (size long dump/markdown/unlock) |
| Dual-track | Aturan **1:1** dry & live; live = realisme endpoint + `risk_ack` |
| Posture paper | dry · risk 5/30/5/5 · manager **OFF** · ab_shadow **ON** · CE `mode=shadow` |
| Risk filter | **shadow ON** · **block OFF** |
| Server | `192.168.1.107` · PM2 bot+dashboard · `./restart.sh` |

## Jangan

- Wire entry short / full live size / longgarkan risk karena “ilmu benar”
- `allow_live` tanpa `risk_ack` + stop rule tertulis
- `risk_filter_block: true` tanpa bukti paper
- Retread H24–H32, H-CYC short unlock, crash-bounce pure
- Klaim live hijau = PROMOTE_PAPER / auto-edge
- Matikan dry “karena live lebih real” (kehilangan volume)
- Samakan PROMOTE_FILTER dengan PROMOTE_PAPER

## Lanjut (pilih 1)

1. **Default:** CE shadow dry (jalan) + baca telaah paper/live di [CANDIDATE_EDGE.md](CANDIDATE_EDGE.md) §0.1.
2. **Dual 1:1:** setelah setuju kalimat risiko — live **shadow** dulu (log only) atau `size`+`allow_live`+`risk_ack` **mikro**.
3. **Kumpulkan** `RISK_FILTER_SHADOW` → A/B risk.
4. **Jangan** re-tune LINK thr.

## Perintah cepat

```bash
git pull
# baca: memory/CONTINUE.md → SESSION_HANDOFF.md → CANDIDATE_EDGE.md
./restart.sh   # di server dry
python ab_report.py   # ReAct A/B; filter via decision_log RISK_FILTER_SHADOW
```

## Promotion rule (ingat)

PROMOTE_PAPER = entry alpha bar penuh.  
PROMOTE_FILTER = meta risk only.  
CE-STANCE = kandidat stance; live mikro ≠ certified edge.
