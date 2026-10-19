# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Tests reach the PRIVATE data repository through this module and SKIP when it is absent.

Closed motors and all 3-D models live only in eMotres/motor-ai-sim-private (owner decision 2026-10-19).  Point
``MOTOR_AI_SIM_PRIVATE_DATA`` at a checkout of it to run the tests that need those files; without it they skip, never fail.

* ``private_file(*parts)``       the file in the private checkout, or ``None``
* ``require_private(*parts)``    the same, or skip the whole module
* ``closed_preset(name)``        a preset of a CLOSED motor (``config/motor_presets_closed.json`` of the private checkout), or
                                 of the public ``config/motor_presets.json``; ``None`` when neither has it
* ``require_closed_preset(name)``  the same, or skip the whole module
* ``require_cad_reference()``    the ``cad/reference`` folder (Fusion profiles, STEP exports), or skip the whole module
* ``cad_reference(*parts)``      a file below it
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

REPO = Path(__file__).resolve().parents[1]
SKIP_NOTE = "needs the private data repository (set MOTOR_AI_SIM_PRIVATE_DATA to a checkout of eMotres/motor-ai-sim-private)"


def private_root() -> Optional[Path]:
    from motor_ai_sim.private_data import private_root as _root
    return _root()


def private_file(*parts: str) -> Optional[Path]:
    from motor_ai_sim.private_data import private_path
    return private_path(*parts)


def require_private(*parts: str) -> Path:
    p = private_file(*parts)
    if p is None:
        pytest.skip(f"{SKIP_NOTE}: {'/'.join(parts)}", allow_module_level=True)
    return p


def closed_preset(name: str) -> Optional[dict]:
    pub = json.loads((REPO / "config" / "motor_presets.json").read_text(encoding="utf-8"))
    if name in pub:
        return pub[name]
    p = private_file("config", "motor_presets_closed.json")
    if p is not None:
        return json.loads(p.read_text(encoding="utf-8")).get(name)
    return None


def require_closed_preset(name: str) -> dict:
    d = closed_preset(name)
    if d is None:
        pytest.skip(f"{SKIP_NOTE}: preset {name!r}", allow_module_level=True)
    return d


def closed_die_dir(name: str) -> Optional[Path]:
    """``config/dies/<name>`` in the public tree, else in the private checkout, else ``None``."""
    pub = REPO / "config" / "dies" / name
    if pub.is_dir():
        return pub
    return private_file("config", "dies", name)


def require_closed_die(name: str) -> Path:
    d = closed_die_dir(name)
    if d is None:
        pytest.skip(f"{SKIP_NOTE}: die {name!r}", allow_module_level=True)
    return d


def cad_reference_dir() -> Optional[Path]:
    from motor_ai_sim.cad import store
    d = store.find_reference_dir()
    return Path(d) if d else None


def require_cad_reference() -> Path:
    d = cad_reference_dir()
    if d is None:
        pytest.skip(f"{SKIP_NOTE} (cad/reference)", allow_module_level=True)
    return d


def cad_reference(*parts: str) -> Path:
    d = require_cad_reference()
    return d.joinpath(*parts)


# ── function-level variants: skip the TEST (not the module) that needs the private file ─────────────────────────────

def need_private(*parts: str) -> Path:
    """The file in the private checkout; skips the running test when it is absent."""
    p = private_file(*parts)
    if p is None:
        pytest.skip(f"{SKIP_NOTE}: {'/'.join(parts)}")
    return p


def need_fixture(*parts: str) -> Path:
    """``tests/fixtures/<parts>`` of the PRIVATE checkout (BOMs, CAD BOM dumps of the closed motors)."""
    return need_private("tests", "fixtures", *parts)


def need_cad_reference(*parts: str) -> Path:
    """``cad/reference/<parts>`` (Fusion profile, STEP export); skips the running test when the files are absent."""
    d = cad_reference_dir()
    if d is None:
        pytest.skip(f"{SKIP_NOTE} (cad/reference)")
    p = d.joinpath(*parts)
    if not p.exists():
        pytest.skip(f"{SKIP_NOTE}: cad/reference/{'/'.join(parts)}")
    return p


def need_closed_preset(name: str) -> dict:
    d = closed_preset(name)
    if d is None:
        pytest.skip(f"{SKIP_NOTE}: preset {name!r}")
    return d


def case(label: str, geo):
    """A parametrized case ``(label, geo)`` with ``label`` as its id."""
    return pytest.param(label, geo, id=label)


def closed_preset_case(label: str, name: str, key: str = "geometry"):
    """``case(label, preset[key])``; SKIPPED (with the reason) when the preset is private and the private checkout is absent."""
    d = closed_preset(name)
    if d is None:
        return pytest.param(label, None, marks=pytest.mark.skip(reason=f"{SKIP_NOTE}: preset {name!r}"), id=label)
    return case(label, d[key])


def need_closed_die_dir(name: str) -> Path:
    d = closed_die_dir(name)
    if d is None:
        pytest.skip(f"{SKIP_NOTE}: die {name!r}")
    return d
