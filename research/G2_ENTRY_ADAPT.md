# G2 disesuaikan → sinyal entry (bukan book LS murni)

> Tujuan pemilik: **edge sinyal entry**.  
> G2 research = long-short quality momentum.  
> Adaptasi runtime = **overlay** di atas sinyal rules yang sudah ada.

---

## 1. Mapping

| Research LS | Entry bot (disesuaikan) |
|---|---|
| Long top 30% quality | Izinkan **LONG** hanya bila simbol di **top** (saat `block`) |
| Short bottom 30% | Izinkan **SHORT** hanya bila simbol di **bottom** |
| Mid band | Netral → **tidak** ditolak |
| Di luar pure majors | Fail-open (bukan deny) |

Alur:

```
Rules signal LONG/SHORT
        ↓
   G2 rank quality (harian)
        ↓
  shadow: log G2_ENTRY_SHADOW bila misaligned (trade tetap jalan)
  block:  skip entry bila misaligned (default OFF)
```

---

## 2. Config (`config.yaml`)

```yaml
agent:
  g2_entry:
    shadow: true   # ON = ukur di paper
    block: false   # OFF sampai bukti would-deny lebih jelek dari kept
    top_q: 0.3
    lookback: 20
    snap_dir: data/snap
```

---

## 3. Kode

| File | Peran |
|---|---|
| `bot/g2_entry.py` | rank + evaluate + stamp |
| `bot/forward.py` | gerbang sebelum `_open_usd` |
| `bot/forward_gates.py` | `_refresh_g2_entry` tiap siklus |
| `research/g2_quality_mom_shadow.py` | counterfactual book (riset) |

---

## 4. Cara ukur “apakah ini edge entry?”

1. Paper dry: `g2_entry.shadow: true`, `block: false`  
2. Kumpulkan `G2_ENTRY_SHADOW` di decision_log + outcome R trade yang tetap diambil  
3. Bandingkan:  
   - mean/worst R **aligned** vs **misaligned (would-deny)**  
4. Hanya bila misaligned **lebih jelek** secara material → pertimbangkan `block: true`  
5. **Jangan** block dulu hanya karena formal PROMOTE di backtest LS  

---

## 5. Jujur

- Ini **bukan** mengganti mesin sinyal penuh dengan G2.  
- Ini **filter arah-quality** di atas entry rules.  
- Formal G2 LS book ≠ otomatis edge entry single-name — **paper A/B** yang jadi hakim.
