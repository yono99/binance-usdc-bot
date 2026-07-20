# Ilmu siklus BTC / alt — pengetahuan pemilik (serap + validasi + backlog)

> **Sumber:** pemilik (filosofi + pengalaman pasar crypto), 2026-07-20.  
> **Status:** **BACKLOG** — diserap untuk desain fitur; **belum** diimplement / di-claim edge.  
> **Kontrak:** semua klaim di bawah harus diuji **data OOS** sebelum jadi sinyal live.  
> **Bukan:** nyalakan manager-mode, H30/L2, atau longgarkan risk karena “ilmu baru”.

Dokumen ini adalah **bahan baku hipotesis struktural** (Fase 4 di PLAN),
bukan override survival + Jalan A.

---

## 1. Ringkasan ilmu (apa yang diajarkan pemilik)

### 1.1 BTC dump → alt turun lebih dalam (beta > 1)

- Bila **BTC turun > ~2%**, altcoin cenderung **turun lebih dalam**.
- Boleh **entry SHORT alt**, tapi **bukan sembarang pair**:
  1. Bandingkan chart **BTC vs coin target**.
  2. Cari indikasi **bearish kuat di alt**:
     - BTC relatif “baik” / sideways kecil, alt sudah lemah / sideways-turun pelan;
     - Saat BTC turun, alt **jatuh lebih dalam** → bearish kuat (beta tinggi / relative weakness).
  3. Short prioritas ke alt yang **memperbesar** gerak turun BTC, bukan yang ikut dangkal.

**Inti edge (klaim):** asimetri **beta** + **relative strength**, bukan prediksi arah BTC murni.

### 1.2 Token unlock / penambahan supply

- Saat **supply ditambah** (unlock, vesting, emission) **tanpa kabar bagus** → sinyal short kuat.
- Rata-rata: unlock → tekanan jual → bearish; bisa short dan **dipegang hari–minggu**
  (bukan scalp 15m), sampai supply shock terserap.

**Inti edge (klaim):** event **struktural on-chain/calendar**, di luar OHLCV murni.

### 1.3 Halving BTC & dominance

- **Halving** = reward miner −50% tiap ~4 tahun → pasokan BTC baru turun;
  historis sering mendahului fase kenaikan besar (bukan garansi kapan / berapa %).
- Pasca-halving / saat **BTC.D** menguat: **hanya sedikit** alt ikut naik; banyak
  **sideways** atau underperform.

### 1.4 Altcoin session (alt season proxy)

- Ditandai: **BTC sideways / naik tipis**, banyak alt **melesat** (bisa puluhan–100%+),
  breadth hijau lebar.
- Bukan “BTC bull = semua alt bull”. Alt season = **risk-on rotasi** keluar dominance BTC.

### 1.5 Empat fase siklus BTC (klasik)

| Fase | Ciri | Implikasi ritel (klaim pemilik) |
|---|---|---|
| **1. Akumulasi** | Sideways panjang setelah dump tajam | Hindari FOMO short/long kasar; range |
| **2. Uptrend** | Kenaikan kuat; supply ketat + demand naik | Bias long struktural (bukan retil scalping) |
| **3. Distribusi** | Puncak euforia; smart money jual; ritel FOMO | **Short / reduce long** untuk ritel yang sadar |
| **4. (implisit) Mark-down / bear** | Setelah distribusi | Survival, jangan catch knife tanpa plan |

### 1.6 Pendorong makro (konteks, bukan entry trigger 15m)

- **Halving** — supply shock periodik.
- **Institusi & ETF spot** — aliran dana TradFi; membedakan siklus post-2024.
- **Makro** — suku bunga bank sentral, likuiditas, adopsi, psikologi.

---

## 2. Validasi jujur (data / teori vs folklore)

| Klaim | Status validasi | Catatan |
|---|---|---|
| Alt beta > 1 saat BTC dump | **Didukung literatur + pengalaman** | Korelasi tinggi; magnitude beta bervariasi per coin & regime |
| BTC −2% → short sembarang alt | **Lemah** | Tanpa filter relative weakness = noise; whipsaw bounce |
| Bandingkan BTC vs alt (relative strength) | **Masuk akal** | Ini **filter pair**, bukan prediksi; cocok di-bot |
| Unlock supply → short multi-hari | **Masuk akal secara flow** | Butuh calendar akurat + kontrol “sudah di-price-in” |
| Halving → selalu bull besar | **Historis sering, bukan kausal pasti** | Sample kecil (4 event); timing lag besar |
| 4 fase Wyckoff-like | **Kerangka bagus** | Label fase **subjektif**; jangan hardcode entry tanpa proxy terukur |
| Alt season = BTC flat + alt breadth | **Proxy bagus** | Butuh **BTC.D** atau breadth index, bukan tebak |
| ETF/makro | **Regime context** | Cocok bias size/stance, **bukan** trigger bar-close |

