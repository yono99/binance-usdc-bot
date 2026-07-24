# EDGE HUNT LOOP — memori otonom (kampanye all-time)

> **Otoritas:** pemilik 2026-07-24 memberi **otoritas penuh** untuk unduh data
> all-time + pencarian edge dari data dulu, lalu forward-test bila lolos bar.  
> **Bukan** wire runtime / live tanpa PROMOTE_PAPER.  
> **Hakim:** OOS kronologis + cost RT 0.18% + multi-trial + (ketat) lockbox/cost×2.  
> **"Tidak ketemu" = hasil valid.**

File ini = **memori loop** yang agent baca tiap sesi agar tidak lupa di loop panjang.
State mesin: [EDGE_HUNT_STATE.json](EDGE_HUNT_STATE.json) · scoreboard lama:
[EDGE_HUNT.md](EDGE_HUNT.md).

---

## 1. Tujuan loop

```
DOWNLOAD all-time OHLCV (futures lolos screen)
        │
        ▼
  PANEL research (1d primer; 1h majors opsional)
        │
        ▼
  DISCOVERY arms (novelty, bukan retread H24–H32 / A–F mati)
        │
        ▼
  STRICT validate (50/30/20 + cost×2 + p_adj)
        │
   ┌────┴────┐
   │         │
 CANDIDATE   0 edge
   │         │
   ▼         ▼
 FORWARD     catat NOT_PROVEN /
 paper       next novelty family
   │
   ▼
 PROMOTE_PAPER hanya bila bar penuh + paper stabil
```

**Sumber data (prioritas):**

| # | Sumber | Pakai untuk |
|---|---|---|
| 1 | **Binance USDM public OHLCV** via ccxt (`research/download_snap_alltime.py`) | Panel utama all-time |
| 2 | Snap lokal `data/snap/*.pkl` | Reproduksi bit-for-bit |
| 3 | Funding / OI hist (bila API + cache) | Non-OHLCV (fase terpisah) |
| 4 | TradingView | **Bukan** pipeline utama (no free bulk export API andal); pakai Binance langsung |

Riset = **USDT-M** (histori panjang). Eksekusi live tetap USDC-M (aturan CE/live terpisah).

---

## 2. Universe & screening unduhan

Samakan semangat screener runtime, **longgar untuk histori**:

| Filter unduhan | Default | Alasan |
|---|---|---|
| Settle | USDT perp COIN | All-time length |
| `min_quote_volume_24h` | **5_000_000** (sama config) | Lolos screening awal likuiditas |
| `min_bars` simpan | 100 | Buang listing sekejap |
| TF primer | **1d** | Cost & multiple-testing jujur |
| Bars target | 3500 (~9.5y) | API mengembalikan yang tersedia |
| Dedup base | prefer liquid USDT | Hindari double BTC/USDT+USDC di panel |

Opsional ketat (fase 2): max spread / ATR — **bukan** untuk unduh awal (butuh call/orderbook mahal).

---

## 3. Bar promosi (jangan dilonggarkan)

**Discovery CANDIDATE (lunak):**

- OOS mean > 0, n ≥ 30, p_adj < 0.05, train mean > 0 (tanda konsisten)

**PROMOTE_PAPER (keras) — semua wajib:**

1. Discovery CANDIDATE  
2. Lockbox mean > 0 (split 50/30/20)  
3. Day equal-weight OOS > 0 (anti cluster)  
4. Cost ×2 OOS > 0  
5. Excess vs BTC bila arm market-beta  
6. n memadai; **bukan** retread arm yang sudah REJECT  
7. Paper forward (param beku) sebelum claim runtime

**PROMOTE_FILTER_PAPER** = meta risk overlay saja (sudah 2 shadow) — **bukan** entry.

---

## 4. Strategi pencarian (agent atur sendiri)

### Larangan retread (sudah mati / WATCHLIST saja)

