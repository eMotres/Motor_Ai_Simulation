"""``python -m motor_ai_sim.dataset_exclusions`` prints the dataset exclusion list as JSON (no secrets, no network).

The engineering-dataset tools (``personal_engineering_ai``, package ``ped``) run it on the server, read-only, to learn which
companies' data must not be written to the dataset (owner 2026-10-27: PRIVATE clients are not recorded at all).  Exit code 0 and
the JSON on stdout, or a non-zero exit with the reason on stderr (the caller must then NOT assume an empty list).
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    from motor_ai_sim import platform_agreement as PA
    try:
        doc = PA.dataset_exclusions()
    except Exception as exc:                                           # noqa: BLE001
        print("dataset_exclusions failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    json.dump(doc, sys.stdout, ensure_ascii=True, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
