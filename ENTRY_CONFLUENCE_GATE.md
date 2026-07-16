# Entry Confluence Gate — 3-Factor Shadow Alignment

> **Philosophy:** Lahir sebagai **SHADOW** — catat hasil gate, **JANGAN** memblokir entry aktual — sampai sampel cukup (N≥30) membuktikan gate menaikkan `exp_R`. Naik kelas ke `enforce` = commit terpisah dengan bukti data.

---

## Masalah yang Dipecahkan

Backtest v7 (semua setup) menunjukkan **systemic entry/SL problem**, bukan setup-specific:
- `trend_continuation`: −0.025R (terbaik)
- `scalp_range`: −0.590R (terburuk)
- **Semua setup negatif** → akar: entry di level palsu + SL mepet + BTC lawan arah

SL tightening bugs sudah diperbaiki (commit `290d316`):
1. Regime-adaptive 1.0×ATR removal
2. `_sl_floor` selalu diterapkan
3. `min(1.5×ATR, 0.5×range)` clamping

**Entry Confluence Gate** = lapisan filter **sebelum** SL/sizing — memastikan entry hanya di confluence valid.

---

## 3 Faktor (Simetris LONG/SHORT)

| Faktor | Fungsi | Tier | Input | Output |
|---|---|---|---|---|
| **1. BTC Macro Alignment** | `btc_macro_tier()` | `full` / `reduced` / `blocked` | `btc_lead_score` (%), `side`, `dump_pct` | Tier + size multiplier |
| **2. Pair Structure Confluence** | `pair_structure_confluence_ok()` | `pass` / `fail` | `trend_score`, `momentum_score`, `side`, `trend_floor`, `momentum_floor` | Boolean floor per-component |
| **3. Nearest Level Quality** | `nearest_level_quality()` | `strong` / `secondary` / `null` | `symbol`, `price`, `side`, `proximity_atr_mult`, `touch_count_min`, `touch_count_strong` | Quality tier + level object |

---

### Faktor 1: BTC Macro Alignment (`bot/altdata.py`)

```python
def btc_macro_tier(btc_lead_score: float | None, side: str, dump_pct: float) -> str:
    """
    Return: "full" | "reduced" | "blocked"
    
    LONG:
      - btc_lead_score >= dump_pct      → "full"    (BTC pump, searah)
      - btc_lead_score <= -dump_pct     → "blocked" (BTC dump, lawan)
      - else                            → "reduced"
    
    SHORT (simetris):
      - btc_lead_score <= -dump_pct     → "full"    (BTC dump, searah)
      - btc_lead_score >= dump_pct      → "blocked" (BTC pump, lawan)
      - else                            → "reduced"
    
    None → "reduced" (fail-open)
    """
```

| `btc_tier` | Size Multiplier (`btc_reduced_mult`, default 0.7) | Arti |
|---|---|---|
| `full` | 1.0 | Entry normal, BTC searah |
| `reduced` | 0.7 | Entry size 70%, BTC netral/lawan lemah |
| `blocked` | 0.0 | **Skip** entry, BTC lawan arah kuat |

**Default `dump_pct` = 0.5%** (dari `config.yaml: btc.dump_pct`)

---

### Faktor 2: Pair Structure Confluence (`bot/signals.py`)

```python
def pair_structure_confluence_ok(
    trend_score: float,
    momentum_score: float,
    side: str,
    trend_floor: float,
    momentum_floor: float
) -> bool:
    """
    Floor per-komponen (simetris):
    
    LONG:  trend_score >= trend_floor   AND   momentum_score >= momentum_floor
    SHORT: trend_score <= -trend_floor  AND   momentum_score <= -momentum_floor
    
    Tidak pakai skor gabungan — **floor per-komponen** (harus lolos KEDUANYA).
    Default floors: 0.1 (butuh kalibrasi via ec_calibrate.py)
    """
```

**Contoh:**
- LONG: trend=0.15, momentum=0.08, floor=0.1/0.1 → **FAIL** (momentum < floor)
- SHORT: trend=−0.2, momentum=−0.15, floor=0.1/0.1 → **PASS** (keduanya bearish cukup)

---

### Faktor 3: Nearest Level Quality (`bot/levels.py`)

