"""CLI: ein einzelner Preis-Check.

    python -m app.check            # ein Durchlauf, Ergebnis als JSON
    python -m app.check --quiet    # nur Statuszeile

Fuer System-Cron auf dem Hetzner-Server (statt APScheduler):
    0 */4 * * *  cd /opt/urlaub-tracker && /opt/urlaub-tracker/.venv/bin/python -m app.check --quiet
"""
from __future__ import annotations

import argparse
import json
import sys

from .collector import run_check
from .logging_setup import setup_logging


def main() -> int:
    ap = argparse.ArgumentParser(description="Urlaub-Tracker: einmaliger Preis-Check")
    ap.add_argument("--quiet", action="store_true", help="nur kurze Statuszeile")
    ap.add_argument("--trigger", default="cli")
    args = ap.parse_args()

    setup_logging("WARNING" if args.quiet else "INFO")
    result = run_check(trigger=args.trigger)

    if args.quiet:
        v = result["verdict"]
        print(f"run #{result['run_id']} {result['status']} | "
              f"Flug={v.get('flight_total')} Hotel={v.get('hotel_total')} "
              f"Einzel={v.get('separate_total')} Pauschal={v.get('package_total')} "
              f"| Alerts: {len(result['alerts'])}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result["status"] != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
