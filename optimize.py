#!/usr/bin/env python3
"""Sweep + walk-forward: cari parameter ber-edge yang lolos OUT-OF-SAMPLE.

  python optimize.py --symbols "BTC/USDC:USDC" --bars 5000 --tf 15m
  python optimize.py --bars 6000 --train 1000 --test 300

Verdict memakai expectancy OUT-OF-SAMPLE (jujur). Jika OOS positif & stabil,
barulah parameter layak dipertimbangkan untuk testnet.
"""
from __future__ import annotations

import argparse
from collections import Counter

from rich.console import Console
from rich.table import Table

from bot.altdata import align, basis_zscore, fetch_bybit_close, fetch_funding, fetch_oi, funding_zscore, oi_delta
from bot.backtest import Backtester, compute_metrics, fetch_history
from bot.config import load_settings
from bot.dataset import df_hash, load_ohlcv, save_ohlcv, split_holdout
from bot.exchange import Exchange
from bot.logger import log
from bot.optimize import build_grid, walk_forward
from bot.orderflow import cvd_features
from bot.strategy_lab import (
    build_grid_v2,
    build_grid_v3,
    build_grid_v4,
    build_grid_v5,
    build_grid_v6,
    build_grid_v7,
    walk_forward_v2,
    walk_forward_v3,
    walk_forward_v4,
    walk_forward_v5,
    walk_forward_v6,
    walk_forward_v7,
)

console = Console()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--strategy", choices=["v1", "v2", "v3", "v4", "v5", "v6", "v7"], default="v2",
                   help="v1=trend, v2=HTF+regime+sesi, v3=+funding+OI, v4=+orderflow/CVD, "
                        "v5=cross-exchange basis, v6=liquidation cascade fade, "
                        "v7=funding regime (sinyal primer)")
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--tf")
    p.add_argument("--bars", type=int, default=5000)
    p.add_argument("--train", type=int, default=1000)
    p.add_argument("--test", type=int, default=300)
    p.add_argument("--min-trades", type=int, default=15)
    p.add_argument("--equity", type=float, default=1000.0)
    p.add_argument("--fee", type=float, default=0.04)
    p.add_argument("--slippage", type=float, default=0.02)
    p.add_argument("--stress-mult", type=float, default=1.0,
                   help="kalikan fee & slippage (mis. 2 = cost-stress; CANDIDATE wajib bertahan)")
    p.add_argument("--conf", nargs="*", type=float, default=[0.50, 0.55, 0.60, 0.65, 0.70])
    p.add_argument("--sl", nargs="*", type=float, default=[1.0, 1.5, 2.0])
    p.add_argument("--tp", nargs="*", type=float, default=[1.5, 2.0, 2.5, 3.0])
    p.add_argument("--basis-z", nargs="*", type=float, default=[1.5, 2.0, 2.5, 3.0],
                   help="ambang |z-score| basis untuk entry (v5)")
    p.add_argument("--cascade-k", nargs="*", type=float, default=[2.0, 2.5, 3.0, 3.5],
                   help="ambang range/ATR untuk deteksi cascade (v6)")
    p.add_argument("--funding-z", nargs="*", type=float, default=[1.0, 1.5, 2.0, 2.5],
                   help="ambang |z-score| funding untuk entry primer (v7)")
    p.add_argument("--htf-mult", type=int, help="override strategy.htf_mult")
    p.add_argument("--sessions", nargs="*", type=int, help="jam UTC diizinkan (v2)")
    p.add_argument("--copilot", action="store_true",
                   help="aktifkan Gemini co-pilot: tafsirkan OOS & usulkan hipotesis berikut")
    p.add_argument("--snapshot-dir",
                   help="dir snapshot OHLCV: load bila ada (reproducible), else fetch+simpan")
    p.add_argument("--holdout-frac", type=float, default=0.0,
                   help="sisihkan fraksi ekor histori sebagai LOCKBOX (tak dipakai riset)")
    p.add_argument("--lockbox", action="store_true",
                   help="UJIAN FINAL: jalankan di segmen lockbox yang disisihkan (pakai SEKALI)")
    return p.parse_args()


