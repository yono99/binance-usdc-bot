#!/usr/bin/env python3
"""Candidate-edge fitness report (dry / live / both).

  python ce_report.py
  python ce_report.py --mode dry
  python ce_report.py --mode live

Does NOT change config. Verdicts are suggestions only.
See memory/CANDIDATE_EDGE.md.
"""
from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="CE-STANCE fitness report")
    p.add_argument("--mode", default="both",
                   choices=["both", "dry", "live", "default"],
                   help="arena to analyze (default: both)")
    p.add_argument("--min-n", type=int, default=20, help="min closes for promote-ish verdict")
    p.add_argument("--json", action="store_true", help="raw JSON only")
    args = p.parse_args(argv)

    from bot.ce_report import report

    mode = None if args.mode == "default" else args.mode
    r = report(mode, min_n=args.min_n)

    if args.json:
        print(json.dumps(r, indent=2, default=str))
        return 0

    print("=== CANDIDATE EDGE REPORT (CE-STANCE) ===")
    print("Pondasi: ilmu pemilik · NOT PROMOTE_PAPER · report non-mutating\n")

    if "dual_verdict" in r:
        print(f"DUAL verdict : {r['dual_verdict']}")
        print(f"             : {r.get('dual_reason')}")
        for label in ("dry", "live"):
            m = r.get(label) or {}
            print(f"\n--- {label.upper()} ---")
            _print_mode(m)
        # live stop state
        ls = (r.get("live") or {}).get("live_state") or {}
        if ls:
            print("\n--- LIVE STOP STATE ---")
            print(f"cum_r={ls.get('cum_r')}  n_closes={ls.get('n_closes')}  "
                  f"stopped={ls.get('stopped')}  reason={ls.get('stop_reason')}")
    else:
        _print_mode(r)

    print("\n--- raw ---")
    print(json.dumps(r, indent=2, default=str))
    return 0


def _print_mode(m: dict) -> None:
    print(f"verdict     : {m.get('verdict')} — {m.get('reason', '')}")
    print(f"shadow      : n={m.get('n_shadow_events')}  "
          f"downsize={m.get('n_shadow_downsize')}  skip_would={m.get('n_shadow_skip_would')}")
    print(f"reasons     : {m.get('reasons')}")
    ra = m.get("risk_all_closes") or {}
    print(f"closes      : n={ra.get('n')}  mean_R={ra.get('mean_r')}  "
          f"sum_R={ra.get('sum_r')}  maxDD={ra.get('max_drawdown_r')}  "
          f"worst={ra.get('worst_r')}  std={ra.get('std_r')}")
    print(f"ENTER settled: {m.get('n_enter_settled')}  CE-stamped: {m.get('n_enter_ce_stamped')}")


if __name__ == "__main__":
    sys.exit(main())
