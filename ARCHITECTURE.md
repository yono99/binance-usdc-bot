# Architecture — kanonik (binance-usdc-bot)

> **Satu peta** untuk manusia & agen. Detail historis/patch log panjang:
> [ARCHITECTURE_DOCUMENTATION.md](ARCHITECTURE_DOCUMENTATION.md) (arsip).
> Operasional harian: [PLAN_OPERASIONAL.md](PLAN_OPERASIONAL.md) ·
> [memory/SESSION_HANDOFF.md](memory/SESSION_HANDOFF.md).

**Terakhir diselaraskan:** 2026-07-21 — CE dual-track, trade_reviews, forward mixin split, `research/`.

---

## 1. Apa repo ini (jujur)

| Adalah | Bukan |
|---|---|
| Lab ops paper + live-mikro dengan disiplin OOS | Produk “profit engine” siap scale |
| Mesin sinyal **deterministik** + gerbang agent | LLM yang “meramal” harga |
| Edge hunt terpisah di `research/` | Auto-wire setiap arm positif in-sample |
| Scoreboard: **PROMOTE_PAPER = 0** (entry) | Klaim edge dari CE atau trade review |

Prinsip: [METHODOLOGY.md](METHODOLOGY.md) · [TUJUAN.md](TUJUAN.md) §2.1 (survival + Jalan A).

---

## 2. High-level

```
┌──────────────┐   ┌─────────────┐   ┌──────────────┐   ┌─────────────┐
│ Market data  │──▶│ Screener    │──▶│ Signals v4/8 │──▶│ Risk HARD   │
│ OHLCV+alt    │   │ rotate      │   │ / Gemini     │   │ size SL TP  │
└──────────────┘   └─────────────┘   └──────┬───────┘   └──────┬──────┘
                                            │                   │
                     ┌──────────────────────┴───────────────────┘
                     ▼
┌────────────────────────────────────────────────────────────────────┐
│ ForwardTester (paper dry / live) — forwardtest.py + PM2            │
│  gates → open → monitor → close → review / CE track / decision_log │
└────────────────────────────┬───────────────────────────────────────┘
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│ Dashboard (FastAPI + web/)  ·  SQLite store  ·  Telegram / SSE      │
└────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│ research/  — edge_hunt*, cyc*, H28/H30, alpha scripts               │
│  TIDAK di path entry runtime kecuali PROMOTE + wire sadar          │
└────────────────────────────────────────────────────────────────────┘
```

---

## 3. Runtime bot — modul kunci

| Path | Peran |
|---|---|
| `forwardtest.py` | Entry PM2: `ForwardTester` + `reset_all_enabled` (default OFF) |
| `bot/forward.py` | Class shell: init, seed, cycle loop, signal buffer |
| `bot/forward_gates.py` | Circuit breaker, ReAct gate, risk_filter, cycle context, corr |
| `bot/forward_open.py` | `_open_usd`, sizing, CE evaluate, live open |
| `bot/forward_close.py` | Monitor, close, post-mortem, CE live stop, calib drift |
| `bot/forward_status.py` | Settings apply, persist, mode switch, `_write_status` |
| `bot/react_agent.py` | OBSERVE→REASON→ACT (fail-open) |
| `bot/entry_confluence.py` | 3-factor gate (**shadow** default) |
| `bot/cycle_candidate.py` | **CE-STANCE** — pondasi ilmu pemilik |
| `bot/trade_review.py` | Post-mortem SQLite di bawah pondasi |
| `bot/risk_filter.py` | Meta overlay breadth/corr/vol (**shadow**) |
| `bot/engine.py` | Jalur alternatif `run.py` (bukan PM2 dry utama) |

**Entry pipeline detail:** [ENTRY_PIPELINE.md](ENTRY_PIPELINE.md)

---

## 4. Hierarki belajar & edge

