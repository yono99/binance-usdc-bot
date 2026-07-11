# Gemini Trader — Praktisi Trader ber-Memori & Refleksi

Visi: Gemini bertindak sebagai **trader praktisi** — mencerna banyak data, mengambil
keputusan open/flat, lalu **belajar dari rekam jejaknya sendiri** dan mengembangkan
*playbook* tertulis. Claude berperan **guru** (menulis kurikulum awal); setelah itu
loop refleksi berjalan mandiri.

> **Mekanisme "belajar" (jujur):** bobot Gemini BEKU — ia tak mengubah otaknya. "Belajar"
> di sini = **memori pengalaman (SQLite) + refleksi terjadwal + retrieval playbook ke
> prompt**. Ini analog agentik yang nyata, bukan true self-learning. Disepakati eksplisit.

> **Anti-takhayul (disepakati):** Gemini boleh *mengusulkan* pelajaran, tetapi sebuah
> pelajaran hanya **AKTIF jadi aturan** setelah lolos **ambang bukti statistik** yang
> dihitung deterministik dari rekam jejak (n cukup + efek nyata). Pelajaran dari
> keberuntungan acak tidak pernah naik kelas.

> **Keselamatan:** semuanya DEMO/paper dulu. Track record Gemini diukur gerbang
> signifikansi yang sama (`bot/stats.py`, `copilot.verdict`). Uang nyata hanya bila lolos.

Lihat juga: [RESEARCH.md](RESEARCH.md) (gerbang & hardening) · [METHODOLOGY.md](METHODOLOGY.md).

---

## Arsitektur (alur mandiri)

```
Claude tulis KURIKULUM ──► Gemini DECIDE (kurikulum + pelajaran aktif + data kaya)
                                   │
                                   ▼  keputusan + konteks → SQLite (gemini_decisions)
                          posisi paper di-eksekusi (risk/SL/TP deterministik)
                                   │
                                   ▼  hasil tiap trade (R) → settle ke SQLite
                          Gemini REFLECT (baca jejak + statistik yg dihitung kode)
                                   │
                                   ▼  usul pelajaran → gemini_lessons (status: proposed)
                          EVIDENCE-GATE (kode hitung n & expectancy per setup)
                                   │ lolos? → lesson.active = 1
                                   └────────► disuntik ke DECIDE berikutnya (loop)
```

Prinsip pembagian peran:
- **Gemini** = arah + keyakinan + alasan + usul pelajaran (kreatif/sintesis).
- **Kode (deterministik)** = sizing, SL/TP, leverage, circuit breaker, **statistik**,
  **evidence-gate**, gerbang signifikansi. AI tak pernah memegang risiko atau hakim.

---

## Data model (SQLite, extend `bot/store.py`)

**`gemini_decisions`** — tiap keputusan + hasilnya
| kolom | arti |
|---|---|
| id, ts, symbol | identitas |
| setup | tag setup (mis. "trend_pullback", "range_fade") — kunci evidence-gate |
| side, conviction | long/short/flat, 0..1 |
| rationale | alasan singkat Gemini |
| context | JSON data yang dilihat (audit/replay) |
| model, decision_ms | provenance |
| status | open / settled |
| outcome_r | R-multiple saat ditutup (diisi `settle`) |

**`gemini_lessons`** — playbook yang Gemini bangun
| kolom | arti |
|---|---|
| id, ts, scope | scope: simbol/regime/setup tertentu |
| text | isi pelajaran |
| n_support, exp_r_support | bukti dari rekam jejak (dihitung KODE) |
| confidence | low/med/high (turunan bukti) |
| active | 0/1 — hanya 1 bila lolos evidence-gate |

**`gemini_reflections`** — evaluasi diri berkala (audit pola pikir)
| kolom | arti |
|---|---|
| id, ts, period | jendela yang direfleksi |
| summary | narasi evaluasi Gemini |
| metrics | JSON statistik nyata jendela itu |

Reuse: `gemini_usage` (token), `kv` (state), `events` (jurnal trade).

---

## Komponen

