"""CIANO14 40: Shaft, Prop_fix, Stator_holder and Stator_holder_back are the real Fusion v19 bodies (template A.7.7, owner 2026-10-19: everything in
production comes ONLY from Fusion).  The STEP files are private data (eMotres/motor-ai-sim-private, cad/reference/fusion_parts/CIANO14_40/); the
tests skip cleanly without that checkout.  The FreeCAD side (import, shift, stretch, gate, fit) is exercised by the local build, see
docs/CAD_GENERATOR.md "CIANO14 40 Fusion parts"; the cut-and-shift arithmetic is run here inside freecadcmd when it is installed."""
import json
import os
import sys

import pytest

from motor_ai_sim.cad import runner, store
from tests.private_fixtures import need_cad_reference

if store.CAD_DIR not in sys.path:
    sys.path.insert(0, store.CAD_DIR)

TPL = os.path.join(store.CAD_DIR, "motor_templates", "segmented_flatwire")
HERE = os.path.dirname(os.path.abspath(__file__))
PARTS = ("Shaft", "Prop_fix", "Stator_holder", "Stator_holder_back")


@pytest.fixture(scope="module")
def prof():
    p = need_cad_reference("CIANO14_40_12.json")
    return json.load(open(p, encoding="utf-8"))["profile"]


@pytest.fixture(scope="module")
def manifest():
    p = need_cad_reference("fusion_parts", "CIANO14_40", "fusion_export_manifest.json")
    return json.load(open(p, encoding="utf-8"))


def test_template_version_is_a77_or_later():
    src = open(os.path.join(TPL, "__init__.py"), encoding="utf-8").read()
    assert 'TEMPLATE_VERSION = "A.7.9"' in src and "A.7.7 (2026-10-19)" in src      # A.7.8 (CIANO28 150_35 from Fusion) keeps the 40 mm parts


def test_the_profile_names_the_four_fusion_parts(prof, manifest):
    fp = prof["fusion_parts"]
    assert set(fp["files"]) == set(fp["volumes_mm3"]) == set(PARTS) == set(manifest["parts"])
    assert fp["dir"] == "CIANO14_40" and fp["dies"] == ["CIANO14 40 new"]
    for n in PARTS:                                               # profile = the export record of the Fusion document
        assert fp["files"][n] == manifest["parts"][n]["file"]
        assert fp["volumes_mm3"][n] == pytest.approx(manifest["parts"][n]["volume_mm3"], abs=1e-6)
    assert fp["ref_motor_length"] == manifest["motor_length_mm"]  # the length the design had when the STEP files were written


def test_the_step_files_are_in_the_private_data(prof, manifest):
    for n in PARTS:
        p = need_cad_reference("fusion_parts", "CIANO14_40", prof["fusion_parts"]["files"][n])
        assert p.stat().st_size > 10_000
        assert b"ISO-10303-21" in p.read_bytes()[:64]


def test_other_dies_that_share_the_reference_keep_the_template_parts(prof):
    p = json.loads(json.dumps(prof))
    assert store.restrict_fusion_parts(p, "CIANO14 40 new") is True and "fusion_parts" in p
    q = json.loads(json.dumps(prof))
    assert store.restrict_fusion_parts(q, "CIANO14 30_10") is False and "fusion_parts" not in q       # the 30 mm die falls back to this reference
    assert store.restrict_fusion_parts({"x": 1}, "any") is False                                         # a profile without Fusion parts is untouched


def test_a_missing_step_refuses_the_build_without_a_fallback():
    src = open(os.path.join(TPL, "fusion_parts.py"), encoding="utf-8").read()
    assert "not found" in src and "raise RuntimeError" in src and "fall-back" in src
    m = open(os.path.join(TPL, "mechanics.py"), encoding="utf-8").read()
    assert "fusion_body(\"Shaft\")" in m and "fusion_body(\"Prop_fix\")" in m


def test_the_stretch_is_a_cut_and_shift_of_the_constant_section(prof, tmp_path):
    """L12 -> L20: +8 mm in the dia 9.9 rotor-seat section; volume = Fusion volume + 8 x section; cut back = the Fusion shaft (freecadcmd)."""
    step = need_cad_reference("fusion_parts", "CIANO14_40", prof["fusion_parts"]["files"]["Shaft"])
    try:
        fc = runner.find_freecadcmd()
    except FileNotFoundError:
        pytest.skip("freecadcmd not installed")
    code, log, st = runner.run_script(os.path.join(HERE, "_fusion_stretch_check.py"),
                                      dict(FC_CAD_ROOT=store.CAD_DIR, FC_STEP=str(step), FC_REPORT=str(tmp_path / "fusion_stretch.json")),
                                      freecadcmd=fc, timeout=600)
    assert code == 0, log
    r = json.load(open(tmp_path / "fusion_stretch.json", encoding="utf-8"))
    assert r["solids"] == 1 and r["valid"]
    assert r["longer_minus_fusion"] == pytest.approx(8 * r["section_area"], rel=2e-5)       # a plain STEP read carries Fusion's 1e-5 tolerance noise (the build imports it canonical)
    assert r["z_max_shift"] == pytest.approx(4.0, abs=1e-6) and r["z_min_shift"] == pytest.approx(-4.0, abs=1e-6)
    assert r["round_trip_volume_error"] < 1e-5 * r["fusion_volume"] and r["round_trip_extent_error"] < 1e-6
    assert r["faces_back"] == r["faces_fusion"]                    # the cylinders stay one face each after the cut-and-shift (removeSplitter)
