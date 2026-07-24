#!/usr/bin/env python3
"""Download OHLCV all-time (max available) ke data/snap untuk edge hunt.

Riset pakai USDT-M (histori panjang); eksekusi live tetap USDC-M.
Default: 1d, perp USDT COIN yang lolos volume screen (screening awal),
bars besar (≈ all-time futures). Sumber = Binance public API (ccxt),
bukan TradingView.

  python research/download_snap_alltime.py
  python research/download_snap_alltime.py --tf 1d --bars 3500 --screen-volume
  python research/download_snap_alltime.py --majors-only
  python research/download_snap_alltime.py --no-screen-volume --max-symbols 300

Memori loop: memory/EDGE_HUNT_LOOP.md · memory/EDGE_HUNT_STATE.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.backtest import fetch_history
from bot.config import load_settings
from bot.dataset import load_ohlcv, save_ohlcv
from bot.exchange import Exchange
from bot.logger import log
from bot.screener import prefilter_volume

MAJORS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "ADA", "DOT",
    "LTC", "ATOM", "NEAR", "FIL", "APT", "ARB", "OP", "SUI", "UNI", "AAVE",
    "TRX", "ETC", "XLM", "ALGO", "ICP", "INJ", "TIA", "SEI", "WLD", "PEPE",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Download all-time OHLCV → data/snap")
    p.add_argument("--snapshot-dir", default=str(ROOT / "data" / "snap"))
    p.add_argument("--tf", default="1d", help="timeframe (default 1d)")
    p.add_argument(
        "--bars",
        type=int,
        default=3500,
        help="max bars per symbol (~9.5y daily; API returns available only)",
    )
    p.add_argument("--settle", default="USDT", choices=["USDT", "USDC"])
    p.add_argument("--majors-only", action="store_true", help="only major coins")
    p.add_argument("--max-symbols", type=int, default=0, help="0 = all after screen")
    p.add_argument("--sleep", type=float, default=0.05, help="pause between symbols")
    p.add_argument(
        "--force",
        action="store_true",
        help="re-fetch even if local file already has recent end",
    )
    p.add_argument(
        "--min-bars-keep",
        type=int,
        default=100,
        help="skip save if fewer bars returned",
    )
    p.add_argument(
        "--screen-volume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="prefilter by 24h quote volume (default ON; screening awal)",
    )
    p.add_argument(
        "--min-qv",
        type=float,
        default=0.0,
        help="min 24h quote volume; 0 = use config screener.min_quote_volume_24h",
    )
    p.add_argument(
        "--manifest",
        default="",
        help="optional path to write JSON manifest of downloaded symbols",
    )
    return p.parse_args()


def symbol_list(
    ex: Exchange,
    settle: str,
    majors_only: bool,
    max_symbols: int,
    *,
    screen_volume: bool,
    min_qv: float,
) -> list[str]:
    if majors_only:
        out = []
        for c in MAJORS:
            sym = f"{c}/{settle}:{settle}"
            if sym in ex.markets:
                out.append(sym)
            else:
                log.warning(f"major missing: {sym}")
        return out
    syms = ex.perp_symbols((settle,))
    log.info(f"listed COIN perp settle={settle}: {len(syms)}")
    if screen_volume and min_qv > 0:
        before = len(syms)
        syms = prefilter_volume(ex, syms, min_qv, top_n=None)
        log.info(f"volume screen qv>={min_qv:g}: {len(syms)}/{before} lolos")
    # prefer liquid majors first (stable ranking for partial runs)
    rank = {c: i for i, c in enumerate(MAJORS)}

    def key(s: str) -> tuple:
        base = s.split("/")[0].upper()
        return (rank.get(base, 999), s)

    syms = sorted(syms, key=key)
    if max_symbols and max_symbols > 0:
        syms = syms[:max_symbols]
    return syms


def needs_refresh(snap: Path, sym: str, tf: str, force: bool) -> bool:
    if force:
        return True
    df = load_ohlcv(snap, sym, tf)
    if df is None or len(df) < 50:
        return True
    last = df.index.max()
    # refresh if last bar older than ~2 days (UTC)
    import pandas as pd

    age = pd.Timestamp.utcnow() - last
    return age > pd.Timedelta(days=2)


def main() -> int:
    args = parse_args()
    snap = Path(args.snapshot_dir)
    snap.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    cfg = settings.raw if hasattr(settings, "raw") else {}
    # public OHLCV only — dry-safe even if config mode=live
    if settings.mode == "live":
        log.warning("config mode=live, but this script only fetches PUBLIC OHLCV (no orders)")
    min_qv = float(args.min_qv)
    if min_qv <= 0:
        try:
            min_qv = float((cfg.get("screener") or {}).get("min_quote_volume_24h") or 5_000_000)
        except Exception:
            min_qv = 5_000_000.0
    ex = Exchange(settings)
    symbols = symbol_list(
        ex,
        args.settle,
        args.majors_only,
        args.max_symbols,
        screen_volume=bool(args.screen_volume) and not args.majors_only,
        min_qv=min_qv,
    )
    log.info(
        f"=== SNAP DOWNLOAD {args.tf} bars≤{args.bars} settle={args.settle} "
        f"screen_vol={args.screen_volume} min_qv={min_qv:g} n={len(symbols)} → {snap} ==="
    )

    ok = skip = fail = 0
    saved: list[dict] = []
    for i, sym in enumerate(symbols, 1):
        try:
            if not needs_refresh(snap, sym, args.tf, args.force):
                skip += 1
                if i % 50 == 0 or i == len(symbols):
                    log.info(f"[{i}/{len(symbols)}] skip-fresh … ok={ok} skip={skip} fail={fail}")
                continue
            df = fetch_history(ex, sym, args.tf, args.bars)
            if df is None or len(df) < args.min_bars_keep:
                fail += 1
                log.warning(f"{sym}: terlalu pendek n={0 if df is None else len(df)}")
                continue
            path = save_ohlcv(df, snap, sym, args.tf)
            ok += 1
            saved.append(
                {
                    "symbol": sym,
                    "n": int(len(df)),
                    "start": str(df.index.min().date()),
                    "end": str(df.index.max().date()),
                    "file": path.name,
                }
            )
            if ok <= 5 or i % 25 == 0 or i == len(symbols):
                log.info(
                    f"[{i}/{len(symbols)}] {sym}: n={len(df)} "
                    f"{df.index.min().date()}→{df.index.max().date()} → {path.name}"
                )
            if args.sleep:
                time.sleep(args.sleep)
        except Exception as e:
            fail += 1
            log.warning(f"{sym} gagal: {e}")
            time.sleep(0.2)

    # coverage summary
    paths = sorted(snap.glob(f"*__{args.tf}.pkl"))
    ends = []
    lens = []
    import pandas as pd

    for p in paths:
        try:
            df = pd.read_pickle(p)
            if len(df):
                ends.append(str(df.index.max().date()))
                lens.append(len(df))
        except Exception:
            pass
    from collections import Counter

    summary = {
        "ok": ok,
        "skip": skip,
        "fail": fail,
        "n_symbols_requested": len(symbols),
        "files_tf": len(paths),
        "len_p50": sorted(lens)[len(lens) // 2] if lens else 0,
        "len_max": max(lens) if lens else 0,
        "end_top": Counter(ends).most_common(5),
        "min_qv": min_qv,
        "screen_volume": bool(args.screen_volume),
        "tf": args.tf,
        "settle": args.settle,
        "saved_this_run": saved[:50],
        "n_saved_this_run": len(saved),
    }
    log.info(
        f"SELESAI ok={ok} skip={skip} fail={fail} | files_{args.tf}={len(paths)} "
        f"len p50={summary['len_p50']} max={summary['len_max']} "
        f"end_top={summary['end_top'][:3]}"
    )
    man = Path(args.manifest) if args.manifest else (snap / f"_manifest_{args.tf}_{args.settle}.json")
    try:
        man.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        log.info(f"manifest → {man}")
    except Exception as e:
        log.warning(f"manifest write fail: {e}")
    return 0 if ok or skip else 1


if __name__ == "__main__":
    raise SystemExit(main())