| Modul | Status | Isi |
|---|---|---|
| `bot/trader_curriculum.py` | **foundation** | Kurikulum: kerangka trader pro (regime, struktur, risk-first, kapan flat, expectancy). Didikan awal + format keputusan. |
| `bot/store.py` (extend) | **foundation** | Tabel + CRUD decisions/lessons/reflections + `setup_stats()` (statistik per setup untuk evidence-gate). |
| `bot/gemini_trader.py` | **foundation** | `build_context`, `decide`, `record`, `settle`, `active_lessons` (evidence-gated), `promote_lessons`. |
| `bot/gemini_trader.py: reflect()` | **MVP** | Loop refleksi: tarik jejak → statistik → Gemini menilai & usul pelajaran → simpan. |
| `forward.py` (wiring) | **MVP** | Teknik "gemini": pakai `GeminiTrader.decide` ganti `decide_v4`; sizing skala conviction; jalan di demo. |
| Dashboard panel | **MVP** | Tampilkan keputusan, pelajaran aktif, track record + signifikansi. |
| Promosi ber-signifikansi | **product** | Track record Gemini diuji `copilot.verdict`; playbook per-regime; auto-tuning ambang. |

---

## Roadmap bertahap

### Fase 1 — FOUNDATION ✅ SELESAI
- [x] Spec ini.
- [x] Skema SQLite + CRUD + `setup_stats()` (evidence-gate primitive) — `bot/store.py`.
- [x] `trader_curriculum.py` (ajaran Claude) — **basis pengetahuan termodul**: proses
      keputusan, risiko, psikologi, struktur pasar/price-action, pola chart, pola candle,
      indikator, meta + taksonomi `SETUPS`. (`curriculum_prompt(modules=...)` selektif.)
- [x] `GeminiTrader`: build_context, decide (JSON, fail-safe FLAT), commit, settle,
      active_lessons (evidence-gated), promote_lessons — `bot/gemini_trader.py`.
- [x] Test: roundtrip skema, **evidence-gate (anti-takhayul)**, context offline,
      decide fail-safe — `tests/test_gemini_trader.py` (9 test).

### Fase 2 — MVP (demo hidup) ✅ SELESAI (tinggal jalankan demo)
- [x] `reflect()` loop — statistik dihitung KODE, Gemini usul pelajaran, evidence-gate
      mempromosikan. Aman offline. (`gemini_trader.py`, test).
- [x] Wiring ke `forward.py` — teknik **"gemini"**: `GeminiTrader.decide` ganti `decide_v4`;
      **sizing skala conviction** (lantai 20%); SL/TP/leverage/circuit-breaker tetap
      deterministik; `commit` saat open → `settle` saat close → `reflect()` tiap 20 close.
      Jalur v4 tak tersentuh (guarded `use_gemini_trader`).
- [x] **Konteks portofolio**: Gemini melihat SEMUA posisi terbuka + eksposur (bukan hanya
      simbol yang diputuskan) → keputusan entry sadar korelasi/risiko (`_portfolio_view`).
- [x] **Kelola posisi terbuka (exit-only, ~1 menit)**: loop terpisah dari entry per-bar;
      Gemini boleh `exit` / `tighten_stop` saja — **tak pernah** melonggarkan stop / menambah /
      membalik. Guardrail `valid_tighten` (pure, teruji) + live = exit-only (jaga proteksi).
- [x] Status disurface untuk dashboard (`rationale`, `setup` per simbol).
- [x] Endpoint `/api/gemini-trader` — track record + **verdict signifikansi** + per-setup +
      playbook aktif (`bot/gemini_trader.py: track_record`).
- [x] **Panel dashboard React** — `web/src/components/GeminiTraderPanel.tsx`: verdict,
      kartu (n/win%/exp_R/eff_n/p_adj), per-setup, playbook teruji, keputusan terakhir.
      (Sudah ter-build ke `web/dist`.)
- [ ] Jalankan demo berhari-hari → kumpulkan rekam jejak nyata.

### Mode LIVE (UANG NYATA) — opsional, ber-gerbang
- [x] Eksekusi live: arah Gemini → `_open_usd` jalur live (order asli + SL/TP exchange).
- [x] **Gerbang keselamatan**: `config.yaml gemini.allow_live_trader` (default **false**).
      Di live, Gemini-trader TIDAK membuka posisi kecuali ini `true`. Demo/paper tak terpengaruh.
- [x] **Belajar di live yang bersih**: `_live_reconcile` men-`settle` keputusan Gemini HANYA
      dengan PnL NYATA & tak ambigu (tepat satu posisi tutup/siklus) → tak mengajari takhayul.

