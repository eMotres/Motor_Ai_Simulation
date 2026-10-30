# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""Fusion's parameter definitions in the builder, and the translation of values
stored under the old ones (owner 2026-10-30: «почему у нас геометрия с Fusion не
совпадает, вроде совершенно одинаковый алгоритм построения»).

* the stator half leg's outer side is tooth/2 + coil_w + tooth2_width — measured
  on the built polygon against a hand computation;
* the magnet: bottom corner at fill_down, knee along the half-pole line, upper
  side aimed at the OD point at fill_up, cut by the top arc — hand-computed
  corners, and the built polygon carries them;
* the translation keeps the old outline (exact where it can be) and the
  migration script rewrites a die / configuration tree once, dry-run by default.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from shapely.geometry import LineString

import motor_ai_sim.cadquery_geometry as cg
from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.geometry import fusion_definitions as fd
from tests.test_stator_outline_symmetry import _BASE

REPO = Path(__file__).resolve().parents[1]


# ── the stator half leg ──────────────────────────────────────────────────────

def test_leg_outer_x_is_tooth_half_plus_coil_w_plus_tooth2():
    # Fusion: x = teeth_w/2 + coil_w + tooth2_w, coil_w = wire_w + 2·ins_w + wire_dist_x
    assert cg._stator_leg_outer_x(13.2, 6.5 + 2 * 0.15 + 0.1, 5.8) == pytest.approx(19.3)


def test_built_stator_leg_sits_where_the_hand_computation_says():
    """Ø30 12s: tooth 2.3, wire 2.0, insulation 0.05, wire_spacing_x 0.1,
    tooth2 1.7 -> slot wall at 1.15 + 2.2 = 3.35, leg outer side at 5.05.  The
    wound tooth is on +Y.  The leg's outer side starts where the notch arc
    (cut_width 1.8, sector half-angle 30°) is tangent to it: notch radius
    ((8.9 + 1.8)·sin30° − 5.05)/(1 − sin30°) = 0.6, centre on r = 11.3 at 30°,
    so the tangent point is (5.05, 11.3·cos30°)."""
    m = CadQueryMotor()
    m.set_parameters(dict(_BASE))
    st = m.get_2d_polygons()["stator"]
    cut = st.intersection(LineString([(0.0, 11.0), (4.5, 11.0)]))
    xs = sorted((round(min(g.coords[0][0], g.coords[-1][0]), 6),
                 round(max(g.coords[0][0], g.coords[-1][0]), 6))
                for g in getattr(cut, "geoms", [cut]))
    assert xs[0] == (0.0, pytest.approx(1.15, abs=1e-6))          # the wound tooth
    assert xs[1][0] == pytest.approx(3.35, abs=1e-6)              # slot wall = leg inner side
    want = (5.05, 11.3 * math.cos(math.radians(30.0)))
    verts = [v for ring in [st.exterior, *st.interiors] for v in ring.coords]
    assert min(math.dist(want, v) for v in verts) < 1e-6


# ── the magnet ───────────────────────────────────────────────────────────────

def test_magnet_corners_by_hand():
    """4 poles (pitch 90°, half 45°), bottom r 10 at fill_down 0.5 (22.5°),
    foot 2 along 45°, OD 20, fill_up 0.5, no up gap: the top corner IS the OD
    point U at 22.5°."""
    s22, c22, s45 = math.sin(math.radians(22.5)), math.cos(math.radians(22.5)), math.sqrt(0.5)
    p4, k, c = cg._magnet_side_local(20.0, 10.0, 4, 0.5, 0.5, 2.0, 0.0)
    assert p4 == pytest.approx((10 * s22, 10 * c22), abs=1e-12)
    assert k == pytest.approx((10 * s22 + 2 * s45, 10 * c22 + 2 * s45), abs=1e-12)
    assert c == pytest.approx((20 * s22, 20 * c22), abs=1e-12)


def test_top_corner_is_where_the_upper_side_meets_the_top_arc():
    p4, k, c = cg._magnet_side_local(20.0, 10.0, 4, 0.5, 0.5, 2.0, 2.0)
    u = (20 * math.sin(math.radians(22.5)), 20 * math.cos(math.radians(22.5)))
    # on the circle r = 18 ...
    assert math.hypot(*c) == pytest.approx(18.0, abs=1e-12)
    # ... and on the straight line K -> U (cross product zero), between them
    cross = (c[0] - k[0]) * (u[1] - k[1]) - (c[1] - k[1]) * (u[0] - k[0])
    assert cross == pytest.approx(0.0, abs=1e-12)
    assert k[1] < c[1] < u[1]


