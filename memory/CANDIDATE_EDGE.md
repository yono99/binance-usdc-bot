# CANDIDATE EDGE — ilmu pemilik → dry kelayakan → live mikro (risiko sadar)

> **Status:** jalur didukung (2026-07-21).  
> **Bukan** PROMOTE_PAPER. **Bukan** klaim “edge sudah lolos.”  
> **Adalah** cara jujur memakai ilmu/pengalaman pemilik sebagai **kandidat**,  
> diuji di **dry**, dan (opsional) di **live mikro** hanya dengan **risiko disadari**.

Baca dulu: [CRYPTO_CYCLE_KNOWLEDGE.md](CRYPTO_CYCLE_KNOWLEDGE.md) ·  
[SESSION_HANDOFF.md](SESSION_HANDOFF.md) scoreboard · [METHODOLOGY.md](../METHODOLOGY.md).

---

## 0. Kontrak mental (wajib dibaca sebelum live)

| | |
|---|---|
| Hist OOS | Cukup untuk **tolak** auto-short dump/unlock sebagai entry alpha |
| Dry | **Penguji kelayakan** kandidat (forward, mesin nyata) |
| Live | **Bukan** hadiah karena “ilmu benar” — hanya **eksposur mikro** jika pemilik **mengerti** kandidat bisa rugi |
| Klaim edge penuh | Hanya setelah bar **PROMOTE_PAPER** (atau PROMOTE_FILTER untuk meta risk) + dry konsisten |

**Kalimat risiko (copy untuk diri sendiri sebelum `allow_live`):**

> Saya mengerti: ilmu siklus saya adalah **kandidat**, belum edge terbukti.  
> Live mikro bisa rugi; dry adalah hakim kelayakan; saya tidak scale karena  
> beberapa TP beruntun atau keyakinan pribadi saja.

---

## 1. Mengapa jalur ini (dukungan metodologi)

Ilmu pemilik **bernilai** sebagai regime/filter/stance, tapi arm **entry short**
(H-CYC-01 trade, H-CYC-02 unlock short) **gagal** bar OOS. Jadi jalur yang didukung:

```
JANGAN:  ilmu → auto-short dump/unlock → live
YA:      ilmu → kandidat STANCE/FILTER → shadow dry → size dry → (opsional) live mikro
         → kumpulkan n → baru ajukan PROMOTE_* atau matikan
```

Ini memenuhi syarat “jadi edge” **secara bertahap**, tanpa membohongi scoreboard
(PROMOTE_PAPER tetap 0 sampai bukti ada).

---

## 2. Tiga kandidat (prioritas)

Hanya **satu jalur aktif** dulu (default: **CE-STANCE**). Jangan campur entry short.

### CE-STANCE (default — paling jujur vs data)

**Klaim:** di regime dump / markdown / unlock window, **kurangi size long**  
(dan opsional soft-skip long baru) → **turunkan risk**, bukan naikkan exp_R.

| Trigger (terukur) | Aksi kandidat |
|---|---|
| `dump_flag` (BTC ~≤−2% 3-bar) | size long × `long_size_on_dump` (default 0.5) |
| phase `markdown` / `bear` calendar | size long × `long_size_on_markdown` (default 0.7) |
| `unlock.in_window` untuk symbol | size long × `long_size_on_unlock` (default 0.5) |
| (opsional soft_block) dump **dan** long baru | **skip** open long (bukan flat posisi lama) |

**Hakim kelayakan (dry):** maxDD / std R / worst R / day_pnl path vs baseline  
(rules/gemini tanpa CE) — mirip Jalan A risk, **bukan** winrate harian.

**Dilarang di CE-STANCE:** auto-short dump, boost short, hard FLAT dari phase.

### CE-RELWEAK (fase 2 — setelah shadow n cukup)

**Klaim:** long alt diizinkan penuh hanya jika **tidak** relative-weak vs BTC  
(`ret_alt − ret_btc` di atas thr kausal). Weak → size down atau skip long.

Butuh: hitung per-symbol di buffer (bukan `dominance_dir` kasar).  
OOS hist terpisah sebelum `mode=size`.

### CE-FILTER-BREADTH (sudah ada sebagian)

Risk-filter `breadth_lo` / corr-vol = **PROMOTE_FILTER_PAPER** shadow.  
Tetap **block OFF** sampai paper risk A/B. Boleh **digabung** CE-STANCE shadow  
(log terpisah), jangan double-count sebagai “2 edge entry”.

---

## 3. Mode runtime (`agent.cycle_candidate`)

| mode | Dry | Live |
|---|---|---|
| `off` | mati | mati |
| `shadow` | log only (`CANDIDATE_EDGE_SHADOW`) | log only (aman) |
| `size` | **size_mult** dikali faktor | hanya jika `allow_live` **dan** `risk_ack` |
| `soft_block` | skip **new long** saat trigger ketat | hanya jika `allow_live` **dan** `risk_ack` |

Config (`config.yaml`):

```yaml
agent:
  cycle_candidate:
    mode: shadow          # off|shadow|size|soft_block
    allow_live: false     # LIVE enforce (size/soft_block) — default OFF
    risk_ack: false       # harus true sadar: kandidat unproven
    long_size_on_dump: 0.5
    long_size_on_markdown: 0.7
    long_size_on_unlock: 0.5
    # soft_block: skip long baru jika dump_flag (default false di size-only path)
    soft_block_long_on_dump: true
```

Modul: `bot/cycle_candidate.py` · wire: `ForwardTester._open_usd` (size) + pre-open long gate.

---

## 4. Jalan implementasi (checklist)