> ⚠️ **LIVE = AI mengendalikan uang nyata.** Aktifkan hanya bila kamu sadar penuh. Rekam jejak
> Gemini **belum** punya bukti edge (verdict `track_record` masih INSUFFICIENT/REJECTED sampai
> terbukti). Mulai dengan modal SANGAT kecil & leverage rendah.

**Cara pakai (demo):** set teknik **"gemini"** dari UI (atau runtime), lalu
`python forwardtest.py --poll 30 --use-store`. Gemini mengambil keputusan tiap bar,
mencatat ke SQLite, dan merefleksi tiap 20 trade tertutup.

### Fase 3 — PRODUCT (bila track record lolos)
- [x] Gerbang signifikansi atas track record Gemini — `track_record()` verdict
      INSUFFICIENT/REJECTED/WEAK/PROMISING (bootstrap+Bonferroni+effective-n, `bot/stats.py`).
- [ ] Playbook per-regime + retrieval relevan.
- [ ] Promosi paper→micro-live HANYA bila track record signifikan & stabil.

---

## Guardrail (di-enforce di KODE)

1. **Risk deterministik**: arah dari Gemini, tapi ukuran/SL/TP/leverage/circuit-breaker
   aturan keras. Saat mengelola posisi, Gemini HANYA boleh mengurangi risiko (exit / geser
   stop mendekat); `valid_tighten` menolak apa pun yang melonggarkan. SL/TP keras = lantai.
2. **Evidence-gate pelajaran**: lesson aktif hanya bila `n_support ≥ MIN` dan efek nyata
   (dihitung kode dari `gemini_decisions`, bukan klaim Gemini).
3. **Statistik dihitung kode**, bukan AI — refleksi berpijak pada angka nyata.
4. **Gerbang signifikansi** atas track record sebelum live (`bot/stats.py`).
5. **Fail-safe**: Gemini gagal/timeout → keputusan default **flat** (tidak buka posisi).
6. **Demo-first**: tidak ada jalur live sampai track record lolos.

## Anti-beku evidence-gate (grounding `setup_track_record`)

Rekam jejak per-setup yang diumpankan ke `decide` kini bawa **`eff_n`** (sampel efektif,
koreksi autokorelasi via `bot/stats.py`) + flag **`evidence`**:

- `evidence="adequate"` (eff_n ≥ 30) → setup ber-exp_r negatif boleh dihindari/kurangi conviction.
- `evidence="insufficient"` (sampel kecil) → exp_r negatifnya **kemungkinan NOISE**, bukan vonis
  → perlakukan **NETRAL**, kumpulkan data dulu.

**Kenapa:** `setup_stats` menghitung exp_r kumulatif tanpa window. Dengan n kecil (mis. 11–28),
exp_r ±0.05 tak bisa dibedakan dari nol (std R ≈ 1). Dulu prompt "exp_r negatif = hindari" tanpa
gerbang sampel → saat SEMUA setup sedikit-negatif, Gemini menolak semua → tak trade → sampel tak
tumbuh → **beku permanen** (absorbing state). Gerbang `eff_n` memecah jebakan ini **tanpa**
memaksa trading: begitu sampel cukup & tetap negatif-signifikan, ia mengerem lagi dengan sah.
(`gemini_trader._track_record`, prompt `trader_curriculum.py`.)

## Efisiensi panggilan — batch decide (hemat RPD/TPM)

Free tier Gemini ketat (≈10 RPM/akun). `decide_batch(contexts)` mengirim **banyak simbol dalam
SATU panggilan**: kurikulum + grounding global (`setup_track_record`, `calibration`, `btc_lead`,
`portfolio`, …) dikirim **sekali** (`_split_batch`), per-simbol hanya market/alt/sl_feedback.
Balas JSON `{symbol: keputusan}`, tiap entry lewat `_sanitize` yang sama; simbol hilang / parse
gagal → **FLAT** (fail-safe identik `decide` tunggal).

Dampak (batch N=10): **request 10→1**, **token ~39k→~11k** (~3–4× lebih hemat TPM). Lihat juga
[RELIABILITY.md](RELIABILITY.md) — cooldown RPD kini **per-(key,model)** agar model primary yang
kuota hariannya habis tak di-retry (429) tiap keputusan.

