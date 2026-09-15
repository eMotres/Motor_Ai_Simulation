"""Every cooled surface reports the wall temperature its film acted on.

Until 2026-09-14 ``thermal_solver_2d.solve_steady_thermal`` told the caller how
many watts each surface removed and said nothing about how HOT that surface was.
The volume sinks had carried their own ``t_mean_c`` since the day they were
written — precisely so ``shaft_ends_W == G·(T_shaft − T_ambient)`` could be
CHECKED from the payload rather than trusted — and the surfaces, which are the
paths an engineer actually looks at, could not be checked at all.

Two things need it, and the robot-joint work needs both at once:

  (a) THE IDENTITY.  ``heat_removed_W == h_conv · area_m2 · (t_mean_c −
      t_sink_c)`` — and not to a tolerance: both sides come out of the SAME facet
      integral, so it holds to round-off.  A payload whose watts and whose
      boundary condition are only approximately each other's is a payload nobody
      can audit.
  (b) THE ITERATION.  Still air is the first mode whose coefficient depends on
      the wall temperature: natural convection goes as ΔT^(1/4) and the
      linearised radiation coefficient re-linearises about the wall.  Something
      has to be iterated, and the only honest thing to iterate on is the film's
      OWN area-mean wall temperature.  The alternative the code had — seed the
      film from the global ``T_max`` — is tens of kelvin wrong on a machine whose
      heat leaves through its mount, and wrong the expensive way: too hot a wall
      is too much cooling.

THE MACHINE IS THE REAL ONE.  Die ``CIANO28 85 20SW1200``, configuration
``L13`` — the Ø85 × 13 mm, 24s/28p joint the still-air work was commissioned for
— at its stored "rated 120С wire 80C NdFeB" duty: 63.7 W of loss (59.8 copper,
2.44 stator iron, 0.18 rotor iron, 1.3 solid).  The geometry is pinned here as a
literal, taken from that duty's own ``_geoSig``, so the test cannot drift with
the die file or with whatever machine the user has loaded.

WHAT IS SYNTHETIC AND WHAT IS NOT.  The electromagnetic LOSS SPLIT is injected
rather than solved: the stored run cost 185 s of transient and this module is
about the conduction solve's bookkeeping, which does not care where the watts
came from — only that they are the machine's real watts, deposited in the real
domains of the real cross-section.  Everything downstream of that is the
product's own code: the mesh comes from the same builder ``/api/thermal/mesh``
uses, and ``solve_thermal_field`` does its own re-tagging, its own material
lookup and its own conduction solve on it.  Nothing here goes through a route,
and nothing here writes to ``config/``.
"""
from __future__ import annotations

import json
import math
import pathlib

import numpy as np
import pytest

#: The L13 as its stored rated run recorded it (``summary._geoSig``), in the
#: shape a ``?geo=`` override takes.  Ø85, 13 mm stack, 24 slots / 28 poles,
#: 0.3 mm gap, 7 mm magnets, Ø50 bore.
GEO_L13 = {
    "stator_diameter": 85.0, "slot_height": 7.4, "core_thickness": 2.4,
    "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.3, "tooth_width": 5.0, "tooth2_width": 2.2, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 3.5, "wire_height": 0.3,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.07, "num_wires_per_slot": 18,
    "wire_split": 1, "slot_hs": 0.13, "magnet_height": 7.0,
    "rotor_house_height": 0.8, "shaft_height": 2.0, "magnet_fill_down": 0.92,
    "magnet_fill_up": 0.22, "magnet_fill_radius": 0.4, "magnet_up_gap": 0.1,
    "rotor_hole": 0.5, "magnet_down_height": 0.6, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0.1, "rotor_fill_r": 0.2,
    "motor_length": 13.0,
}

#: The materials that duty was solved with.  Pinned in the SANDBOX config for
#: the module, not passed per request: the thermal solve reads the conductivities
#: from the loaded machine's assignment, and the suite's sandbox copy starts as
#: whatever the user had open.  (The sandbox-leak family — see
#: tests/test_thermal_loss_reuse.py::pinned_config_materials.)
MATERIALS_L13 = {"magnet": "F52SH_120C", "stator_core": "20SW1200",
                 "rotor_core": "20SW1200", "shaft": "Aluminium_6061",
                 "slot_insulation": "Nomex", "wire_insulation": "polyimide",
                 "slot": "copper"}

#: The stored "rated 120С wire 80C NdFeB" loss split [W], whole machine.
P_CU_W, P_STATOR_FE_W, P_ROTOR_FE_W, P_SOLID_W = 59.8, 2.439, 0.175, 1.3
P_TOTAL_W = P_CU_W + P_STATOR_FE_W + P_ROTOR_FE_W + P_SOLID_W        # 63.7 W

