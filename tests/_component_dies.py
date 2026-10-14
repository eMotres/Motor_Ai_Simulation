# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""A stand-in die catalog for the component-card tests.

The motor cards take their mass from the app's own mass model on the passport's die + configuration.  The
real dies of the passports (``CIANO14 40 new``) live in the private catalog, so the tests build a small catalog
in a temp folder: a die named like the passport's, with the geometry and materials of the repository's tracked
``CIANO14 40_12`` die (the older 40 mm build).  The numbers are therefore the app's numbers for THAT geometry;
the tests compare them with ``masses.compute_masses`` called directly, not with a constant.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml

REPO = Path(__file__).resolve().parents[1]
TRACKED = REPO / "config" / "dies" / "CIANO14 40_12"
STAND_IN_DIE = "CIANO14 40 new"


def make_dies(root: Path) -> Path:
    """``root/dies/<STAND_IN_DIE>/{die.yaml, L12.yaml, L20.yaml}``; returns ``root/dies``."""
    d = root / "dies" / STAND_IN_DIE
    d.mkdir(parents=True)
    die = yaml.safe_load((TRACKED / "die.yaml").read_text(encoding="utf-8"))
    die = {"name": STAND_IN_DIE, "geometry": die["geometry"]}
    (d / "die.yaml").write_text(yaml.safe_dump(die), encoding="utf-8")
    cfg = yaml.safe_load((TRACKED / "L12.yaml").read_text(encoding="utf-8"))
    base: Dict = {"name": "L12", "die": STAND_IN_DIE, "geometry_overrides": dict(cfg["geometry_overrides"]),
                  "materials": {"magnet": "F52SH_120C", "stator_core": "20SW1200", "rotor_core": "20SW1200"},
                  "winding": cfg.get("winding") or {}}
    (d / "L12.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    l20 = dict(base, name="L20", geometry_overrides=dict(base["geometry_overrides"], motor_length=20))
    (d / "L20.yaml").write_text(yaml.safe_dump(l20), encoding="utf-8")
    return root / "dies"
