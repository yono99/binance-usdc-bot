# G2 Quality-Momentum — Paper Shadow

> **Status:** PAPER_SHADOW_ONLY · **wire = false**  
> **Arm beku:** `G2_qmom_h10_q0.3`  
> Spek discovery: [EDGE_RISET_MULTIFAMILY_V2.md](EDGE_RISET_MULTIFAMILY_V2.md)

---

## Apa ini

Counterfactual **long-short quality momentum** di pure majors:

- skor = mean return 20d / std return 20d  
- long top **30%**, short bottom **30%**  
- hold **10** hari  
- cost **0.18%** RT pada day-book LS  

**Tidak** membuka posisi di `forwardtest`. Hanya log + R counterfactual dari close snap.

---

## Perintah

```bash
# dari repo root (server: export PYTHONPATH=/root/binance-usdc-bot)
python research/g2_quality_mom_shadow.py --backfill   # rebuild history
python research/g2_quality_mom_shadow.py --once        # hari terbaru
python research/g2_quality_mom_shadow.py --report      # ringkas log
```

## Artefak

| File | Isi |
|---|---|
| `logs/g2_qmom_shadow.jsonl` | tiap sinyal: longs/shorts/R net |
| `logs/g2_qmom_shadow_report.json` | train/OOS/lock ringkas |
| `logs/g2_qmom_shadow_state.json` | meta backfill |

---

## Aturan

1. **Jangan** ubah lookback/hold/q tanpa pra-registrasi baru  
2. **Jangan** wire ke entry bot  
3. Health `OK_POSITIVE_OOS_LOCK` = counterfactual masih hijau — **bukan** izin live  
4. Live hanya setelah paper shadow manusia + n memadai + review  

---

## Satu kalimat

> G2 di-shadow: param beku, ukur R palsu dari data, **tanpa** size dan **tanpa** claim profit.