```python
def nearest_level_quality(
    symbol: str,
    price: float,
    side: str,                    # "long" → cari support, "short" → cari resistance
    proximity_atr_mult: float = 0.5,
    touch_count_min: int = 12,
    touch_count_strong: int = 25,
    timeframe: str = "1h"
) -> tuple[str | None, Level | None]:
    """
    Return: (quality, level)
    
    quality ∈ {"strong", "secondary", None}
    
    Level = dataclass(price, level_type, strength, raw_touches,
                      high_touches, low_touches, bin_low, bin_high,
                      last_touch_idx, dist_atr)
    
    Logika:
    1. Filter level tipe support (LONG) / resistance (SHORT)
    2. Ambil nearest (jarak minimum ke price)
    3. Jika dist_atr > proximity_atr_mult → None
    4. Jika raw_touches >= touch_count_strong → "strong"
    5. Jika raw_touches >= touch_count_min → "secondary"
    6. Else → None
    """
```

---

## BNB Case Study (Acceptance Test)

Dari `TODO.md` — validasi nyata untuk Faktor 3:

| Price | Setup | Level | Touches | Quality | Expected |
|---|---|---|---|---|---|
| **577** | LONG (range_fade) | Support 577 (6-bar) | 6 | **None** (weak) | `decision="skip"` |
| **572–574** | LONG (range_fade) | Support 572-574 (40-bar) | ~40 | **Strong** | `decision="enter"` |
| **579.7** | SHORT (range_fade) | Resistance 579.7 (28-bar) | 28 | **Strong** | `decision="enter"` |

> **Key insight:** Entry 577 = level palsu (6 touch dalam 6 bar), entry 572-574 = true structural demand (40 touch dalam 41+ bar).

---

## Gate Utama: `entry_confluence_gate()` (`bot/entry_confluence.py`)

```python
def entry_confluence_gate(
    symbol: str,
    side: str,
    setup: str,
    price: float,
    atr: float,
    trend_score: float,
    momentum_score: float,
    btc_lead_score: float | None,
    levels_module,          # injected bot.levels
    signals_module,         # injected bot.signals
    altdata_module,         # injected bot.altdata
    full_config: dict
) -> dict:
    """
    Returns:
    {
        "decision": "enter" | "skip",
        "reason": str,
        "btc_tier": "full" | "reduced" | "blocked",
        "structure_pass": bool,
        "location_quality": "strong" | "secondary" | None,
        "size_mult": float,
        "nearest_level": Level | None
    }
    """
```

### Alur Keputusan

```
                    ┌─────────────────────┐
                    │  entry_confluence_gate  │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       ┌─────────────┐  ┌────────────────┐  ┌──────────────┐
       │ Faktor 1:   │  │ Faktor 2:      │  │ Faktor 3:    │
       │ BTC Macro   │  │ Pair Structure │  │ Level Quality│
       └──────┬──────┘  └───────┬────────┘  └──────┬───────┘
              │                 │                  │
       ┌──────┴──────┐  ┌──────┴──────┐    ┌──────┴──────┐
       │ blocked?    │  │ both floors │    │ setup in    │
       └──────┬──────┘  │ passed?     │    │ fade list?  │
              │         └──────┬──────┘    └──────┬──────┘
       ┌──────┴──────┐         │                  │
       ▼             ▼         ▼                  ▼
    "skip"        "skip"     "skip"            (if null)
       │             │         │                  │
       └─────────────┴─────────┴──────────────────┘
                              │
                              ▼
                    ┌───────────────────┐
                    │ CALCULATE size_mult│
                    │ btc_reduced_mult   │
                    │ location_sec_mult  │
                    └────────┬──────────┘
                             │
                             ▼
                    ┌───────────────────┐
                    │ "enter" + reason  │
                    └───────────────────┘
```

### Setup yang Memerlukan Faktor 3 (Level Check)

`range_fade`, `scalp_range`, `trend_pullback`, `range_fade_v2`, `scalp_range_v2`

**Setup yang BYPASS Faktor 3:** `trend_continuation`, `breakout_continuation` (breakout pakai momentum, bukan level)

---

## Size Multiplier Calculation

```python
size_mult = 1.0
if btc_tier == "reduced":
    size_mult *= btc_reduced_mult        # default 0.7
if location_quality == "secondary":
    size_mult *= location_secondary_mult # default 0.8
```

| Scenario | btc_tier | location | size_mult |
|---|---|---|---|
| Optimal | full | strong | 1.0 |
| BTC netral | reduced | strong | 0.7 |
| Level secondary | full | secondary | 0.8 |
| BTC netral + level secondary | reduced | secondary | 0.56 |

---

## Shadow Logging (`entry_confluence_shadow` table)

**SQLite schema (auto-created via `init_db()`):**

