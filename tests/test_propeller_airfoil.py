"""P0 of the propeller module: airfoil shapes, the terms record, the manufacturing profile and the manufactured section."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from motor_ai_sim import propeller as pp
from motor_ai_sim import propeller_airfoil as af


def test_naca4_matches_the_published_formula_values():
    x, yu, yl = af.naca4("0012", 201)
    props = af.section_props(x, yu, yl)
    assert props.t_c == pytest.approx(0.12, abs=2e-4)
    assert props.camber == pytest.approx(0.0, abs=1e-9)
    assert props.te_gap_c == pytest.approx(0.0, abs=2e-4)                  # closed trailing edge
    assert props.le_radius_c == pytest.approx(1.1019 * 0.12 ** 2, rel=0.03)  # r_le = 1.1019 t^2
    i = int(np.argmin(np.abs(x - 0.3)))
    xi = x[i]
    assert yu[i] == pytest.approx(0.6 * (0.2969 * math.sqrt(xi) - 0.1260 * xi - 0.3516 * xi ** 2 + 0.2843 * xi ** 3 - 0.1036 * xi ** 4), rel=1e-9)
    x2, yu2, yl2 = af.naca4("4412", 201)
    p2 = af.section_props(x2, yu2, yl2)
    assert p2.camber == pytest.approx(0.04, abs=2e-3) and p2.t_c == pytest.approx(0.12, abs=2e-3)
    xo, yuo, ylo = af.naca4("0012", 201, closed_te=False)
    assert af.section_props(xo, yuo, ylo).te_gap_c > 0.001                 # open coefficient leaves a gap


def test_bad_codes_are_refused():
    for bad in ("12", "abcd", "4400", ""):
        with pytest.raises(pp.PropellerDataError):
            af.naca4(bad)
    with pytest.raises(pp.PropellerDataError):
        af.generated("clarky")


def _selig_text(code="2412", n=60):
    x, yu, yl = af.naca4(code, n)
    pts = list(zip(x[::-1], yu[::-1])) + list(zip(x[1:], yl[1:]))
    return "TEST FOIL\n" + "\n".join("%.6f %.6f" % p for p in pts) + "\n"


def test_selig_parser_round_trip_and_refusals():
    name, x, yu, yl = af.parse_selig(_selig_text())
    assert name == "TEST FOIL"
    p = af.section_props(x, yu, yl)
    assert p.t_c == pytest.approx(0.12, abs=3e-3) and p.camber == pytest.approx(0.02, abs=2e-3)
    for text in ("x\n1 0\n", "n\n" + "\n".join("%d a" % i for i in range(20)),
                 _selig_text().replace("\n", "\n0.5 nan\n", 1)):
        with pytest.raises(pp.PropellerDataError):
            af.parse_selig(text)
    # x not increasing on a surface
    lines = _selig_text().splitlines()
    lines[5], lines[6] = lines[6], lines[5]
    with pytest.raises(pp.PropellerDataError):
        af.parse_selig("\n".join(lines))


def test_library_terms_decide_commercial_use(tmp_path):
    fam = tmp_path / "famA"
    fam.mkdir()
    (fam / "foil1.dat").write_text(_selig_text())
    (fam / "bad.dat").write_text("junk\n")
    lib = af.load_library(tmp_path)
    assert list(lib) == ["famA-foil1"] and lib["famA-foil1"].terms_status == "unknown" and not lib["famA-foil1"].commercial_use_ok
    (fam / "_terms.yaml").write_text(yaml.safe_dump({"terms_status": "permitted", "source_url": "u"}))
    assert not af.load_library(tmp_path)["famA-foil1"].commercial_use_ok          # permitted but nobody confirmed
    (fam / "_terms.yaml").write_text(yaml.safe_dump({"terms_status": "permitted", "source_url": "u", "confirmed_by": "owner"}))
    assert af.load_library(tmp_path)["famA-foil1"].commercial_use_ok
    (fam / "_terms.yaml").write_text(yaml.safe_dump({"terms_status": "something", "confirmed_by": "owner"}))
    assert af.load_library(tmp_path)["famA-foil1"].terms_status == "unknown"
    assert af.generated("naca4-4412").commercial_use_ok
    assert af.get_airfoil("naca4-2412", {}).id == "naca4-2412"
    assert af.load_library(tmp_path / "nowhere") == {}
    assert af.load_library() == {}                                                  # nothing ships yet


def _profile(**vals):
    lim = {k: {"value": v, "source": "test", "confirmed_by": "tester"} for k, v in vals.items()}
    return af.parse_manufacturing({"process": "injection", "limits": lim})


def test_profile_parsing_and_flags():
    assert af.parse_manufacturing(None).flags() == [af.FLAG_UNCONFIRMED]            # nothing set: flagged
    p = af.parse_manufacturing({"limits": {"te_thickness_min_mm": {"value": 0.4, "source": "placeholder"}}})
    assert p.unconfirmed() == ["te_thickness_min_mm"] and p.value("te_thickness_min_mm") == 0.4
    assert p.limits["te_thickness_min_mm"].unit == "mm" and not p.limits["te_thickness_min_mm"].confirmed
    full = {n: {"value": 1.0, "source": "s", "confirmed_by": "c"} for n in af.LIMIT_UNITS}
    assert af.parse_manufacturing({"limits": full}).flags() == []
    for bad in ({"limits": {"nope": {"value": 1}}}, {"limits": {"wall_min_mm": {"value": -1, "source": "s"}}},
                {"limits": {"wall_min_mm": {"value": 1}}}, {"limits": {"wall_min_mm": {"value": True, "source": "s"}}},
                {"limits": 3}, 5):
        with pytest.raises(pp.PropellerDataError):
            af.parse_manufacturing(bad)


def test_trailing_edge_is_thickened_to_the_minimum_only():
    x, yu, yl = af.naca4("4410", 201)
    s = af.manufactured_section(x, yu, yl, 20.0, _profile(te_thickness_min_mm=0.4))
    assert s.made.te_gap_c * 20.0 == pytest.approx(0.4, rel=1e-6)
    assert s.active == ("te_thickness_min_mm",)
    # the wedge adds x * gap of thickness: at most the added gap, and the camber line is untouched
    assert 0.0 <= s.made.t_c - s.ideal.t_c <= 0.4 / 20.0 and s.made.camber == pytest.approx(s.ideal.camber, abs=1e-6)
    # already thick enough: untouched
    s2 = af.manufactured_section(x, yu, yl, 20.0, _profile(te_thickness_min_mm=0.0))
    assert s2.active == () and np.allclose(s2.yu, yu)


def test_thickness_limits_scale_about_the_camber_line_and_the_governing_one_is_named():
    x, yu, yl = af.naca4("4406", 201)                                              # 6 % thick
    s = af.manufactured_section(x, yu, yl, 15.0, _profile(wall_min_mm=1.2, thickness_ratio_min=0.07))
    # wall 1.2 mm / 15 mm = 8 % > 7 % > 6 %: the wall limit governs
    assert s.notes["thickness_governing_limit"] == "wall_min_mm"
    assert s.made.t_c == pytest.approx(0.08, abs=1e-3)
    assert s.made.camber == pytest.approx(s.ideal.camber, abs=1e-4)
    s2 = af.manufactured_section(x, yu, yl, 15.0, _profile(le_radius_min_mm=0.3))
    assert s2.notes["thickness_governing_limit"] == "le_radius_min_mm"
    assert s2.made.le_radius_c * 15.0 >= 0.3 * 0.97                                 # leading edge radius reaches the minimum
    s3 = af.manufactured_section(x, yu, yl, 15.0, _profile(wall_min_mm=0.1))
    assert s3.active == () and "thickness_scale" not in s3.notes


def test_unset_profile_changes_nothing_and_is_flagged():
    x, yu, yl = af.naca4("4412", 101)
    s = af.manufactured_section(x, yu, yl, 20.0)
    assert np.allclose(s.yu, yu) and np.allclose(s.yl, yl) and s.active == () and af.FLAG_UNCONFIRMED in s.flags
    with pytest.raises(pp.PropellerDataError):
        af.manufactured_section(x, yu, yl, 0.0)


def test_blade_checks_twist_release_and_thickness_along_the_blade():
    prof = _profile(twist_release_max_deg=30.0, wall_min_mm=1.0, thickness_ratio_min=0.06)
    ok = af.check_blade([40, 30, 20], [25, 22, 15], [0.10, 0.08, 0.07], prof)
    assert ok == []
    bad = af.check_blade([50, 30, 10], [25, 22, 10], [0.10, 0.08, 0.05], prof)
    kinds = sorted((b["limit"], b["station"]) for b in bad)
    assert kinds == [("thickness_ratio_min", 2), ("twist_release_max_deg", None), ("wall_min_mm", 2)]
    assert af.check_blade([50, 10], [10, 10], [0.01, 0.01], af.EMPTY_PROFILE) == []   # no limit set: nothing to violate


def test_tolerance_perturbations():
    assert af.tolerance_perturbations(af.EMPTY_PROFILE) == [{"d_chord_mm": 0.0, "d_twist_deg": 0.0, "d_thickness_mm": 0.0, "d_te_mm": 0.0}]
    p = _profile(tol_chord_mm=0.2, tol_twist_deg=1.0, tol_thickness_mm=0.1, tol_te_mm=0.05)
    pert = af.tolerance_perturbations(p)
    assert len(pert) == 1 + 8 and pert[0]["d_chord_mm"] == 0.0 and pert[0]["d_te_mm"] == 0.0
    assert all(d["d_te_mm"] == -0.05 for d in pert[1:])
    assert {d["d_chord_mm"] for d in pert[1:]} == {-0.2, 0.2} and {d["d_twist_deg"] for d in pert[1:]} == {-1.0, 1.0}
    only = af.tolerance_perturbations(_profile(tol_twist_deg=1.0))
    assert len(only) == 3 and {d["d_twist_deg"] for d in only} == {0.0, -1.0, 1.0}