> Status wiring: primitive `decide_batch` + test SELESAI. Integrasi ke loop `forward.py`
> (fase A kumpulkan simbol lolos pre-gate → B batch → C terapkan gating+open, budget per-request)
> = perubahan jalur uang tersendiri, di-`/verify` end-to-end sebelum aktif.

## Batasan jujur
- "Berkembang" = playbook tertulis + retrieval, bukan otak berubah.
- LLM lambat & non-deterministik → cocok untuk keputusan per-bar (15m), bukan tick.
- Refleksi bisa bias; evidence-gate + statistik-kode adalah penangkalnya, bukan jaminan.
- Tidak ada jaminan profit. Sistem ini menemukan kebenaran (termasuk "tidak ada edge").

---

## Horizontal Scaling — 26 API Keys untuk Sideways Sniper Profit Konsisten

**Tujuan**: profit mikro (0.005–0.30%) di **setiap osilasi range**, tanpa menunggu tren. Range punya peluang tiap 15m bar (3–5×/sesi), jadi 26 key harus dirotasi efisien agar Gemini dipanggil **setiap siklus** untuk simbol `scalp_range`.

### Arsitektur 26 Key (gemini_client.py)

| Fitur | Sebelum (2 key) | Sekarang (26 key) |
|-------|-----------------|-------------------|
| Circuit breaker | Global (5 fail → kill ALL) | **Per-key** (8 fail → 30s cooldown key itu saja) |
| Throttle/key | 6.5s | **1.0s** (`GEMINI_MIN_INTERVAL_S=1.0`) |
| Effective RPM | 10 | **260** (26×10 RPM/project) |
| Model health | Global | **Per (key,model)** |

**Mengapa per-key?** Dengan 26 project terpisah, satu key kena 429 tidak boleh bunuh 25 key lain. Breaker per-key memastikan throughput tetap tinggi walau beberapa key cooldown.

### Budget & Chunking (config.yaml)

```yaml
gemini:
  gemini_decide_cap: 100        # dari 24 — max calls/siklus
  batch_chunk_size: 12          # dari 4 — simbol per batch call
  sideways_sniper:
    budget_boost_pct: 300       # +300% bila >50% simbol range
    micro_tp_pct_min: 0.005     # ambil 0.005% profit
    devil_advocate_for_scalp: false  # hemat RPD, Devil di-skip
```

### Range Bonus di Ranking (_gemini_score)

```python
# forward.py _gemini_score
if adx_v <= 15:
    _range_bonus = 3.0  # 3× skor agar range tak kalah dari simbol trend
```

Tanpa ini, ATR rendah di range → skor kecil → budget habis di simbol trend → `scalp_range` tidak pernah mendapat giliran.

### Mikro-TP Dinamis Berbasis pos_in_range

Entry di support (pos_in_range < 0.5) → TP ke **swing_high** (resisten). Entry di resisten (> 0.5) → TP ke **swing_low**. Jika swing terlalu dekat → fallback micro-TP config (0.005–0.30%).

**Mengapa ini konsisten:** range sempit (ATR <0.15%) geraknya cuma ke tepi range. TP fixed ATR-based (1.2×) terlalu lambat/banyak gagal. TP ke tepi range = ambil seluruh osilasi yang tersedia.

### Devil's Advocate Selective Skip

```yaml
devil_advocate_for_scalp: false
```

Devil's Advocate memanggil LLM kedua (double RPD) untuk menitipkan). Di `scalp_range` profit mikro, overhead 2× tidak layak — kurikulum sudah menekankan SL ketat 1×ATR + exit paksa 3 bar.

### Environment Variable

```bash
GEMINI_MIN_INTERVAL_S=1.0   # jeda per-key (paid tier bisa 0.5/0.2)
```

### Hasil yang Diharapkan

- **400 calls/siklus** saat >50% simbol range (budget boost 4× cap 100)
- **Setiap `scalp_range` dipanggil tiap siklus** (throttle 60s + budget besar)
- **Profit 0.005–0.3% per entry** × 3–5 entry/sesi = konsisten tanpa tren
- **0 fallback ke rules-based** (RPD tidak habis karena rotasi 26 key seimbang)

> Catatan: `GEMINI_MIN_INTERVAL_S=1.0` memerlukan **26 API key dari project Google Cloud berbeda**. Jika key dari project yang sama, RPM terbagi → RPM efektif < 260, adjust interval ke atas.
