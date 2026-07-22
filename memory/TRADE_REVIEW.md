# Trade Review — post-mortem SQLite (belajar di bawah pondasi)

> **Status:** aktif (2026-07-21).  
> **Bukan** mesin temukan edge. **Adalah** jejak proses + hipotesis disiplin.  
> Hierarki: HARD → **PONDASI (ilmu pemilik / CE)** → review → inject soft → edge terpisah.

Baca: [CANDIDATE_EDGE.md](CANDIDATE_EDGE.md) · [CRYPTO_CYCLE_KNOWLEDGE.md](CRYPTO_CYCLE_KNOWLEDGE.md) ·  
[SESSION_HANDOFF.md](SESSION_HANDOFF.md).

---

## 1. Apa yang disimpan

Tabel SQLite `trade_reviews` di `logs/bot.db` (via `bot/store.py`).

Tiap close (paper `_close_usd` / live single-close track) → 1 baris:

| Field | Arti |
|---|---|
| outcome_r, exit_reason, side, symbol, mode | Fakta trade |
| dump_flag, phase, unlock, conviction, setup, CE reasons | Konteks |
| error_class | Kelas **proses** (bukan “edge found”) |
| lesson_text | `IF…THEN…BECAUSE` deterministik (higienis) |
| conflicts_foundation | 1 bila bertentangan CE / H-CYC / risk |
| status | `hypothesis` \| `injectable` \| `retired` |

**Tidak ada** status `promoted_edge`. Edge tetap jalur [CANDIDATE_EDGE.md](CANDIDATE_EDGE.md).

---

## 2. Hierarki (anti-tabrakan dengan ilmu pemilik)

```
1 HARD     risk lock, circuit, fail-open
2 PONDASI  CE-STANCE, no auto-short dump/unlock, dump_short_boost OFF
3 REVIEW   tulis trade_reviews (selalu, termasuk conflict)
4 INJECT   HANYA status=injectable AND conflicts_foundation=0 → prompt ReAct
5 EDGE     CE dual-track / OOS — review tidak auto-ubah config CE
```

Contoh **conflict** (disimpan audit, **tidak** di-inject):

- Lesson “THEN short after dump”
- “full size on markdown”
- “longgarkan daily loss”
- Klaim “edge ditemukan” dari 1 SL

Contoh **injectable** (selaras pondasi):

- Long di dump/markdown → reduce size / skip new long  
- Low conviction → raise confluence bar / abstain  
- Liq → tighten risk  
- **SL hit** → lesson proses per **setup** (MFE/MAE, confluence) — **bukan** ban pair  

### Kebijakan pemilik (2026-07): belajar ≠ menghukum pair

- Rugi / loss streak = **risiko** yang dicatat & dipelajari.  
- **Jangan** blacklist / cooldown pair karena SL (`rotate.blacklist_after_sl: 0`,
  `cooldown_minutes: 0`).  
- Lesson teks **tidak** boleh menyuruh ban simbol; fokus setup / konfluensi / SL placement.  
- Hard risk (daily loss, max pos, circuit) tetap — itu survival, bukan hukuman pair.

---

## 3. Kode

| Berkas | Peran |
|---|---|
| `bot/trade_review.py` | classify, foundation filter, build/record, merge prompt |
| `bot/store.py` | `insert_trade_review`, `recent_trade_reviews`, `trade_review_stats` |
| `bot/forward.py` | panggil `record_close_review` saat close; merge lessons di ReAct gate |
| `tests/test_trade_review.py` | unit |

---

## 4. Query cepat

```bash
# stats
python -c "from bot.store import trade_review_stats; print(trade_review_stats('dry'))"

# recent injectable
python -c "from bot.trade_review import injectable_lessons; print(injectable_lessons('dry'))"
```

---

## 5. Yang tidak dilakukan

- Auto-ubah entry rules / CE multipliers dari 1 review  
- Auto-short dump karena loss long  
- Samakan post-mortem dengan PROMOTE_PAPER  
- Biarkan lesson conflict masuk prompt  

---

## 6. Satu kalimat

> Loss ditulis ke SQLite sebagai **post-mortem proses** di bawah ilmu pondasi;  
> hanya pelajaran yang **tidak menabrak** CE/H-CYC yang boleh lunak mempengaruhi entry berikutnya — **bukan** auto-edge.

---

*Dibuat 2026-07-21 — otoritas full pemilik untuk layer review.*
