# Metodologi Pengujian Strategi

Dokumen ini, bukan profit, adalah **artefak utama** proyek ini. Ia menjelaskan
*asumsi, batasan, dan temuan* secara cukup eksplisit untuk **direproduksi** orang lain.
Sebagian besar "bot trading" mengklaim cuan tanpa bukti; di sini sebaliknya — kami
mendokumentasikan **bagaimana kami menguji** dan **apa yang TIDAK kami temukan**.

> **Klaim jujur:** sistem ini **tidak menemukan edge yang tradeable** pada data yang
> bisa di-backtest. Yang bernilai di sini adalah *sistem pengujiannya*, bukan strateginya.

---

## 1. Prinsip

1. **Out-of-sample atau tidak berarti.** Performa in-sample selalu bisa dibuat positif;
   hanya OOS yang dihitung.
2. **Tanpa lookahead.** Sinyal dihitung dari bar yang sudah TERTUTUP; entry di bar berikutnya.
3. **Biaya nyata selalu dimasukkan.** Tanpa fee+slippage, semua backtest menipu.
4. **Metrik utama = expectancy dalam R**, independen dari sizing.
5. **Fitur yang tak membuktikan diri DIBUANG.** (Contoh nyata: v5 event-guard dihapus
   karena memperburuk OOS.)
6. **Parameter forward-test dikunci.** Re-optimize sambil jalan = menipu diri.

## 2. Asumsi data & biaya

| Aspek | Nilai |
|---|---|
| Pasar | Binance USDC-M / USDⓈ-M Futures (via ccxt) |
| Fee | 0.04% taker per sisi (default) |
| Slippage | 0.02% per sisi (di-bake ke harga fill) |
| Timeframe utama | 15m (juga 5m/1h untuk preset) |
| Simbol uji | BTC, ETH, SOL /USDC (perp) |
| Histori | 1.500–5.000 bar (≈15–52 hari di 15m) |

Catatan keterbatasan data: **Open Interest Binance hanya ~30 hari** → fitur OI hanya
teruji pada rentang pendek. **Orderbook L2 tak tersedia historis** → harus dikumpulkan
forward (lihat `l2collect.py`).

## 3. Metrik: kenapa R-multiple

`R` = hasil trade / risiko-saat-entry (jarak ke SL). Mengukur dalam R membuat
expectancy **independen dari ukuran posisi & equity** — inilah ukuran *edge* yang jujur.
`expectancy_R > 0` setelah biaya = ada edge. Kurva equity hanya ilustrasi compounding.

## 4. Backtest (`bot/backtest.py`)

Event-driven per bar. Sinyal dari `df[:i]` (bar tertutup ≤ i-1), **entry di open bar i**.
Exit via high/low bar (SL/TP); SL-dan-TP dalam satu bar → diasumsikan SL (konservatif).
Fee+slippage dimasukkan ke R. Lihat `tests/test_backtest.py` (matematika R, no-lookahead).

## 5. Walk-forward (`bot/optimize.py`)

Anti-overfit:
1. Pilih parameter terbaik di jendela **train** (in-sample).
2. Uji parameter itu di jendela **test** (out-of-sample) yang belum dilihat.
3. Geser jendela maju; kumpulkan semua trade OOS.
4. **Verdict = expectancy OOS gabungan**, bukan in-sample.

Fitur sinyal di-precompute sekali (vektor) agar ribuan kombinasi cepat diuji; akuntansi
entry/exit reuse `Backtester` (identik dengan backtest). Faithfulness jalur-vektor vs
signal-engine diuji di `tests/test_optimize.py`.

## 6. Fitur yang diuji (berlapis)

| Versi | Tambahan | Backtestable? |
|---|---|---|
| v1 | Trend (EMA/ADX) + momentum (RSI/MACD) + struktur | ya |
| v2 | + filter HTF + regime trend/mean-reversion + sesi | ya |
| v3 | + funding rate + open interest | ya (OI ≤30 hari) |
| v4 | + order flow / CVD (taker buy-sell imbalance) | ya |
| v5 | **Cross-exchange basis** (Binance vs Bybit, mean-reversion) — **REJECTED** | ya |
| v6 | **Liquidation cascade fade** (proxy OHLCV: range+volume spike) — **REJECTED** | ya |
| v7 | **Funding regime sebagai sinyal primer** (fade funding ekstrem) — **REJECTED** | ya |
| News veto | Gemini menilai headline high-impact | **TIDAK** (real-time) |
| L2 | orderbook depth/imbalance/micro-price | hanya forward |

> ⚠️ **Penamaan v5:** label "v5" kini = **cross-exchange basis** (siklus riset baru).
> Eksperimen lama berlabel v5 (*event/volatility guard*) sudah **dibuang** karena
> memperburuk OOS — jangan dirancukan. v5 baru adalah **sumber sinyal struktural
> berbeda** (antar-venue, bukan turunan OHLCV), bukan sekadar filter v1–v4.

## 7. Temuan

### Lintasan edge (OOS, walk-forward — BTC/ETH/SOL)

| Strategi | exp_R | PF | win% |
|---|---|---|---|
| v1 | −0.206 | 0.71 | 41 |
| v2 | −0.105 | 0.86 | 36 |
| v3 | −0.017 | 0.97 | 45 |
| **v4** | **−0.007** | **0.99** | 40 |
| v5 (cross-exchange basis) | −0.123 | 0.80 | 46 |
| v6 (liquidation cascade fade) | −0.430 | 0.46 | 32 |
| v7 (funding regime primer) | −0.116 | 0.82 | 45 |