```
HARD risk
  → PONDASI (ilmu pemilik / CE-STANCE)
    → trade_reviews (soft, injectable, !conflicts_foundation)
      → prompt ReAct / lessons
        → research edges (terpisah; PROMOTE_PAPER gate)
```

| Lapisan | Wire? | Boleh memblokir entry? |
|---|---|---|
| HARD | ya | ya |
| CE-STANCE `mode:size` | ya | size-down / soft_block; **bukan** auto-short |
| EC gate | shadow | **tidak** (sampai enforce + bukti) |
| risk_filter | shadow | **tidak** (`block` default false) |
| trade_reviews | inject prompt | **tidak** langsung |
| edge_hunt arms | research only | **tidak** di runtime |

---

## 5. Dual-track CE (dry ⇄ live)

- **Pondasi** = ilmu siklus pemilik (dump / markdown / unlock → hati-hati long).  
- **Bukan** PROMOTE_PAPER; **bukan** auto-short.  
- Aturan entry **1:1** dry dan live; live butuh `allow_live` + `risk_ack`.  
- Stop live: akumulasi R CE-touched ≤ `stop_loss_r_live` → enforce OFF.  
- Spek: [memory/CANDIDATE_EDGE.md](memory/CANDIDATE_EDGE.md) · checklist live: [memory/LIVE_MICRO_CHECKLIST.md](memory/LIVE_MICRO_CHECKLIST.md)  
- Hakim: `python ce_report.py`

---

## 6. Trade review (post-mortem)

- Tabel `trade_reviews` di `bot.db` (runtime, gitignored).  
- Tiap close → `error_class`, lesson IF/THEN, `conflicts_foundation`.  
- Merge ke prompt **hanya** injectable & !conflict.  
- Spek: [memory/TRADE_REVIEW.md](memory/TRADE_REVIEW.md) · `bot/trade_review.py`

---

## 7. Agent stack (default frugal di dry)

| Flag | Default ops (Jalan A) | Arti |
|---|---|---|
| `agent_manager_mode` | ON | Disiplin, bukan peramal; rules arah |
| `agent_ab_shadow` | ON | ReAct catat tanpa blokir |
| `full_auto` / tool_loop | OFF | Hemat; bukan hunting profit LLM |
| planner + autonomous | via manager posture | Ketatkan kuota/risiko saja |

Lihat [AGENT.md](AGENT.md) · A/B: `python ab_report.py`

---

## 8. Research vs production

| | Production (`bot/`, `forwardtest.py`) | Research (`research/`) |
|---|---|---|
| Tujuan | Paper dry + live mikro aman | Cari / falsifikasi edge |
| Deploy PM2 | ya | tidak |
| Path data | `data/`, `logs/` dari repo root | sama (jalankan dari **repo root**) |
| Contoh | — | `python research/edge_hunt.py` |

README riset: [research/README.md](research/README.md) · log: [RESEARCH_LOG.md](RESEARCH_LOG.md) · scoreboard: [memory/EDGE_HUNT.md](memory/EDGE_HUNT.md)

---

## 9. Deploy ringkas

| Item | Nilai |
|---|---|
| Host dry | `192.168.1.107` |
| Repo | `/root/binance-usdc-bot` |
| PM2 | tepat **1** `bot` (forwardtest) + `dashboard` |
| Restart | `./restart.sh` setelah `git pull` |
| Doc | [DEPLOY.md](DEPLOY.md) |

---

## 10. Skor kematangan (honest)

| Dimensi | Status |
|---|---|
| Risk HARD + paper ops | matang |
| Observability (status, decision_log, dashboard) | matang |
| Agent disiplin (Jalan A) | matang sebagai **manajer**, bukan alpha |
| Entry edge OOS | **0 PROMOTE** — jujur |
| Live scale | **belum** — mikro + checklist saja |
| Code structure | forward dipecah mixin; research terisolasi |

Bukan Freqtrade/Hummingbot “product”; lebih dekat **ops lab + methodology** dengan bukti OOS ketat.
