#!/usr/bin/env python3
"""Forward-test (paper) / live runner strategi v4 di data real-time.

  python forwardtest.py                       # whitelist config, parameter default
  python forwardtest.py --symbols "BTC/USDC:USDC" --poll 30
  python forwardtest.py --once                # satu siklus (uji cepat)
  python forwardtest.py --mode dry --use-store
  python forwardtest.py --mode live --use-store   # UANG NYATA (butuh BINANCE_LIVE_*)

Parameter TETAP selama jalan (tidak re-optimize). Hasil di logs/trades_<mode>.jsonl.

PENTING — single-instance PER MODE:
  - Dua proses **mode sama** (mis. 2× dry) = botstate/events bentrok (insiden 2026-07-20).
  - Dry + live **boleh** paralel: lock terpisah `logs/forwardtest_<mode>.lock`,
    state `botstate_dry` / `botstate_live`, journal `trades_dry` / `trades_live`.
  - Tanpa `--mode`: lock legacy `logs/forwardtest.lock` (satu proses global).
"""
from __future__ import annotations

import argparse
import atexit
import os
import sys
from pathlib import Path

from bot.config import load_settings
from bot.forward import ForwardTester, default_params
from bot.logger import log
from bot.settings_store import reset_all_enabled

ROOT = Path(__file__).resolve().parent
_LOCK_FD = None
_LOCK_PATH: Path | None = None


def _lock_path_for_mode(mode: str | None) -> Path:
    """Satu lock per mode (dry/test/live). Legacy tanpa --mode → lock global."""
    if mode in ("dry", "test", "live"):
        return ROOT / "logs" / f"forwardtest_{mode}.lock"
    return ROOT / "logs" / "forwardtest.lock"


def _acquire_single_instance_lock(mode: str | None = None) -> None:
    """Gagal start bila sudah ada forwardtest **mode yang sama** (PM2 atau manual)."""
    global _LOCK_FD, _LOCK_PATH
    lock_path = _lock_path_for_mode(mode)
    _LOCK_PATH = lock_path
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:
        log.error(f"Tidak bisa buka lock file {lock_path}: {e}")
        sys.exit(1)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        os.close(fd)
        label = mode or "global"
        log.error(
            f"FORWARDTEST mode={label} SUDAH JALAN (lock {lock_path.name}). "
            "Tepat 1 proses per mode — bunuh zombie dulu: "
            "ps aux | grep forwardtest; pm2 list"
        )
        sys.exit(2)
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _LOCK_FD = fd
    log.info(f"Instance lock OK: {lock_path.name} pid={os.getpid()}")

    def _release() -> None:
        global _LOCK_FD
        if _LOCK_FD is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(_LOCK_FD, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(_LOCK_FD, fcntl.LOCK_UN)
            os.close(_LOCK_FD)
        except Exception:
            pass
        _LOCK_FD = None

    atexit.register(_release)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--poll", type=int, default=30)
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--conf", type=float)
    p.add_argument("--sl", type=float)
    p.add_argument("--tp", type=float)
    p.add_argument("--no-htf", action="store_true")
    p.add_argument("--no-regime", action="store_true")
    p.add_argument("--no-funding", action="store_true")
    p.add_argument("--oi", action="store_true")
    p.add_argument("--no-of", action="store_true")
    p.add_argument("--use-store", action="store_true",
                   help="baca pengaturan dari UI (runtime.json) tiap siklus")
    p.add_argument("--mode", choices=["dry", "test", "live"],
                   help="KUNCI proses ke satu mode (abaikan mode aktif UI). "
                        "Jalankan satu proses per mode untuk multi-mode paralel.")
    p.add_argument("--once", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    # Single-instance PER MODE: cegah 2 bot mode sama menulis botstate yang sama.
    # Dry + live paralel OK (lock terpisah). Lihat memory/LIVE_AND_DRY.md.
    if not args.once:
        _acquire_single_instance_lock(args.mode)
    # Skip reset_all_enabled in production (PM2) - use SKIP_ENABLED_RESET=1 env var
    # Catatan dual-bot: proses live yang start belakangan JANGAN matikan enabled dry.
    # Hanya proses pertama / tanpa SKIP yang mereset semua mode ke OFF.
    if not os.getenv("SKIP_ENABLED_RESET"):
        reset_all_enabled()
        log.info("Startup: SEMUA mode di-reset ke OFF — nyalakan dari dashboard.")
    else:
        log.info("Startup: SKIP enabled reset (SKIP_ENABLED_RESET=1)")
    settings = load_settings()
    if args.mode:
        from dataclasses import replace
        if args.mode == "live" and not (os.getenv("BINANCE_LIVE_KEY") and os.getenv("BINANCE_LIVE_SECRET")):
            log.error("--mode live butuh BINANCE_LIVE_KEY/SECRET di .env — berhenti.")
            return
        settings = replace(settings, mode=args.mode)
    symbols = args.symbols or settings.raw["market"].get("whitelist") or ["BTC/USDC:USDC"]

    params = default_params()
    if args.conf is not None:
        params["entry_confidence"] = args.conf
    if args.sl is not None:
        params["sl_atr_mult"] = args.sl
    if args.tp is not None:
        params["tp_atr_mult"] = args.tp
    params["use_htf"] = not args.no_htf
    params["regime"] = not args.no_regime
    params["use_funding"] = not args.no_funding
    params["use_oi"] = args.oi
    params["use_of"] = not args.no_of

    ft = ForwardTester(settings, symbols, params, equity=args.equity,
                       use_store=args.use_store, pin_mode=bool(args.mode))

    if args.once:
        ft.seed()
        log.info(f"params={params}")
        ft.on_cycle()
        log.info(f"stats: {ft.stats()}")
        return

    ft.run(poll_s=args.poll)


if __name__ == "__main__":
    main()
