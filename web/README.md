# Dashboard frontend (React + Vite + TypeScript)

SPA yang mengonsumsi REST API dari `bot/dashboard.py` (FastAPI). Tema & fitur
identik dashboard lama, tapi modular & type-safe.

## Dev (hot-reload)
```bash
# terminal 1 — backend API
python dashboard.py            # http://127.0.0.1:8000

# terminal 2 — Vite dev server (proxy /api -> :8000)
cd web && npm install && npm run dev    # http://127.0.0.1:5173
```

## Produksi (di-serve FastAPI)
```bash
cd web && npm run build        # -> web/dist
python dashboard.py            # FastAPI menyajikan web/dist di "/"
```

`dashboard.py` otomatis menyajikan `web/dist` bila ada; bila belum di-build,
fallback ke halaman HTML lama (PAGE). API `/api/*` selalu diprioritaskan.

## Struktur
- `src/api.ts` — klien REST + helper format
- `src/types.ts` — tipe respons API
- `src/hooks.ts` — `usePoll` (auto-refresh 10 dtk)
- `src/components/` — panel per fitur (kontrol, akun, status, chart, riwayat)
- chart pakai `lightweight-charts` (candle/EMA/RSI + kurva equity)
