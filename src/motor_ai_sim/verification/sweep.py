"""CLI for the applicant verification sweep: silent-applicant reminders, escalations, and daily digest.

Usage::

    python -m motor_ai_sim.verification.sweep [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys

from motor_ai_sim.verification import service


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sweep verification cases for reminders, escalations, and daily digest.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without modifying cases or sending mails.")
    args = parser.parse_args(argv)

    counts = service.sweep(dry_run=args.dry_run)
    prefix = "[dry-run] " if args.dry_run else ""
    print(f"{prefix}verification sweep: {counts['reminded']} reminded, {counts['escalated']} escalated, {counts['digest']} digest sent, {counts['waiting']} waiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
