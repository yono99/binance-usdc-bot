#!/usr/bin/env python3
"""Multi-family v2 — DEEPTHINK portfolio (structurally NEW vs F1–F6).

Why v2 exists:
  F1–F5 mostly = single-name pullback/breakout × bull/bear.
  Competitive search needs *different economic stories*, not more ADX/SL knobs.

Pre-registered families (≤4 arms each, 50/30/20, cost 0.18% + ×2):

  G1  btc_lead_lag_catchup
      After BTC day ret > thr, long alts that *lagged* BTC that day (resid < -gap),
      hold H. Story: risk-on catch-up / beta lag.

  G2  quality_mom_ls
      Cross-section: score = ret20 / (vol20+eps); long top q, short bottom q, hold H.
      Story: quality momentum (not residual-z fade F4).

  G3  squeeze_break_btc_aligned
      BB width in bottom pctl then close breaks BB in BTC-regime direction only.
      Story: vol squeeze + macro alignment (≠ F5 naked breakout).

  G4  resid_mom_follow
      Long positive residual-vs-BTC momentum (not fade). Hold H.
      Story: continuation of relative strength (opposite F4).

  G5  vertical_fade
      After |ret1d| > k*ATR% against 5d sign, fade 1–2d.
      Story: exhaustion / short-term overextension.

  G6  pair_resid_majors
      Pair residual z fade: ETH-BTC, SOL-BTC, BNB-BTC, XRP-BTC (z thr × hold).
      Story: pairs (not basket residual).

  G7  pure_majors_short_pb_bear  (stress gate)
      Frozen A1 short pb bear on PURE majors list (no 1000*, no meme).
      Story: kill or confirm F3.

Bar entry: train mean_R>0, OOS CANDIDATE (family Bonferroni), lock>0, cost2x OOS>0.
Wire: never auto. Report formal vs operational.

  PYTHONPATH=. python research/edge_hunt_multifamily_v2.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "research")]

from bot.indicators import atr, bollinger, ema  # noqa: E402
from edge_hunt import pack, verdict_arm  # noqa: E402
from edge_hunt_multifamily import (  # noqa: E402
    COST_RT,
    pack_splits,
    judge_family,
    sim_short_or_long_pb,
    split_three,
)
from edge_hunt_param_regimes import btc_regime, load_1d, prep_arrays  # noqa: E402

# Pure majors — explicit allowlist (no 1000*, no micro)
PURE_MAJORS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "ADA", "DOT",
    "LTC", "ATOM", "NEAR", "UNI", "AAVE", "APT", "ARB", "OP", "SUI", "FIL",
    "INJ", "TIA", "SEI", "TRX", "ETC", "XLM", "ALGO", "ICP",
]


def load_named(snap: Path, bases: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for p in snap.glob("*__1d.pkl"):
        if "BTCDOM" in p.name.upper() or "USDC_USDC" in p.name:
            # prefer USDT dual
            pass
        stem = p.stem.replace("__1d", "")
        parts = stem.split("_")
        if len(parts) >= 3 and parts[-1] == parts[-2]:
            sym = f"{'_'.join(parts[:-2])}/{parts[-2]}:{parts[-2]}"
        else:
            sym = stem
        base = sym.split("/")[0].upper()
        if base.startswith("1000") or base.startswith("1M"):
            continue
        if base not in bases:
            continue
        if "USDT" not in sym and "USDC" not in sym:
            continue
        # prefer USDT
        if base in {s.split("/")[0].upper() for s in out} and "USDC" in sym:
            continue
        try:
            df = pd.read_pickle(p)
        except Exception:
            continue
        if len(df) < 400:
            continue
        df = df.sort_index()
        out[sym] = df[~df.index.duplicated(keep="last")]
    return out


def close_panel(dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    cols = {s: df["close"].astype(float) for s, df in dfs.items()}
    return pd.DataFrame(cols).sort_index()


def day_trades_from_signals(
    panel: pd.DataFrame,
    entry_mask: pd.DataFrame,
    side: int,
    hold: int,
    cost: float,
) -> list[float]:
    """side +1 long -1 short; EW across names with signal each day; list of day PnLs."""
    fwd = panel.shift(-hold) / panel - 1.0
    rs = []
    for t in panel.index:
        if t + pd.Timedelta(days=hold) > panel.index[-1]:
            break
        m = entry_mask.loc[t]
        if not hasattr(m, "any") or not bool(m.fillna(False).any()):
            continue
        vals = fwd.loc[t, m.fillna(False)].replace([np.inf, -np.inf], np.nan).dropna()
        if len(vals) < 1:
            continue
        rs.append(float(side * vals.mean()) - cost)
    return rs


def xs_ls_scores(
    panel: pd.DataFrame,
    score: pd.DataFrame,
    hold: int,
    cost: float,
    top_q: float = 0.2,
) -> list[float]:
    fwd = panel.shift(-hold) / panel - 1.0
    rs = []
    for t in panel.index:
        if t + pd.Timedelta(days=hold) > panel.index[-1]:
            break
        sc = score.loc[t]
        fr = fwd.loc[t]
        ok = sc.notna() & fr.notna() & panel.loc[t].notna()
        if ok.sum() < 12:
            continue
        sub = sc[ok].sort_values()
        k = max(1, int(len(sub) * top_q))
        lng, sht = sub.iloc[-k:].index, sub.iloc[:k].index
        r = float(fr[lng].mean() - fr[sht].mean()) - cost
        if np.isfinite(r):
            rs.append(r)
    return rs


def main() -> int:
    snap = Path("data/snap")
    btc_path = snap / "BTC_USDT_USDT__1d.pkl"
    if not btc_path.exists():
        btc_path = list(snap.glob("BTC_*__1d.pkl"))[0]
    btc_df = pd.read_pickle(btc_path)
    btc_close = btc_df["close"].astype(float)
    btc_ret = btc_close.pct_change()
    reg = btc_regime(btc_df)

    pure = load_named(snap, PURE_MAJORS)
    broad = load_1d(snap, max_alts=40)
    # strip 1000 from broad for one family if needed
    print("pure majors", len(pure), sorted(pure.keys())[:20])
    print("broad", len(broad))

    panel_p = close_panel(pure)
    panel_b = close_panel(broad)
    # align btc
    btc_p = btc_close.reindex(panel_p.index).ffill()
    btc_b = btc_close.reindex(panel_b.index).ffill()
    btc_r_p = btc_p.pct_change()
    btc_r_b = btc_b.pct_change()

    families = {}

    # ─── G1 BTC lead-lag catch-up ─────────────────────────────────────────
    print("=== G1 btc_lead_lag_catchup ===")
    g1_arms = []
    # thr BTC day, lag gap (alt-btc), hold
    for thr, gap, hold in ((0.02, 0.01, 2), (0.02, 0.015, 3), (0.03, 0.01, 2), (0.025, 0.01, 3)):
        alt_ret = panel_p.pct_change()
        lag = alt_ret.sub(btc_r_p, axis=0)  # negative = lagged
        btc_up = (btc_r_p > thr).fillna(False)
        # signal on closed day t; enter using shift(1) → no lookahead
        mask = lag.lt(-gap).mul(btc_up, axis=0)
        r1 = day_trades_from_signals(panel_p, mask.shift(1).fillna(False), +1, hold, COST_RT)
        r2 = day_trades_from_signals(panel_p, mask.shift(1).fillna(False), +1, hold, COST_RT * 2)
        row = pack_splits(r1, r2, n_trials=4)
        row["id"] = f"G1_btc{thr}_gap{gap}_h{hold}"
        row["params"] = {"btc_thr": thr, "lag_gap": gap, "hold": hold}
        g1_arms.append(row)
        print(
            f"  {row['id']}: n={row['n_total']} tr={row['train'].get('mean')} "
            f"oos={row['oos'].get('mean')} n_oos={row['oos'].get('n')} "
            f"{row['verdict_oos']['verdict']}"
        )
    families["G1_btc_lead_lag"] = judge_family(g1_arms, 4)

    # ─── G2 quality momentum LS ───────────────────────────────────────────
    print("=== G2 quality_mom_ls ===")
    g2_arms = []
    ret = panel_p.pct_change()
    vol20 = ret.rolling(20).std()
    ret20 = ret.rolling(20).mean()
    score = ret20 / (vol20 + 1e-8)
    for hold, q in ((5, 0.2), (10, 0.2), (5, 0.3), (10, 0.3)):
        r1 = xs_ls_scores(panel_p, score, hold, COST_RT, top_q=q)
        r2 = xs_ls_scores(panel_p, score, hold, COST_RT * 2, top_q=q)
        row = pack_splits(r1, r2, n_trials=4)
        row["id"] = f"G2_qmom_h{hold}_q{q}"
        row["params"] = {"hold": hold, "top_q": q}
        g2_arms.append(row)
        print(
            f"  {row['id']}: n={row['n_total']} tr={row['train'].get('mean')} "
            f"oos={row['oos'].get('mean')} {row['verdict_oos']['verdict']}"
        )
    families["G2_quality_mom"] = judge_family(g2_arms, 4)

    # ─── G3 squeeze break BTC-aligned ─────────────────────────────────────
    print("=== G3 squeeze_break_btc_aligned ===")
    g3_arms = []
    # per-symbol path with R multiples for SL/TP style? use hold returns EW for speed
    vol10 = panel_p.pct_change().rolling(10).std()
    vol60 = panel_p.pct_change().rolling(60).std()
    w = vol10 / (vol60 + 1e-12)
    w_rank = w.rank(axis=1, pct=True)
    r1d = panel_p.pct_change()
    reg_s = reg.reindex(panel_p.index).ffill()
    for width_pct, hold, btc_side in ((0.25, 3, 1), (0.20, 5, 1), (0.25, 3, -1), (0.20, 5, -1)):
        squeeze = w_rank < width_pct
        if btc_side > 0:
            brk = r1d > 1.5 * vol10
            reg_ok = (reg_s == 1).fillna(False)
            side = +1
        else:
            brk = r1d < -1.5 * vol10
            reg_ok = (reg_s == -1).fillna(False)
            side = -1
        # squeeze known prev day; break today; regime today
        mask = squeeze.shift(1).fillna(False) & brk.fillna(False)
        mask = mask.mul(reg_ok, axis=0)
        r1 = day_trades_from_signals(panel_p, mask, side, hold, COST_RT)
        r2 = day_trades_from_signals(panel_p, mask, side, hold, COST_RT * 2)
        row = pack_splits(r1, r2, n_trials=4)
        row["id"] = f"G3_sq{width_pct}_h{hold}_{'bullL' if btc_side>0 else 'bearS'}"
        row["params"] = {"width_pct": width_pct, "hold": hold, "btc_side": btc_side}
        g3_arms.append(row)
        print(
            f"  {row['id']}: n={row['n_total']} tr={row['train'].get('mean')} "
            f"oos={row['oos'].get('mean')} {row['verdict_oos']['verdict']}"
        )
    families["G3_squeeze_break"] = judge_family(g3_arms, 4)

    # ─── G4 residual momentum FOLLOW (not fade) ───────────────────────────
    print("=== G4 resid_mom_follow ===")
    g4_arms = []
    resid = panel_p.pct_change().sub(btc_r_p, axis=0)
    mom = resid.rolling(5).mean()
    reg_s = reg.reindex(panel_p.index).ffill()
    bull = (reg_s == 1).fillna(False)
    for hold, thr in ((3, 0.0), (5, 0.0), (5, 0.002), (10, 0.0)):
        mask = (mom > thr).mul(bull, axis=0)
        r1 = day_trades_from_signals(panel_p, mask.shift(1).fillna(False), +1, hold, COST_RT)
        r2 = day_trades_from_signals(panel_p, mask.shift(1).fillna(False), +1, hold, COST_RT * 2)
        row = pack_splits(r1, r2, n_trials=4)
        row["id"] = f"G4_rmom_h{hold}_thr{thr}"
        row["params"] = {"hold": hold, "thr": thr}
        g4_arms.append(row)
        print(
            f"  {row['id']}: n={row['n_total']} tr={row['train'].get('mean')} "
            f"oos={row['oos'].get('mean')} {row['verdict_oos']['verdict']}"
        )
    families["G4_resid_mom_follow"] = judge_family(g4_arms, 4)

    # ─── G5 vertical fade ─────────────────────────────────────────────────
    print("=== G5 vertical_fade ===")
    g5_arms = []
    # need ATR% per symbol — use rolling std as proxy
    for k_atr, hold in ((2.0, 1), (2.5, 1), (2.0, 2), (3.0, 2)):
        r1d = panel_p.pct_change()
        sig = r1d.rolling(20).std()
        # vertical up → short fade; vertical down → long fade
        up = r1d > k_atr * sig
        dn = r1d < -k_atr * sig
        # combine: for each day, take shorts on up and longs on dn as one EW book
        fwd = panel_p.shift(-hold) / panel_p - 1.0
        rs1, rs2 = [], []
        for t in panel_p.index:
            if t + pd.Timedelta(days=hold) > panel_p.index[-1]:
                break
            parts = []
            if up.loc[t].fillna(False).any():
                parts.extend((-fwd.loc[t, up.loc[t].fillna(False)]).dropna().tolist())
            if dn.loc[t].fillna(False).any():
                parts.extend((fwd.loc[t, dn.loc[t].fillna(False)]).dropna().tolist())
            if not parts:
                continue
            rs1.append(float(np.mean(parts)) - COST_RT)
            rs2.append(float(np.mean(parts)) - 2 * COST_RT)
        row = pack_splits(rs1, rs2, n_trials=4)
        row["id"] = f"G5_vert_k{k_atr}_h{hold}"
        row["params"] = {"k_atr": k_atr, "hold": hold}
        g5_arms.append(row)
        print(
            f"  {row['id']}: n={row['n_total']} tr={row['train'].get('mean')} "
            f"oos={row['oos'].get('mean')} {row['verdict_oos']['verdict']}"
        )
    families["G5_vertical_fade"] = judge_family(g5_arms, 4)

    # ─── G6 pair residual majors ──────────────────────────────────────────
    print("=== G6 pair_resid_majors ===")
    g6_arms = []
    pairs = []
    for a in ("ETH", "SOL", "BNB", "XRP", "AVAX", "LINK"):
        col = next((c for c in panel_p.columns if c.split("/")[0].upper() == a), None)
        btc_col = next((c for c in panel_p.columns if c.split("/")[0].upper() == "BTC"), None)
        if col and btc_col:
            pairs.append((a, col, btc_col))
    # if BTC not in panel, use btc_p series
    for z_thr, hold in ((1.5, 3), (1.5, 5), (2.0, 3), (2.0, 5)):
        rs1, rs2 = [], []
        for name, col, _ in pairs:
            y = panel_p[col].astype(float)
            x = btc_p
            # residual: y_ret - beta * x_ret, beta rolling 60
            yr, xr = y.pct_change(), x.pct_change()
            # simple residual without beta: yr - xr
            resid = yr - xr
            z = (resid - resid.rolling(20).mean()) / (resid.rolling(20).std() + 1e-12)
            for t_i, t in enumerate(z.index):
                if t_i + hold >= len(z):
                    break
                zv = z.loc[t]
                if not np.isfinite(zv):
                    continue
                # enter at t close, exit t+hold
                fut = y.iloc[t_i + hold] / y.iloc[t_i] - 1.0 if t_i + hold < len(y) else np.nan
                if not np.isfinite(fut):
                    continue
                if zv > z_thr:
                    # short alt
                    rs1.append(-fut - COST_RT)
                    rs2.append(-fut - 2 * COST_RT)
                elif zv < -z_thr:
                    rs1.append(fut - COST_RT)
                    rs2.append(fut - 2 * COST_RT)
        row = pack_splits(rs1, rs2, n_trials=4)
        row["id"] = f"G6_pairz{z_thr}_h{hold}"
        row["params"] = {"z_thr": z_thr, "hold": hold, "pairs": [p[0] for p in pairs]}
        g6_arms.append(row)
        print(
            f"  {row['id']}: n={row['n_total']} tr={row['train'].get('mean')} "
            f"oos={row['oos'].get('mean')} {row['verdict_oos']['verdict']}"
        )
    families["G6_pair_resid"] = judge_family(g6_arms, 4)

    # ─── G7 pure majors short pb bear (F3 stress) ─────────────────────────
    print("=== G7 pure_majors_short_pb_bear ===")
    prep = {s: prep_arrays(df, reg.reindex(df.index).ffill()) for s, df in pure.items() if s.split("/")[0].upper() != "BTC"}
    # drop BTC as trade target optional — keep ETH etc.
    g7_arms = []
    for adx_min, sl, rr, touch in ((22, 1.5, 1.5, 1.0), (22, 1.5, 2.5, 1.0)):
        r1, r2 = [], []
        for arr in prep.values():
            r1.extend(
                sim_short_or_long_pb(
                    arr, side="short", regime_need=-1, adx_min=adx_min,
                    sl_atr=sl, tp_rr=rr, touch=touch, max_hold=15, cost=COST_RT,
                )
            )
            r2.extend(
                sim_short_or_long_pb(
                    arr, side="short", regime_need=-1, adx_min=adx_min,
                    sl_atr=sl, tp_rr=rr, touch=touch, max_hold=15, cost=COST_RT * 2,
                )
            )
        row = pack_splits(r1, r2, n_trials=2)
        row["id"] = f"G7_short_pb_bear_adx{adx_min}_sl{sl}_rr{rr}"
        row["params"] = {"adx_min": adx_min, "sl": sl, "rr": rr, "touch": touch}
        g7_arms.append(row)
        print(
            f"  {row['id']}: n={row['n_total']} tr={row['train'].get('mean')} "
            f"oos={row['oos'].get('mean')} lock={row['lockbox'].get('mean')} "
            f"{row['verdict_oos']['verdict']}"
        )
    families["G7_pure_majors_short_pb"] = judge_family(g7_arms, 2)

    # scoreboard
    entry_promoted = []
    leans = []
    for fname, fam in families.items():
        entry_promoted.extend(fam.get("promoted") or [])
        for a in fam.get("arms") or []:
            o, t, l = a.get("oos") or {}, a.get("train") or {}, a.get("lockbox") or {}
            if (t.get("mean") or 0) > 0 and (o.get("mean") or 0) > 0:
                leans.append(
                    {
                        "family": fname,
                        "id": a["id"],
                        "train": t.get("mean"),
                        "oos": o.get("mean"),
                        "n_oos": o.get("n"),
                        "lock": l.get("mean"),
                        "c2": (a.get("oos_cost2x") or {}).get("mean"),
                        "p_adj": (a.get("verdict_oos") or {}).get("p_adj"),
                        "verdict": (a.get("verdict_oos") or {}).get("verdict"),
                        "promo": a.get("promotion"),
                    }
                )
    leans.sort(key=lambda x: (x.get("oos") or -9), reverse=True)

    out = {
        "meta": {
            "version": 2,
            "spec": "deepthink multi-family — novel structures vs F1-F6",
            "cost_rt": COST_RT,
            "n_families": len(families),
            "promote_paper": len(entry_promoted) > 0,
            "entry_promoted": entry_promoted,
            "pure_majors_n": len(pure),
            "pure_majors": list(pure.keys()),
            "note": "Formal promote ≠ wire. Stress G7 kills F3 if fails.",
        },
        "families": families,
        "scoreboard_leans": leans[:25],
    }
    Path("logs/edge_hunt_multifamily_v2.json").write_text(
        json.dumps(out, indent=2, default=str), encoding="utf-8"
    )
    print("\n=== V2 SCOREBOARD ===")
    print("ENTRY PROMOTED:", entry_promoted or "NONE")
    for x in leans[:15]:
        print(
            f"  {x['family']} {x['id']}: oos={x['oos']} n={x['n_oos']} tr={x['train']} "
            f"lock={x['lock']} c2={x['c2']} p_adj={x['p_adj']} {x['verdict']} promo={x['promo']}"
        )
    print("wrote logs/edge_hunt_multifamily_v2.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
