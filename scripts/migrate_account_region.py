#!/usr/bin/env python
"""Move ONE account's data to its home region (docs/regions/DESIGN.md section 13, runbook docs/regions/MIGRATION.md).

Every step is a DRY RUN unless ``--apply`` is given.  Run it inside the API container of the named server, EXCEPT the
transfer (the API image has no rsync / ssh: it runs on the host).  Bundles go to ``/srv/motres/identity/region_bundles``
(0700, a host bind mount: it survives a blue/green switch and the host can rsync it; B = that folder/<bundle id>):

    # 1. source (EU, container): what would move, what blocks it            (dry run)
    python scripts/migrate_account_region.py export --email A
    # 2. source (container): freeze the account's writes and build the signed bundle
    python scripts/migrate_account_region.py export --email A --apply
    # 3. source HOST: copy the bundle to the target over WireGuard (the dry run in the container prints the command)
    python scripts/migrate_account_region.py transfer --bundle B --to 10.77.0.2:/srv/motres/identity/region_bundles
    rsync -a --checksum --chmod=D700,F600 -e ssh B/ 10.77.0.2:/srv/motres/identity/region_bundles/<id>/
    # 4. target (US, container): verify hashes + signature, install, create the account; deletes the US bundle copy
    python scripts/migrate_account_region.py import --bundle B --apply
    # 5. source (container): re-hash the source, compare password, check the receipt, guard on, revoke keys, unfreeze
    python scripts/migrate_account_region.py finish --email A --bundle B --apply
    # abort before step 5 (lifts the freeze only)
    python scripts/migrate_account_region.py unfreeze --email A --apply
    # LATER, after the target's backup has run once: delete the source copy and the bundle
    python scripts/migrate_account_region.py delete-source --email A --bundle B --target-backup-confirmed --apply

``verify --bundle <dir>`` checks a bundle against its manifest on either host.  ``--json`` prints the raw report.
Exit code 0 = done (or dry run clean), 1 = refused / blocked, 2 = usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print(rep: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(rep, indent=1, sort_keys=True, default=str))
        return
    step = rep.get("step", "verify")
    print("%s %s" % (step.upper(), "(DRY RUN)" if rep.get("dry_run", False) else "(APPLIED)" if "dry_run" in rep else ""))
    for k in ("email", "bundle_id", "bundle_dir", "source_region", "target_region", "root_sha256", "file_count", "bytes",
              "to_install", "already_identical", "installed", "delete", "deleted", "ok"):
        if k in rep and rep[k] not in (None, ""):
            print("  %-18s %s" % (k, rep[k]))
    for k in ("projects", "dies", "cases", "items", "pinned_dies", "will", "command"):
        if rep.get(k):
            print("  %-18s %s" % (k, ", ".join(str(x) for x in rep[k])))
    for k in ("merged", "verify", "target_receipt", "receipt"):
        if rep.get(k):
            print("  %-18s %s" % (k, json.dumps(rep[k], sort_keys=True, default=str)))
    for w in rep.get("warnings") or []:
        print("  WARNING  " + w)
    for b in rep.get("blockers") or []:
        print("  BLOCKED  " + b)
    for c in (rep.get("conflicts") or [])[:20]:
        print("  CONFLICT " + c)
    for c in (rep.get("changed") or [])[:20]:
        print("  CHANGED  " + c + " (since the export)")
    for d, why in (rep.get("kept_dies") or {}).items():
        print("  KEEP DIE %s: %s" % (d, "; ".join(why)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, email=False, bundle=False):
        p.add_argument("--apply", action="store_true", help="do it (default: dry run)")
        p.add_argument("--json", action="store_true", help="print the report as JSON")
        p.add_argument("--actor", default="migrate_account_region.py", help="who runs it (audit lines)")
        if email:
            p.add_argument("--email", required=True)
        if bundle:
            p.add_argument("--bundle", required=True, type=Path, help="the bundle folder (<out>/<bundle id>)")
        return p

    ex = common(sub.add_parser("export", help="plan / freeze + build the bundle (source)"), email=True)
    ex.add_argument("--out", type=Path, default=None,
                    help="folder that receives <bundle id>/ (default /srv/motres/identity/region_bundles: a host mount)")
    ex.add_argument("--to", default=None, help="target region (default: the configured peer)")
    ex.add_argument("--die", action="append", default=[], help="an extra die that belongs to the account (repeat)")
    ex.add_argument("--allow-shared-dies", action="store_true", help="move dies also linked by projects that stay")
    ex.add_argument("--allow-open-dies", action="store_true", help="move dies open in the public catalog")
    ex.add_argument("--allow-active-jobs", action="store_true", help="export although jobs of the account run")
    ex.add_argument("--allow-project-members", action="store_true",
                    help="move projects although other non-staff members work in them (owner's decision)")
    common(sub.add_parser("verify", help="check a bundle against its manifest"), bundle=True)
    tr = common(sub.add_parser("transfer", help="rsync the bundle to the target (source)"), bundle=True)
    tr.add_argument("--to", required=True, help="host:/path on the target, over WireGuard")
    tr.add_argument("--ssh", default="ssh", help="the ssh command rsync uses (-e)")
    im = common(sub.add_parser("import", help="verify + install the bundle (target)"), bundle=True)
    im.add_argument("--keep-bundle", action="store_true", help="keep the bundle folder after a done import")
    common(sub.add_parser("finish", help="compare the receipt, guard on, unfreeze (source)"), email=True, bundle=True)
    common(sub.add_parser("unfreeze", help="abort before finish: lift the freeze (source)"), email=True)
    ds = common(sub.add_parser("delete-source", help="LATER: delete the source copy (source)"), email=True, bundle=True)
    ds.add_argument("--target-backup-confirmed", action="store_true",
                    help="the target region's backup of the account has run once")
    a = ap.parse_args(argv)

    from motor_ai_sim.regions import migrate as MG
    try:
        if a.cmd == "export":
            rep = MG.export(a.email, a.out, apply=a.apply, actor=a.actor, target_region=a.to,
                            extra_dies=a.die, allow_shared_dies=a.allow_shared_dies,
                            allow_open_dies=a.allow_open_dies, allow_active_jobs=a.allow_active_jobs,
                            allow_project_members=a.allow_project_members)
            if not a.json:
                rep.pop("files", None)
        elif a.cmd == "verify":
            rep = MG.verify_bundle(a.bundle)
        elif a.cmd == "transfer":
            rep = MG.transfer(a.bundle, a.to, apply=a.apply, ssh=a.ssh)
        elif a.cmd == "import":
            rep = MG.import_bundle(a.bundle, apply=a.apply, actor=a.actor, keep_bundle=a.keep_bundle)
        elif a.cmd == "finish":
            rep = MG.finish(a.email, a.bundle, apply=a.apply, actor=a.actor)
        elif a.cmd == "unfreeze":
            rep = MG.unfreeze(a.email, apply=a.apply, actor=a.actor)
        else:
            rep = MG.delete_source(a.email, a.bundle, apply=a.apply, target_backup_confirmed=a.target_backup_confirmed,
                                   actor=a.actor)
    except MG.MigrationError as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 1
    _print(rep, a.json)
    if rep.get("blockers") or rep.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
