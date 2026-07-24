# Strict family — Swing SHORT pullback di BEAR

> **Jalur “yang masuk akal”** setelah param hunt besar (0 CANDIDATE).  
> Bukan re-grid. **1 family pra-registrasi · 4 arms · 50/30/20 · cost×2.**  
> Skrip: `research/edge_hunt_validate_short_pb_bear.py`  
> Log: `logs/edge_hunt_validate_short_pb_bear.json`  
> **2026-07-24 · PROMOTE_PAPER = NO**

---

## 1. Kenapa family ini?

Param hunt swing/scalp×bull/bear menemukan **satu lean berulang**:

- **Short pullback** saat **BTC bear** (close < SMA200)  
- ADX ketat, SL 1.5–2 ATR, RR 1.5–2.5  

Scalp 15m & long swing **gagal**. Jadi langkah jujur berikutnya =  
**uji ketat lean itu saja**, bukan 100+ kombinasi lagi.

---

## 2. Pra-registrasi (beku sebelum verdict)

| Item | Nilai |
|---|---|
| Universe | Top **25** liquid 1d (rank volume di snap) |
| Regime | **Bear only** (BTC < SMA200) |
| Side | **Short only** |
| Setup | Pullback: di bawah EMA50, RSI>45, −DI>+DI, \|c−EMA21\|/ATR ≤ touch |
| Cost | 0.18% RT + stress **×2** |
| Split | **50% train / 30% OOS / 20% lockbox** (urutan trade kronologis) |
| n_trials | **4** (Bonferroni) |
| Max hold | 15 bar harian |

### 4 arms

| id | adx | sl·ATR | RR | touch |
|---|---:|---:|---:|---:|
| A1 | 22 | 1.5 | 2.5 | 1.0 |
| A2 | 22 | 1.5 | 1.5 | 1.0 |
| A3 | 22 | 2.0 | 2.5 | 1.8 |
| A4 | 30 | 1.5 | 2.5 | 1.0 |

**PROMOTE_PAPER** hanya jika: train mean_R>0 **dan** OOS CANDIDATE (p_adj&lt;0.05) **dan** lockbox>0 **dan** cost×2 OOS>0.

---

## 3. Hasil

| Arm | n tot | train mean R | OOS mean R | n OOS | raw p | p_adj | lock mean R | cost2x OOS | verdict | promo |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| **A1** adx22 sl1.5 rr2.5 | 183 | +0.039 | **+0.286** | 55 | 0.041 | **0.164** | **+0.351** | **+0.271** | NOT_PROVEN | **NO** |
| **A2** adx22 sl1.5 rr1.5 | 185 | +0.049 | **+0.293** | 56 | 0.022 | **0.088** | **+0.240** | **+0.278** | NOT_PROVEN | **NO** |
| A3 adx22 sl2 rr2.5 t1.8 | 179 | **−0.001** | +0.180 | 54 | 0.098 | 0.392 | +0.270 | +0.168 | NOT_PROVEN | NO |
| A4 adx30 sl1.5 rr2.5 | 101 | +0.226 | +0.406 | 30 | 0.035 | 0.141 | +0.366 | +0.390 | NOT_PROVEN | NO |

```
PROMOTED: NONE
PROMOTE_PAPER: false
```

---

## 4. Baca jujur

### Yang **menarik** (bukan claim edge)

- A1/A2: train+, OOS+, lock+, cost×2 OOS+ — **arah konsisten** di 3 jendela.  
- A2 paling dekat (p_adj **0.088**, masih ≥ 0.05).  
- Cost×2 tidak membunuh lean (bukan artefak fee tipis).

### Yang **menggagalkan promote**

1. **p_adj ≥ 0.05** setelah family trials=4 — belum signifikan ketat.  
2. A3 train ≈ 0 — inkonsisten.  
3. A4 n_oos=30 pas di lantai.  
4. **Universe bias:** rank “volume” di snap mengangkat banyak **meme/small-cap** (DOGS, SATS, NEIRO, …). Lean bisa = karakteristik small-cap di bear, **bukan** edge majors.  
5. Split 50/30/20 di **daftar trade** (bukan kalender panel murni) — cukup untuk family test, bukan bukti final live.

---

## 5. Putusan operasional

| Label | Status |
|---|---|
| PROMOTE_PAPER | **TIDAK** |
| Wire forwardtest / size live | **TIDAK** |
| WATCHLIST riset | **YA** — `short_pb_bear` A1/A2 only |
| Paper shadow log-only | opsional nanti (bukan wajib) |
| Re-grid parameter | **DILARANG** |

### Satu langkah berikutnya (hanya jika dilanjutkan)

1. **Ulang family yang sama** di universe **majors+large only** (buang meme rank) — spek tetap 4 arms.  
2. Jika masih + di OOS/lock/cost2x **dan** p_adj&lt;0.05 → baru bicara paper shadow.  
3. Jika flip − → **RETIRE** family, jangan dipoles.

---

## 6. Satu kalimat

> Family pendek short-pullback bear **lolos arah** (train/OOS/lock/cost2x hijau di A1–A2) tapi **gagal signifikansi & universe-jujur** → **bukan edge**; WATCHLIST saja, tidak di-wire.