def test_a_foot_past_the_top_arc_is_fusions_plain_trapezoid():
    """The owner's Ø12: mag_down_h = the whole magnet.  No upper side: the
    lower side runs along the half-pole line until the top arc."""
    p4, k, c = cg._magnet_side_local(20.0, 10.0, 4, 0.5, 0.5, 15.0, 2.0)
    assert math.hypot(*c) == pytest.approx(18.0, abs=1e-12)
    assert (c[0] - p4[0]) == pytest.approx(c[1] - p4[1], abs=1e-12)      # 45° line
    assert k == pytest.approx(((p4[0] + c[0]) / 2, (p4[1] + c[1]) / 2), abs=1e-12)


def test_built_magnet_carries_the_fusion_corners():
    """The rotor polygon build: the knee and the bottom corner of magnet 0 are
    vertices of its polygon (pole frame rotated by the builder's zero offset)."""
    geo = dict(_BASE, magnet_fill_radius=0.0)
    m = CadQueryMotor()
    m.set_parameters(geo)
    polys = m.get_2d_polygons()
    (p4, k, c), _a = cg._magnet_local_from_params(m.parameters)[0][:3], None
    n = int(m.parameters["num_poles"])
    th = math.radians(-(90.0 - 180.0 / n))
    want = [(x * math.cos(th) - y * math.sin(th), x * math.sin(th) + y * math.cos(th))
            for x, y in (p4, k, c)]
    mag = min((mp for mp, _ in polys["magnets"]),
              key=lambda mp: mp.centroid.distance(type(mp.centroid)(want[1])))
    verts = list(mag.exterior.coords)
    for w in want:
        assert min(math.dist(w, v) for v in verts) < 1e-6, w


# ── the translation of stored values ─────────────────────────────────────────

def test_tooth2_translates_exactly():
    t = fd.translate_geometry(dict(_BASE, tooth2_width=1.6))
    assert t["values"]["tooth2_width"] == pytest.approx(1.7)
    assert t["dev_mm"]["stator_leg"] == 0.0


def test_no_foot_translates_the_magnet_exactly():
    """magnet_down_height = 0: old and new magnets are both the trapezoid
    bottom corner -> top corner, so new values exist that draw it exactly —
    the top corner, the lips (fill_up·rotor_hole) and the area are kept."""
    g = dict(_BASE, magnet_down_height=0.0)
    t = fd.translate_geometry(g)
    assert t["dev_mm"]["magnet"] < 1e-9
    assert abs(t["area_change_pct"]["magnet"]) < 1e-9
    v = t["values"]
    assert v["magnet_fill_down"] == pytest.approx(g["magnet_fill_down"], abs=1e-12)
    assert v["magnet_fill_up"] * v["rotor_hole"] == pytest.approx(
        g["magnet_fill_up"] * g["rotor_hole"], rel=1e-9)
    # and the corner the old builder put at fill_up on the top arc is where the
    # new one puts it
    fr = fd.rotor_frame(g)
    args = (fr["rotor_or"], fr["magnet_r"], fr["num_poles"])
    old = fd.legacy_magnet_side(*args, g["magnet_fill_down"], g["magnet_fill_up"], 0.0,
                                g["magnet_up_gap"])
    new = fd.fusion_magnet_side(*args, v["magnet_fill_down"], v["magnet_fill_up"], 0.0,
                                g["magnet_up_gap"])
    assert new[2] == pytest.approx(old[2], abs=1e-9)


def test_with_a_foot_the_residual_is_small_and_reported():
    g = dict(_BASE)                                   # foot 1 mm at fill_down 0.76
    t = fd.translate_geometry(g)
    before = fd.outline_change(g, g)
    after = fd.outline_change(g, {**g, **t["values"]})
    assert after["stator_leg"] == 0.0 and before["stator_leg"] == pytest.approx(0.1)
    assert after["lip"] < 1e-9
    assert after["magnet"] == pytest.approx(t["dev_mm"]["magnet"], abs=1e-9)
    assert after["magnet"] < 0.06                       # ≈ foot·(pitch/2 − fill_down·pitch/2)
    assert abs(after["magnet_area_pct"]) < abs(before["magnet_area_pct"])


# ── the migration script on a temp tree ──────────────────────────────────────

_DIE_GEO = {k: v for k, v in _BASE.items()
            if k not in ("rotor_inner_radius", "rotor_outer_radius", "stator_inner_radius",
                         "stator_outer_radius", "num_poles", "num_slots", "shaft_diameter")}