**Kesimpulan validasi:**  
Ilmu ini **bernilai** sebagai **regime + filter struktural + event calendar**.  
**Bukan** otomatis “bot AI trading terbaik” sampai tiap klaim lolos **spek OOS** (train/test,
permutation, paper arm).

---

## 3. Mapping ke kode yang SUDAH ADA (jangan duplikasi buta)

| Konsep pemilik | Sudah di repo? | Lokasi / gap |
|---|---|---|
| BTC dump → short alt (beta) | **Sebagian** | `bot/forward.py` `_btc_lead` (`dump_flag`, `dominance_dir`); `bot/altdata.py` `btc_gate*` / fade confirm; prompt `gemini_trader` |
| Threshold dump ~2% | **Ada, longgar** | `btc.dump_pct` → `dump_flag = r3 <= -4*dump_pct` (default ~2% 3-bar) — **belum** filter “alt turun LEBIH dalam dari BTC” per pair |
| Relative weakness alt vs BTC | **Belum ketat** | `dominance_dir` masih **proxy kasar** (bukan hitung ret_alt − ret_btc per symbol) |
| Halving phase | **Ada (kasar)** | `_halving_phase()` tanggal hardcode → label string ke Gemini/ReAct; **bukan** 4 fase akumulasi/uptrend/distribusi terukur dari harga |
| Alt season | **Stub** | `dominance_dir=-1` saat BTC naik — **bukan** breadth/BTC.D nyata |
| Token unlock / supply | **Belum** | Tidak ada calendar unlock di pipeline entry |
| ETF flow / makro rates | **Belum** | Tidak di wire ke gate |
| Short multi-hari post-unlock | **Belum** | Horizon bot = bar TF pendek + SL/TP; hold days–weeks perlu **mode swing** terpisah |

Jadi: pemilik **tidak mulai dari nol** — sebagian intuisi sudah di-wire ke Gemini context,
tapi **banyak yang masih heuristik / belum diukur OOS**.

---

## 4. Backlog kerja (urut prioritas)

> **Gerbang:** survival 7-hari + manager OFF tetap.  
> **Prinsip:** AI = **baca regime/filter/event**, bukan “tebak arah OHLCV”.

### P0 — Ukur H-CYC-01 ✅ SELESAI (2026-07-20)

**Script:** `cyc01_dump_weakness.py`  
**Data:** `data/snap_smallcap1800` · 78 simbol · 1d · 2021-07-28 → 2026-07-01  
**Biaya:** fee 0.04% + slip 0.05% per leg → RT 0.18%  
**Split:** train 70% (→2025-01-07) / OOS 30% (2025-01-08→)  
**Artefak:** `logs/cyc01_dump_weakness.json` (+ sensitivitas `*_d3.json`, `*_gap5.json`)

#### Deskriptif — ilmu beta **BENAR**

| Metrik (dump BTC ≤ −2%, n=304 hari) | Nilai |
|---|---:|
| mean ret BTC hari dump | **−3.88%** |
| mean ret alt (EW) | **−4.98%** |
| fraksi hari alt **lebih dalam** dari BTC | **64.5%** |
| mean (alt − btc) | **−1.10%** |

→ Alt memang cenderung **beta > 1** saat BTC dump. Ini **struktur pasar**, bukan edge trade.

#### Arms short (entry close hari dump) — **bukan edge**

| Arm | Train hold=1 | OOS hold=1 | OOS hold=7 | vs short_all (perm OOS) |
|---|---:|---:|---:|---:|
| **short_weak** (klaim utama) | **−0.43%** | **−0.31%** | +2.17% (p≈0.019) | perm **~0.42** (tidak unggul) |
| short_all | −0.52% | −0.24% | +1.89% | — |
| short_strong | −0.46% | **+0.21%** | +1.64% | weak **kalah** di hold pendek |
| short_btc | −0.38% | −0.10% | +0.90% | — |
| short_preweak | −0.51% | −0.12% | +2.23% | mirip weak; tidak stabil |

