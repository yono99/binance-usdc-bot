# G2 Fase S1 — Path A + Path B (hasil)

> Lanjutan audit: solidifikasi G2 untuk edge entry.  
> **2026-07-24** · wire live = **tidak** · block entry = **tetap false**

---

## Path A — Full book engine (setia research)

| Item | Nilai |
|---|---|
| Arm | `G2_qmom_h10_q0.3` |
| Cadence | rebalance non-overlap tiap **10** hari |
| Panel | pure majors 28 · 2019→2026-07 |
| n rebalance | **235** |

| Split | n | mean R | win% |
|---|---:|---:|---:|
| Train 70% | 164 | **+0.0174** | 54% |
| OOS 30% | 71 | **+0.0101** | 51% |
| Lockbox 20% | 47 | **+0.0018** | 51% |
| OOS cost×2 | 71 | **+0.0083** | 49% |
| MaxDD (sum R) | — | **−0.71** | — |

```
VERDICT PATH_A: PATH_A_PASS_PAPER_BOOK
```

**Arti:** sebagai **full strategy engine** (LS book), G2 lolos bar paper di snap  
(train+, OOS+, lock+, cost×2+). Lock tipis — margin ekonomi kecil, tapi **bukan** gagal.

Skrip: `research/g2_book_paper.py` · `logs/g2_book_paper.json`

---

## Path B — Entry overlay A/B (tujuan edge entry bot)

Metode: 602 open→close dry paper; rank G2 di **tanggal open** (hanya pure majors).

| Bucket | n | mean R | win% |
|---|---:|---:|---:|
| **aligned** | 47 | **−0.159** | 32% |
| **misaligned** | 27 | **−0.561** | 19% |
| neutral | 57 | −0.214 | 30% |
| outside G2 universe | 471 | −0.65* | — |

\*outside = alt di luar pure majors (G2 N/A / fail-open)  
**Δ (aligned − misaligned) ≈ +0.40 R**

```
VERDICT PATH_B: PATH_B_LEAN_POSITIVE
```

**Arti jujur:**
- G2 **membantu relatif**: entry yang **selaras** quality jauh **kurang jelek** dari yang melawan rank.  
- Mean aligned **masih negatif** → **rules entry sendiri** masih merugikan; G2 filter **belum** membuat entry +EV.  
- Belum `PATH_B_PASS` (butuh n lebih besar + ideally aligned mean > 0).  
- **`block=true` BELUM** — filter saja tidak cukup bila mesin arah masih −EV.

Skrip: `research/g2_entry_ab_report.py` · stamp open: `bot/forward_open.py` (g2_* di journal)

---

## Cabang keputusan (dari audit)

| Path A | Path B | Keputusan operasional |
|---|---|---|
| **PASS** | **LEAN_POSITIVE** (aligned mean masih −) | G2 = **engine book** solid di paper + **filter entry** yang membantu tapi **tidak** menggantikan perbaikan arah rules |

### Implikasi

1. **Jangan** matikan rules dan full-size G2 book di live.  
2. **Boleh** lanjut paper:  
   - Book Path A sebagai **modul portfolio shadow** (sudah di-backtest)  
   - Overlay Path B tetap shadow; kumpulkan n aligned/misaligned dengan stamp live  
3. **Prioritas edge entry:** perbaiki/ ganti sumber **arah** (rules), G2 sebagai **konfirmasi quality**  
4. Atau: spek **G2 book paper daemon** terpisah (bukan campur scalping 15m rules)

---

## Status config

```yaml
agent.g2_entry.shadow: true
agent.g2_entry.block: false   # TETAP — Path B belum PASS penuh
```

Bot dry sudah stamp `g2_aligned` / `g2_bucket` di open.

---

## Perintah ulang

```bash
export PYTHONPATH=/root/binance-usdc-bot
python research/g2_book_paper.py
python research/g2_entry_ab_report.py --mode dry
```

---

## Satu kalimat

> **Path A lolos** (G2 book = full engine paper OK); **Path B lean** (filter membantu, entry rules masih −EV) → G2 **solid sebagai book + konfirmasi quality**, **belum** cukup sebagai satu-satunya mesin entry profit.