- H24–H32, H30 maker, crash-bounce pure, short-alts markdown-only  
- A–F discovery ulang tanpa novelty data/konstruk  
- Re-tune threshold LINK residual  
- Auto-short dump/unlock sebagai entry  
- Klaim edge dari in-sample / train+ saja  

### Antrian novelty (prioritas, update tiap putaran)

| Pri | Family | Status 2026-07-24 |
|---|---|---|
| P0 | **Data all-time refresh** + coverage doc | ✅ remote download 513/528 ok, end 2026-07-24 |
| P0 | Re-run A–F di panel all-time (sanity) | ✅ **0 CANDIDATE** (38 REJECT / 4 NOT_PROVEN) |
| P0 | **R11 — listing-age / maturity** | ✅ **0 CANDIDATE** (rev20 lean NOT_PROVEN only) |
| P0 | Strict risk_filter + LINK pairs all-time | ✅ FILTER×2 · LINK none |
| P0 | **R12** + strict (riset_edge cats) | ✅ **0 PROMOTE** |
| P0 | **R14** 1h liquid | ✅ **0** (11 REJECTED) |
| P0 | Funding/carry arsip RESEARCH_LOG | ✅ REJECTED hist |
| STOP | **surrender_ohlcv_entry** | ✅ aktif — no R15 OHLCV retread |
| LATER | Non-OHLCV novelty (L2 / funding pipeline baru) | ⏸ butuh data baru |
| OPS | Paper dry survival + filter shadow | lanjut |

### Compact tiap putaran (wajib)

1. Append seksi di [EDGE_HUNT.md](EDGE_HUNT.md)  
2. Update [EDGE_HUNT_STATE.json](EDGE_HUNT_STATE.json)  
3. JSON di `logs/edge_hunt_*.json`  
4. Bila **ada** CANDIDATE ketat → tulis `research/EDGE_RISET_<id>.md`  
5. Ringkas 5 baris di [CONTINUE.md](CONTINUE.md)

---

## 5. Perintah kanonik

```bash
# di server riset (utama: 192.168.1.107) atau lokal
cd /root/binance-usdc-bot   # atau repo Windows

# 1) unduh all-time + volume screen
python research/download_snap_alltime.py --tf 1d --bars 3500 --settle USDT \
  --screen-volume --min-qv 5000000

# 2) coverage
python research/_snap_coverage.py

# 3) discovery (contoh)
python research/edge_hunt.py --round all --out logs/edge_hunt_alltime_YYYYMMDD.json
python research/_summarize_alltime_hunt.py logs/edge_hunt_alltime_YYYYMMDD.json

# 4) strict (bila ada lean)
python research/edge_hunt_validate_pairs.py
python research/edge_hunt_validate_risk_filter.py
```

Deploy bot paper **terpisah** — jangan campur unduh massal dengan PM2 bot tanpa niat.

---

## 6. Status kampanye (update agent)

| Field | Nilai |
|---|---|
| Kampanye | `alltime_2026-07-24` |
| Data host | `192.168.1.107` `data/snap` |
| Download | ✅ SELESAI ok=513 fail=15 files_1d≈760 end_top **2026-07-24** |
| Panel A–F | 1122×65 · cost 0.18% · **0 CANDIDATE** |
| R11–R14 | **0 CANDIDATE / 0 PROMOTE_PAPER** |
| PROMOTE_PAPER | **0** |
| Surrender | **OHLCV entry = true** (fase ini) |
| Next | survival ops · novelty non-OHLCV only |
| Status file | `research/EDGE_RISET_STATUS.md` |

---

## 7. Satu kalimat

> Loop panjang: unduh Binance all-time → uji novelty OOS jujur → 0 edge = valid →
> compact memori → lanjut family berikutnya; wire runtime hanya setelah PROMOTE_PAPER.

---

*Dibuat 2026-07-24 — otoritas penuh pemilik untuk pencarian edge data-first.*
