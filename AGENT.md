# Agent ReAct — manajemen keputusan self-improving

Layer agen yang **mengelola keputusan** entry di atas mesin sinyal deterministik.
Menggantikan veto Gemini yang pasif (`gemini.allows`) dengan loop penalaran aktif.

> **Jujur (sama spt METHODOLOGY):** LLM **tidak** memprediksi sinyal — OHLCV sudah
> diarbitrase. Agen hanya **mengelola keputusan & disiplin** (gate entry, pelajaran,
> evolusi threshold tervalidasi OOS). Tiap "edge" tetap harus lolos bukti OOS.

## Loop ReAct (tiap kandidat entry)

```
OBSERVE → REASON → ACT → RECORD
```

1. **OBSERVE** — rakit state: harga, ATR, funding/OI/CVD (bila ada), regime, skor
   sinyal long/short, posisi terbuka, PnL harian (R), 10 pelajaran terbaru.
2. **REASON** — kirim ke Gemini (JSON terstruktur) → `{action, reasoning, confidence,
   key_risks, lesson_triggered}`. `action ∈ {ENTER_LONG, ENTER_SHORT, SKIP, REDUCE_RISK, FLAT}`.
3. **ACT** — izinkan buka posisi **searah sinyal** hanya bila `action` cocok; selain itu skip.
4. **RECORD** — tulis satu baris ke `logs/decision_log.jsonl`.

### Fail-safe (HARD CONSTRAINT)
Kegagalan LLM **tak pernah** memblokir trading:
- LLM mati/nonaktif/timeout/parse-gagal → **fallback deterministik**: ikut sinyal rules
  (sumber `LLM_UNAVAILABLE` / `LLM_DISABLED`).
- `SKIP` keyakinan-rendah (<0.3) → diserahkan ke **veto lama** (`VETO_FALLBACK`).

## Dua jalur eksekusi

ReAct aktif di **kedua loop** (jalankan **salah satu** — keduanya menulis `decision_log.jsonl` yang sama):

| Loop | Entry | Catatan |
|---|---|---|
| `run.py` → `Engine` | gerbang ganti `gemini.allows` | alt-data belum di-wire (None) |
| `forwardtest.py` → `ForwardTester` (di-deploy) | gerbang utk teknik **non-gemini** | alt-data **nyata** (funding/OI/CVD); teknik `gemini` pakai jalur gemini-trader sendiri |

Gerbang ReAct dipanggil **hanya saat semua cek deterministik lolos** (news/circuit-breaker/
slot/korelasi) → hemat panggilan LLM.

## Artefak (runtime, di-`.gitignore`)

| Berkas | Isi |
|---|---|
| `logs/decision_log.jsonl` | tiap keputusan: alasan, confidence, risiko, skor sinyal, market_state, lalu **outcome R** diisi saat posisi tutup |
| `lessons.json` | pelajaran `IF…THEN…BECAUSE` + akurasi (`times_correct/triggered`) |
| `logs/evolution_log.jsonl` | riwayat evolusi threshold (before/after, p-value, applied) |

### Lifecycle outcome
Saat posisi tutup → baris ENTER terakhir simbol itu diperbarui `outcome`/`outcome_r`/
`filled_at_close`. Paper: R dari jarak SL. Live: R dari Δequity/bet, **hanya bila tepat
satu posisi tutup** siklus itu (PnL tak ambigu).

## Lessons engine (`bot/lessons.py`)
- Tiap trade tertutup → Gemini turunkan satu pelajaran (fallback deterministik bila LLM mati).
- 10 pelajaran teraktif disuntik ke prompt agen.
- Tiap pelajaran yang **dipicu** → lacak benar/salah; **dipensiunkan** bila akurasi <0.4 setelah ≥10 pemicu.

## Evolusi threshold — validasi OOS (`bot/evolve.py`)
Prinsip walk-forward diterapkan ke performa **live**, bukan backtest:
1. ≥20 trade tertutup → split kronologis train 70% / test 30% (test ≥10).
2. Cari pengetatan `entry_confidence` yang memaksimalkan exp_R di **train**.
3. Validasi di **test (OOS)** via **permutation test**.
4. Terapkan **hanya** bila perbaikan signifikan (`p<0.05`). Tiap event dicatat.

> Auto-apply berlaku di jalur **Engine** (`cfg["signals"]`). Forward memakai parameter
> store, jadi evolusi di forward bersifat analitik/log, bukan auto-apply.

## Deployment (PM2)
`ecosystem.config.cjs` menjalankan **bot + dashboard** dengan auto-restart & auto-start
boot — lihat [DEPLOY.md](DEPLOY.md) (seksi PM2).

## Pemantauan
Halaman **`/agent`** di dashboard (mis. `http://<host>:8000/agent`):
Agent Health (rasio LLM vs fallback) · Keputusan terakhir · Pelajaran aktif + akurasi ·
Riwayat evolusi. Endpoint JSON: `/api/decisions`, `/api/lessons`, `/api/agent-health`,
`/api/evolution`.

## Berkas terkait
`bot/react_agent.py` · `bot/decision_log.py` · `bot/lessons.py` · `bot/evolve.py` ·
gerbang di `bot/engine.py` & `bot/forward.py`.
