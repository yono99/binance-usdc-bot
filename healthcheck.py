#!/usr/bin/env python3
"""Alarm kalau snapshot bot basi (ts > MAX menit) atau dashboard tak terjangkau.

Pakai:
  python healthcheck.py                       # default host+30 menit
  python healthcheck.py --url http://192.168.1.107:8000 --max-min 30

Exit 0 = sehat, 1 = ALARM (basi / unreachable). Cocok buat cron / Task Scheduler:
  */5 * * * *  python /path/healthcheck.py || (echo bot basi | ...kirim notif...)
"""
import argparse, json, sys, urllib.request
from datetime import datetime, timezone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://192.168.1.107:8000")
    ap.add_argument("--max-min", type=float, default=30.0)
    a = ap.parse_args()

    try:
        with urllib.request.urlopen(a.url.rstrip("/") + "/api/status", timeout=10) as r:
            ts = json.load(r)["ts"]
    except Exception as e:
        print(f"ALARM: dashboard tak terjangkau ({a.url}): {e}")
        return 1

    age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 60
    if age_min > a.max_min:
        print(f"ALARM: snapshot basi {age_min:.1f} menit (> {a.max_min:.0f}) — bot berhenti nge-tick? ts={ts}")
        return 1
    print(f"OK: snapshot umur {age_min:.1f} menit (<= {a.max_min:.0f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
