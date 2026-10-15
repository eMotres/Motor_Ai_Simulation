# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The SIMPLIFIED model generator (owner 2026-10-15) and its patent hygiene.

The model carries no AeroStator Core internals: stator + rotor + magnets are ONE monolithic solid (filled slots, pockets, magnet
shapes, cut-outs), every coil ONE thick turn, every bearing ONE ring.  The geometry tests run a synthetic slotted / pocketed motor
through the real builders inside FreeCAD (skipped when freecadcmd is not installed); the rest - the wiring of the build, the
manifest, the mass on the metadata - needs no FreeCAD."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from motor_ai_sim.cad import build, runner, store

CAD_DIR = store.CAD_DIR
if CAD_DIR not in sys.path:
    sys.path.insert(0, CAD_DIR)
HERE = Path(__file__).resolve().parent
SCRIPTS = Path(CAD_DIR) / "fc_scripts"


def _freecad():
    try:
        return runner.find_freecadcmd()
    except FileNotFoundError:
        return None


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    fc = _freecad()
    if not fc:
        pytest.skip("freecadcmd not installed")
    out = tmp_path_factory.mktemp("simpl") / "r.json"
    code, log, st = runner.run_script(str(HERE / "_simplified_fc_check.py"), dict(FC_CAD_ROOT=CAD_DIR, FC_REPORT=str(out)),
                                      freecadcmd=fc, timeout=600)
    assert code == 0, log[-2000:]
    return json.load(open(out))


# -- patent hygiene: the geometry -----------------------------------------------------------------------------------------------

def test_the_stator_rotor_magnets_are_one_filled_solid_with_envelope_faces_only(synthetic):
    r = synthetic
    assert r["mono_solids"] == 1 and r["mono_shells"] == 1                       # one solid, ONE shell: no internal cavity
    assert r["bad"] == []                                                       # the export's own gate passes
    # the real parts have dozens of faces (12 slots, 12 cut-outs, 8 pockets, 8 magnets ...); the monolith has the envelope only
    assert r["stator_faces"] > 90 and r["rotor_faces"] > 40
    assert r["mono_faces"] == 3 + r["bore_edges"] and r["mono_faces"] <= 12     # outer cylinder + 2 ends + the bore (2 keys)
    assert set(r["face_types"]) <= {"Cylinder", "Plane"}
    assert max(r["radii"]) == pytest.approx(40.0)                               # the outer circle, nothing between it and the bore
    assert all(x == pytest.approx(40.0) or x <= 15.0 + 1e-6 for x in r["radii"])  # outer circle or the bore (radius 15)
    # no slot, pocket, notch or magnet shape is recoverable: the volume IS the envelope volume
    assert r["mono_volume"] == pytest.approx(r["envelope_volume"], rel=1e-6)
    assert not r["stepped"]
    assert r["mono_bbox"][1] == pytest.approx(40.0) and r["mono_bbox"][2:] == [pytest.approx(-5.0), pytest.approx(5.0)]


def test_the_hygiene_gate_refuses_an_unfilled_stator_and_a_coil_of_single_turns(synthetic):
    assert synthetic["bad_slotted"] and any("faces" in b or "volume" in b for b in synthetic["bad_slotted"])
    assert synthetic["bad_turns"] and any("coil block" in b for b in synthetic["bad_turns"])


def test_a_coil_is_one_thick_turn_from_the_bottom_turn_to_the_top(synthetic):
    r = synthetic
    assert r["block_solids"] == 1 and r["block_faces"] <= 20 < r["coil_faces"]       # 6 separate turns have far more faces
    y0, y1 = r["block_bbox"]
    assert y1 == pytest.approx(38.0) and y0 == pytest.approx(38.0 - 0.3 - 5 * 0.37)  # inner to outer turn, the whole winding height
    assert r["block_volume"] > r["turns_volume"]                                   # the gaps between the turns are filled


# -- the build wiring ------------------------------------------------------------------------------------------------------------

def test_the_build_writes_the_simplified_files_for_every_config_and_lists_them():
    src = Path(build.__file__).read_text(encoding="utf-8")
    assert '_fc("export_simplified.py"' in src and "--no-simplified" in src
    assert 'mid + "_simplified.step", mid + "_simplified.glb", mid + "_simplified.json"' in src      # in the manifest with their sha256
    assert 'm["simplified"] = simp' in src
    # for EVERY config: taken whatever the variant (full or frameless); only a failure of the script skips it
    assert "if not args.no_simplified and not args.no_step:" in src
    assert "cad_variant" not in src.split("def _simplified(")[1].split("def _simplified_mass(")[0]


def test_the_total_mass_goes_onto_the_metadata(tmp_path):
    path = tmp_path / "m_simplified.json"
    path.write_text(json.dumps({"cad_variant": "frameless", "mass": None}))
    build._simplified_mass(str(tmp_path), "m", {"ok": True, "mass_g": 395.6})                      # noqa: SLF001
    assert json.load(open(path))["mass"] == {"total_g": 395.6, "scope": "frameless kit",
                                            "basis": "bill of materials total of the real model (model solids x densities)"}
    path.write_text(json.dumps({"cad_variant": "full", "mass": None}))
    build._simplified_mass(str(tmp_path), "m", {"ok": True, "mass_g": 501.4})                      # noqa: SLF001
    assert json.load(open(path))["mass"]["scope"] == "full motor"
    path.write_text(json.dumps({"cad_variant": "full", "mass": None}))
    build._simplified_mass(str(tmp_path), "m", {"ok": False})                                      # noqa: SLF001
    assert json.load(open(path))["mass"] is None                                                   # never a made-up mass


def test_the_script_keeps_what_a_customer_integrates_and_drops_the_rest():
    s = (SCRIPTS / "export_simplified.py").read_text(encoding="utf-8")
    assert 'if name.startswith("Bearing_618_8_"):' in s and 'S.bearing_ring(outer, g("bearing_d"))' in s     # one ring per bearing
    assert "PhaseBoard_pins" in s and "terminals" in s and "mounting" in s and "mass=None" in s
    assert "S.check(monolith, rep, coil_blocks)" in s
    assert "simplified model refused" in s                                                           # a failed gate writes nothing


def test_the_module_states_its_limits():
    from motor_templates.segmented_flatwire import simplified as S
    assert S.COIL_MAX_FACES <= 60 and S.VOLUME_TOL <= 1e-5 and S.MONOLITH_BASE_FACES == 3
