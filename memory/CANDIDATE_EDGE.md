# CANDIDATE EDGE — ilmu pemilik = pondasi · dry ⇄ live 1:1 · risiko sadar

> **Status:** jalur didukung + direvisi (2026-07-21).  
> **Pondasi:** ilmu & pengalaman pemilik (`CRYPTO_CYCLE_KNOWLEDGE.md`) — **bukan**
> hasil edge-hunt OHLCV (PROMOTE_PAPER = 0).  
> **Bukan** klaim “edge sudah lolos.”  
> **Adalah:** aturan yang sama di **dry paper** dan **live mikro**, diukur terpisah,
> digabung sebagai hakim — live untuk **realisme endpoint**, dry untuk **volume
> tanpa modal**.

Baca dulu: [CRYPTO_CYCLE_KNOWLEDGE.md](CRYPTO_CYCLE_KNOWLEDGE.md) ·  
[SESSION_HANDOFF.md](SESSION_HANDOFF.md) · [METHODOLOGY.md](../METHODOLOGY.md) ·  
[TUJUAN.md](../TUJUAN.md) §2.1.

---

## 0. Kontrak mental

| | |
|---|---|
| **Pondasi** | Ilmu siklus / beta / unlock / fase pemilik → diukur jadi CE-STANCE (stance/filter) |
| **Hist OOS** | Cukup **tolak** auto-short dump/unlock sebagai *entry alpha* — tidak membatalkan stance |
| **Dry paper** | Volume, counterfactual, proses — **murah**, gap fill vs exchange |
| **Live mikro** | Realisme fill/fee/funding/reject — **mahal**, wajib `risk_ack` |
| **1:1** | **Aturan & mode sama** di dry dan live; yang beda = modal & risk lock |
| **Klaim edge penuh** | Hanya setelah dry **dan** live mikro **searah** + (bila entry) bar PROMOTE_* |

**Kalimat risiko (wajib sebelum `allow_live` + `risk_ack`):**

> Saya mengerti: ilmu saya adalah **pondasi kandidat**, belum edge terbukti.  
> Dry dan live memakai **aturan yang sama (1:1)**; live lebih realistis ke Binance  
> tapi **bisa rugi uang nyata**. Saya tidak scale karena win streak paper atau  
> keyakinan pribadi saja. Stop rule live: −X R kumulatif → matikan `allow_live`.

---

## 0.1 Telaah jujur: “live lebih bagus deteksi edge karena gap paper”

### Klaim pemilik (diringkas)

> Test dry vs live: live lebih bagus / realistis karena gap paper → endpoint
> Binance bikin kesalahan kecil tidak akurat; jadi live juga perlu sebagai
> deteksi otomatis edge.

### Putusan telaah (bukan setuju buta, bukan tolak total)

| Bagian klaim | Verdict | Alasan di repo ini |
|---|---|---|
| Paper ≠ fill exchange | **BENAR** | Dry: fill disimulasi (fee+slippage model, `Backtester` / paper path). Live: order limit/resting, precision, reject, partial, latency, funding, balance dompet nyata (`_live_open`, pending). |
| Gap bisa bikin “salah kecil” di paper | **BENAR** | Edge **eksekusi/likuiditas** (maker, antrian, slip) **hanya** terbukti di live. Paper bisa over/under-state fill. |
| Live **lebih baik** untuk *semua* jenis edge | **SEBAGIAN** | Untuk **CE-STANCE** (size long saat dump/phase/unlock): input utama = OHLCV + label siklus — **candle sama** di dry & live. Gap fill **lebih kecil** pengaruhnya dibanding edge scalping. Live tetap penting untuk *realized R path*. |
| Live = deteksi edge **otomatis** | **SALAH bila dibaca “auto-promote”** | Live **mengukur** path real; **tidak** otomatis label PROMOTE / naik size. n kecil + variance → bisa hijau/merah palsu. Putusan fase = manusia + metrik. |
| Dry tidak berguna | **SALAH** | Dry = n besar gratis, counterfactual shadow, uji crash/log, A/B risk tanpa modal. Live tanpa dry = mahal & underpowered. |
| Dry dulu selalu, live belakangan | **TERLALU KAKU** (revisi) | Untuk **pondasi ilmu pemilik + stance**, jalur **ganda 1:1** lebih jujur: dry (volume) **paralel** live mikro (realisme) dengan aturan identik — asalkan risk live sadar & ketat. |

### Implikasi operasional (yang kita anut)

