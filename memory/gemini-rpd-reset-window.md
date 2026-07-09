---
name: gemini-rpd-reset-window
description: Gemini API RPD (kuota harian) reset ~08:00 UTC = 15:00 WIB; bot fallback ke rules-based bila semua key habis
metadata:
  type: project
---

Bot punya sedikit Gemini API key (2 saat insiden 2026-07-08) yang mudah habis RPD harian, terutama model preview (RPD ~20). Mekanisme reset: `bot/gemini_client.py:168` `_secs_to_rpd_reset()` → reset ~08:00 UTC (tengah malam Pacific) = 15:00 WIB. Saat reset, `_load_persisted()` otomatis buang cooldown stale (`gemini_client.py:109`) jadi key pulih tanpa restart manual.

`forward.py:1405` `all_keys_dead()` → bila SEMUA key habis RPD untuk SEMUA model, bot fallback ke rules-based trading (bot tetap jalan, hanya tanpa LLM). Log khas: "Semua N key Gemini habis RPD harian untuk SEMUA model — fallback ke rules-based trading siklus ini."

**Why:** 2026-07-08 21:49 WIB user kira Gemini "stuck" tidak bergerak; sebenarnya cooldown wajar ~17 jam menuju reset 15:00 WIB esok, bukan bug.

**How to apply:** Untuk diagnose "gemini stuck", cek apakah sekarang < 15:00 WIB dan apakah log sudah bilang RPD habis. Tambah lebih banyak key / prioritaskan model RPD longgar (2.5-flash-lite RPD 1000) bila sering habis.
