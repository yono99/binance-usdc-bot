#!/usr/bin/env python3
"""Tahap 0 (plan-sess) — migrasi saldo single 'balance_usd' ke split per-wallet.

Jalan sekali aman (idempoten). Cek tiap runtime:<mode> di kv SQLite:
  - bila punya 'balance_usd' & 'dry_quote_split_usdc' → pakai split tsb
  - bila hanya punya 'balance_usd' → pecah 50/50 ke balance_usdt/balance_usdc
  - bila sudah punya balance_usdt & balance_usdc → lewati

Output: print ringkasan migrasi & tulis ulang kv 'runtime:<mode>' agar field
baru tersimpan. Data asli TIDAK dihapus (balance_usd tetap di-writa sebagai
back-compat baca — di-deprecate, code-path baru mengabaikannya).

Cara pakai:
    python migrate_balance_split.py            # migrasi semua mode (dry/test/live)
    python migrate_balance_split.py --dry      # hanya dry (untuk uji sebagian)
    python migrate_balance_split.py --dry --test --live
"""
from __future__ import annotations

import argparse
import sys

from bot import store
from bot.settings_store import load_settings, save_settings


def migrate(mode: str) -> str:
    """Migrasi satu mode. Kembalikan 'migrated' | 'no_op' | 'missing'."""
    raw = store.get_kv(f"runtime:{mode}")
    if raw is None:
        return "missing"
    has_split = "balance_usdt" in raw and "balance_usdc" in raw
    if has_split:
        return "no_op"
    if "balance_usd" not in raw:
        return "no_op"
    legacy_balance = float(raw.get("balance_usd") or 0.0)
    split = raw.get("dry_quote_split_usdc", -1.0)
    try:
        split = float(split)
    except (TypeError, ValueError):
        split = -1.0
    if 0.0 <= split <= 1.0 and legacy_balance > 0:
        usdc = round(legacy_balance * split, 6)
        usdt = round(legacy_balance - usdc, 6)
    elif legacy_balance > 0:
        half = round(legacy_balance / 2.0, 6)
        usdt, usdc = half, round(legacy_balance - half, 6)
    else:
        usdt, usdc = 12.0, 6.0
    raw["balance_usdt"] = usdt
    raw["balance_usdc"] = usdc
    # Simpan via save_settings agar clamp() & validasi diterapkan (idempotent).
    rs = load_settings(mode)
    rs.balance_usdt = usdt
    rs.balance_usdc = usdc
    save_settings(rs, set_active=False)
    return f"migrated (usdt={usdt}, usdc={usdc})"


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="migrasi semua mode (default)")
    g.add_argument("--dry", action="store_true")
    g.add_argument("--test", action="store_true")
    g.add_argument("--live", action="store_true")
    args = p.parse_args()
    targets = ["dry", "test", "live"] if args.all or not any(
        [args.dry, args.test, args.live]) else []
    for m in ("dry", "test", "live"):
        if getattr(args, m):
            targets.append(m)
    seen = []
    for m in targets:
        if m in seen:
            continue
        seen.append(m)
        result = migrate(m)
        print(f"[{m}] {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
