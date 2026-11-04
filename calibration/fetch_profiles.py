"""Fetch name + business summary for every universe name in one industry, for calibrate.py.

Usage: python -m calibration.fetch_profiles --industry "Information Technology" --out profiles.json
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from helper.universe import load_universe
from helper.yahoo import fetch_profile

PAUSE_SECONDS = 1.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--industry", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default="cache")
    args = ap.parse_args()
    cache = Path(args.cache)
    rows = [r for r in load_universe(cache) if args.industry in r.industry]
    profiles, failed = {}, []
    for row in rows:
        try:
            p = fetch_profile(row.symbol, cache, row)
            profiles[row.symbol] = {"name": p.name, "summary": p.summary}
        except Exception as exc:  # report and continue; one bad ticker should not stop the sweep
            failed.append(f"{row.symbol}: {exc}")
        time.sleep(PAUSE_SECONDS)
    Path(args.out).write_text(json.dumps(profiles, indent=1, ensure_ascii=False), encoding="utf-8")
    with_summary = sum(1 for v in profiles.values() if v["summary"])
    print(f"fetched {len(profiles)} ({with_summary} with summary); failed: {failed or 'none'}")


if __name__ == "__main__":
    main()
