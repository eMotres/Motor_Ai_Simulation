# SPDX-License-Identifier: Apache-2.0
# Copyright (C) MOTRES d.o.o. and contributors
"""The lamination flag ``stator_outer_notches`` (2026-11-03) and the
stator-OD sleeve contact it feeds.

ON (absent = on) must build byte-for-byte what every die built before the flag
existed; OFF gives a smooth, full-circle OD (e.g. a submerged motor, where the
OD notches only add drag) with the slots unchanged.

Tracked catalog dies only by default.  Set ``MOTOR_AI_SIM_DIES_DIR`` to a local
``config/dies`` directory to run the per-die checks over every die there too
(private dies stay out of this public repository).

Geometry only (shapely): no solve, no CAD kernel, no server.
"""
from __future__ import annotations

import contextlib
import glob
import io
import logging
import math
import os
from pathlib import Path

import pytest

shapely = pytest.importorskip("shapely")
yaml = pytest.importorskip("yaml")

from motor_ai_sim.cadquery_geometry import (  # noqa: E402
    CadQueryMotor, _poly_cache_key, measure_od_contact, stator_od_contact)
from motor_ai_sim.geometry.motor_geometry import (  # noqa: E402
    MotorGeometryParams, canonical_outer_notches, describes_whole_lamination,
    merge_outer_notches, stator_outer_notches)

ROOT = Path(__file__).resolve().parents[1]
TRACKED = ROOT / "config" / "dies"

#: (die directory, stator area mm² from the builder BEFORE the flag existed —
#: measured with the origin builder, 2026-11-03).
PINNED = {
    "CIANO14 30_10": 251.198822,
    "CIANO20 150_35 new": 5200.092919,
}


def _geo(die_dir: Path) -> dict:
    with open(die_dir / "die.yaml", encoding="utf-8") as f:
        return dict((yaml.safe_load(f) or {}).get("geometry") or {})


def _polys(geo: dict) -> tuple:
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            m = CadQueryMotor()
            m.set_parameters(dict(geo))
            return m, m.get_2d_polygons(rotor_angle_deg=0.0)
    finally:
        logging.disable(logging.NOTSET)


def _die_dirs():
    dirs = [d for d in sorted(TRACKED.iterdir()) if (d / "die.yaml").is_file()]
    extra = os.environ.get("MOTOR_AI_SIM_DIES_DIR")
    if extra:
        dirs += [Path(p).parent for p in sorted(glob.glob(os.path.join(extra, "*", "die.yaml")))]
    return dirs


DIES = _die_dirs()


# ── the flag itself ─────────────────────────────────────────────────────────

def test_flag_reading_absent_means_on():
    assert stator_outer_notches({}) is True
    assert stator_outer_notches({"stator_outer_notches": None}) is True
    assert stator_outer_notches({"stator_outer_notches": True}) is True
    assert stator_outer_notches({"stator_outer_notches": False}) is False
    assert stator_outer_notches({"stator_outer_notches": 0}) is False
    assert stator_outer_notches({"stator_outer_notches": "off"}) is False


def test_canonical_form_is_absent_for_on_and_false_for_off():
    assert canonical_outer_notches({"a": 1, "stator_outer_notches": True}) == {"a": 1}
    assert canonical_outer_notches({"stator_outer_notches": 0}) == {"stator_outer_notches": False}
    # the live geometry object never carries an ON flag
    g = _geo(TRACKED / "CIANO14 30_10")
    assert "stator_outer_notches" not in MotorGeometryParams(
        {**g, "stator_outer_notches": True}).to_dict()
    assert MotorGeometryParams({**g, "stator_outer_notches": False}).to_dict()[
        "stator_outer_notches"] is False


def test_whole_lamination_override_silent_on_flag_is_notched():
    g = _geo(TRACKED / "CIANO14 30_10")
    assert describes_whole_lamination(g)
    merged = merge_outer_notches({"stator_outer_notches": False, **g}, g)
    assert "stator_outer_notches" not in merged
    # a partial override (a sweep candidate) inherits the base's flag
    part = merge_outer_notches({"stator_outer_notches": False, "tooth_width": 3.0},
                               {"tooth_width": 3.0})
    assert part["stator_outer_notches"] is False
    # and the CAD does the same: a notched die built after a notch-free machine
    _m, p_off = _polys({**g, "stator_outer_notches": False})
    m = CadQueryMotor()
    with contextlib.redirect_stdout(io.StringIO()):
        m.set_parameters({**g, "stator_outer_notches": False})
        m.set_parameters(dict(g))
    assert "stator_outer_notches" not in m.parameters


