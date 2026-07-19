# Tujuan Proyek & Peran LLM

Dokumen ini merekam **apa yang ingin dicapai** dan **mengapa LLM/Gemini ditempatkan
di tempatnya** — bukan sebagai alat yang lemah, tapi sebagai kekuatan pada bidangnya.
Ditulis agar arah tidak melenceng dan agar peran LLM dinilai adil.

---

## 1. Apa proyek ini (dan apa yang BUKAN)

**Adalah:** sistem trading yang **jujur & reproducible** — bertahan hidup (tidak
blow-up), mengukur expectancy secara benar, dan mendokumentasikan apa yang berhasil
maupun gagal.

**Bukan:** mesin "win rate 80%" atau "profit konsisten tiap hari". Klaim seperti itu
di pasar hampir selalu backtest overfit, survivorship, atau RR bencana yang
disembunyikan. Nilai proyek ini = **sistem uji yang jujur**, bukan angka pamer.

---

## 2. Tujuan yang ingin dicapai (urut prioritas)

1. **Survival.** Tidak blow-up. Circuit breaker harian + kill-switch drawdown total
   (`forward.py`) adalah fondasi — sebelum bicara cuan, akun harus bertahan.
2. **Expectancy positif SETELAH fee.** Bukan win rate. Target = EV/trade > 0 setelah
   fee/slippage/funding. Diukur, bukan diasumsikan (lihat §6).
3. **Menemukan KAPAN, bukan hanya APAKAH.** Kalau ada satu **regime** yang +EV
   sementara yang lain menggerus, jawabannya adalah *gate* (hanya trade di regime itu)
   — bukan "otak lebih pintar".
4. **Skill & sistem.** Data pipeline, risk, eksekusi, kalibrasi — keahlian nyata yang
   berguna terlepas dari cuan. Ini keuntungan pasti; cuan tidak pernah pasti.
5. **Optionality.** Kalau suatu saat edge muncul (eksekusi/likuiditas/data langka),
   mesinnya sudah siap. Tapi edge tidak diasumsikan ada duluan.
6. **LLM sebagai pengarah yang disiplin.** LLM meningkatkan **kualitas keputusan &
   disiplin**, dari data kita sendiri (§4).

### 2.1 Tujuan operasional aktif (2026-07-19) — Jalan A

Setelah riset arah (22+ hipotesis) **tidak** menghasilkan edge tradeable, prioritas
operasional dikunci ulang. Detail penuh: **[PLAN_OPERASIONAL.md](PLAN_OPERASIONAL.md)**.

| Prioritas | Isi |
|---|---|
| 1 | Survival paper (`dry`) dengan rem ketat (loss harian, max trade, leverage, slot) |
| 2 | **Jalan A:** agent = manajer disiplin + A/B shadow (`reduces_risk`), bukan peramal |
| 3 | H28 paper param beku — menyusul setelah proses 7 hari stabil |
| 4 | Hipotesis baru hanya sumber struktural belum diuji; H30 maker ritel **tutup** |

KPI harian = **proses & risk**, bukan “hijau tiap hari”.

---

## 3. Ekspektasi jujur

- **Tidak ada "kepastian" di pasar.** Mengejar kepastian di scalping = over-leverage =
  blow-up (satu kalah besar menghapus banyak menang kecil). Yang realistis: expectancy
  tipis positif setelah fee, dengan variance tinggi.
- **Profil ritel:** disclosure broker (regulasi EU) rutin menunjukkan 70–85% akun rugi.
  Firma bermodal-besar mendominasi trading sistematis, dan edge pun luntur.
- **Prediksi arah:** program riset kita sendiri (22 hipotesis, `RESEARCH_LOG.md`)
  menemukan **0 edge tradeable**. Itu bukan kegagalan — itu temuan jujur yang menghemat
  waktu & uang. Edge ritel yang tersisa lebih mungkin ada di **eksekusi/likuiditas**.

---

## 4. Peran LLM/Gemini — dihargai, tidak disudutkan

**LLM adalah kekuatan pada bidangnya: menalar atas KONTEKS & BAHASA.** Kelemahan
"lemah matematika/halu" **sudah kita selesaikan** secara arsitektur — bukan dibiarkan
jadi batasan:

- **Grounded via tools.** `bot/react_agent.py` + `bot/tools.py` adalah ReAct loop
  sejati: *reason → panggil tool → observasi → reason lagi*. Aritmetika dikerjakan API/
  tool read-only (`get_orderbook`, `get_funding`, `get_open_interest`, `get_btc_context`,
  `check_correlation`, `get_lessons`). LLM **tidak menghitung sendiri** → tidak halu
  angka. Ini pola yang benar, dan **sudah jalan di repo ini.**
