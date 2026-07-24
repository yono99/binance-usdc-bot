# Edge Riset — Memory Loop Backtest (`riset_edge.txt` revisi)

> Spek: [riset_edge.txt](../riset_edge.txt) (prompt AI + SQLite memory)  
> Harness: `research/memory_edge_backtest.py`  
> **Tanggal:** 2026-07-24  
> **PROMOTE_PAPER: false**

---

## Apa yang diuji

Implementasi **deterministik** dari arsitektur prompt (bukan panggilan LLM):

```
regime classifier → retrieve technique×regime×tier stats (memory)
  → playbook signal (swing / breakout / mean_reversion / momentum_pullback)
  → confidence = tech(0–0.4) + hist(0–0.4) + regime_fit(0–0.2)
  → entry jika conf ≥ 0.5
  → exit SL/TP R:R 1:2 (atau timeout)
  → update rolling stats di SQLite / memory
```

| Parameter | Nilai |
|---|---|
| Data | `data/snap` 1d, top **40** liquid USDT perps |
| Cost RT | **0.18%** |
| conf_min | **0.5** |
| min_n memory | **30** (seperti spek) |
| Split | kronologis **70% train / 30% OOS** (cut ≈ 2025-12-27) |
| Scalping | **tidak** di 1d (spek: 1–5m) |

---

## Hasil

| Split | n | mean R | win% | PF | sum R |
|---|---:|---:|---:|---:|---:|
| **TRAIN** | 441 | **+0.054** | 39% | 1.13 | +24.0 |
| **OOS** | 103 | **−0.296** | 31% | **0.44** | −30.5 |
| OOS + memory sufficient | 73 | −0.204 | 37% | 0.59 | −14.9 |
| OOS cold-start (n&lt;30) | 30 | −0.519 | 13% | 0.14 | −15.6 |

### OOS per teknik

| Teknik | n | mean R | win% | PF |
|---|---:|---:|---:|---:|
| breakout | 64 | −0.20 | 38% | 0.50 |
| swing | 39 | −0.45 | 18% | 0.38 |
| mean_reversion / mom_pullback | ~0 di OOS breakdown | — | — | jarang tembak / tidak lolos conf |

### OOS per regime

| Regime | n | mean R |
|---|---:|---:|
| high_vol_expansion | 55 | −0.13 |
| trending_up | 39 | −0.45 |
| low_vol_contraction | 9 | −0.66 |

---

## Verdict

```
REJECTED — OOS mean_R = −0.296 ≤ 0  (n=103, PF=0.44)
PROMOTE_PAPER = false
```

**Pelajaran jujur:**

1. **Train hijau tipis / OOS merah** = pola overfit klasik (memory “belajar” rezim train).  
2. Memory **membantu sedikit vs cold-start** (OOS −0.20 vs −0.52) tapi **tidak** menghasilkan edge positif.  
3. Playbook teknikal (swing/breakout) di 1d + cost 0.18% **tidak** lolos bar proyek.  
4. Spek SQLite memory = **arsitektur bagus untuk disiplin/audit**, **bukan** bukti edge entry.

---

## Artefak

| File | Isi |
|---|---|
| `logs/memory_edge_backtest.json` | angka penuh |
| `logs/memory_edge_bt.db` | SQLite trade_log (server) |
| `research/memory_edge_backtest.py` | harness |

---

## Satu kalimat

> Memory loop `riset_edge.txt` di-backtest secara jujur: **belajar dari histori teknik×regime tidak menghasilkan OOS +EV** pada panel 1d ini — **REJECTED**, jangan wire live.