# ── (1) ON builds exactly what it always built ─────────────────────────────

@pytest.mark.parametrize("name", sorted(PINNED))
def test_default_stator_is_the_pinned_pre_flag_stator(name):
    _m, p = _polys(_geo(TRACKED / name))
    assert p["stator"].geom_type == "Polygon"
    assert p["stator"].area == pytest.approx(PINNED[name], rel=1e-7)
    assert p["stator_outer_notches"] is True


@pytest.mark.parametrize("die", DIES, ids=lambda d: d.name.encode("ascii", "replace").decode())
def test_absent_and_true_build_byte_identical_polygons(die):
    g = _geo(die)
    _m0, p0 = _polys(g)
    _m1, p1 = _polys({**g, "stator_outer_notches": True})
    for k in ("stator", "rotor", "shaft"):
        assert p0[k].wkb == p1[k].wkb, k
    for k in ("coils", "slot_insulation", "wire_insulation"):
        assert [x.wkb for x in p0[k]] == [x.wkb for x in p1[k]], k
    assert (p0["slot_cut_x_mm"], p0["slot_cut_x_max_mm"]) == (
        p1["slot_cut_x_mm"], p1["slot_cut_x_max_mm"])


# ── (2) OFF: one piece, a full-circle OD, more iron, the same slots ─────────

@pytest.mark.parametrize("die", DIES, ids=lambda d: d.name.encode("ascii", "replace").decode())
def test_off_is_one_piece_with_a_full_circle_od(die):
    g = _geo(die)
    m_on, p_on = _polys(g)
    m_off, p_off = _polys({**g, "stator_outer_notches": False})
    st = p_off["stator"]
    R = float(m_off.parameters["stator_outer_radius"])
    assert st.geom_type == "Polygon" and st.is_valid
    assert p_off["stator_outer_notches"] is False
    radii = [math.hypot(x, y) for x, y in st.exterior.coords]
    assert min(radii) == pytest.approx(R, rel=1e-9)       # every outer vertex ON the circle
    assert max(radii) == pytest.approx(R, rel=1e-9)
    assert measure_od_contact(st, R) / (2 * math.pi * R) == pytest.approx(1.0, abs=1e-4)
    assert st.area > p_on["stator"].area
    # the slots (coils + liners) are untouched
    assert [x.wkb for x in p_on["coils"]] == [x.wkb for x in p_off["coils"]]
    assert [x.wkb for x in p_on["slot_insulation"]] == [x.wkb for x in p_off["slot_insulation"]]
    # the notch area is what was added, all of it outside the ON stator
    added = st.difference(p_on["stator"]).area
    assert added == pytest.approx(st.area - p_on["stator"].area, rel=1e-6)


def test_off_notch_area_on_a_tracked_die():
    g = _geo(TRACKED / "CIANO14 30_10")
    _m, p_on = _polys(g)
    _m, p_off = _polys({**g, "stator_outer_notches": False})
    assert p_off["stator"].area == pytest.approx(338.78, rel=1e-3)   # +34.9 % iron
    assert p_off["stator"].area / p_on["stator"].area == pytest.approx(1.3487, rel=1e-3)


def test_display_mesh_follows_the_flag():
    g = _geo(TRACKED / "CIANO14 30_10")
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            m_on = CadQueryMotor(); m_on.set_parameters(dict(g))
            a = m_on.get_2d_mesh_data()["stator_core"]
            m_off = CadQueryMotor(); m_off.set_parameters({**g, "stator_outer_notches": False})
            b = m_off.get_2d_mesh_data()["stator_core"]
    finally:
        logging.disable(logging.NOTSET)
    assert a["vertices"] != b["vertices"]
    assert getattr(m_off, "_stator_outline_violation", None) is None


# ── (3) every cache keys the flag ──────────────────────────────────────────

def test_cache_keys_differ_between_on_and_off():
    from motor_ai_sim.masses import _area_key
    g = _geo(TRACKED / "CIANO14 30_10")
    m_on, _ = _polys(g)
    m_off, _ = _polys({**g, "stator_outer_notches": False})
    assert _poly_cache_key(m_on.parameters, 0.0) != _poly_cache_key(m_off.parameters, 0.0)
    assert m_on.get_parameter_hash() != m_off.get_parameter_hash()
    assert _area_key(g) != _area_key({**g, "stator_outer_notches": False})
    # absent and true are ONE key — a notched machine keys as before the flag
    assert _area_key(g) == _area_key({**g, "stator_outer_notches": True})
    assert "stator_outer_notches" not in dict(_area_key(g))