def params_str(p: dict) -> str:
    if "basis_z_entry" in p:
        return f"z{p['basis_z_entry']}/{p['sl_atr_mult']}/{p['tp_atr_mult']}"
    if "cascade_k" in p:
        return f"k{p['cascade_k']}/{p['sl_atr_mult']}/{p['tp_atr_mult']}"
    if "funding_z_entry" in p:
        return f"fz{p['funding_z_entry']}/{p['sl_atr_mult']}/{p['tp_atr_mult']}"
    base = f"{p['entry_confidence']}/{p['sl_atr_mult']}/{p['tp_atr_mult']}"
    if "use_htf" in p:
        base += f" htf={int(p['use_htf'])} reg={int(p['regime'])}"
    if "use_funding" in p:
        base += f" fnd={int(p['use_funding'])} oi={int(p['use_oi'])}"
    if "use_of" in p:
        base += f" of={int(p['use_of'])}"
    return base


def main() -> None:
    args = parse_args()
    settings = load_settings()
    cfg = settings.raw
    tf = args.tf or cfg["market"]["timeframe"]
    symbols = args.symbols or cfg["market"].get("whitelist") or ["BTC/USDC:USDC"]

    ex = Exchange(settings)
    bt = Backtester(cfg, fee_pct=args.fee * args.stress_mult,
                    slippage_pct=args.slippage * args.stress_mult)
    if args.stress_mult != 1.0:
        log.warning(f"COST-STRESS ×{args.stress_mult}: fee={args.fee*args.stress_mult:.3f}% "
                    f"slippage={args.slippage*args.stress_mult:.3f}% — CANDIDATE harus tetap lolos.")

    htf_mult = args.htf_mult or cfg["strategy"]["htf_mult"]
    sessions = set(args.sessions) if args.sessions else (set(cfg["strategy"]["sessions"]) or None)

    # Dominansi BTC (mother coin): muat close BTC sekali → gerbang direction-aware
    # dipakai SEMUA teknik (v1–v7) via run_walk. enabled=false → nonaktif.
    from bot.altdata import btc_ret_arr
    btc_close = None
    bcfg = cfg.get("btc", {})
    if bcfg.get("enabled", True):
        try:
            btc_sym = bcfg.get("symbol", "BTC/USDC:USDC")
            btc_df = load_ohlcv(args.snapshot_dir, btc_sym, tf) if args.snapshot_dir else None
            if btc_df is None:
                btc_df = fetch_history(ex, btc_sym, tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(btc_df, args.snapshot_dir, btc_sym, tf)
            btc_close = btc_df["close"]
            log.info(f"BTC-gate: {btc_sym} {len(btc_close)} bar (dump_pct={bcfg.get('dump_pct', 0.5)}%)")
        except Exception as e:  # boundary — gagal muat BTC → gerbang nonaktif
            log.warning(f"BTC-gate nonaktif (muat BTC gagal): {e}")

    def btc_ret_for(df):
        return btc_ret_arr(btc_close, df.index) if btc_close is not None else None

    if args.strategy == "v7":
        grid = build_grid_v7(args.funding_z, args.sl, args.tp)

        def run_wf(df, sym):
            since = int(df.index[0].timestamp() * 1000)
            fz = funding_zscore(fetch_funding(ex, sym, since), cfg["strategy"]["funding_z_window"])
            funding_z = align(df.index, fz, 0.0)
            log.info(f"{sym}: funding terisi {int((funding_z != 0).sum())}/{len(df)} bar, "
                     f"|z|≥{min(args.funding_z)}: {int((abs(funding_z) >= min(args.funding_z)).sum())} bar")
            return walk_forward_v7(df, cfg, grid, bt, args.train, args.test,
                                   args.min_trades, funding_z, btc_ret=btc_ret_for(df))
    elif args.strategy == "v6":
        grid = build_grid_v6(args.cascade_k, args.sl, args.tp)

        def run_wf(df, sym):
            from bot.optimize import precompute as _pre
            from bot.altdata import cascade_components
            ra, vr, cl = cascade_components(df, _pre(df, cfg).atr,
                                            cfg["strategy"]["cascade_vol_lookback"])
            events = int(((ra >= min(args.cascade_k)) & (vr >= cfg["strategy"]["cascade_vol_mult"])).sum())
            log.info(f"{sym}: cascade events (k≥{min(args.cascade_k)}, vol≥"
                     f"{cfg['strategy']['cascade_vol_mult']}): {events}/{len(df)} bar")
            return walk_forward_v6(df, cfg, grid, bt, args.train, args.test, args.min_trades,
                                   btc_ret=btc_ret_for(df))
    elif args.strategy == "v5":
        grid = build_grid_v5(args.basis_z, args.sl, args.tp)

        def run_wf(df, sym):
            since = int(df.index[0].timestamp() * 1000)
            bybit_close = fetch_bybit_close(sym, tf, since, len(df) + 50)
            bz = basis_zscore(df["close"], bybit_close, cfg["strategy"]["basis_z_window"])
            log.info(f"{sym}: bybit terisi {int(len(bybit_close))} bar, "
                     f"basis_z aktif {int((bz != 0).sum())}/{len(df)} bar, "
                     f"|z|>2: {int((abs(bz) > 2).sum())} bar")
            return walk_forward_v5(df, cfg, grid, bt, args.train, args.test,
                                   args.min_trades, bz, btc_ret=btc_ret_for(df))
    elif args.strategy == "v4":
        grid = build_grid_v4(args.conf, args.sl, args.tp, [True], [True, False],
                             [False, True], [False], [False, True])

        def run_wf(df, sym):
            since = int(df.index[0].timestamp() * 1000)
            fz = funding_zscore(fetch_funding(ex, sym, since), cfg["strategy"]["funding_z_window"])
            funding_z = align(df.index, fz, 0.0)
            oid = oi_delta(df.index, fetch_oi(ex, sym, tf, since), cfg["strategy"]["oi_delta_lookback"])
            imb, div = cvd_features(ex, sym, tf, df, cfg["strategy"]["cvd_lookback"])
            log.info(f"{sym}: funding {int((funding_z!=0).sum())}/{len(df)}, "
                     f"OI {int((oid!=0).sum())}/{len(df)}, CVD {int((imb!=0).sum())}/{len(df)} bar")
            return walk_forward_v4(df, cfg, grid, bt, args.train, args.test, args.min_trades,
                                   htf_mult, sessions, funding_z, oid, imb, div,
                                   btc_ret=btc_ret_for(df))
    elif args.strategy == "v3":
        grid = build_grid_v3(args.conf, args.sl, args.tp, [True], [True, False],
                             [False, True], [False, True])

        def run_wf(df, sym):
            since = int(df.index[0].timestamp() * 1000)
            fz = funding_zscore(fetch_funding(ex, sym, since), cfg["strategy"]["funding_z_window"])
            funding_z = align(df.index, fz, 0.0)
            oid = oi_delta(df.index, fetch_oi(ex, sym, tf, since), cfg["strategy"]["oi_delta_lookback"])
            nz_f = int((funding_z != 0).sum())
            nz_o = int((oid != 0).sum())
            log.info(f"{sym}: funding terisi {nz_f}/{len(df)} bar, OI terisi {nz_o}/{len(df)} bar")
            return walk_forward_v3(df, cfg, grid, bt, args.train, args.test, args.min_trades,
                                   htf_mult, sessions, funding_z, oid, btc_ret=btc_ret_for(df))
    elif args.strategy == "v2":
        grid = build_grid_v2(args.conf, args.sl, args.tp, [False, True], [False, True])

        def run_wf(df, sym):
            return walk_forward_v2(df, cfg, grid, bt, args.train, args.test,
                                   args.min_trades, htf_mult, sessions, btc_ret=btc_ret_for(df))
    else:
        grid = build_grid(args.conf, args.sl, args.tp)

        def run_wf(df, sym):
            return walk_forward(df, cfg, grid, bt, args.train, args.test, args.min_trades,
                                btc_ret=btc_ret_for(df))

    log.info(f"Walk-forward strategy={args.strategy} tf={tf} bars={args.bars} "
             f"train={args.train} test={args.test} grid={len(grid)}/window symbols={symbols}")

    all_oos = []
    chosen = Counter()
    per_symbol: dict = {}

    if args.lockbox and args.holdout_frac <= 0:
        console.print("[red]--lockbox butuh --holdout-frac > 0 (segmen yang disisihkan).[/red]")
        return

    for sym in symbols:
        try:
            df = load_ohlcv(args.snapshot_dir, sym, tf) if args.snapshot_dir else None
            if df is None:
                df = fetch_history(ex, sym, tf, args.bars)
                if args.snapshot_dir:
                    save_ohlcv(df, args.snapshot_dir, sym, tf)
            else:
                log.info(f"{sym}: pakai SNAPSHOT ({len(df)} bar) — reproducible")
        except Exception as e:  # boundary
            log.error(f"fetch {sym} gagal: {e}")
            continue

        research_df, lockbox_df = split_holdout(df, args.holdout_frac)
        if args.holdout_frac > 0:
            tag = "LOCKBOX (ujian final)" if args.lockbox else "RISET"
            use_df = lockbox_df if args.lockbox else research_df
            log.info(f"{sym}: holdout {args.holdout_frac:.0%} → pakai segmen {tag} "
                     f"({len(use_df)} bar, hash={df_hash(use_df)})")
            df = use_df

        results, oos = run_wf(df, sym)
        all_oos += oos
        if not results:
            log.warning(f"{sym}: data kurang untuk walk-forward")
            continue

        per_symbol[sym] = [{"oos_exp": w.oos_exp, "oos_n": w.oos_n,
                            "params": params_str(w.params)} for w in results]
        tbl = Table(title=f"{sym} — walk-forward ({len(results)} window)")
        for c in ["window", "params", "IS exp_R", "IS n", "OOS exp_R", "OOS n"]:
            tbl.add_column(c, justify="right")
        for i, w in enumerate(results):
            p = w.params
            chosen[params_str(p)] += 1
            tbl.add_row(str(i + 1), params_str(p), f"{w.is_exp:+.3f}", str(w.is_n),
                        f"{w.oos_exp:+.3f}", str(w.oos_n))
        console.print(tbl)

    if not all_oos:
        console.print("[red]Tak ada trade OOS — perbesar --bars atau longgarkan grid.[/red]")
        return

    m = compute_metrics(all_oos, cfg, args.equity)
    summary = Table(title="GABUNGAN OUT-OF-SAMPLE (semua window, semua simbol)")
    for c in ["OOS trades", "win%", "exp_R", "PF", "maxDD%", "ret%"]:
        summary.add_column(c, justify="right")
    pf = m["profit_factor"]
    summary.add_row(str(m["trades"]), f"{m['win_rate']:.1f}", f"{m['expectancy_r']:+.3f}",
                    ("∞" if pf == float("inf") else f"{pf:.2f}"),
                    f"{m['max_drawdown_pct']:.1f}", f"{m['return_pct']:+.1f}")
    console.print(summary)

    if chosen:
        common = chosen.most_common(3)
        console.print("Parameter paling sering terpilih (conf/sl/tp): " +
                      ", ".join(f"{k} ×{v}" for k, v in common))

    e = m["expectancy_r"]
    if e > 0.05:
        console.print(f"[green]OOS POSITIF ({e:+.3f}R). Kandidat layak diuji di testnet — "
                      f"set parameter tersering ke config.yaml lalu MODE=test.[/green]")
    elif e > 0:
        console.print(f"[yellow]OOS tipis ({e:+.3f}R) — belum meyakinkan. Perluas data/grid atau "
                      f"perbaiki logika sinyal sebelum live.[/yellow]")
    else:
        console.print(f"[red]OOS NEGATIF ({e:+.3f}R). Strategi belum punya edge yang general. "
                      f"JANGAN live; perbaiki fitur sinyal (bukan sekadar tuning).[/red]")

    # Catat siklus ke registry SELALU (sumber kebenaran tunggal), apa pun --copilot.
    from bot import registry
    from bot.copilot import STRATEGY_SOURCE, CycleResult, verdict as _verdict

    grid_trials = len(grid)                                   # ukuran ruang pencarian siklus ini
    cum_trials = registry.total_trials() + grid_trials        # kumulatif (multiple-testing)
    cycle = CycleResult(
        strategy=args.strategy,
        hypothesis=f"strategy {args.strategy}",
        per_symbol=per_symbol,
        aggregate={k: m[k] for k in ("trades", "win_rate", "expectancy_r",
                                     "profit_factor", "max_drawdown_pct") if k in m},
        chosen_params=[k for k, _ in chosen.most_common(3)],
        oos_r=[t.r for t in all_oos],
        trials=cum_trials,
    )
    vlabel, vreason = _verdict(cycle)
    console.print(f"[bold]Verdict deterministik:[/bold] {vlabel} — {vreason}")
    if args.lockbox:
        console.print("[bold red]⚠ INI HASIL LOCKBOX (ujian final).[/bold red] Segmen ini "
                      "seharusnya dipakai SEKALI saja; mengulang = mencemari holdout.")
    rec_id = f"{args.strategy}_lockbox" if args.lockbox else args.strategy
    registry.record({"id": rec_id, "source": STRATEGY_SOURCE.get(args.strategy, "other"),
                     "name": f"strategy {args.strategy}" + (" [LOCKBOX]" if args.lockbox else ""),
                     "oos_exp": round(m["expectancy_r"], 4), "verdict": vlabel,
                     "n": m["trades"], "trials": grid_trials})

    if args.copilot:
        run_copilot(settings, cfg, cycle)


def run_copilot(settings, cfg, cycle) -> None:
    """Gemini co-pilot: tafsirkan hasil OOS & usulkan hipotesis berikut (advisory)."""
    from bot.copilot import StrategyCopilot

    advice = StrategyCopilot(settings, cfg).advise(cycle)

    panel = Table(title="🤖 STRATEGY CO-PILOT (Gemini — advisory, BUKAN hakim)")
    panel.add_column("aspek", style="bold")
    panel.add_column("isi", overflow="fold")
    panel.add_row("Verdict (deterministik)", f"{advice['verdict']} — {advice['verdict_reason']}")
    panel.add_row("Sumber narasi", advice.get("source", "-"))
    if advice.get("interpretation"):
        panel.add_row("Interpretasi", advice["interpretation"])
    if advice.get("overfit_risk"):
        panel.add_row("Risiko overfit", advice["overfit_risk"])
    if advice.get("dedup_warning"):
        panel.add_row("⚠ Dedup", advice["dedup_warning"])
    if advice.get("next_source_tag"):
        panel.add_row("Sumber berikut (tag)", advice["next_source_tag"])
    if advice.get("next_hypothesis"):
        panel.add_row("Hipotesis berikut", advice["next_hypothesis"])
    if advice.get("economic_rationale"):
        panel.add_row("Rasional ekonomi", advice["economic_rationale"])
    if advice.get("falsifier"):
        panel.add_row("Pemfalsifikasi", advice["falsifier"])
    if advice.get("live_trading"):
        panel.add_row("Live trading", advice["live_trading"])
    console.print(panel)


if __name__ == "__main__":
    main()