**Train (2021–awal 2025):** hampir **semua** arm short **negatif** setelah biaya → bounce/mean-reversion pasca-dump mendominasi.  
**OOS (2025–2026):** hold 5–7d kadang hijau (regime bearish/chop), tapi:

- filter **relative weakness tidak mengalahkan** short_all / short_random (perm p ≫ 0.05);
- **short_strong** sering sama atau lebih baik di hold pendek → ranking “paling lemah hari itu” **bukan** alpha;
- train−OOS **tidak konsisten** → jangan CANDIDATE.

**Sensitivitas:** dump 3% + min_gap 3 → **REJECTED** (OOS weak jelek). min_gap 5 dump 2% → tetap **NOT_PROVEN** (perm gagal).

#### Verdict P0

| Kode | Arti |
|---|---|
| **H-CYC-01 trade short_weak** | **NOT_PROVEN → praktis REJECTED sebagai entry edge** |
| **H-CYC-01 deskriptif beta>1** | **CONFIRMED** |
| **Implikasi bot** | Fakta regime saja. **Bukan** auto-short; **bukan** hard block_long hold1 (P0b tolak). Hygiene: matikan short dump-boost |

**Jangan** implement P1 `alt_beta_short` sebagai sinyal buka short.  
**Boleh** (nanti, spek terpisah): `btc_dump → block_long_alt` / kurangi size — itu disiplin, selaras Jalan A; tetap ukur A/B shadow dulu.

### P0b — Universe scale + dump_flag audit + block_long ✅ (2026-07-20)

**Script:** `cyc01b_universe_and_blocklong.py`  
**Data:** `data/snap` @1d · **598 alt** (min_bars≥200) · BTC 2000 bar (2021-01→2026-07)  
**Artefak:** `logs/cyc01b_universe_blocklong.json`

#### Apakah n=78 meremehkan “alt lebih dalam”?

| Universe (alts) | frac_deeper / dump day | mean (alt−btc) |
|---:|---:|---:|
| 50 | 64.4% | −1.13% |
| **78** | **63.9%** | −1.05% |
| 150 | 64.4% | −1.17% |
| 300 | 64.7% | −1.24% |
| 500 | 64.9% | −1.25% |
| **598 (full)** | **64.9%** | **−1.25%** |

- Pair-day level (87k pairs): frac deeper **61.4%** (sedikit lebih rendah — hari dgn banyak alt baru/micro cap).
- Median frac/day **~74%** vs mean **~65%** → kebanyakan hari dump alt memang lebih dalam; ada ekor hari “bounce” yang menarik mean.
- **Δ 78 → 598 ≈ +1 poin persen saja.** Klaim “kalau lebih banyak coin pasti >>64%” **tidak terbukti** pada data ini. Beta>1 **stabil**, bukan artefak sample kecil.

#### LONG alt EW pada hari dump (nilai `block_long`)

Equal-weight long semua alt yang ada data di hari BTC ≤ −2%, hold H, cost RT 0.18%.  
Cut OOS: 2024-11-09.

| Hold | Train mean LONG | OOS mean LONG | p_long_neg OOS |
|---:|---:|---:|---:|
| 1 | **+0.74%** (win 60%) | **+0.09%** | 0.59 |
| 3 | +1.29% | −0.42% | 0.27 |
| 5 | +2.02% | −0.78% | 0.15 |
| 7 | +2.41% | **−1.92%** | **0.011** |

**Verdict `block_long` (hold primer 1d):** **REJECTED_AS_FILTER**  
Train + OOS hold1 long **tidak** negatif andal → memblokir long “karena dump kemarin” di horizon 1d **bisa memotong bounce** (terutama era train bull).  
OOS hold 5–7 long negatif (regime 2024-11+) → **regime-dependent**; bukan spek filter universal tanpa regime gate.

#### Audit `dump_flag` (kode runtime)

