#!/usr/bin/env python3
"""R14 — 1h liquid majors/top-N only (riset_edge subday). Cost RT harsh.

Families (pre-registered small set):
  - session long EW net (asia/eu/us)
  - 4h momentum hold 4/12 bars
  - residual vs BTC z fade
  - hour-of-day pre-reg: UTC 0-4 long vs 12-16 (NOT mined best hours)

  PYTHONPATH=. python research/edge_hunt_round14_1h.py
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

_RESEARCH = str(Path(__file__).resolve().parent)
_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [_RESEARCH, str(_ROOT)]

from edge_hunt import COST_RT, load_1h, pack, verdict_arm  # noqa: E402
from bot.xsectional import align_close_panel  # noqa: E402

MAJORS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "ADA", "DOT"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", default=str(_ROOT / "data" / "snap"))
    ap.add_argument("--out", default=str(_ROOT / "logs" / "edge_hunt_round14.json"))
    ap.add_argument("--max-syms", type=int, default=20)
    args = ap.parse_args()

    snap = Path(args.snap)
    dfs = load_1h(snap)
    if not dfs:
        print("NO_1H_DATA — download 1h first")
        Path(args.out).write_text(
            json.dumps({"error": "no_1h_data", "candidates": [], "verdicts": {}}, indent=2),
            encoding="utf-8",
        )
        return 2

    # prefer majors then by length
    ranked = []
    for s, df in dfs.items():
        base = s.split("/")[0].upper() if "/" in s else s.split("_")[0].upper()
        pri = MAJORS.index(base) if base in MAJORS else 100
        ranked.append((pri, -len(df), s, df))
    ranked.sort()
    use = {s: df for _, _, s, df in ranked[: args.max_syms]}
    panel = align_close_panel(use, min_coverage=0.6)
    if panel.shape[1] < 3 or len(panel) < 500:
        print("thin panel", panel.shape)
        return 2
    print("1h panel", panel.shape, panel.index.min(), panel.index.max())

    ret = panel.pct_change()
    ew = ret.mean(axis=1).dropna()
    # BTC column
    btc_col = next((c for c in panel.columns if str(c).upper().startswith("BTC")), None)
    btc_r = panel[btc_col].pct_change() if btc_col else ew * 0
    resid = ew - btc_r.reindex(ew.index).fillna(0)
    cut = ew.index[int(len(ew) * 0.70)]
    arms = []

    hours = ew.index.hour
    sessions = {
        "asia": (hours >= 0) & (hours < 8),
        "eu": (hours >= 8) & (hours < 16),
        "us": (hours >= 16) & (hours < 24),
    }
    for name, mask in sessions.items():
        for split, m in (("train", mask & (ew.index < cut)), ("oos", mask & (ew.index >= cut))):
            r = ew[m].to_numpy() - COST_RT
            arms.append({"id": f"sess_{name}_net", "split": split, "r": r})

    # collapse arms dict style
    def collect(prefix_masks):
        out = {}
        for name, mask in prefix_masks.items():
            for split, m in (("train", mask & (ew.index < cut)), ("oos", mask & (ew.index >= cut))):
                out.setdefault(name, {})[split] = pack(ew[m].to_numpy() - COST_RT)
        return out

    results = []
    sess = collect(sessions)
    for name, sp in sess.items():
        v = verdict_arm(sp["oos"], n_trials=12, train_mean=sp["train"].get("mean"), min_n=50)
        results.append({"id": f"sess_{name}_net", "family": "session", "train": sp["train"], "oos": sp["oos"], **v})

    # pre-reg hours
    for label, hs in (("utc0_4_long", range(0, 4)), ("utc12_16_long", range(12, 16))):
        mask = ew.index.hour.isin(hs)
        tr = pack(ew[(ew.index < cut) & mask].to_numpy() - COST_RT)
        oos = pack(ew[(ew.index >= cut) & mask].to_numpy() - COST_RT)
        v = verdict_arm(oos, n_trials=12, train_mean=tr.get("mean"), min_n=50)
        results.append({"id": label, "family": "hour_prereg", "train": tr, "oos": oos, **v})

    # 4h mom: ret last 4 bars, hold 4 / 12
    for lb, hold in ((4, 4), (4, 12), (12, 12)):
        mom = panel.pct_change(lb).mean(axis=1)
        # long if mom>0 else flat (simple)
        sig = (mom > 0).astype(float)
        # forward hold return of EW
        fwd = ew.rolling(hold).sum().shift(-hold)  # approx sum of hourly rets
        # only enter when sig and not na
        r = (fwd - COST_RT).where(sig > 0)
        tr = pack(r[r.index < cut].dropna().to_numpy())
        oos = pack(r[r.index >= cut].dropna().to_numpy())
        v = verdict_arm(oos, n_trials=12, train_mean=tr.get("mean"), min_n=50)
        results.append(
            {
                "id": f"mom{lb}_hold{hold}_long",
                "family": "mom_1h",
                "train": tr,
                "oos": oos,
                **v,
            }
        )

    # residual z fade vs btc on EW (market level)
    z = (resid - resid.rolling(48).mean()) / (resid.rolling(48).std() + 1e-12)
    for thr, hold in ((1.5, 4), (1.5, 12), (2.0, 4)):
        # fade: if z>thr short ew next hold; if z<-thr long
        fwd = ew.rolling(hold).sum().shift(-hold)
        long_m = z < -thr
        short_m = z > thr
        r = pd.Series(np.nan, index=ew.index, dtype=float)
        r = r.mask(long_m.reindex(r.index).fillna(False), fwd)
        r = r.mask(short_m.reindex(r.index).fillna(False), -fwd)
        r = r - COST_RT
        tr = pack(r[r.index < cut].dropna().to_numpy())
        oos = pack(r[r.index >= cut].dropna().to_numpy())
        v = verdict_arm(oos, n_trials=12, train_mean=tr.get("mean"), min_n=50)
        results.append(
            {
                "id": f"residz_fade_z{thr}_h{hold}",
                "family": "resid_fade_1h",
                "train": tr,
                "oos": oos,
                **v,
            }
        )

    results.sort(key=lambda r: (r["verdict"] != "CANDIDATE", -(r["oos"].get("mean") or -9)))
    out = {
        "meta": {
            "round": "R14_1h_liquid",
            "panel": list(panel.shape),
            "range": [str(panel.index.min()), str(panel.index.max())],
            "cost_rt": COST_RT,
            "n_trials": 12,
        },
        "verdicts": dict(Counter(r["verdict"] for r in results)),
        "arms": results,
        "candidates": [r for r in results if r["verdict"] == "CANDIDATE"],
        "train_oos_pos": [
            r
            for r in results
            if (r["train"].get("mean") or 0) > 0 and (r["oos"].get("mean") or 0) > 0
        ],
    }
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("verdicts", out["verdicts"])
    print("CANDIDATES", len(out["candidates"]))
    for r in out["train_oos_pos"][:10]:
        print(
            f"  {r['id']}: oos={r['oos']['mean']:+.4%} n={r['oos']['n']} train={r['train']['mean']:+.4%} v={r['verdict']}"
        )
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