def test_geometry_signature_stamps_only_an_off_flag():
    from motor_ai_sim.routes.presets import _geo_sig
    from motor_ai_sim.report import _geo_sig_inputs_differ
    g = _geo(TRACKED / "CIANO14 30_10")
    on = _geo_sig(g)
    assert _geo_sig({**g, "stator_outer_notches": True}) == on
    off = _geo_sig({**g, "stator_outer_notches": False})
    assert "stator_outer_notches:0" in off and "stator_outer_notches" not in on
    assert _geo_sig_inputs_differ(on, off)
    assert not _geo_sig_inputs_differ(on, on)


def test_die_shape_diffs_see_the_flag():
    from motor_ai_sim.routes.family import die_shape_diffs
    g = _geo(TRACKED / "CIANO14 30_10")
    assert not [d for d in die_shape_diffs(g, {**g, "stator_outer_notches": True})
                if d["key"] == "stator_outer_notches"]
    d = [x for x in die_shape_diffs(g, {**g, "stator_outer_notches": False})
         if x["key"] == "stator_outer_notches"]
    assert d and d[0]["die"] is True and d[0]["live"] is False


# ── (4) validation passes OFF, and does not talk about notches ──────────────

@pytest.mark.parametrize("name", sorted(PINNED))
def test_validation_passes_for_a_notch_free_stator(name):
    from motor_ai_sim.geometry_validation import (
        validate_geometry, validate_parameter_values, slot_cut_limits)
    g = {**_geo(TRACKED / name), "stator_outer_notches": False}
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            res = validate_geometry(g)
    finally:
        logging.disable(logging.NOTSET)
    codes = {v.code for v in res.errors}
    assert not codes & {"stator_not_one_piece", "stator_outline_not_symmetric",
                        "winding_split_does_not_fit"}, codes
    assert not [r for r in validate_parameter_values(g)
                if r["field"] == "stator_outer_notches"]
    # the OFF slot limit: the pocket wall against the half-sector line at the bore
    cut_x, cut_x_max, _n = slot_cut_limits(g)
    on_x, _on_max, _ = slot_cut_limits(_geo(TRACKED / name))
    assert cut_x == pytest.approx(on_x - float(g["tooth2_width"]))
    assert cut_x < cut_x_max


# ── the OD contact arcs ─────────────────────────────────────────────────────

@pytest.mark.parametrize("die", DIES, ids=lambda d: d.name.encode("ascii", "replace").decode())
def test_contact_fraction_closed_form_matches_the_polygon(die):
    g = _geo(die)
    m, p = _polys(g)
    a = stator_od_contact(g)
    b = stator_od_contact(g, stator=p["stator"])
    assert a and b
    assert 0.0 < a["fraction"] < 1.0
    assert a["fraction"] == pytest.approx(b["fraction"], abs=2e-4)
    off = stator_od_contact({**g, "stator_outer_notches": False})
    assert off["fraction"] == 1.0 and off["notch_opening_mm"] == 0.0


def test_contact_fraction_on_a_tracked_die():
    c = stator_od_contact(_geo(TRACKED / "CIANO14 30_10"))
    assert c["fraction"] == pytest.approx(0.4826, abs=1e-4)
    assert c["od_circumference_mm"] == pytest.approx(math.pi * 30.0)


# ── the sleeve: contact arcs + wall + film ─────────────────────────────────

def test_sleeve_contact_conductance_follows_the_contact_fraction():
    from motor_ai_sim.simulation.cooling_models import (
        HOUSING_CONTACT_H, sleeve_contact_path)
    g = _geo(TRACKED / "CIANO14 30_10")
    f_on = stator_od_contact(g)["fraction"]
    f_off = stator_od_contact({**g, "stator_outer_notches": False})["fraction"]
    kw = dict(r_od_m=0.015, stack_m=0.010, wall_m=1.5e-3, k_sleeve=160.0, h_out=3000.0)
    on = sleeve_contact_path(contact_fraction=f_on, **kw)
    off = sleeve_contact_path(contact_fraction=f_off, **kw)
    assert on["G_W_per_K"] < off["G_W_per_K"]
    # the contact term is exactly proportional to the contact fraction
    assert on["G_contact_W_per_K"] / off["G_contact_W_per_K"] == pytest.approx(f_on / f_off)
    assert off["G_contact_W_per_K"] == pytest.approx(
        HOUSING_CONTACT_H * 2 * math.pi * 0.015 * 0.010)
    # the series sum
    inv = 1 / on["G_contact_W_per_K"] + 1 / on["G_sleeve_W_per_K"] + 1 / on["G_film_W_per_K"]
    assert on["G_W_per_K"] == pytest.approx(1 / inv)
    assert sleeve_contact_path(contact_fraction=f_on, **{**kw, "h_out": 0.0})["G_W_per_K"] == 0.0