- **Anti-halu berlapis.** `_safe()` membungkus tiap tool; devil's-advocate pass
  menantang tiap keputusan; grounding ke `memory`/`lessons`.

**Di mana LLM unggul & memang ditempatkan (slow loop):**
- Membaca **news/konteks** dan menilai **regime** (`gemini_layer.regime_score`).
- **Belajar dari riwayat trade kita sendiri** (`lessons.py`, `memory.py`) — "setup ini
  mirip yang 3× rugi, jangan."
- **Veto meta & devil's advocate** — rem terhadap bias, bukan gas.
- **Generator hipotesis** yang lalu diuji walk-forward.

**Mengapa LLM TIDAK di hot-path scalping** — ini **keputusan penempatan rekayasa,
bukan penghinaan** terhadap kecerdasannya:
- **Latency:** loop LLM = detik; scalping butuh milidetik. Ini fisika, bukan "bodoh".
- **Reproducibility:** output stokastik → tak bisa di-backtest deterministik bertahun
  seperti rules engine (jadi edge-nya sulit dibuktikan seperti hipotesis lain).
- **Cost per keputusan:** wajar untuk keputusan lambat, boros untuk per-tick.

**Prinsip yang dijaga:** kegagalan LLM **tak pernah memblokir trading** (fallback
deterministik). LLM adalah **pengarah (direktur)**, bukan **pemicu (trigger)** —
justru peran yang paling menonjolkan kekuatannya.

---

## 5. Pembagian tugas — kekuatan yang saling melengkapi

| Pekerjaan | Alat | Di repo |
|---|---|---|
| Indikator, threshold, sizing, RR | Kode deterministik (cepat, teruji) | `indicators.py`, `signals.py`, `risk.py` |
| Sinyal → probabilitas terkalibrasi | Model statistik kecil (OOS/walk-forward) | `slcalib.py`, calibration layer |
| Nalar konteks, news, regime, jurnal | **LLM/Gemini** (grounded, async) | `react_agent.py`, `tools.py`, `gemini_layer.py`, `lessons.py`, `memory.py` |
| Eksekusi, funding, slippage, anti-blowup | Kode + guard | `execution.py`, `position.py`, circuit breaker `forward.py` |

Ini bukan "LLM inferior" — ini **tiap alat di bidang juaranya**.

---

## 6. Alat ukur — `regime_ev.py`

Mengubah pertanyaan dari *"LLM cukup pintar?"* (bukan itu masalahnya) menjadi
**"ada dimensi yang +EV setelah fee?"** (itu yang menentukan).

```bash
python regime_ev.py                 # laporan expectancy dari logs/trades.jsonl
python regime_ev.py --haircut 0.02  # stress-test fee tambahan $/trade
python regime_ev.py --selfcheck     # tes internal
```

Mengelompokkan EV/trade per **regime · reason · side · conviction · symbol**. Regime
kini distempel saat open (`ForwardTester._regime_stamp`, diuji di
`tests/test_regime_stamp.py`) dan ditulis di `forward_close` → bucket regime terisi
seiring paper berjalan.

**Temuan sampel awal (36 trade paper):** EV/trade −0.13, **72% exit kena SL** —
konfirmasi empiris "entry tak punya follow-through", bukan soal kecerdasan otak.

---

## 7. Langkah berjalan & cabang keputusan

1. ✅ Stempel regime ke log (patch + tes, tanpa nambah risiko loop live).
2. ⏳ Jalankan paper beberapa hari → `regime_ev.py` mengisi bucket regime.
3. **Cabang:**
   - Ada regime +EV setelah fee → **gate** ke situ (fokus, à la konsentrasi).
   - Tidak ada → arah memang mati → alihkan energi ke **eksekusi/exit/SL-sizing**
     (catatan sampel: `liq` & `gemini_exit` masing-masing −1.0 → itu **risk/exit**,
     bukan sinyal — perbaikan paling nyata di situ).

**Yang dihindari:** menambah gerbang entry baru (memperbanyak cara kena chop) atau
memoles komponen yang bukan sumber edge.

---

## 8. Prinsip pegangan

- Ukur, jangan asumsikan. Expectancy > win rate.
- Walk-forward/OOS default, bukan opsi.
- Money-path berubah **bareng tesnya**.
- LLM = pengarah & pembelajar, bukan peramal. Kekuatannya dihormati, penempatannya
  ditentukan oleh fisika (latency/reproducibility), bukan oleh anggapan rendah.
- Kejujuran soal "belum ada edge" **lebih berharga** daripada klaim WR 80%.
