# Hapus `balance_usd` Total — Ganti ke Split Per-Wallet Lengkap

## Gambaran
Sistem sudah sepenuhnya split per-wallet (USDT/USDC) untuk sizing, drawdown, PnL. `balance_usd` hanyalah agregat computed (`usdt+usdc`) yang tidak lagi dibutuhkan. Hapus dari seluruh stack: backend property, dataclass field, status payload, frontend, dan tests.

---

## Step 1: `bot/settings_store.py` — Hapus field dari RuntimeSettings
- Hapus field `balance_usd: float = 12.0` (baris 47-50)
- Hapus `self.balance_usd = max(0.0, ...)` dari `clamp()` (baris 109)
- **JAGA** migrasi di `_from_dict` (baris 200-208) — tetap baca `balance_usd` lama dari KV lama → split ke per-wallet. Aman karena migrasi jalan SEBELUM `known` filter.

## Step 2: `bot/forward.py` — Hapus property & fix semua usage
**Hapus:**
- `balance_usd` @property getter+setter (baris 209-219)
- `_peak_balance` @property getter+setter (baris 232-243) — pola sama, ikut dihapus
- Semua `self.balance_usd = X` assignment (setter calls, ~8 tempat — semua redundant karena per-wallet sudah di-set sebelumnya)
- `_last_cfg_balance` attribute & semua read/write — ganti gate `_restore_state` baris 1037-1038 pakai per-wallet comparison (`cfg_balance_usdt`/`cfg_balance_usdc`)

**Replace reads** → `self.balance_usdt + self.balance_usdc`:
- `one_r = self.balance_usd * self.risk_frac` (baris 378, 500) — R-context untuk Gemini agent
- `"balance_usd": round(self.balance_usd, 2)` di status payload (baris 2439) → **hapus key ini**
- `"balance_usd": round(self.balance_usd, 2)` di `_portfolio_view` (baris 530) → **hapus key ini**
- `balance=self.balance_usd` di `gtrader.build_context` (baris 2290) → `balance_usdt=..., balance_usdc=...`
- `eq = round(self.balance_usd, 2)` di `stats()` (baris 789)
- `self.ex.balances(self.balance_usd)` di `_live_reconcile` (baris 1383) — fallback arg
- Journal/log/notify equity display (baris 1380, 1805, 1806, 1809)
- `_persist_state` baris 1088: hapus `"balance"` (legacy aggregate) dari KV write
- `_persist_state` baris 1093: hapus `"cfg_balance"` (legacy total) dari KV write
- **JAGA** `"balance"`, `"cfg_balance"` reads di `_restore_state` (baris 1031, 1037) untuk migrasi KV lama

## Step 3: `bot/gemini_trader.py` — Split context key
- Ubah `build_context` signature: `balance` → `balance_usdt` + `balance_usdc`
- Ubah dict key: `"balance_usd"` → `"balance_usdt"` dan `"balance_usdc"`
- Update `_SHARED_KEYS` (baris 271): hapus `"balance_usd"`, tambah `"balance_usdt"`, `"balance_usdc"`

## Step 4: `bot/dashboard.py` — Bersihkan HTML lama & endpoint
- Status payload: hapus `balance_usd` (step 2 sudah)
- `/api/validate-key` response: hapus field `balance_usdc`/`balance_usdt` → kirim per-wallet (sudah dari step sebelumnya, pastikan konsisten)
- HTML fallback JS: hapus semua `s.balance_usd` refs, `document.getElementById('balance_usd')`, `window.pendingBal=s.balance_usd`
- HTML fallback form: hapus input `balance_usd`, tambahkan input USDT/USDC terpisah
- HTML fallback status: sudah pakai per-wallet dari edit sebelumnya, tapi hapus `Total $${f(s.balance_usd,2)}`

## Step 5: Frontend — Hapus `balance_usd` dari types & components
- `web/src/types.ts`: hapus `balance_usd` dari `Status` dan `Settings`
- `web/src/components/BotStatus.tsx`: hapus tampilan Total (sudah ada per-wallet)
- `web/src/components/ControlPanel.tsx`: hapus `balance_usd` dari Form type, `toForm`, `balRef`, `pendingBal`
- `web/src/api.ts`: pastikan validateKey return type konsisten

## Step 6: Tests — Rewrite semua (16 file, ~30 perubahan)
- Hapus `test_backward_compat_balance_usd_setter_defaults_to_usdc` di test_adaptive_bet.py
- Ganti semua `ft.balance_usd = X` → `ft.balance_usdc = X; ft.balance_usdt = Y` (sesuai konteks test)
- Ganti `ft._peak_balance = X` → `ft._peak_balance_usdc = X; ft._peak_balance_usdt = 0.0`
- Ganti `ft._last_cfg_balance = X` → `ft._last_cfg_balance_usdc = X; ft._last_cfg_balance_usdt = 0.0`
- Ganti `assert ft.balance_usd == X` → `assert ft.balance_usdt + ft.balance_usdc == X`
- Update test_settings_store.py yang referensi `balance_usd` field

## Step 7: Build & Verifikasi
- `npx tsc --noEmit` (frontend type-check)
- `pytest tests/ -x -q` (semua test)
- `npm run build` (production bundle)

## File-file yang berubah:
1. `bot/settings_store.py` — 3 baris hapus
2. `bot/forward.py` — ~25 perubahan (hapus + replace)
3. `bot/gemini_trader.py` — ~5 perubahan
4. `bot/dashboard.py` — ~15 perubahan (HTML+JS+endpoint)
5. `web/src/types.ts` — 2 hapus
6. `web/src/components/BotStatus.tsx` — 1 hapus (Total)
7. `web/src/components/ControlPanel.tsx` — ~10 perubahan
8. `web/src/api.ts` — 0-1 perubahan
9. `tests/*.py` — 16 file, ~30 perubahan
10. `web/dist/` — rebuild

**TIDAK berubah:** `migrate_balance_split.py` (tetap jalan untuk migrasi KV lama), `bot/config.py`, `bot/exchange.py`, `chart_ingest.py`