### Siklus riset baru: sumber edge struktural (di luar OHLCV)

Setelah v1–v4 mentok impas, riset berpindah dari *mengkombinasi indikator OHLCV* ke
*mencari sumber sinyal yang struktural berbeda*. **Tiga siklus pertama semuanya REJECTED:**

1. **v5 cross-exchange basis** (−0.123R) — basis pada majors sudah diarbitrase HFT sub-bar;
   ambang \|z\| besar menangkap momen volatilitas yang *berlanjut*, bukan reversi.
2. **v6 liquidation cascade fade** (−0.430R, terburuk) — fade kalah telak; di resolusi 15m
   event volatilitas besar **berlanjut (momentum)**, bukan snap-back.
3. **v7 funding regime primer** (−0.116R) — funding ekstrem bisa bertahan/menguat saat tren
   jalan → fading = nangkap pisau jatuh; karenanya funding lebih tepat jadi *filter* (v3),
   bukan pemicu.

Ketiganya **mengonfirmasi** temuan inti: edge directional yang tradeable tak hidup di
resolusi-bar pada majors — termasuk sumber antar-venue, event likuidasi, dan positioning.
Loop riset + arsitektur **Gemini co-pilot** + log tiap hipotesis didokumentasikan di
[RESEARCH.md](RESEARCH.md) dan [RESEARCH_LOG.md](RESEARCH_LOG.md).

**Hardening sebelum live (mengejar nol false-positive yang bisa dikendalikan).** Gerbang
CANDIDATE diperkuat agar tak bisa lolos karena kebetulan: (1) **test anti-leakage** otomatis
(racuni bar masa depan → output masa lalu wajib sama); (2) **registry** sumber-kebenaran
tunggal + dedup deterministik (Gemini tak bisa salah-ulang ide teruji); (3) **signifikansi
statistik** — bootstrap blok (Bonferroni atas jumlah trial kumulatif) + effective-n,
**bukan** sekadar tanda exp_R>0.05; (4) **stabilitas parameter** antar-window; (5)
**lockbox holdout + snapshot** (reproducible bit-for-bit) & (6) **cost-stress** 2×.
Promosi ke live bertahap & non-negotiable (RISET→cost-stress→lockbox→paper→micro-live).

### Insight kunci

1. **Filtering ≠ alpha.** Perbaikan v1→v4 hampir seluruhnya dari *filter* (HTF/regime)
   yang membuang trade buruk — **bukan** dari menambah daya prediksi. Memfilter sinyal
   ber-ekspektasi-nol **secara matematis tak bisa** menghasilkan ekspektasi positif;
   hanya menggeser kerugian *menuju* nol. Itu sebab plateau-nya tepat di impas.
2. **"Tak terdeteksi" ≠ "tak ada".** Pada ~100–150 trade OOS, edge +0.05R tak bisa
   dibedakan dari nol (noise > sinyal). Tapi keputusan praktis tak berubah: bahkan bila
   nyata, +0.05R pada maxDD 15–22% punya Sharpe yang tak layak di-trade.
3. **Diminishing returns.** v3→v4 hanya +0.01R → sumber sinyal resolusi-bar
   (harga/volume/funding/OI/CVD) sudah terdiversifikasi habis.
4. **Batasan modal struktural.** Edge yang bertahan butuh **skala** (biaya infra tetap +
   fee-tier + minimum notional). Pada modal kecil, ROI realistis = **skill + artefak
   riset**, bukan profit trading.

## 8. Batasan (limitations)

- Sampel kecil (statistical power rendah) → temuan "impas" tak konklusif secara absolut.
- Satu venue (Binance), periode pasar terbatas (regime-specific).
- OI histori ≤30 hari; L2/microstructure belum dipakai (baru dikumpulkan).
- News veto real-time, tak dapat divalidasi historis.
- Self-collected L2: REST-poll bisa kena rate-limit (Binance menyarankan WebSocket);
  timing snapshot harus konsisten agar tak menimbulkan bias.

## 9. Reproduksi

```bash
pip install -r requirements.txt
pytest -q                                              # 113 test (inti + anti-leakage + signifikansi + gerbang + trader)
python backtest.py --symbols "BTC/USDC:USDC" --bars 1500
python optimize.py --strategy v4 --symbols "BTC/USDC:USDC" "ETH/USDC:USDC" "SOL/USDC:USDC" \
  --bars 3500 --train 1000 --test 300                  # verdict OOS
python optimize.py --strategy v5 --symbols "BTC/USDC:USDC" "ETH/USDC:USDC" "SOL/USDC:USDC" \
  --bars 5000 --train 1000 --test 300 --copilot        # cross-exchange basis + Gemini co-pilot
```
Rust core: `cd core && cargo test` (8 test). Angka bisa sedikit berbeda karena rentang
data live bergerak, tapi **kesimpulan (impas) stabil**.

## 10. Kesimpulan

Pada data yang praktis dapat di-backtest, **tidak ditemukan edge directional yang
tradeable** — dan itu didokumentasikan, bukan disembunyikan. Nilai proyek = **disiplin
pengujian yang reproducible**: backtester no-lookahead, walk-forward OOS, forward-test
parameter-terkunci, dan pembuangan fitur yang tak membuktikan diri. Itulah yang
membedakannya dari bot yang mengklaim profit tanpa bukti.
