# Dashboard frontend (React + Vite + TypeScript)

SPA ops console untuk `bot/dashboard.py`. Navigasi **route-first** (bukan scroll monolit).

## Routes

| Path | Isi |
|---|---|
| `/` | Overview — saldo, posisi, KPI, status pair |
| `/trade` | Chart, setup, confluence shadow, trade log |
| `/agent` | Health, A/B, flags, decisions, lessons, evolusi |
| `/history` | Trade history, news/screen log, Gemini usage |
| `/settings` | Kontrol bot + agent flags + Gemini trader |

## Dev
```bash
python dashboard.py                 # :8000
cd web && npm install && npm run dev  # :5173, proxy /api
```

## Produksi
```bash
cd web && npm run build             # → web/dist
python dashboard.py                 # serve SPA + deep-link fallback
```

Tanpa `web/dist`, backend jatuh ke HTML monolit lama.

## Struktur
```
src/
  App.tsx                 # BrowserRouter
  layout/AppShell.tsx     # nav + top bar
  layout/DashboardContext.tsx  # SSE + shared polls
  pages/                  # satu file per route
  components/             # panel reusable
  api.ts · hooks.ts · types.ts · styles.css
```
