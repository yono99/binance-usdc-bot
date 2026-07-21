# LIVE MICRO — checklist dual-track CE-STANCE

> **Kapan pakai:** sebelum / saat mode `live` dengan kandidat edge aktif.  
> **Kontrak:** ilmu pemilik = pondasi; dry ⇄ live **1:1 aturan**; kerugian **sudah dipertimbangkan**.  
> Spek: [CANDIDATE_EDGE.md](CANDIDATE_EDGE.md).

---

## 0. Kalimat risiko (Anda sudah setuju 2026-07-21)

> Ilmu saya adalah pondasi **kandidat**, belum edge terbukti.  
> Dry & live memakai aturan CE yang sama. Live lebih realistis ke Binance  
> dan **bisa rugi uang nyata**. Saya tidak scale karena win streak.  
> Stop: cum R CE-touched ≤ `stop_loss_r_live` (−5 default) → enforce OFF.

---

## 1. Config CE (1:1 — sama dry & live)

Sudah di `config.yaml` (setelah paket dual-track):

| Field | Nilai | Arti |
|---|---|---|
| `mode` | `size` | Size-down long di dump/markdown/unlock |
| `allow_live` | `true` | Live **boleh** enforce |
| `risk_ack` | `true` | Pemilik sadar risiko unproven |
| `stop_loss_r_live` | `-5.0` | Latch otomatis setelah −5R kumulatif CE-touched |
| multipliers | 0.5 / 0.7 / 0.5 | dump / markdown / unlock |

**Matikan live enforce darurat:**
```yaml
allow_live: false
# atau risk_ack: false
```

**Reset stop latch** (setelah review sadar):
```bash
python -c "from bot.cycle_candidate import reset_live_stop; print(reset_live_stop())"
```

---

## 2. Risk lock LIVE (lebih ketat dari dry) — di UI / settings_store mode=live

Dry paper acuan: loss 5% / trades 30 / pos 5 / lev 5 / bet ~4.

| Field live (saran mikro) | Nilai | Catatan |
|---|---:|---|
| `enabled` | ON hanya saat Anda siap | Default startup OFF |
| `bet_usd` | **2–3** (atau lebih kecil) | Mikro |
| `leverage` | **3–5** | Jangan 10+ untuk tes CE |
| `daily_max_loss_pct` | **2–3** | Ketat |
| `daily_max_trades` | **10–15** | Batas frekuensi |
| `max_open_positions` | **2–3** | Slot kecil |
| `max_drawdown_pct` | **10–15** | Kill-switch kumulatif |
| `mode` | `live` | Credentials LIVE di `.env` |

**Jangan** samakan bet live dengan dry “supaya apple-to-apple $” — apple-to-apple = **aturan CE**, bukan notional.

---

## 3. Dry (tetap jalan)

| | |
|---|---|
| Server | `192.168.1.107` PM2 `bot`+`dashboard` |
| Mode | `dry` · risk 5/30/5/5 · ab_shadow ON · manager OFF |
| CE | `mode=size` (enforce size-down di paper) |
| Deploy | `git pull && ./restart.sh` |

---

## 4. Live (mesin / proses terpisah)

1. `.env`: `BINANCE_LIVE_KEY` / `SECRET` valid.  
2. Settings **mode=live** dengan tabel §2.  
3. Pastikan **tepat 1** proses live (jangan double dengan dry di host yang sama tanpa niat).  
4. `enabled=true` hanya setelah checklist ini dicentang.  
5. Pantau: dashboard + `python ce_report.py --mode live` + `logs/ce_live_state.json`.

---

## 5. Hakim (mingguan / tiap n≥20 close)

```bash
python ce_report.py              # dry + live
python ce_report.py --mode dry
python ce_report.py --mode live
```

| Verdict | Tindakan |
|---|---|
| `KEEP_SHADOW` / data kurang | Lanjut; jangan scale |
| `DUAL_OK` | Scale **hati-hati** (naik bet pelan) |
| `GAP_SUSPECT` | Dry bagus live jelek → jangan scale; review fill |
| `RETIRE` | `mode: off` atau `allow_live: false` |
| Stop latched | Review; reset hanya sadar |

---

## 6. Yang tidak dilakukan

- Full size live “karena ilmu benar”  
- Matikan dry  
- Auto-short dump/unlock  
- Klaim PROMOTE_PAPER dari CE-STANCE  
- Reset stop berulang tanpa review (mengalahkan stop rule)

---

*Paket dual-track 2026-07-21 — terima kasih atas data & arah pemilik.*