```
ILMU PEMILIK (pondasi)
        │
        ▼
  CE-STANCE rules (1 set config)
        │
   ┌────┴────┐
   ▼         ▼
 DRY        LIVE MIKRO
 (volume,   (fill nyata,
  free,      fee/funding,
  gap fill)  risk_ack)
   │         │
   └────┬────┘
        ▼
  Hakim gabungan (risk + realism)
        │
   sejalan → lanjut fase / scale hati-hati
   bertentangan → KEEP / RETIRE / perbaiki model fill
   live jelek dry bagus → curiga gap paper (bukan “ilmu salah total”)
   dry jelek live bagus → curiga n kecil / selection — jangan scale
```

**Satu kalimat telaah:**  
Live **wajib** sebagai **hakim realisme endpoint** (bukan pengganti dry, bukan auto-edge).  
Dry **wajib** sebagai **hakim volume & proses**. Keduanya 1:1 pada **aturan**; beda pada **modal**.

---

## 1. Pondasi = ilmu pemilik (bukan scoreboard hunt)

Edge-hunt OHLCV (~300 arms) → **PROMOTE_PAPER = 0**. Itu **tidak** membatalkan
pengalaman pemilik; itu membatalkan **klaim entry short** yang sudah diuji OOS.

| Ilmu pemilik (CRYPTO_CYCLE_KNOWLEDGE) | Status di mesin | Peran pondasi |
|---|---|---|
| BTC dump → alt beta > 1 | OOS: beta **CONFIRMED**; short entry **REJECT** | → **size-down long** / hindari long agresif (bukan auto-short) |
| Unlock / supply shock | OOS short **NOT_PROVEN** | → size-down long di window; calendar context |
| Fase markdown / bear | CONTEXT_ONLY + CE size | → stance defensif long |
| Dominance / alt season | context | → bias, bukan entry 15m |
| Halving calendar | context | → label, bukan trigger scalp |

```
JANGAN:  ilmu → auto-short dump/unlock → full live
YA:      ilmu → CE-STANCE (1 config) → dry + live mikro 1:1 → ukur → scale / retire
```

---

## 2. Kandidat aktif: CE-STANCE

**Klaim:** di dump / markdown / unlock, **kurangi size long** (opsional soft-skip long baru)  
→ **turunkan risk**, bukan janji naik exp_R.

| Trigger | Aksi |
|---|---|
| `dump_flag` | size long × `long_size_on_dump` (0.5) |
| phase `markdown` / cal `bear` | × `long_size_on_markdown` (0.7) |
| `unlock.in_window` | × `long_size_on_unlock` (0.5) |
| soft_block + dump | skip **new** long |

**Dilarang:** auto-short dump, `dump_short_boost`, hard FLAT dari phase label.

**Fase 2 (nanti):** CE-RELWEAK · CE-FILTER-BREADTH (sudah shadow terpisah).

---

## 3. Mode runtime — **satu config, dua arena**

| mode | Dry | Live |
|---|---|---|
| `off` | mati | mati |
| `shadow` | log only | log only (aman; **1:1 logging**) |
| `size` | enforce size-down | enforce **hanya** jika `allow_live` **dan** `risk_ack` |
| `soft_block` | skip new long | sama + ack |

```yaml
agent:
  cycle_candidate:
    mode: shadow          # off|shadow|size|soft_block — SAMA untuk dry & live
    allow_live: false     # kunci LIVE enforce
    risk_ack: false       # sadar: unproven + uang nyata
    long_size_on_dump: 0.5
    long_size_on_markdown: 0.7
    long_size_on_unlock: 0.5
    soft_block_long_on_dump: true
```

**1:1 artinya:**

| Sama (wajib) | Boleh beda |
|---|---|
| `mode`, multiplier, soft_block flag | `bet_usd` / loss% / max_pos (live **lebih ketat**) |
| Trigger (dump/phase/unlock) | Modal total |
| Schema log (`CANDIDATE_EDGE_SHADOW`, stamp) | Path log file per mode (`decision_log_dry` vs live) |
| Fail-open (error ≠ block trade) | |

Modul: `bot/cycle_candidate.py` · wire: `ForwardTester._open_usd`.

---

## 4. Jalan implementasi (dual-track)

### F0 — Kontrak + pondasi

- [x] Spek + wire + tes + dry `mode=shadow` deploy  
- [x] Telaah paper↔live (bagian 0.1)  
- [ ] Pemilik setuju kalimat risiko §0 sebelum L0  
- [ ] Tidak nyalakan dump_short_boost / manager demi “ilmu”

### F1 — Shadow 1:1 (logging, tanpa size)

1. Dry: `mode: shadow` (**sudah**).  
2. Live (opsional paralel): `mode: shadow` saja — **tidak** butuh `allow_live`  
   (shadow tidak apply size; hanya log). Berguna membandingkan *kapan* trigger  
   sama di kedua arena.  
3. Kumpulkan n trigger + (setelah close) outcome R.  
4. **Lolos F1:** log stabil, n dry ≥30 *atau* n live ≥15 shadow events.