```sql
CREATE TABLE IF NOT EXISTS entry_confluence_shadow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    setup TEXT NOT NULL,
    btc_tier TEXT NOT NULL,
    structure_pass INTEGER NOT NULL,
    location_quality TEXT,
    would_enter INTEGER NOT NULL,      -- 1/0
    actually_entered INTEGER DEFAULT 0, -- 1/0 (diisi saat posisi terbuka)
    conviction REAL DEFAULT 0.0,
    price REAL DEFAULT 0.0,
    reason TEXT,
    outcome_r REAL                       -- diisi saat trade settle
);
CREATE INDEX IF NOT EXISTS idx_ec_shadow_ts ON entry_confluence_shadow(ts);
```

### Fungsi Store (`bot/store.py`)

| Fungsi | Deskripsi |
|---|---|
| `log_entry_confluence_shadow(result: GateResult)` | Insert record |
| `entry_confluence_shadow_stats(limit=100)` | Query ringkasan (paginated) |
| `entry_confluence_agg()` | Aggregate: total, enter_rate, by_setup, by_btc_tier, avg_outcome_r |
| `settle_entry_confluence_outcome(id, outcome_r=None, actually_entered=None)` | Update outcome / actually_entered |

### GateResult Dataclass

```python
@dataclass
class GateResult:
    ts: str
    symbol: str
    side: str
    setup: str
    btc_tier: str
    structure_pass: bool
    location_quality: str | None
    would_enter: bool
    actually_entered: bool = False
    conviction: float = 0.0
    price: float = 0.0
    reason: str = ""
    outcome_r: float | None = None
```

---

## Wiring ke Forward Loop (`bot/forward.py`)

```python
# Di _process_symbol(), setelah Gemini decision == ENTER_*
ec_result = entry_confluence_gate(
    symbol, side, setup,
    price, atr,
    trend_score, momentum_score,
    btc_lead_score,
    levels_module=levels,      # bot.levels
    signals_module=signals,    # bot.signals
    altdata_module=altdata,    # bot.altdata
    full_config=config
)

# Log ke shadow table (TIDAK memblokir entry!)
gate_result = GateResult(
    ts=now_iso(),
    symbol=symbol, side=side, setup=setup,
    btc_tier=ec_result["btc_tier"],
    structure_pass=ec_result["structure_pass"],
    location_quality=ec_result["location_quality"],
    would_enter=(ec_result["decision"] == "enter"),
    actually_entered=False,       # di-update nanti saat posisi terbuka
    conviction=..., price=..., reason=ec_result["reason"]
)
log_shadow(gate_result)

# NOTE: ec_result["decision"] == "skip" HANYA dicatat, entry tetap jalan!
# Shadow = observasi, bukan enforce.
```

**Settle outcome (saat posisi tutup):**
```python
settle_entry_confluence_outcome(gate_result_id, outcome_r=actual_r)
# dan di entry: settle_entry_confluence_outcome(id, actually_entered=True)
```

---

## Calibration Script (`bot/ec_calibrate.py`)

```bash
python bot/ec_calibrate.py
```

**Output:**
```json
{
  "sample_n": 47,
  "best_config": {
    "touch_count_min": 12,
    "touch_count_strong": 25,
    "proximity_atr_mult": 0.5,
    "trend_floor": 0.1,
    "momentum_floor": 0.1
  },
  "by_setup": {...},
  "by_btc_tier": {...},
  "by_location": {...}
}
```

Analisis settled trades untuk temukan threshold optimal:
- `touch_count_min` / `touch_count_strong` — minimal touch untuk valid level
- `proximity_atr_mult` — jarak maksimal ke level (ATR multiples)
- `trend_floor` / `momentum_floor` — floor per-component pair structure

**Butuh minimal 30 settled trades** untuk hasil bermakna.

---

## Dashboard Endpoint & Panel

### REST API
```
GET /api/entry-confluence-shadow?limit=100
```

**Response:**
```json
{
  "summary": {
    "total_logged": 156,
    "would_enter": 89,
    "enter_rate_pct": 57.1,
    "avg_outcome_r_all": 0.12,
    "avg_outcome_r_entered": 0.18,
    "avg_outcome_r_skipped": -0.05
  },
  "by_setup": {
    "range_fade": {"total": 45, "enter_rate_pct": 62.2, "avg_outcome_r": 0.21},
    "trend_continuation": {"total": 38, "enter_rate_pct": 50.0, "avg_outcome_r": -0.02}
  },
  "by_btc_tier": {
    "full": {"total": 98, "enter_rate_pct": 72.4, "avg_outcome_r": 0.24},
    "reduced": {"total": 42, "enter_rate_pct": 35.7, "avg_outcome_r": -0.08},
    "blocked": {"total": 16, "enter_rate_pct": 0.0, "avg_outcome_r": null}
  },
  "by_location_quality": {
    "strong": {"total": 67, "enter_rate_pct": 82.1, "avg_outcome_r": 0.31},
    "secondary": {"total": 22, "enter_rate_pct": 40.9, "avg_outcome_r": 0.02}
  },
  "records": [...]
}
```