| Item | Fakta |
|---|---|
| Definisi | `forward._btc_lead`: `dump_flag = ret_BTC_3bar% ≤ −4×btc.dump_pct` → default **≈ −2% / 3 bar TF buffer** (bukan 1d murni) |
| Config | `btc.dump_pct: 0.5`, `block_counter: true` |
| Dipakai untuk | (1) context Gemini/ReAct prompt; (2) **SHORT conviction ×1.5** di jalur Gemini (`forward.py` ~1711 & ~2749) — **tanpa** gate exp_R setup |
| **Tidak** dipakai untuk | hard `block_long`; rules entry; size-down long |
| Gate terpisah | `btc_gate` di `signals.py` / v8: blok **counter-trend** bila \|btc_ret\| ≥ **0.5%** (1 bar) — **lebih ketat** dari dump 2%, dan berlaku long **dan** short lawan arah |
| Entry confluence | `btc_macro_tier` → blocked bila long vs dump; **SHADOW only** di forward (catat, tidak blokir) |
| Gap berbahaya | Boost short ×1.5 **mengasumsikan** edge short yang P0 **sudah tolak**; thr dump_flag (2%/3bar) ≠ thr btc_gate (0.5%/1bar) |

**Patch hygiene (2026-07-20, disetujui pemilik):**

1. ✅ **`btc.dump_short_boost: false`** di `config.yaml` — kedua boost di `forward.py` di-gate lewat `_dump_short_boost_enabled()` (default OFF).  
2. ✅ Dashboard/curriculum: `btc_dominance_short` = DISABLED / bukan “structural edge”.  
3. ✅ Tes: `tests/test_dump_short_boost.py`.  
4. **Jangan** merge hard `block_long` universal (data hold1 menolak).  
5. `btc_gate` counter-trend **tetap ON**. Opt-in boost: set `dump_short_boost: true` hanya bila spek OOS baru lolos.

### P1 — Fitur (setelah audit)

3. ~~`block_long_on_btc_dump` hard filter universal~~ — **REJECTED** sebagai spek hold1 (lihat P0b).  
4. ~~`alt_beta_short` entry~~ — **REJECTED** (P0).  
5. **Patch hygiene:** disable dump short-boost (rekomendasi; belum dikode).  
6. **`dominance_dir` nyata** — breadth / BTC.D (tetap backlog).

### P2 — Event struktural

6. **Token unlock calendar** — masih open (belum diukur).  
7. **Fase siklus terukur** — stance/size only.

### P3 — AI layer

8. Inject dump/regime ke ReAct sebagai **SKIP long / size**, bukan FLAT.  
9. Lessons: “IF btc_dump THEN skip long alt” — hanya setelah shadow gate.

### Larangan tetap

- manager-mode / H30 dari ilmu ini  
- claim edge short-after-dump  
- campur swing multi-minggu dengan scalp risk tanpa isolasi

---

## 5. Spek hipotesis

```
H-CYC-01  BTC dump + alt relative weakness → short alt
  Status:   P0 SELESAI 2026-07-20 — trade arm NOT_PROVEN/REJECTED; beta deskriptif CONFIRMED
  Script:   cyc01_dump_weakness.py
  Data:     snap_smallcap1800 @1d
  Null:     short_all / short_random / short_strong / short_btc
  Hakim:    train/OOS 70/30 + permutation weak>all
  Hasil:    jangan merge entry short; pertimbangkan risk-filter long saja

H-CYC-01b block_long_on_btc_dump (disiplin, bukan alpha)
  Status:   spek open — belum diukur sebagai gate paper
  Klaim:    mengurangi loss long alt pada hari dump (reduces_risk)
  Null:     rules long tanpa gate

H-CYC-02  Token unlock window → short swing
  Status:   backlog P2 — belum diukur
  ...
```

---

## 6. Kaitan ke mimpi “AI belajar dari loss”

Ilmu ini **melengkapi** mimpi SQLite-lessons:

| Mimpi | Cara pakai ilmu siklus |
|---|---|
| Belajar dari loss | Tag loss: “long alt saat BTC dump + rel weak” → lesson **SKIP long** |
| Entry lebih baik | Gate deterministik dulu; AI hanya jelaskan / rank confidence |
| Manage / TP | **Bukan** FLAT harian; untuk unlock-swing = **time horizon + R plan** tertulis |
| Hindari loss besar | Phase distribusi + dump_flag = **kurangi size / block long**, bukan panik close |

---

## 7. Satu kalimat untuk agent sesi berikutnya

> Pemilik memberi **kerangka beta/dominance/halving/alt-season/unlock/4-fase**.  
> Serap sebagai **backlog struktural** di file ini; **ukur P0 dulu**; jangan merge ke
> entry live atau nyalakan manager. Survival + Jalan A tetap raja sampai spek lolos.

---

*Diisi: 2026-07-20. P0 H-CYC-01 diukur — lihat §4.*