### F2 — Size 1:1 (enforce stance)

1. Dry: `mode: size`, `allow_live: false`.  
2. Live: **hanya** jika pemilik sadar:  
   `mode: size` + `allow_live: true` + `risk_ack: true`  
   + risk live **lebih ketat** (bet mikro, loss% kecil, max pos rendah).  
3. **Aturan CE identik**; modal tidak.  
4. **Lolos F2 (kandidat layak):**  
   - risk metric (maxDD / worst R / std) di **dry** tidak memburuk material, **dan**  
   - path **live** tidak meledak / tidak kontradiksi parah (live jauh lebih jelek tanpa alasan eksekusi).  
5. **Gagal:** `mode: shadow` atau `off`; matikan `allow_live`.

### F3 — soft_block (opsional, dry dulu disarankan)

OOS hist block_long pernah REJECT — hati-hati. Live soft_block hanya setelah dry F2 OK.

### L1 — Menuju “edge policy” (bukan all-in)

Dry + live mikro **searah** (risk↓ atau netral) + n memadai → spek PROMOTE_FILTER /  
stance policy. Scale bertahap. **Bukan** PROMOTE_PAPER entry alpha kecuali konstruk baru lolos OOS.

---

## 5. Deteksi “kebenaran” — semi-otomatis, dual hakim

| Lapisan | Otomatis? | Isi |
|---|---|---|
| Trigger + log shadow | **Ya** | `CANDIDATE_EDGE_SHADOW`, `size_would`, `skip_would`, stamp open |
| Outcome R per trade | **Ya** (baris ENTER) | `record_outcome` — join manual/skrip ke stamp CE |
| Verdict `PROMOTE_*` / `RETIRE` | **Tidak** (sengaja) | Manusia + report; **tidak** auto-ubah config / scale |
| Live “deteksi edge” | **Ukur otomatis, putuskan tidak** | Realized R + fill quality; stop rule manual/config |

### Metrik (dry **dan** live, terpisah lalu banding)

```
n_long_closed
n_shadow_downsize / n_shadow_skip
mean_R / worst_R / maxDD  — bucket would-size vs full
fill notes (live only): reject, pending timeout, fee real vs model
gap note: |paper_R_proxy − live_R| bila trade sejenis
```

| Verdict | Arti |
|---|---|
| `KEEP_SHADOW` | n kurang / netral |
| `PROMOTE_DRY_SIZE` | Dry risk OK → F2 dry |
| `PROMOTE_LIVE_MICRO` | Siap L0: ack + size 1:1 mikro |
| `DUAL_OK` | Dry & live sejalan (risk) — kandidat layak scale hati-hati |
| `GAP_SUSPECT` | Dry bagus, live jelek → perbaiki model fill / ekspektasi paper, jangan buang ilmu dulu |
| `RETIRE` | Kedua arena memperburuk risk atau n cukup + nol manfaat |
| `NOT_EDGE_ENTRY` | Selalu untuk CE-STANCE sampai spek entry baru lolos OOS |

**Live tidak “otomatis bilang edge.”** Live **otomatis mengumpulkan bukti realisme**;  
**manusia** (atau skrip report non-mutating) mengeluarkan verdict.

---

## 6. Yang dilarang

| Dilarang | Alasan |
|---|---|
| Auto-short dump/unlock default | OOS entry REJECT / NOT_PROVEN |
| `dump_short_boost: true` | Hygiene H-CYC |
| Live full size “karena ilmu saya benar” | Melanggar §0 |
| Live tanpa `risk_ack` | Kode: `applied=False` di live |
| Samakan live hijau = PROMOTE_PAPER | Variance + n kecil |
| Matikan dry “karena live lebih real” | Kehilangan volume & counterfactual |
| Auto-scale dari report | Self-promotion diam-diam |

---

## 7. Mapping mimpi “ilmu jadi edge”

| Syarat | Jalur ini |
|---|---|
| Pondasi pengalaman | **Ya** — CRYPTO_CYCLE → CE-STANCE |
| Aturan terukur 1:1 dry/live | **Ya** — satu config |
| Realisme Binance | **Live mikro** + risk_ack |
| Volume / proses | **Dry** |
| Deteksi otomatis penuh | **Tidak** — ukur auto, promote manual |
| PROMOTE entry | Hanya konstruk baru + bar OOS; stance = filter/policy |

---

## 8. Satu kalimat

> Ilmu pemilik adalah **pondasi**; dry dan live memakai **aturan CE yang sama (1:1)**;  
> dry menguji volume, live menguji **realisme endpoint dengan risiko diketahui**;  
> keduanya mengukur, **tidak** auto-mempromosikan edge.

---

*Revisi: 2026-07-21 (dual-track + telaah paper/live). Wire: `bot/cycle_candidate.py`.*