### Fase D0 — Kontrak (1×)

- [x] Dokumen ini  
- [x] Pemilik setuju jalur: dry = hakim kelayakan; live mikro hanya + risiko sadar; PROMOTE_PAPER masih 0  
- [x] Tidak nyalakan manager-mode / dump_short_boost / risk_filter_block demi “ilmu”  
- [x] Wire kode + config default `mode: shadow` + tes unit

### Fase D1 — Dry shadow (minimal 7 hari proses / n≥30 keputusan long)

1. [x] `mode: shadow` di dry (default setelah wire).  
2. Bot jalan normal (gemini/rules + risk lock 5/30/5/5).  
3. Tiap kandidat long: tulis log  
   `action=CANDIDATE_EDGE_SHADOW`, factors, `size_would`, `skip_would`, cycle tags.  
4. Report mingguan:  
   - long yang **would-downsize** vs outcome R  
   - long yang **would-skip** vs outcome R (counterfactual kasar: apakah TP tetap? / SL?)  
5. **Lolos D1** jika: log stabil, tidak crash, n cukup, arah risk tidak kacau  
   (bukan “harus profit”).

### Fase D2 — Dry size (kelayakan stance)

1. Hanya setelah D1 OK.  
2. `mode: size` **hanya dry** (`allow_live: false`).  
3. Bandingkan 7–14 hari:  
   - maxDD / worst R / sum R long-only vs window shadow sebelumnya  
4. **Lolos D2 (kandidat layak lanjut)** jika risk membaik **atau** net R tidak memburuk material  
   dengan n≥20 long closes.  
5. **Gagal D2** → kembali shadow atau `off`; jangan live.

### Fase D3 — Dry soft_block (opsional, lebih agresif)

1. Hanya jika D2 size netral/positif risk.  
2. `mode: soft_block` di dry: skip **new long** saat dump (bukan tutup open).  
3. Catat opportunity cost (missed TP) vs avoided SL.  
4. OOS hist block_long hold1 **pernah REJECT** — soft_block bisa gagal; hormati data.

### Fase L0 — Live mikro (hanya risiko sadar)

**Syarat ALL true:**

| # | Syarat |
|---|---|
| 1 | D2 (atau D3) dry **lolos** checklist di atas |
| 2 | `risk_ack: true` di config (eksplisit) |
| 3 | `allow_live: true` |
| 4 | Risk live **lebih ketat** dari dry: loss% kecil, bet mikro, max pos rendah |
| 5 | `mode` hanya `size` dulu (bukan soft_block) di live awal |
| 6 | Pemilik tidak scale karena 1 minggu hijau |
| 7 | Stop rule tertulis: mis. −X R kumulatif live → `allow_live: false` otomatis/manual |

**Bukan syarat L0:** PROMOTE_PAPER (itu L1).  
**L0 = eksperimen berbayar sadar**, bukan “edge certified.”

### Fase L1 — Baru boleh bilang “menuju edge”

- Dry + live mikro **searah** dengan hist (untuk CE-STANCE: risk↓)  
- n live memadai  
- Baru ajukan spek **PROMOTE_FILTER** / stance policy — atau entry terpisah jika ada konstruk baru  
- Scale bertahap; bukan all-in

---

## 5. Yang tidak akan diimplementasi di jalur ini

| Dilarang | Alasan |
|---|---|
| Auto-short dump / unlock sebagai default | OOS entry **REJECT / NOT_PROVEN** |
| `dump_short_boost: true` | Hygiene H-CYC-01 |
| `risk_filter_block: true` tanpa paper A/B | Shadow dulu |
| Manager-mode ON “supaya disiplin ilmu” | agent_flat −EV |
| Klaim PROMOTE_PAPER dari CE-STANCE | Jenisnya risk/stance, bukan entry alpha |
| Live full size karena “ilmu saya benar” | Melanggar kontrak §0 |

---

## 6. Metrik laporan (dry)

Tiap minggu (atau `python` report kecil nanti):

```
n_long_closed
n_shadow_downsize / n_shadow_skip
mean_R kept vs would-skip bucket (jika counterfactual ada)
maxDD period
worst R
notes: regime phase, dump days
```

Verdict kandidat:

| Verdict | Arti |
|---|---|
| `KEEP_SHADOW` | Data kurang / netral |
| `PROMOTE_DRY_SIZE` | Lanjut D2 |
| `PROMOTE_LIVE_MICRO` | Lolos D2 + risk_ack — L0 only |
| `RETIRE` | Memperburuk risk atau n cukup + tidak ada manfaat |
| `NOT_EDGE_ENTRY` | Selalu benar untuk CE-STANCE sampai spek entry baru lolos OOS |

---

## 7. Mapping ke mimpi “ilmu saya jadi edge sepenuhnya”

| Syarat edge penuh | CE-STANCE path |
|---|---|
| Aturan terukur | dump / phase / unlock → size/skip |
| OOS hist | Entry short **sudah gagal**; stance **belum** bar penuh → candidacy jujur |
| Dry forward | **D1–D3** = inti kelayakan |
| Live | **L0** mikro + ack risiko |
| PROMOTE | Hanya setelah bukti — tidak di-skip |

---

## 8. Satu kalimat

> Ilmu pemilik dijalankan sebagai **kandidat stance/filter di dry** untuk menguji  
> kelayakan risk; **live mikro hanya dengan `risk_ack`**, tanpa menyamakan itu  
> dengan edge entry yang sudah lolos PROMOTE_PAPER.

---

*Dibuat: 2026-07-21. Wire kode: `bot/cycle_candidate.py` + config `agent.cycle_candidate`.*