#: A quarter machine — gcd(24, 28) = 4 — which is the wedge the stored run used.
SYM = 4
POLES_PER_SECTOR = 7

#: Coarse on purpose: this module asserts an identity and an area, and a
#: converged map would buy neither at several times the wall clock.
MESH_MM, MIN_MM = 2.0, 0.35

#: The room, and the wall the film is SEEDED at.  40 °C is the plan's ambient for
#: the joint; 100 °C is a plausible first guess for the housing, and the whole
#: point of ``t_mean_c`` is that the solve then says what the wall really is.
T_AMBIENT_C = 40.0
T_WALL_SEED_C = 100.0


@pytest.fixture(scope="module")
def pinned_materials():
    """``MATERIALS_L13`` in the sandbox config for the module, restored after."""
    import yaml

    from motor_ai_sim.config import DEFAULT_CONFIG_PATH, clear_config_cache

    path = pathlib.Path(DEFAULT_CONFIG_PATH)
    original = path.read_text(encoding="utf-8")
    doc = yaml.safe_load(original) or {}
    mats = dict(doc.get("materials") or {})
    mats.update(MATERIALS_L13)
    doc["materials"] = mats
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    clear_config_cache()
    yield dict(MATERIALS_L13)
    path.write_text(original, encoding="utf-8")
    clear_config_cache()


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path, monkeypatch):
    """Nothing this module solves may land beside the user's machine.

    Same fixture as tests/test_thermal_routes.py, for the same reason: the
    router remembers its last answer and its loss maps in ``config/``, and this
    suite promises not to touch that directory.  ``solve_thermal_field`` called
    as a FUNCTION does not remember anything (that is the route's job) and an
    injected loss map never reaches the map store at all — the redirect is here
    so that stays true by construction rather than by reading.
    """
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(th, "_last_store_path",
                        lambda: str(tmp_path / ".last_thermal.pkl"))
    monkeypatch.setattr(th, "_loss_maps_path",
                        lambda: str(tmp_path / ".loss_maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    th._LOSS_MAPS.clear()
    yield
    th._LOSS_MAPS.clear()


def _element_areas(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    xy = verts[tris]
    return 0.5 * np.abs(
        (xy[:, 1, 0] - xy[:, 0, 0]) * (xy[:, 2, 1] - xy[:, 0, 1])
        - (xy[:, 2, 0] - xy[:, 0, 0]) * (xy[:, 1, 1] - xy[:, 0, 1]))


@pytest.fixture(scope="module")
def l13(pinned_materials):
    """Solve the L13 once: mesh, inject the duty's losses, conduction.

    The whole fixture is a few seconds — the expensive half of a thermal answer
    is the electromagnetic run, and that is exactly the half being injected.
    ``solve_steady_thermal`` is wrapped so the tests can read the SOLVER's own
    surface list, which is where ``t_mean_c`` lives; the route payload does not
    carry the wall temperature yet (that is the next step's work) and asserting
    on the solver directly is the honest place for a solver contract anyway.
    """
    from motor_ai_sim.routes import thermal as th
    from motor_ai_sim.routes.simulation import (_parse_geo_override,
                                                build_fem_mesh_2d_sliding_band)
    from motor_ai_sim.simulation import cooling_models as cm
    from motor_ai_sim.simulation import thermal_solver_2d as ts

    geo_json = json.dumps(GEO_L13)
    geo_ov = _parse_geo_override(geo_json)
    # The same auto-refined coil mesh and the same snapped sector count the
    # thermal route would use — a preview built with other flags is another mesh.
    coil_mesh = th._auto_coil_mesh("", MESH_MM, geo_ov)
    n_sect = th._snap_n_sectors(SYM, geo_ov)
    assert n_sect == SYM, n_sect

    em = th._run_coro(build_fem_mesh_2d_sliding_band(
        rotor_angle_deg=0.0, mesh_size_mm=MESH_MM, min_size_mm=MIN_MM,
        outer_air_factor=1.3, band_thickness_mm=0.4, gap_layers=2.0,
        n_sectors=int(n_sect), stator_fillet_mm=0.0, component_mesh=coil_mesh,
        surface_deviation=0.005, normal_deviation=8.0, aspect_ratio=10.0,
        pole_copy=False, iron_template=True, geo_mesh=True, hi_fidelity=False,
        structured_gap=True, geo=geo_json))

    verts = np.asarray(em["vertices"], float)
    tris = np.asarray(em["triangles"], int)
    tags = np.asarray(em["domain_per_tri"], int)
    a_elem = _element_areas(verts, tris)
    stack_m = GEO_L13["motor_length"] * 1e-3

    # The duty's iron and solid losses as a per-element density, whole-machine
    # watts over whole-machine volume (the wedge then carries its quarter, which
    # is what `symmetry_mult` puts back).  The COPPER is not here: the route
    # deposits `P_cu_exact_W` itself, over the winding volume it derives from the
    # geometry — see `q_cu` in solve_thermal_field.
    dens = np.zeros(tris.shape[0])
    for tagset, watts in ((( 1,), P_STATOR_FE_W),        # stator core
                          (( 5,), P_ROTOR_FE_W),         # rotor back-iron
                          (( 4, 44), P_SOLID_W)):        # magnets, N and S
        m = np.isin(tags, tagset)
        vol = float(a_elem[m].sum()) * stack_m * SYM
        assert vol > 0.0, tagset
        dens[m] = watts / vol

    em_map = dict(em)
    em_map.update({
        "loss_density_per_tri": dens.tolist(),
        "loss_density_label": ("the L13 rated duty's stored loss split, "
                               "deposited per domain"),
        "P_cu_exact_W": P_CU_W, "P_cu_W": P_CU_W,
        "P_fe_W": P_STATOR_FE_W + P_ROTOR_FE_W,
        "P_mag_eddy_W": P_SOLID_W,
        "P_loss_total_exact_W": P_TOTAL_W, "P_loss_total_W": P_TOTAL_W,
        "n_sectors": SYM, "symmetry_mult": SYM,
        "poles_per_sector": POLES_PER_SECTOR,
    })

    seed = cm.outer_still(t_wall_c=T_WALL_SEED_C, t_ambient_c=T_AMBIENT_C,
                          d_housing_m=GEO_L13["stator_diameter"] * 1e-3,
                          emissivity=cm.EMISSIVITY_DEFAULT)

    captured = {}
    real = ts.solve_steady_thermal

    def _spy(*a, **kw):
        out = real(*a, **kw)
        captured["solver"] = out
        return out

    mp = pytest.MonkeyPatch()
    mp.setattr(ts, "solve_steady_thermal", _spy)
    try:
        out = th.solve_thermal_field(
            ambient_temp=T_AMBIENT_C, h_conv=seed["h_total"],
            cooling_mode="manual", bore_mode="none",
            rpm=1000.0, gamma_deg=2.0, I_phase_rms=14.708,
            n_steps_per_period=4, n_periods=1.0,
            mesh_size_mm=MESH_MM, min_size_mm=MIN_MM, n_sectors=SYM,
            coil_temp_c=120.0, geo=geo_json,
            _em_map=em_map,
            _em_loss_source={"kind": "provided",
                             "note": "the L13 rated duty's stored loss split"})
    finally:
        mp.undo()

    assert "solver" in captured, "solve_steady_thermal was never reached"
    return {"field": out, "solver": captured["solver"], "seed": seed,
            "geo": geo_json}


# ---------------------------------------------------------------------------
# (a) the identity
# ---------------------------------------------------------------------------

def test_every_surface_reports_the_wall_temperature_its_film_acted_on(l13):
    """``t_mean_c`` on every entry — and ``None``, not 0, where there are no facets.

    A surface that is switched off has no wall: reporting 0 °C would put a
    plausible number where there is no measurement, and the bore of a machine
    with no bore cooling would read as a wall at freezing.
    """
    surfaces = l13["solver"]["surfaces"]
    assert surfaces, "the solve reported no surfaces at all"
    by_name = {s["name"]: s for s in surfaces}
    assert "outer" in by_name, sorted(by_name)

    for s in surfaces:
        assert "t_mean_c" in s, s["name"]
        if s["n_facets"] and s["area_m2"] > 0.0:
            assert s["t_mean_c"] is not None, s["name"]
            assert math.isfinite(s["t_mean_c"]), s["name"]
        else:
            assert s["t_mean_c"] is None, (s["name"], s["t_mean_c"])

    housing = by_name["outer"]
    assert housing["t_mean_c"] > T_AMBIENT_C      # it is being cooled, so it is hotter


def test_the_heat_a_surface_removed_is_its_own_h_area_and_wall_temperature(l13):
    """THE identity, and it is exact rather than close.

    ``heat_removed_W == h_conv · area_m2 · (t_mean_c − t_sink_c)``.  Both sides
    come out of the same facet integral — ∫T dA for the watts, ∫T dA / ∫dA for
    the mean — so this holds to round-off, not to the 1 % an independent
    re-derivation would need.  Asserting it tightly is the point: a payload that
    only approximately agrees with its own boundary condition cannot be audited,
    and the looser the assertion the longer a real inconsistency could hide in it.
    """
    for s in l13["solver"]["surfaces"]:
        if s["t_mean_c"] is None:
            assert s["heat_removed_W"] == pytest.approx(0.0, abs=1e-12), s["name"]
            continue
        expect = s["h_conv"] * s["area_m2"] * (s["t_mean_c"] - s["t_sink_c"])
        assert s["heat_removed_W"] == pytest.approx(expect, rel=1e-9), (
            s["name"], s["heat_removed_W"], expect)
        # …and the number is a real one, not a pair of zeros agreeing
        if s["name"] == "outer":
            assert s["heat_removed_W"] > 1.0


def test_the_wall_is_not_the_hottest_point_in_the_machine(l13):
    """Why the film could not simply be seeded from ``T_max``.

    The housing is the far end of every conduction path in the machine: the
    copper makes the heat, the liner and the tooth iron stand between, and the
    surface the air touches is the coldest solid in the cross-section.  Seeding a
    wall-dependent film from the global maximum therefore over-reads ΔT — and
    over-reading ΔT over-reads the cooling, which is the expensive direction.
    """
    housing = {s["name"]: s for s in l13["solver"]["surfaces"]}["outer"]
    field = l13["field"]
    assert housing["t_mean_c"] < field["T_max"]
    winding = (field.get("components") or {}).get("winding") or {}
    if winding.get("avg") is not None:
        assert housing["t_mean_c"] < winding["avg"]


# ---------------------------------------------------------------------------
# (b) the iteration it exists for
# ---------------------------------------------------------------------------

def test_the_wall_temperature_is_what_a_still_air_film_iterates_on(l13):
    """Re-evaluating the film at the solved wall MOVES it — which is the loop.

    ``outer_still`` is the first coefficient in this product that depends on the
    wall: h_conv ∝ ΔT^(1/4) and the radiation term re-linearises about T_w.  The
    seed here was a 100 °C guess; the solve came back with a different wall, and
    the film evaluated there is a different boundary condition.  Without
    ``t_mean_c`` there is nothing to close that loop on.
    """
    from motor_ai_sim.simulation import cooling_models as cm

    housing = {s["name"]: s for s in l13["solver"]["surfaces"]}["outer"]
    seed = l13["seed"]
    again = cm.outer_still(t_wall_c=housing["t_mean_c"], t_ambient_c=T_AMBIENT_C,
                           d_housing_m=GEO_L13["stator_diameter"] * 1e-3,
                           emissivity=cm.EMISSIVITY_DEFAULT)
    assert again["h_total"] != pytest.approx(seed["h_total"], rel=1e-3)
    # the wall came back HOTTER than the guess (still air cannot hold this
    # machine — see the next test), so the film is stronger, never weaker
    assert housing["t_mean_c"] > T_WALL_SEED_C
    assert again["h_total"] > seed["h_total"]
    assert again["t_wall_c"] == pytest.approx(housing["t_mean_c"], abs=0.01)


def test_still_air_alone_cannot_hold_this_machine_and_the_numbers_say_why(l13):
    """The result the mount exists to fix, stated in watts.

    The L13's housing is π·0.085·0.013 ≈ 3.5e-3 m² of cylinder (a little more
    with the stator's outer cuts opened to the air).  At the still-air pair —
    h_conv ≈ 6, h_rad ≈ 8.3 at ΔT 60 K — that surface can hand the room ~3 W
    against the duty's 63.7 W: under 5 %.  So the solve run with the housing as
    the machine's ONLY path lands at a temperature no insulation class survives,
    and that is the honest answer rather than a failure — it is the reason the
    bolted mount is modelled at all.
    """
    housing = {s["name"]: s for s in l13["solver"]["surfaces"]}["outer"]
    sym = SYM
    area_machine = housing["area_m2"] * sym
    cylinder = math.pi * GEO_L13["stator_diameter"] * 1e-3 * 0.013
    assert cylinder <= area_machine < 1.35 * cylinder, (area_machine, cylinder)

    # what that area removes at a wall a machine could actually run at
    seed = l13["seed"]
    at_100c = seed["h_total"] * area_machine * (T_WALL_SEED_C - T_AMBIENT_C)
    assert at_100c == pytest.approx(3.0, abs=1.0)
    assert at_100c < 0.06 * P_TOTAL_W

    # …and with nothing else attached the machine has to shed all 63.7 W here,
    # so it does — at a wall that is not a design, it is a diagnosis
    assert housing["heat_removed_W"] * sym == pytest.approx(P_TOTAL_W, rel=0.05)
    assert l13["field"]["T_max"] > 300.0
