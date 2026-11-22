"""Dry list (READ ONLY): configurations of a catalog that still carry a rotor-shape override (magnet_fill_up / _down / _radius) in
``geometry_overrides``.  Since the review of #765 these keys are no longer motor keys: a configuration cannot have its own (one
die = one rotor).  The list is what has to be cleared (``PATCH /api/family/config/{die}/{cfg}/motor_keys`` with the key set to null)
or turned into a die change by the structure session.

    python scripts/config_shape_override_dry_list.py --root /srv/motres/shared/dies
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

KEYS = ("magnet_fill_up", "magnet_fill_down", "magnet_fill_radius")


def scan(root: Path):
    rows = []
    for die_dir in sorted(p for p in Path(root).iterdir() if p.is_dir() and (p / "die.yaml").is_file()):
        try:
            die_geo = (yaml.safe_load((die_dir / "die.yaml").read_text(encoding="utf-8")) or {}).get("geometry") or {}
        except Exception as exc:                                        # noqa: BLE001
            rows.append({"die": die_dir.name, "config": "-", "error": str(exc)})
            continue
        for cf in sorted(die_dir.glob("*.yaml")):
            if cf.name == "die.yaml":
                continue
            try:
                ov = (yaml.safe_load(cf.read_text(encoding="utf-8")) or {}).get("geometry_overrides") or {}
            except Exception as exc:                                    # noqa: BLE001
                rows.append({"die": die_dir.name, "config": cf.stem, "error": str(exc)})
                continue
            found = {k: {"override": ov[k], "die": die_geo.get(k)} for k in KEYS if ov.get(k) is not None}
            if found:
                rows.append({"die": die_dir.name, "config": cf.stem, "keys": found})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", required=True, type=Path)
    a = ap.parse_args(argv)
    rows = scan(a.root)
    for r in rows:
        print(r["die"], "/", r["config"], r.get("error") or r["keys"])
    print("%d configuration(s) carry a rotor-shape override" % sum(1 for r in rows if "keys" in r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
