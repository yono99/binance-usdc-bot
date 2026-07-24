# Edge Riset — status final kampanye (riset_edge.txt + all-time)

> **Bukan** klaim edge. **Adalah** jejak loop sampai **ketemu** atau **menyerah jujur**.  
> Spek prompt: [riset_edge.txt](../riset_edge.txt) · loop: [memory/EDGE_HUNT_LOOP.md](../memory/EDGE_HUNT_LOOP.md)  
> **Terakhir:** 2026-07-24 · **PROMOTE_PAPER entry = 0** · **SURRENDER OHLCV entry (fase ini)**

---

## 0. Putusan (satu layar)

| Pertanyaan | Jawaban jujur |
|---|---|
| Ada edge **entry** lolos bar proyek? | **TIDAK** (PROMOTE_PAPER = 0) |
| Memory-loop `riset_edge.txt` (revisi) | **REJECTED** OOS mean_R **−0.30** n=103 — [EDGE_RISET_MEMORY_LOOP.md](EDGE_RISET_MEMORY_LOOP.md) |
| Data all-time lolos screen diunduh? | **YA** (server; volume screen 158 pair qv≥5e6; 513 fresh end 2026-07-24) |
| Loop `riset_edge` categories diuji? | **YA** (trend / mean-rev / mom / vol / session / volshock + hist funding/carry di RESEARCH_LOG) |
| Menyerah? | **YA — entry alpha OHLCV publik + cost 0.18%** pada spek ini. Bukan menyerah survival/filter. |
| Masih hidup | PROMOTE_FILTER×2 (shadow), CE-STANCE, paper dry disiplin |

**Kalimat penyerahan (fase entry hunt OHLCV):**  
Setelah unduh all-time + discovery A–F, R11, R12 (+strict), R14 1h, strict LINK/RF, dan arsip riset funding/carry/settlement di `RESEARCH_LOG.md` semuanya **gagal bar entry**, kami **berhenti mengklaim / mencari entry edge baru dari OHLCV publik yang sama** sampai ada **novelty data** (eksekusi L2 hist, funding multi-tahun universe besar dengan pipeline baru, atau sumber non-publik).  
**"Tidak ketemu" = hasil valid** (METHODOLOGY + riset_edge.txt multiple-testing).

---

## 1. Data (screening awal)

| Item | Nilai |
|---|---|
| Sumber | Binance USDM public klines (ccxt) — **bukan** TradingView bulk |
| Host | `192.168.1.107` `data/snap` |
| Screen | COIN perp · `min_quote_volume_24h` **5_000_000** |
| Download 2026-07-24 | ok=513 (sebelumnya) · re-screen loop: **skip-fresh 149 / fail 9** (universe qv≥5e6 ≈158) |
| files_1d | ≈760 · end_top **2026-07-24** (513) + stale 2026-07-01 (247) |
| 1h | 24 files (majors-ish; end ~2026-07-01) |
| Manifest | `data/snap/_manifest_1d_USDT.json` |
| Skrip | `research/download_snap_alltime.py --screen-volume` · `edge_hunt_riset_loop.py` |

---

## 2. Scoreboard putaran kampanye

| Putaran | Panel | Arms | CANDIDATE | PROMOTE | Catatan |
|---|---|---:|---:|---:|---|
| Hist R1–R10 + H* + cyc | — | ~300+ | 0 | 0 entry / 2 filter | Lihat EDGE_HUNT.md |
| A–F all-time | 1122×65 | 42 | 0 | 0 | calendar/session mati cost |
| R11 listing-age | 1415×34 | 50 | 0 | 0 | post-list n tipis |
| Strict LINK pairs | 1601×9 | 8 | 0 | 0 | WATCHLIST only |
| Strict risk_filter | — | 4 | — | **FILTER×2** | shadow only |
| **R12** vol/regime + riset cats | 1415×34 | 35 | **0** | 0 | 12 train+OOS+ lean; p_adj gagal |
| **R12 strict** 50/30/20+c2 | — | 10 | **0** | **NONE** | break/range/volshock gagal |
| **R14** 1h liquid | 15000×11 | 11 | **0** | 0 | **11/11 REJECTED** net cost |
| Funding/carry (arsip) | — | — | — | REJECTED | v7, H24, H25 RESEARCH_LOG |

