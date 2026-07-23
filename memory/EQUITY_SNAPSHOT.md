# Equity snapshot — paper dry (Proxmox)

> **Sumber:** `http://192.168.1.107:8000` + `logs/trades_dry.jsonl` via SSH.  
> **Mode:** `dry` (paper) — **bukan** live Binance.  
> **Terakhir diukur:** 2026-07-23 ~21:40 UTC  
> **Jujur:** recovery dari trough ≠ PROMOTE_PAPER.  
> **Penting:** exp_R **all-time** tercemar arsitektur lama — pakai window **post-fix**.

---

## 0. exp_R adil — open setelah fix arsitektur (opsi A)

**Metode:** pair `forward_open` → `forward_close` FIFO per simbol (`logs/trades_dry.jsonl`).  
**Window primer:** `open_ts >= 2026-07-20T00:00:00Z` (setelah ghost lock + manager OFF).  
**Bukan** filter “close saja” (itu mencampur open era kotor).  
**Bukan** PROMOTE_PAPER.

### Cutoff ↔ push GitHub

| Cutoff (open_ts) | Commit acuan | Isi fix |
|---|---|---|
| ≥ **19 Jul** | `b312da8` dupe · `ddd9a23` journal · `65aa389` plan | Anti-duplikat close/open |
| ≥ **20 Jul** **PRIMARY** | `91d12ef` ghost+lock · `750fded` manager OFF · `b9ddc40` SL fixed | Desync 2 proses, FLAT massal, manage SL |
| ≥ **21 Jul** | `c67d34c` risk filter · `190c1d3` CE size · `373e41a` mixin | Stance/filter + arch |

### Tabel exp_R (paired)

| Window | n | WR% | **exp_R** | sum_R | PF (R) | sum_pnl $ | lev mean | eq path |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| **ALL-TIME** | 612 | 29.9 | **−0.557** | −340.8 | 0.28 | −10.52 | 9.8 | 11.6→22.3 (min 5.4) |
| ALL-TIME clean (buang R&lt;−10) | 611 | — | **−0.179** | — | — | — | — | 1 trade PLAY −231R (7 Jul) |
| open ≥ **19 Jul** | 118 | 44.1 | **+0.094** | +11.1 | 1.25 | +12.94 | 8.8 | 11.0→22.3 |
| open ≥ **20 Jul** **PRIMARY** | **91** | **51.7** | **+0.119** | **+10.8** | **1.26** | **+12.65** | 8.5 | 9.7→22.3 |
| open ≥ **21 Jul** | 59 | 57.6 | **+0.218** | +12.9 | 1.47 | +9.78 | 10.3 | 11.5→22.3 |
| open ≥ **22 Jul** | 46 | 60.9 | **+0.300** | +13.8 | 1.69 | +10.77 | 11.9 | 10.4→22.3 |
| close ≥ 20 Jul (campur open lama) | 113 | 46.0 | +0.100 | +11.3 | 1.25 | +12.78 | 8.8 | — |

### Primary (open ≥ 20 Jul) — per hari close

| Hari | n | WR% | exp_R | sum_pnl $ |
|---|---:|---:|---:|---:|
| 20 Jul | 28 | 39 | −0.100 | +2.28 |
| 21 Jul | 13 | 46 | −0.112 | −0.35 |
| **22 Jul** | 32 | 62 | **+0.276** | +3.19 |
| **23 Jul** | 18 | 56 | **+0.345** | +7.53 |

Primary worst: CLO short **liq −2.22R** (−$2). Best: T long TP **+3.34R**; CLO short TP **+2.62R** (+$4.49).

### Interpretasi window adil

1. **All-time exp_R −0.56 jangan dipakai** menilai bot **sekarang** — era pre-fix + outlier R.  
2. **Primary exp_R ≈ +0.12 (n=91)** mendukung klaim: setelah fix arsitektur, paper **bukan** −EV seberat all-time.  
3. Hijau lebih kuat di **22–23 Jul** (n masih kecil) — jangan overfit 2 hari.  
4. `lev_mean` primary ~**8.5** (akhir-akhir lebih tinggi) — **bukan** posture survival 5x; recovery diukur di leverage longgar.  
5. Tetap **bukan** bar PROMOTE_PAPER / auto live scale.

**Artefak server:** `logs/expr_post_fix_dry.json` (dihasilkan `tmp_expr_post_fix.py`).

```bash
# ulang ukur di server
cd /root/binance-usdc-bot
python3 tmp_expr_post_fix.py   # atau salin skrip dari repo dev
```

---

## 1. Ringkas equity (path)

| Metrik | Nilai |
|---|---:|
| **Trough journal (paling dalam)** | **$5.40** (2026-07-07 AERO SL) |
| Low sekunder (handoff lama) | **$5.65** (2026-07-12) |
| Lonjakan mencurigakan | **~$19.96** first hit ≥$10/$15 **2026-07-14** ~07:06 (bukan pure compound) |
| ATH path close | **$24.28** (era post-fix) |
| **Equity last close** | **$22.26** (ukur 23 Jul ~21:17 UTC) |
| Ledger status (USDT+USDC) | **~$22.26** (18.83 + 3.43) |
| Naik trough $5.40 → last | **+~312%** (basis kecil + path + kemungkinan top-up 14 Jul) |

### Status bot saat snapshot post-fix

| Item | Nilai |
|---|---|
| Mode / enabled | dry / ON |
| Open (saat audit status) | ~10 |
| Risk store dry | lev **20** · pos **10** · trades **990** · DD 20% — **drift vs lock 5/5/30** |
| Stats API all-time | WR ~30% · exp_R **−0.15…−0.17** (tercemar) |
| **exp_R adil (open≥20 Jul)** | **+0.119** · WR 51.7% · n=91 |

---

## 2. Interpretasi (disiplin)

1. Paper equity di curve diisi saat close.  
2. Dari low struktural, path naik ke ~$22 — **% besar** karena basis mikro + volatility + **bukan** bukti edge entry.  
3. **exp_R all-time negatif** = campuran bug/era lama; **exp_R post-fix primer positif tipis**.  
4. KPI tetap proses/risk; PROMOTE_PAPER = **0**.  
5. Jangan longgarkan risk / scale live hanya karena recovery % atau 2 hari hijau.

---

## 3. Cara ulang ukur

```bash
ssh -i ~/.ssh/id_ed25519_proxmox root@192.168.1.107 \
  'cd /root/binance-usdc-bot && python3 tmp_expr_post_fix.py'
# UI: http://192.168.1.107:8000
```

Atau API: `/api/stats` (all-time, **jangan** samakan dengan primary).

---

## 4. Artefak raw (ringkas)

```
trough_deep: 2026-07-07T04:39Z  AERO  equity=5.40
trough_hand: 2026-07-12          LAB   equity≈5.65
jump_14jul:  2026-07-14T07:06Z         equity≈19.96 (first ≥10/15)
last:        2026-07-23T21:17Z         equity=22.26
ALL exp_R:   -0.557  n=612
PRIMARY open≥2026-07-20: exp_R=+0.119  WR=51.7%  n=91  PF_R=1.26  sum_pnl=+$12.65
NOT PROMOTE_PAPER
```

*Update “Terakhir diukur” + tabel §0 bila snapshot diulang.*
