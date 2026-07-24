# Parameter hunt — Swing & Scalp × Bull/Bear

> Owner: cari parameter sendiri sampai dapat edge (atau jujur gagal).  
> Harness: `research/edge_hunt_param_regimes.py`  
> Log: `logs/edge_hunt_param_regimes.json`  
> **Tanggal:** 2026-07-24  
> **PROMOTE_PAPER: 0** · **CANDIDATE discovery: 0**

---

## Asumsi (default, tanpa tanya ulang)

| Item | Pilihan |
|---|---|
| Bull / Bear | BTC close **> SMA200** = bull, else bear |
| Swing | **1d**, top **25** liquid USDT alts |
| Scalp | **15m** majors (BTC/ETH/SOL/BNB/XRP) — data 5m tidak ada |
| Cost | **0.18%** RT per trade |
| Exit | SL ATR-mult + TP R:R; max hold swing 15d / scalp 12×15m |
| Grid | Pullback (EMA+ADX+RSI+DI) **dan** BB breakout; long_only / short_only |
| Hakim | train mean_R>0, OOS mean_R>0, n_oos≥40, p_adj Bonferroni |

---

## Hasil ringkas

| Style | Trials | CANDIDATE | train+OOS+ | Verdict global |
|---|---:|---:|---:|---|
| **Swing** | 128 | **0** | 25 leans | NOT_PROVEN / INCONCLUSIVE / REJECT |
| **Scalp 15m** | 128 | **0** | **0** | **REJECTED** / INCONCLUSIVE |

**Tidak ada parameter set yang lolos bar CANDIDATE.**  
**PROMOTE_PAPER = false.**

---

## Lean terbaik (bukan edge)

### Swing — short pullback (bear)

| id (ringkas) | train mean R | OOS mean R | n OOS | raw p | status |
|---|---:|---:|---:|---:|---|
| bear short pb adx30 sl1.5 rr2.5 | +0.27 | **+0.41** | 31 | 0.04 | **INCONCLUSIVE** (n&lt;40) |
| bear short pb adx22 sl1.5 rr2.5 | +0.13 | **+0.28** | 55 | 0.03 | **NOT_PROVEN** (p_adj=1 multi-trial) |
| bull short pb adx22 sl2 rr2.5 | +0.24 | **+0.25** | 68 | 0.04 | **NOT_PROVEN** (p_adj) |

Pola: **short pada pullback** (ADX ketat) lean + di train & OOS, tapi:

1. n sering tipis, atau  
2. setelah **384/128 family trials** Bonferroni → p_adj gagal, atau  
3. long swing / breakout mayoritas **REJECT** / jelek OOS.

### Scalp 15m

- **0** arm train+OOS+  
- Cost 0.18% per round-turn di 15m **memakan** edge tipis  
- Selaras temuan historis R8 / majors sub-day

---

## Verdict jujur

```
TIDAK ADA EDGE PARAMETER yang lolos spek proyek untuk:
  - swing bull long / swing bear short (sebagai PROMOTE)
  - scalp 15m long/short bull/bear

Lean menarik: swing SHORT pullback (bear, adx≈22–30)
  → pantau sebagai WATCHLIST riset saja, BUKAN wire / size live
  → butuh n lebih besar + lockbox + cost×2 + pra-registrasi family kecil
    sebelum boleh disebut CANDIDATE
```

---

## Apa yang **tidak** saya lakukan

- Klaim “sudah dapat edge” dari lean n=31  
- Auto-wire ke `forwardtest`  
- Melonggarkan bar (n≥40 / p_adj) supaya kelihatan lolos  
- Grid jutaan kombinasi (data dredging)

---

## Lanjut yang masuk akal

1. **Stop** memperbesar grid OHLCV swing/scalp di panel yang sama.  
2. Jika short-pullback bear mau dilanjutkan: **pra-registrasi 1 family** (≤4 arms), strict 50/30/20 + cost×2, universe fixed.  
3. Scalp: butuh **fee maker lebih rendah + TF 1–5m + data L2** — spek terpisah, bukan ulangi 15m cost 0.18%.  
4. Paper dry: survival + filter shadow (bukan parameter hunt).

---

## Satu kalimat

> Parameter swing & scalp di-hunt per rezim bull/bear dengan OOS + cost: **0 CANDIDATE**; scalp gagal total; swing short-pullback bear hanya **lean** underpowered — **bukan edge**.
