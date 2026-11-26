"""List (default) or repair stored duty results that lost ``shaft_conv`` (PR #900 follow-up).

DRY RUN BY DEFAULT: prints die, configuration, duty and drive of every stored result whose summary carries
``shaft_convention = fe_on_shaft_k3d_v1`` but whose result lacks ``shaft_conv`` - no values.  ``--apply`` (with
``--backup-dir``) copies each changed configuration file there first, then sets that single key through the application's own
save path.  Payload-only: it changes nothing else of a record and creates no record.  Run it where the application's data tree
is the one to repair (the same environment the application runs in); see ``motor_ai_sim.shaft_conv_repair``.

    python scripts/repair_shaft_conv.py
    python scripts/repair_shaft_conv.py --apply --backup-dir /path/to/backup
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from motor_ai_sim import shaft_conv_repair  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: list only)")
    ap.add_argument("--backup-dir", type=Path, help="required with --apply: each changed file is copied here first")
    a = ap.parse_args(argv)
    if a.apply and not a.backup_dir:
        ap.error("--apply needs --backup-dir")
    res = shaft_conv_repair.repair(apply=a.apply, backup_dir=a.backup_dir)
    for r in res["rows"]:
        print(f"{r['die']} / {r['config']} / {r.get('duty')}  drive={r.get('drive') or 'headline'}"
              + (f"  [{r['error']}]" if r.get("error") else ""))
    print(f"{len(res['rows'])} stored result(s) to repair" + (f"; {res['saved']} saved" if res["applied"] else " (dry run, nothing written)"))
    for f in res["failed"]:
        print(f"NOT saved: {f['die']} / {f['config']} ({f['error']})")
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
