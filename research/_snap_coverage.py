#!/usr/bin/env python3
"""Print data/snap coverage (1d/1h) — compact for edge-hunt loop memory."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def scan(snap: Path, tf: str) -> dict:
    files = sorted(snap.glob(f"*__{tf}.pkl"))
    ends: list[str] = []
    starts: list[str] = []
    lens: list[int] = []
    bad = 0
    for p in files:
        try:
            df = pd.read_pickle(p)
            if df is None or len(df) == 0:
                bad += 1
                continue
            lens.append(int(len(df)))
            ends.append(str(df.index.max().date()))
            starts.append(str(df.index.min().date()))
        except Exception:
            bad += 1
    lens_s = sorted(lens)
    p50 = lens_s[len(lens_s) // 2] if lens_s else 0
    return {
        "tf": tf,
        "files": len(files),
        "ok": len(lens),
        "bad": bad,
        "len_min": min(lens) if lens else 0,
        "len_p50": p50,
        "len_max": max(lens) if lens else 0,
        "end_top": Counter(ends).most_common(5),
        "start_oldest": sorted(Counter(starts).items())[:3] if starts else [],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=str(ROOT / "data" / "snap"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    snap = Path(args.snap)
    out = {
        "snap": str(snap.resolve()),
        "1d": scan(snap, "1d"),
        "1h": scan(snap, "1h"),
    }
    # BTC tip
    for name in ("BTC_USDT_USDT__1d.pkl", "BTC_USDC_USDC__1d.pkl"):
        p = snap / name
        if p.exists():
            df = pd.read_pickle(p)
            out["btc"] = {
                "file": name,
                "n": len(df),
                "start": str(df.index.min()),
                "end": str(df.index.max()),
            }
            break
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"snap={out['snap']}")
        for k in ("1d", "1h"):
            s = out[k]
            print(
                f"{k}: files={s['files']} ok={s['ok']} bad={s['bad']} "
                f"len[min/p50/max]={s['len_min']}/{s['len_p50']}/{s['len_max']}"
            )
            print(f"  end_top={s['end_top']}")
        if "btc" in out:
            b = out["btc"]
            print(f"BTC {b['file']}: n={b['n']} {b['start']} → {b['end']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
