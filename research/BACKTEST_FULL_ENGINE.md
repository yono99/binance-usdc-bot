# Backtest Full Strategy Engine — hasil

> **Tanggal:** 2026-07-24  
> **Skrip:** `research/backtest_full_engine.py`  
> **Data:** pure majors 28 · snap server · fee 0.04% + slip 0.05% per sisi (~0.18% RT)  
> **TF rules:** **1d** (sama horizon G2)  
> **Bukan:** CE / ReAct / news / live fill (event-driven SL/TP saja)

---

## 1. Apa yang di-backtest

| Arm | Isi | Engine |
|---|---|---|
| **A** `RULES_ENGINE` | `bot.signals.evaluate` + `Backtester` (sama kode sinyal live rules) | Full entry rules 1d |
| **B** `RULES+G2_FILTER` | A, hanya trade **aligned** G2 quality (top long / bottom short) | Rules + filter entry |
| **C** `G2_BOOK_ENGINE` | LS rebalance 10d quality-mom (Path A) | Full G2 book |

Split: kronologis **70% train / 30% OOS** pada daftar trade (A/B) atau rebalance (C).

---

## 2. Hasil OOS (utama)

| Arm | n OOS | exp_R / mean | win% | PF | maxDD | Verdict |
|---|---:|---:|---:|---:|---:|---|
| **A Rules** | **1044** | **+0.169 R** | 47.8% | **1.32** | ~30.6% | POSITIVE_OOS |
| **B Rules+G2** | **581** | **+0.230 R** | 50.3% | **1.46** | ~**16.0%** | POSITIVE_OOS |
| **C G2 Book** | **71** rebal | **+0.010 R**/periode | 50.7% | — | sumR DD −0.71 | **PASS** (lock + cost2x +) |

### Delta G2 filter vs rules saja
- **Δ OOS exp_R ≈ +0.060** (B − A)  
- Trade lebih sedikit (−890 denied)  
- **maxDD turun ~31% → ~16%**  
- `g2_filter_helps_rules: true`  
- **best_for_entry_signal: B**  
- **best_for_full_engine book: C**

### All-sample (referensi, bukan hakim)

| Arm | n | exp_R | PF |
|---|---:|---:|---:|
| A | 3480 | +0.196 | 1.38 |
| B | 1935 | +0.282 | 1.59 |
| C | 235 rebal | mean +0.015 | — |

---

## 3. Cara baca jujur

### Yang kuat
1. Di **pure majors + 1d + engine sinyal runtime**, rules **bukan** impas — OOS exp_R **+**.  
2. **G2 filter** menaikkan exp_R OOS **dan** memotong drawdown — cocok tujuan edge entry.  
3. **G2 book** tetap lolos bar paper (sudah S1 Path A).

### Batasan (wajib)
1. **TF = 1d**, bukan 15m paper dry harian Anda.  
   Paper dry Path B historis (15m multi-pair) bisa beda — memang beda eksperimen.  
2. **Tanpa** overlay CE / news / ReAct / slot limit — ini backtest **inti sinyal+SL/TP**, bukan PM2 full stack.  
3. `return_pct` di `compute_metrics` bisa **sangat besar** (compounding asumsi) — **jangan** pakai sebagai KPI; pakai **exp_R / PF / DD**.  
4. G2 book mean R **per rebalance** kecil (+1% R period) — beda skala vs trade rules 1d.  
5. Belum cost×2 stress untuk arm A/B di skrip ini (C sudah).  
6. Belum lockbox 50/30/20 terpisah untuk A/B (hanya 70/30).

---

## 4. Implikasi operasional

| Keputusan | Rekomendasi |
|---|---|
| G2 sebagai **filter entry** | Didukung backtest A→B; tetap `shadow` dulu, pertimbangkan uji `block` **hanya** di universe pure majors + TF selaras |
| G2 sebagai **book engine** | Path A/C **PASS** — lanjut paper book runner (sudah) |
| Rules 1d majors | Menarik di OOS; **bukan** otomatis “hidupkan 1d di dry 15m” tanpa spek |
| Live | **Masih tidak** dari backtest ini saja |

### Konflik dengan Path B paper (15m)?
- Path B paper: aligned mean **−0.16** (rules dry multi-alt 15m).  
- Backtest A/B di sini: rules **1d pure majors** exp_R **+**.  
→ **Bukan kontradiksi:** universe + timeframe beda.  
→ Peluang: edge entry lebih masuk akal di **1d majors + G2**, bukan scalping 15m luas.

---

## 5. Perintah ulang

```bash
export PYTHONPATH=/root/binance-usdc-bot
python research/backtest_full_engine.py --tf 1d
# opsional 15m (butuh data 15m majors):
# python research/backtest_full_engine.py --tf 15m
```

Artefak: `logs/backtest_full_engine.json`

---

## 6. Satu kalimat

> Full-engine backtest: **rules 1d majors OOS +EV**; **rules+G2 filter lebih baik** (exp_R↑, DD↓); **G2 book lolos** — peluang profit terukur paling kuat di **1d majors + G2**, bukan mengklaim 15m dry sudah edge.
