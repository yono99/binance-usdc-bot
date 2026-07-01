#!/usr/bin/env python3
"""A/B report — rules-saja vs rules+ReAct, dari decision_log mode shadow.

  python ab_report.py

Aktifkan dulu `agent.ab_shadow: true` di config.yaml, jalankan bot mengumpulkan trade,
lalu jalankan ini untuk verdict jujur (apakah ReAct benar-benar menambah nilai).
"""
import json

from bot import ab


def main() -> None:
    r = ab.report()
    print("=== A/B HARNESS — rules vs rules+ReAct ===")
    print(f"verdict    : {r['verdict']} — {r.get('reason', '')}")
    print(f"rules saja : exp_R={r.get('exp_r_rules')}  n={r.get('n_total')}")
    print(f"rules+ReAct: exp_R={r.get('exp_r_rules_react')}  n={r.get('n_kept')}")
    print(f"ditolak    : exp_R={r.get('exp_r_denied')}  n={r.get('n_denied')}")
    if r.get("p_value") is not None:
        print(f"improvement={r.get('improvement')}  p={r.get('p_value')}  "
              f"significant={r.get('significant')}")
    rr, rk = r.get("risk_rules") or {}, r.get("risk_react") or {}
    print("\n--- RISIKO (Jalan A: manajer disiplin) ---")
    print(f"max drawdown : rules={rr.get('max_drawdown_r')}R  rules+ReAct={rk.get('max_drawdown_r')}R")
    print(f"volatilitas  : rules std={rr.get('std_r')}  rules+ReAct std={rk.get('std_r')}")
    print(f"R terburuk   : rules={rr.get('worst_r')}  rules+ReAct={rk.get('worst_r')}")
    print(f"KURANGI RISIKO: {r.get('reduces_risk')}")
    print("\n--- raw ---")
    print(json.dumps(r, indent=2))


if __name__ == "__main__":
    main()