def test_default_thermal_request_is_unchanged():
    """'direct' is never sent and never keyed: the request, the duty condition
    and the cache tuple are the ones every stored map was made with."""
    from motor_ai_sim import thermal_settings as ts
    from motor_ai_sim import coupled_continuous_rating as ccr
    base = {"coolMode": "liquid", "ambientT": "30", "flowLpm": "6"}
    assert ts.cooling_fields({**base, "statorOd": "direct"}) == ts.cooling_fields(base)
    assert not {"stator_od_cooling", "od_sleeve_wall_mm", "od_sleeve_k"} & set(
        ts.cooling_fields(base))
    sl = ts.cooling_fields({**base, "statorOd": "sleeve_contact", "sleeveWallMm": "2"})
    assert (sl["stator_od_cooling"], sl["od_sleeve_wall_mm"], sl["od_sleeve_k"]) == (
        "sleeve_contact", 2.0, 160.0)
    assert ts.cooling_issue({**base, "statorOd": "sleeve_contact"}) is None
    assert ts.cooling_issue({"coolMode": "none", "boreMode": "air",
                             "statorOd": "sleeve_contact"})
    assert "stator_od_cooling" not in ccr._prune(dict(ts.cooling_fields(base)))
    assert set(ts.CANONICAL_COOLING) == set(ts.COOLING_KEYS)


def test_thermal_cache_key_unchanged_for_direct(monkeypatch):
    from motor_ai_sim.routes import thermal as th
    import motor_ai_sim.routes.simulation as sim
    monkeypatch.setattr(sim, "_geometry_fingerprint", lambda *_a, **_k: "fp")
    monkeypatch.setattr(sim, "_config_physics_fingerprint", lambda *_a, **_k: "cfg")
    monkeypatch.setattr(th, "_override_props", lambda: {})
    monkeypatch.setattr(th, "_thermal_k", lambda *_a, **_k: 1.0)
    monkeypatch.setattr(th, "_thermal_k_any", lambda *_a, **_k: 1.0)
    kw = dict(ambient_temp=40, h_conv=50, slot_k=0.5, rpm=1000, gamma_deg=0,
              I_phase_rms=10, n_steps_per_period=10, n_periods=1, mesh_size_mm=1,
              min_size_mm=0.5, outer_air_factor=1.2, n_sectors=1, coil_temp_c=80,
              component_mesh="auto", cooling_mode="manual", air_speed_mps=0,
              fluid="water", fluid_temp_in_c=25, flow_lpm=0)
    k0 = th._field_cache_key(None, {}, **kw)
    assert th._field_cache_key(None, {}, stator_od_cooling="direct", **kw) == k0
    k1 = th._field_cache_key(None, {}, stator_od_cooling="sleeve_contact", **kw)
    assert k1 != k0 and k1[:len(k0)] == k0


def test_thermal_validator_refuses_a_sleeve_it_cannot_solve():
    from fastapi import HTTPException
    from motor_ai_sim.routes import thermal as th
    base = dict(cooling_mode="manual", ambient_temp=40, h_conv=3000, air_speed_mps=0,
                fluid="water", fluid_temp_in_c=25, flow_lpm=0)
    assert th._validate_field_params(**base) == ("manual", "none")
    assert th._validate_field_params(**base, stator_od_cooling="sleeve_contact") == ("manual", "none")
    for bad in (dict(stator_od_cooling="nope"),
                dict(stator_od_cooling="sleeve_contact", od_sleeve_wall_mm=0),
                dict(stator_od_cooling="sleeve_contact", od_sleeve_k=-1),
                dict(stator_od_cooling="sleeve_contact", cooling_mode="robotics",
                     heat_path="housing")):
        with pytest.raises(HTTPException) as exc:
            th._validate_field_params(**{**base, **bad})
        assert exc.value.status_code == 422
