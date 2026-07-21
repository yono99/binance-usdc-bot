# Entry Pipeline — urutan gerbang (kode = kebenaran)

> Diagram ini selaras **kode runtime** di `ForwardTester` (deploy: `forwardtest.py`).
> Bukan spek ideal — bila diagram ≠ kode, **kode menang**; perbaiki diagram.

**Sumber method (setelah pecah mixin 2026-07-21):**

| Area | Modul |
|---|---|
| Loop siklus | `bot/forward.py` → `_on_cycle_store`, `on_cycle`, `run` |
| Gerbang / context | `bot/forward_gates.py` |
| Open / sizing | `bot/forward_open.py` → `_open_usd` |
| Close / settle | `bot/forward_close.py` |
| Status / persist | `bot/forward_status.py` |

---

## 1. Pipeline satu siklus (paper dry / live)

```
                    ┌─────────────────────┐
                    │  run() poll loop    │
                    │  on_cycle()         │
                    └──────────┬──────────┘
                               ▼
                    ┌─────────────────────┐
                    │ _apply_settings()   │  UI runtime.json / pin_mode
                    │ posture agent       │  manager_mode, ab_shadow, …
                    └──────────┬──────────┘
                               ▼
              ┌────────────────────────────────┐
              │ HARD pre-entry (global)        │
              │  • rs.enabled == False → idle  │
              │  • _circuit_breaker()          │  daily loss / trade cap
              │  • news.check() veto           │
              │  • _refresh_risk_filter()      │  shadow default; block OFF
              │  • max slots / screener        │
              └────────────────┬───────────────┘
                               ▼
              ┌────────────────────────────────┐
              │ Per-simbol: signal / Gemini    │
              │  rules (v4/v8) ATAU gtrader    │
              │  cooldown / blacklist store    │
              │  corr gate (opsional)          │
              └────────────────┬───────────────┘
                               ▼
              ┌────────────────────────────────┐
              │ ReAct gate (_react_gate)       │  HANYA teknik non-gemini
              │  ab_shadow=true → catat,      │  tak blokir
              │  else action harus searah      │
              │  LLM mati → fallback ALLOW     │
              └────────────────┬───────────────┘
                               ▼
              ┌────────────────────────────────┐
              │ Entry Confluence (SHADOW)      │  ec_gate — catat, JANGAN blokir
              │  sampai N bukti enforce        │  lihat ENTRY_CONFLUENCE_GATE.md
              └────────────────┬───────────────┘
                               ▼
              ┌────────────────────────────────┐
              │ _open_usd()                    │
              │  1. cooldown/blacklist         │
              │  2. gemini live allow flag     │
              │  3. conf_min / size tier       │
              │  4. CE-STANCE (cycle_candidate)│  size-down / soft_block — BUKAN short auto
              │  5. SL floor + valid vs liq    │
              │  6. adaptive bet / margin      │
              │  7. paper open ATAU _live_open │
              └────────────────┬───────────────┘
                               ▼
                         posisi di self.open
```

---

## 2. Hierarki keputusan (HARD → soft)

Urutan **kekuatan** (yang di atas menang; yang di bawah tak boleh melonggarkan):

```
1. HARD risk          daily loss, max trades, max pos, lev, circuit breaker, news
2. PONDASI pemilik    CE-STANCE (size-down long dump/markdown/unlock); SL fixed setelah open
3. Rules / signal     OHLCV + alt; teknik gemini vs rules
4. ReAct / planner    hanya ketatkan (SKIP / reduce); fail-open bila LLM mati
5. trade_reviews      soft inject ke prompt — !conflicts_foundation
6. Edge hunt / H*     TERPISAH — research/ saja; PROMOTE_PAPER=0 → tidak wire entry
```

**Dilarang:**

- Auto-short karena dump/unlock  
- Auto-promote edge dari 1 putaran hunt  
- Inject lesson yang `conflicts_foundation`  
- `risk_filter_block=true` tanpa bukti paper A/B risk  

---

## 3. Dual-track dry ⇄ live

| | Dry (paper) | Live (uang) |
|---|---|---|
| Entry rules | **1:1 sama** | **1:1 sama** |
| CE evaluate | ya | ya + `allow_live` + `risk_ack` |
| Stop CE | — | `stop_loss_r_live` (−5R default) → latch enforce OFF |
| Risk UI | lock survival (loss 5%, trades 30, …) | **lebih ketat** di mode live |
| Eksekusi | `_open_usd` paper ledger | `_live_open` / reconcile exchange |

Hakim: `python ce_report.py` · spek: [memory/CANDIDATE_EDGE.md](memory/CANDIDATE_EDGE.md)

---

## 4. Close path (ringkas)

```
_monitor_usd / SL-TP / manual
    → _close_usd | _live_close
    → vrp/mtf shadow log
    → calibration (Brier) bila conviction ada
    → settle EC shadow outcome_r
    → gemini.settle ATAU _react_settle
    → _post_mortem_close → trade_reviews (SQLite)
    → _ce_live_track_close (live, CE-touched only)
```

---

## 5. Alignment checklist (saat mengubah gerbang)

1. Ubah method di mixin yang benar (`forward_gates` / `forward_open` / …).  
2. Update diagram di file ini **di commit yang sama**.  
3. Tes: `tests/test_entry_confluence.py`, `test_cycle_candidate.py`, `test_adaptive_bet.py`, `test_trade_review.py`.  
4. Jangan wire research arm ke `_open_usd` tanpa PROMOTE_PAPER + OOS + PR sadar.  

Lihat juga: [ARCHITECTURE.md](ARCHITECTURE.md) · [ENTRY_CONFLUENCE_GATE.md](ENTRY_CONFLUENCE_GATE.md) · [AGENT.md](AGENT.md)
