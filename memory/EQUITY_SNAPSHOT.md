# Equity snapshot — paper dry (Proxmox)

> **Sumber:** `http://192.168.1.107:8000` via SSH (`~/.ssh/id_ed25519_proxmox`) → localhost API.  
> **Mode:** `dry` (paper) — **bukan** live Binance.  
> **Terakhir diukur:** 2026-07-23 ~08:52 UTC  
> **Jujur:** recovery dari trough ≠ edge. `exp_R` paper masih **negatif**.

---

## Ringkas (kenaikan dari titik terendah)

| Metrik | Nilai |
|---|---:|
| **Titik terendah (trough)** | **$5.65** |
| Waktu trough (approx) | **2026-07-12** ~21:13 UTC (`LAB/USDT` close ≈ equity 5.65) |
| **Puncak setelah trough (ATH path)** | **$20.00** |
| Waktu ATH (approx) | **2026-07-14** ~16:18 UTC (`FF/USDT` TP) |
| **Equity last close (curve)** | **$15.69** |
| Ledger sekarang (USDT+USDC) | **$15.69** (13.07 + 2.62) |
| MTM + unrealized open | **~$16.44** (open PnL ~+$0.75, 9 posisi) |
| **Naik dari trough → last** | **+177.7%** ($5.65 → $15.69) |
| **Naik dari trough → ATH** | **+254.0%** ($5.65 → $20.00) |
| Return start curve → last | **+147.9%** ($6.33 → $15.69) |
| Drawdown dari ATH path → now | **~21.6%** ($20 → $15.69) |

### Status bot saat snapshot

| Item | Nilai |
|---|---|
| Mode / enabled | dry / ON |
| Open positions | 9 |
| Day PnL / trades | +$1.13 / 7 |
| Win rate (stats) | 29.3% |
| exp_R (stats) | **−0.170** |
| DRAWDOWN LOCK | unlocked (USDT peak $13.34, dd ~2.1%) |

---

## Interpretasi (disiplin)

1. **Paper equity** di curve diisi saat close (`equity` di journal).  
2. Dari **low $5.65** (12 Jul) equity path pernah ke **$20** (14 Jul), lalu mundur ke **~$15.7** (23 Jul).  
3. **Kenaikan dari trough besar secara %** karena basis kecil (mikro paper) + path volatile — **bukan** bukti PROMOTE_PAPER.  
4. **exp_R negatif** + win rate ~29% = edge entry **belum** terbukti; KPI tetap proses/risk (lihat PLAN_OPERASIONAL).  
5. Jangan longgarkan risk / scale live karena “naik dari low”.

---

## Cara ulang ukur

```bash
# dari mesin dev (SSH key Proxmox)
ssh -i ~/.ssh/id_ed25519_proxmox root@192.168.1.107 \
  'curl -sS http://127.0.0.1:8000/api/stats | python3 -m json.tool | head'
# UI: http://192.168.1.107:8000
```

Atau skrip ad-hoc di server: min/max `equity_curve` di `/api/stats` + scan `/api/trades` untuk timestamp trough/ATH.

---

## Artefak raw (ringkas)

```
trough: 2026-07-12T21:13:19Z  LAB/USDT  equity≈5.65
ath:    2026-07-14T16:18:40Z  FF/USDT   equity≈20.00
now:    2026-07-23 ledger 15.69  mtm≈16.44  open=9
recovery trough→last +177.7%  trough→ath +254%
exp_R -0.17  wr 29.3%
```

*Update baris “Terakhir diukur” bila snapshot diulang.*
