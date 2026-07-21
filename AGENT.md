# Agent ReAct — manajemen keputusan self-improving

Layer agen yang **mengelola keputusan** entry di atas mesin sinyal deterministik.
Menggantikan veto Gemini yang pasif (`gemini.allows`) dengan loop penalaran aktif.

> **Jujur (sama spt METHODOLOGY):** LLM **tidak** memprediksi sinyal — OHLCV sudah
> diarbitrase. Agen hanya **mengelola keputusan & disiplin** (gate entry, pelajaran,
> evolusi threshold tervalidasi OOS). Tiap "edge" tetap harus lolos bukti OOS.

## Sesi Grok CLI baru — baca dulu
Konteks operasional **tidak** ikut otomatis saat TUI ditutup. Sumber kebenaran sesi berikutnya:
**[memory/SESSION_HANDOFF.md](memory/SESSION_HANDOFF.md)** · **[PLAN_OPERASIONAL.md](PLAN_OPERASIONAL.md)** ·
**[CHECKLIST_HARIAN.md](CHECKLIST_HARIAN.md)** · rule `.grok/rules/operasional-aktif.md`.
Cara mengaktifkan Grok Memory opsional: lihat handoff §4–§5.

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

> **Default startup = OFF.** `forwardtest.py` dan `dashboard.py` memanggil
> `bot/settings_store.reset_all_enabled()` di awal `main()` → `rs.enabled=False`
> untuk semua mode (dry/test/live). Bot tak akan jalan trading sampai ON
> dinyalakan dari dashboard. Mencegah auto-aktif pakai state sesi sebelumnya.

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

## Otonomi (point 1–3) — dari "gerbang" ke agent investigatif

Opsi di `config.yaml` → `agent:` (semua default **off**, paper-aman):

> **Cara cepat:** `full_auto: true` = satu saklar yang menyalakan tool_loop **dan**
> autonomous sekaligus. (Flag individual tetap ada untuk kontrol granular.)

> **JALAN A — `agent_manager_mode: true`** (toggle UI): agent = **MANAJER DISIPLIN**, bukan
> peramal arah. Override: arah dari RULES (matikan teknik gemini), planner+autonomous ON,
> tool_loop OFF (frugal). Untuk majors efisien di mana edge prediktif tipis → nilai agent =
> kelola risiko & bertahan, BUKAN menaikkan exp_R. Ukur via metrik risiko A/B (di bawah).


**1. Tool-loop sejati** (`tool_loop: true`) — agen tak lagi satu-shot. Ia **memanggil tool**
iteratif (nalar → tool → observasi → nalar → aksi), maks `tool_max_iters`. Gagal → fallback
single-shot deterministik. Tools (`bot/tools.py`, read-only, fail-soft):
`get_orderbook` (L2 imbalance), `get_ticker`, `get_portfolio`, `check_correlation`,
`get_funding` (funding+basis), `get_open_interest`, `get_lessons`.

**2. Otonomi portofolio** (`autonomous: true`) — tiap `autonomous_interval_s`, agen meninjau
SEMUA posisi terbuka & boleh **REDUCE_RISK** (geser stop ke breakeven pada yang profit) atau
**FLAT** (tutup semua). HANYA mengurangi risiko. Di LIVE, FLAT butuh `allow_live_trader: true`.

**3. Sumber edge DI LUAR OHLCV** — tool `get_orderbook`/`get_funding`/`get_open_interest`
memberi agen sinyal non-OHLCV (L2 imbalance, funding/basis premium, open interest) —
karena OHLCV murni sudah diarbitrase. Ini bahan baku edge yang belum tentu habis.

**Memori lintas-tick** (`bot/memory.py`, otomatis saat tool-loop) — agen mengingat
observasi tool & keputusan terakhir per simbol (time/size-bounded) → penalaran
berkesinambungan antar-siklus (tak menyelidiki dari nol tiap tick). Disuntik ke prompt
sebagai "Recent memory"; di-snapshot ke SQLite (tahan restart). Ingatan KERJA jangka
pendek — bukan pengganti decision_log (audit permanen) / lessons (evidence-gated).

**4. Planner tipis — goal-directed** (`planner: true`, `bot/planner.py`) — tiap
`plan_horizon_h` jam agen menetapkan TUJUAN sesi: `stance` (aggressive/normal/defensive/
risk_off), `bias` (long/short/neutral), kuota trade & eksposur. Keputusan per-tick TUNDUK
pada rencana (`enforce()` deterministik di kode). **HANYA bisa mengetatkan** — di-clamp ≤
batasmu, tak pernah melonggarkan. Gagal/Gemini off → rencana netral (tak ada batasan). Audit
di `decision_log` (`symbol="*PLAN*"`) & endpoint `/api/plan`. Ikut menyala bila `full_auto`.
> **Lantai kuota** (`gemini.planner_min_trades`, default 3): planner tak boleh mencekik
> "Kuota trade" sesi di bawah angka ini (kecuali `stance=risk_off` = stop eksplisit). Naikkan
> bila planner terlalu sering memberi kuota kecil; tetap di-clamp ≤ `daily_max_trades`.

