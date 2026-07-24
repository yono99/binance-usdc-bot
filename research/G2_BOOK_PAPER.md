# G2 Paper Book Engine (Path A) — terpisah dari bot rules

> **Rekomendasi pasca-S1:** Path A **PASS** → jalankan G2 sebagai **full strategy engine paper**,  
> terpisah dari scalping/rules dry. Path B tetap shadow filter.  
> **Wire live = tidak.**

---

## Apa ini

| | Bot rules dry (`forwardtest`) | G2 paper book |
|---|---|---|
| Sinyal | EMA/rules/Gemini | Quality mom LS |
| Universe | screener luas | pure majors 28 |
| Horizon | 15m / SL-TP | **10 hari** rebalance |
| Order Binance | paper / live | **tidak pernah** |
| Tujuan | survival + ukur entry | edge **book engine** |

Arm beku: `G2_qmom_h10_q0.3` · cost 0.18% RT · long top 30% / short bottom 30%.

---

## Perintah

```bash
export PYTHONPATH=/root/binance-usdc-bot   # atau repo root
cd /root/binance-usdc-bot

# sekali: bangun riwayat + state
python research/g2_book_runner.py --backfill

# harian (setelah snap 1d ter-update): settle + buka buku bila waktunya
python research/g2_book_runner.py --once

# ringkas
python research/g2_book_runner.py --report

# audit non-overlap penuh (S1)
python research/g2_book_paper.py
```

## Artefak

| File | Isi |
|---|---|
| `logs/g2_book_state.json` | open book + equity_sum_r |
| `logs/g2_book_ledger.jsonl` | open/close tiap periode |
| `logs/g2_book_live_report.json` | train/OOS ringkas |
| `logs/g2_book_paper.json` | audit Path A |

## PM2 opsional (cron harian 02:00 UTC)

Uncomment app `g2-book` di `ecosystem.config.cjs`, lalu:

```bash
pm2 start ecosystem.config.cjs --only g2-book
pm2 save
```

Atau cron:

```bash
0 2 * * * cd /root/binance-usdc-bot && PYTHONPATH=. venv/bin/python research/g2_book_runner.py --once >> logs/g2_book_cron.log 2>&1
```

**Syarat:** snap 1d pure majors ter-update (manual atau job unduh terpisah).

---

## Path B (tetap)

```bash
python research/g2_entry_ab_report.py --mode dry
```

Stamp `g2_*` di open rules bot sudah aktif (shadow).  
`block=false` sampai Path B PASS penuh.

---

## Larangan

- Jangan campur size G2 book ke `forwardtest` live  
- Jangan retune q/hold  
- Jangan anggap equity_sum_R = uang dompet (unit R paper)  

---

## Satu kalimat

> G2 book paper = **mesin strategi penuh di kertas** (rebalance 10d), hidup **di samping** bot rules — bukan pengganti survival dry, bukan live.