### React Component: `EntryConfluenceShadow.tsx`

Dua tab:
1. **Ringkasan** — kartu summary + breakdown by setup / BTC tier / location
2. **Records** — tabel paginated dengan filter, sort, export

**Registered di:** `web/src/App.tsx` → route `/entry-confluence`

---

## Config (`config.yaml`)

```yaml
entry_confluence:
  mode: "shadow"              # off | shadow (default) | enforce (future)
  trend_floor: 0.1            # minimal trend_score (LONG) / -trend_floor (SHORT)
  momentum_floor: 0.1         # minimal momentum_score
  proximity_atr_mult: 0.5     # jarak maks ke level (×ATR)
  touch_count_min: 12         # minimal touches untuk level valid
  touch_count_strong: 25      # touches untuk level "strong"
  btc_reduced_mult: 0.7       # size multiplier saat btc_tier=reduced
  location_secondary_mult: 0.8 # size multiplier saat location=secondary

btc:
  dump_pct: 0.5               # ambang BTC alignment (%)
```

---

## Unit Tests (`tests/test_entry_confluence.py`)

38 test cases covering:

| Class | Tests | Coverage |
|---|---|---|
| `TestBtcMacroTier` | 10 | All 3 tiers + None + boundary + symmetry |
| `TestPairStructureConfluence` | 8 | All floor permutations + zero floor + neutral |
| `TestNearestLevelQuality` | 6 | BNB fixture (577/572/579.7) + thresholds + proximity |
| `TestEntryConfluenceGate` | 10 | Full integration + BTC block + structure fail + setup bypass + multipliers + symmetry |
| `TestGateResult` | 2 | Defaults + full construction |
| `TestShadowDb` | 3 | Log/query + settle outcome + actually_entered update |

**Run:**
```bash
python -m pytest tests/test_entry_confluence.py -v
# 38 passed
```

---

## Migration: Shadow → Enforce (Future)

**Kriteria promosi (sudah tertulis di TODO.md Phase 5.5):**
1. Minimal **30 settled trades** di shadow table
2. `exp_R(entered) > exp_R(skipped)` **statistically significant** (permutation test, p<0.05)
3. BTC `full` tier significantly outperforms `reduced`/`blocked`
4. `strong` location significantly outperforms `secondary`/`null`
5. Breakdown per-setup: `range_fade`/`scalp_range`/`trend_pullback` masing-masing lolos

**Jika lolos →** ganti `mode: "enforce"` di config + return `"skip"` di gate = **hard block**.

---

## Files Created/Modified

| File | Role |
|---|---|
| `bot/altdata.py` | `btc_macro_tier()` |
| `bot/signals.py` | `pair_structure_confluence_ok()` |
| `bot/levels.py` | `nearest_level_quality()`, `Level` dataclass |
| `bot/entry_confluence.py` | `entry_confluence_gate()`, `GateResult`, `log_shadow()` |
| `bot/ec_calibrate.py` | Calibration script |
| `bot/store.py` | Shadow table schema + 4 store functions |
| `bot/forward.py` | Shadow wiring in entry flow |
| `bot/dashboard.py` | `/api/entry-confluence-shadow` endpoint |
| `web/src/components/EntryConfluenceShadow.tsx` | Dashboard panel |
| `web/src/App.tsx` | Route registration |
| `tests/test_entry_confluence.py` | 38 unit tests |

---

## Quick Start (Paper Test)

```bash
# 1. Pastikan config.yaml entry_confluence.mode = "shadow"
# 2. Jalankan forward test + dashboard
python forwardtest.py --poll 30 --use-store
python dashboard.py

# 3. Buka http://<host>:8000 → tab "Entry Confluence"
# 4. Biarkan jalan beberapa hari → kumpulkan ≥30 settled trades
# 5. Kalibrasi:
python bot/ec_calibrate.py

# 6. Evaluasi: cek apakah gate benar-benar memisahkan exp_R positif vs negatif
```

---

## Referensi Terkait

- `TODO.md` — Phase 1.1-1.6 gate framework, Phase 2 calibration
- `METHODOLOGY.md` — Walk-forward OOS philosophy
- `AGENT.md` — ReAct agent flow (gate dipanggil sebelum agent)
- `ARCHITECTURE_DOCUMENTATION.md` — High-level architecture diagram
- `DASHBOARD.md` — Dashboard panel & API reference