_DIE_GEO["tooth2_width"] = 1.6                       # stored in the OLD definition


def _run(*args):
    return subprocess.run([sys.executable, str(REPO / "scripts" / "migrate_fusion_definitions.py"),
                           *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(REPO), timeout=300)


def _tree(root: Path, cfg_over=None) -> Path:
    d = root / "dies" / "T 30"
    d.mkdir(parents=True)
    (d / "die.yaml").write_text(yaml.safe_dump({"name": "T 30", "geometry": _DIE_GEO},
                                               sort_keys=False), encoding="utf-8")
    (d / "L10.yaml").write_text(yaml.safe_dump(
        {"name": "L10", "geometry_overrides": dict(cfg_over or {"motor_length": 10})},
        sort_keys=False), encoding="utf-8")
    return d


def test_dry_run_reports_and_writes_nothing(tmp_path):
    d = _tree(tmp_path)
    before = {p.name: p.read_bytes() for p in d.iterdir()}
    r = _run(str(tmp_path / "dies"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tooth2_width: 1.6 -> 1.7" in r.stdout
    assert "machines whose outline CHANGES" in r.stdout
    assert {p.name: p.read_bytes() for p in d.iterdir()} == before
    assert not (tmp_path / "dies" / ".fusion_definitions_migrated.json").exists()


def test_apply_translates_once_and_adds_what_a_configuration_needs(tmp_path):
    # the configuration has its own wire: under the old definition that moved
    # its leg by one more wire_spacing_x, so it needs its own tooth2_width
    d = _tree(tmp_path, {"motor_length": 10, "wire_spacing_x": 0.2})
    r = _run(str(tmp_path / "dies"), "--apply", "--date", "20301030")
    assert r.returncode == 0, r.stdout + r.stderr
    die = yaml.safe_load((d / "die.yaml").read_text(encoding="utf-8"))["geometry"]
    cfg = yaml.safe_load((d / "L10.yaml").read_text(encoding="utf-8"))["geometry_overrides"]
    assert die["tooth2_width"] == pytest.approx(1.7)
    assert cfg["tooth2_width"] == pytest.approx(1.8)            # 1.6 + its own 0.2
    assert cfg["wire_spacing_x"] == 0.2 and cfg["motor_length"] == 10
    # the configuration's outline: old builder on its old merge == new builder on its new merge
    old_merged = {**_DIE_GEO, "motor_length": 10, "wire_spacing_x": 0.2}
    new_merged = {**die, **cfg}
    ch = fd.outline_change(old_merged, new_merged)
    assert ch["stator_leg"] < 1e-12 and ch["lip"] < 1e-9
    assert (d / "die.yaml.bak-20301030").is_file() and (d / "L10.yaml.bak-20301030").is_file()
    man = json.loads((tmp_path / "dies" / ".fusion_definitions_migrated.json").read_text(encoding="utf-8"))
    assert len(man["files"]) == 2
    # one-shot: a second --apply is refused and changes nothing
    snap = (d / "die.yaml").read_bytes()
    r2 = _run(str(tmp_path / "dies"), "--apply", "--date", "20301031")
    assert r2.returncode == 2 and "refusing a second --apply" in r2.stdout
    assert (d / "die.yaml").read_bytes() == snap


def test_fusion_pins_win_over_the_translation(tmp_path):
    d = _tree(tmp_path)
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"T 30": {"tooth2_width": 1.75, "magnet_fill_up": 0.4}}),
                    encoding="utf-8")
    r = _run(str(tmp_path / "dies"), "--apply", "--date", "20301030", "--fusion-values", str(pins))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[FUSION VALUES]" in r.stdout
    die = yaml.safe_load((d / "die.yaml").read_text(encoding="utf-8"))["geometry"]
    assert die["tooth2_width"] == 1.75 and die["magnet_fill_up"] == 0.4
    assert die["magnet_fill_down"] == _DIE_GEO["magnet_fill_down"]      # not pinned, not translated


def test_as_stored_pin_keeps_the_die(tmp_path):
    d = _tree(tmp_path)
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"T 30": "as_stored"}), encoding="utf-8")
    snap = (d / "die.yaml").read_bytes()
    r = _run(str(tmp_path / "dies"), "--apply", "--date", "20301030", "--fusion-values", str(pins))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "KEPT AS STORED" in r.stdout
    assert (d / "die.yaml").read_bytes() == snap
    assert "T 30/die (max 0.1000 mm)" in r.stdout              # listed for a rerun