---

## 3. Mapping `riset_edge.txt` → hasil

| Kategori prompt | Diuji di | Hasil entry |
|---|---|---|
| Trend / breakout | R12 break_up/dn 20/50 | lean OOS+ train campur → strict **NO** |
| Mean-reversion | R12 residz + range fade; R2/R4 hist | NOT_PROVEN / REJECT |
| Momentum XS | R12 mom/rev20; R4 mom | NOT_PROVEN / REJECT |
| Vol breakout / shock | R12 volshock + range_expand | discovery lean; strict **NO** |
| Seasonality | A–F calendar/session; R14 hour | REJECT net cost |
| Funding / carry | v7, H24, H25, carry.py arsip | **REJECTED** |
| Pairs relative | R10 LINK | WATCHLIST p_adj gagal |
| Multiple-testing | Bonferroni p_adj di semua round | yang “kelihatan bagus” gugur |

---

## 4. Lean terbaik R12 (bukan edge)

Discovery (70/30, n_trials=35 → p_adj ketat):

| id | OOS | n | train | raw p_pos OOS |
|---|---:|---:|---:|---:|
| break_up_50_h5 | +2.43% | 102 | +0.66% | 0.15 |
| range_expand_fade_h3 | +0.60% | 254 | +0.62% | 0.15 |
| volshock_hi_fade_h3 | +0.44% | 311 | +0.07% | 0.17 |

Strict validate: **PROMOTED: NONE**  
(contoh: `break_up_50_h5` train flip − di 50% cut; several OOS flip −).

R14 1h: semua session/mom/hour **mean OOS < 0** setelah cost 0.18%/rebalance — selaras “majors subday diarbitrase”.

---

## 5. Apa yang **tidak** diserahkan

| Tetap | Alasan |
|---|---|
| Paper dry + risk lock | Survival (TUJUAN §2.1) |
| risk_filter shadow | PROMOTE_FILTER bukti DD↓ |
| CE-STANCE | pondasi ilmu, bukan entry alpha |
| Trade review / A/B | proses belajar di bawah pondasi |
| Novelty **non-OHLCV** nanti | L2 hist, funding pipeline baru, eksekusi — spek terpisah |

---

## 6. Syarat buka lagi entry hunt

Minimal **satu** novelty:

1. Dataset **bukan** OHLCV daily/1h publik yang sama, **atau**  
2. Konstruk **pra-registrasi** beda kelas (bukan breakout/RSI/vol z retread), **atau**  
3. Edge **eksekusi** (maker queue) dengan data L2 — H30 sudah ditolak ritel; butuh spek baru  

Tanpa itu: **jangan** round R15+ OHLCV “sedikit diubah”.

---

## 7. Artefak loop ini

| File | Isi |
|---|---|
| `research/edge_hunt_riset_loop.py` | download screen + R12 orchestrator |
| `research/edge_hunt_round12.py` | discovery riset_edge cats |
| `research/edge_hunt_validate_r12.py` | strict 50/30/20 |
| `research/edge_hunt_round14_1h.py` | 1h liquid |
| `logs/edge_hunt_round12.json` | discovery |
| `logs/edge_hunt_validate_r12.json` | strict **NONE** |
| `logs/edge_hunt_round14.json` | 1h **0** |
| `memory/EDGE_HUNT_STATE.json` | state mesin |

---

## 8. Satu kalimat

> Data screening diunduh; kategori `riset_edge.txt` diuji jujur di all-time;  
> **tidak ada PROMOTE_PAPER**; loop entry OHLCV **diserahkan** sampai novelty data;  
> nilai proyek = sistem uji + survival + filter risiko, bukan sinyal cuan palsu.

---

*Agent: update STATE + CONTINUE + EDGE_HUNT.md seiring file ini. Jangan wire runtime.*