**Alarm drift kalibrasi — Phase 6** (`_check_calib_drift`, tiap 20 trade Gemini tutup, per
mode) — bandingkan Brier 50-trade terakhir vs **baseline 14-hari**. Bila memburuk melewati
`calib_drift_margin` (default 0.05) **dan** di atas koin (0.25) dengan sampel ≥
`calib_drift_min_n` (default 20) → **ALARM Telegram + saran naikkan `conf_min` (dicatat
`journal: calib_drift`)**, TAPI **threshold TAK diubah otomatis** — keputusan manusia (auto-tune
diam-diam = bot meyakinkan diri sendiri ia masih benar). Anti-spam: alarm hanya saat MASUK
kondisi drift, reset saat pulih. Kedua ambang hot-reload (RuntimeSettings, sama seperti tier
Phase 2). Instrumentasi murni — tak pernah memblokir trade.
> **Cakupan skor Brier**: jalur paper (`_close_usd`) selalu skor; jalur LIVE (`_live_reconcile`)
> skor HANYA saat TEPAT SATU posisi Gemini tutup per siklus (Δequity = PnL posisi itu, tak
> ambigu). Multi-close bersamaan dilewati (jangan skor dari PnL agregat yang kotor).

> Jujur: ini menaikkan "level agentik", **bukan** jaminan profit. Buktikan dengan A/B di bawah.

## A/B harness — apakah ReAct benar-benar menambah nilai? (`bot/ab.py`)

UKUR, jangan tebak. Set `agent.ab_shadow: true` di `config.yaml` → ReAct jalan **shadow**:
menalar & mencatat verdict (`react_action`) **tanpa memblokir** (rules tetap eksekusi
semua entry). Karena tiap trade benar-benar diambil, kita punya outcome R untuk SEMUA —
termasuk yang ReAct ingin tolak.

```
Arm A (kontrol)   = rules-saja         → exp_R semua trade
Arm B (perlakuan) = rules + ReAct      → exp_R subset yang ReAct SETUJUI
Denied            = yang ReAct TOLAK   → bila exp_R-nya lebih buruk, veto-nya berguna
```

Verdict `REACT_ADDS_VALUE` **hanya** bila `exp_R(B) > exp_R(A)` DAN kept signifikan > denied
(permutation test, `p<0.05`). Selain itu `NOT_PROVEN` — diterima jujur.

**Metrik RISIKO (Jalan A):** karena manajer disiplin dinilai dari **pengurangan risiko**, bukan
exp_R, `analyze` juga melaporkan **max drawdown, volatilitas (std), R terburuk** untuk rules-saja
vs rules+ReAct, plus flag `reduces_risk`. Di majors, verdict exp_R bisa `NOT_PROVEN` **tapi**
agent tetap bernilai bila `reduces_risk=true` (drawdown lebih kecil). Itulah tolok ukur Jalan A.

```bash
python ab_report.py          # laporan CLI
```
Atau panel `/agent` (kartu A/B) · endpoint `/api/ab`.

> Jujur: ini menjawab "apakah layer agent membantu?" dengan **data**, bukan keyakinan.
> Di OHLCV yang impas OOS, jangan kaget bila verdict-nya `NOT_PROVEN`.

## Deployment (PM2)
`ecosystem.config.cjs` menjalankan **bot + dashboard** dengan auto-restart & auto-start
boot — lihat [DEPLOY.md](DEPLOY.md) (seksi PM2).

## Pemantauan
Halaman **`/agent`** di dashboard (mis. `http://<host>:8000/agent`):
Agent Health (rasio LLM vs fallback) · Keputusan terakhir · Pelajaran aktif + akurasi ·
Riwayat evolusi. Endpoint JSON: `/api/decisions`, `/api/lessons`, `/api/agent-health`,
`/api/evolution`.

## Candidate edge — ilmu siklus pemilik (bukan PROMOTE_PAPER)

Jalur didukung: **dry menguji kelayakan**, live mikro hanya dengan **risiko disadari**.  
Spek: [memory/CANDIDATE_EDGE.md](memory/CANDIDATE_EDGE.md) · modul `bot/cycle_candidate.py`.

- Default `agent.cycle_candidate.mode: shadow` → log `CANDIDATE_EDGE_SHADOW` (size/skip *would*).
- `size` / `soft_block` di **dry**: kurangi size long (dump/markdown/unlock) atau skip long baru.
- **Live** enforce hanya jika `allow_live: true` **dan** `risk_ack: true`.
- **Dilarang:** auto-short dump/unlock, samakan dengan PROMOTE_PAPER, scale tanpa dry lolos.

## Risk filter overlay (Jalan A meta — bukan entry alpha)

`bot/risk_filter.py` — skip kandidat saat breadth rendah / corr tinggi / BTC vol tinggi
(hasil `PROMOTE_FILTER_PAPER` edge hunt 2026-07-21). **Default shadow:** log
`RISK_FILTER_SHADOW` + stamp open; **`risk_filter_block` tetap false** sampai paper
membuktikan would-deny lebih berisiko. Fail-open bila panel/snap gagal.

Config: `agent.risk_filter_shadow` · `risk_filter_block` · `risk_filter_breadth` ·
`risk_filter_corr_vol`. Gate: `ForwardTester._refresh_risk_filter` (1× per siklus).

## Berkas terkait
`bot/react_agent.py` · `bot/decision_log.py` · `bot/lessons.py` · `bot/evolve.py` ·
`bot/risk_filter.py` · gerbang di `bot/engine.py` · `bot/forward.py` · **Entry Confluence Gate: `bot/entry_confluence.py` + `bot/ec_calibrate.py` + `tests/test_entry_confluence.py` + [ENTRY_CONFLUENCE_GATE.md](ENTRY_CONFLUENCE_GATE.md)**.

## Rencana operasional aktif
Posture paper **Jalan A** (manager-mode + A/B shadow, risk dry dikunci, larangan H30/L2
profit-hunting) didokumentasikan di **[PLAN_OPERASIONAL.md](PLAN_OPERASIONAL.md)** —
selaras [TUJUAN.md](TUJUAN.md) §2.1.
