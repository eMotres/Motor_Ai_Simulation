"""ONE temperature per rotor part — the Thermal → Mechanical coupling.

Added 2026-09-08 for the user's request: *"в механический расчёт тоже нужно
делать каплинг, чтобы температуры везде были одинаковы"*.  The Thermal solve
already reports a temperature for every solid; the Mechanical solve used to take
two numbers typed by hand (``rotor_temp_c`` for the core, the magnets AND the
shaft, ``sleeve_temp_c`` for the band).  Two hand-typed numbers are how the two
solvers drift apart, so ``solve_rotor_stress`` now takes ``part_temps_c`` and the
route takes ``magnet_temp_c`` / ``rotor_core_temp_c`` / ``shaft_temp_c`` beside
the ``sleeve_temp_c`` it already had.

Four claims, in the order they matter:

  (a) NOTHING MOVED.  ``part_temps_c`` holding exactly the old pairing produces
      exactly the old solve — proven at the only place where "exactly" is
      meaningful (see the docstring of `test_a_...eigenstrain...`: the linear
      solver is pypardiso, which is multi-threaded and NOT bit-reproducible, so
      the bit-level claim is made about the eigenstrain that reaches it).
  (b) THE PHYSICS — the user's rule of 2026-09-09: *"нам нужно учитывать
      температуру только как изменение давления на бандаж, если он есть"*.
      A temperature is a load on the rotor in ONE place, a retaining band: the
      iron under it grows at 12 ppm/K, the carbon does not, and the fit
      tightens.  Everywhere else — the magnets in their epoxy bed with a
      0.04 mm pocket clearance, an all-iron rotor expanding freely — it is not
      a load at all.  So on a sleeveless rotor every temperature map is the
      cold answer (and the answer says so), and on a banded rotor the ONLY
      strain that reaches the solve is the band's own, sized by the fit at
      temperature.  The per-part eigenstrain of 2026-09-07 (a hot magnet
      pressing into a cold pocket, a hot core opening away from its magnets)
      is kept as the solver's verification model, ``thermal_model=
      "free_expansion"``, which no route offers; one test here keeps that door
      exercised.
  (c) THE ROUTE round-trips the three new parameters, keys the cache on them
      ONLY when they are given, and echoes what every part was solved at.
  (d) THE HOOK ``run_rotor_stress_at`` runs the same solve the button runs, at
      temperatures somebody else computed, with the rest taken from the user's
      saved Mechanical-panel fields.

(b) is deliberately solved on a purpose-built spoke rotor rather than on the
loaded machine: the claim is about the rule, and it must not change meaning
when the user opens a different motor.
"""
from __future__ import annotations

import numpy as np
import pytest
from shapely import affinity
from shapely.geometry import Point, Polygon

from motor_ai_sim.simulation.mechanical import contact as ctc
from motor_ai_sim.simulation.mechanical import rotor_stress as rs
from motor_ai_sim.simulation.mechanical.rotor_stress import REF_TEMP_C

# ---------------------------------------------------------------------------
# A spoke rotor, built here so the physics claim cannot move with the machine
# ---------------------------------------------------------------------------
# A steel annulus with four radial magnet slabs let into it.  The magnet's
# material axis 1 is the magnetisation, which the solver takes as the tangent at
# the magnet's own centroid — for these slabs that is the THIN direction, i.e.
# straight at the two pole faces the magnet is pressed between.  So the +5 ppm/K
# coefficient acts exactly where the magnet_rotor contact is.

R_BORE, R_OD = 20.0, 50.0                 # mm
MAG_W, MAG_R0, MAG_R1 = 8.0, 26.0, 46.0   # mm — slab width, inner and outer r
N_POLES = 4

ASSIGN = {"rotor_core": "20SW1200", "magnet": "F52SH_120C",
          "sleeve": "HM63_UD_60", "shaft": "Aluminium_7075"}
CONTACTS = {"magnet_rotor": ctc.ContactSpec("separation", 0.0)}


SLEEVE_MM = 2.0                           # the band, when the fixture has one


def _spoke_polys(sleeve: bool = False):
    disk = Point(0, 0).buffer(R_OD, resolution=128)
    annulus = disk.difference(Point(0, 0).buffer(R_BORE, resolution=96))
    magnets = []
    for k in range(N_POLES):
        slab = Polygon([(MAG_R0, -MAG_W / 2), (MAG_R1, -MAG_W / 2),
                        (MAG_R1, MAG_W / 2), (MAG_R0, MAG_W / 2)])
        magnets.append((affinity.rotate(slab, 360.0 * k / N_POLES, origin=(0, 0)),
                        1 if k % 2 == 0 else -1))
    rotor = annulus
    for mp, _pol in magnets:
        rotor = rotor.difference(mp)
    if sleeve:
        band = Point(0, 0).buffer(R_OD + SLEEVE_MM, resolution=128).difference(disk)
        return {"rotor": rotor, "magnets": magnets, "sleeve": band, "shaft": None,
                "sleeve_r_mm": (R_OD, R_OD + SLEEVE_MM)}
    return {"rotor": rotor, "magnets": magnets, "sleeve": None, "shaft": None,
            "sleeve_r_mm": (0.0, 0.0)}


def _spoke_solve(part_temps=None, *, sleeve: bool = False, **kw):
    """Standstill, no interference: the ONLY load is whatever the temperatures
    are allowed to be."""
    contacts = dict(CONTACTS)
    if sleeve:
        contacts["sleeve_rotor"] = ctc.ContactSpec("separation", 0.0)
    return rs.solve_rotor_stress(
        _spoke_polys(sleeve), ASSIGN, 0.0, 1.0, 0.0, stack_length_mm=50.0,
        mesh_size_mm=2.0, order=1, with_field=False, contacts=contacts,
        lift_off_solves=0, case_mode="single", loads="centrifugal",
        part_temps_c=part_temps, **kw)


def _joint(out):
    """The magnet_rotor interface of the one case that was solved."""
    return out["cases"][out["primary_case"]]["interfaces"]["magnet_rotor"]


# ---------------------------------------------------------------------------
# (a) the old pairing, spelled out per part, is the old answer
# ---------------------------------------------------------------------------

ROTOR_C, SLEEVE_C = 150.0, 80.0
SAME_AS_OLD = {"rotor_core": ROTOR_C, "magnet": ROTOR_C, "shaft": ROTOR_C,
               "sleeve": SLEEVE_C}


@pytest.fixture(scope="module")
def old_and_new():
    """Both paths, plus every eigenstrain array that reached the assembler.

    WHY THE EIGENSTRAIN AND NOT THE STRESSES.  ``contact._solver()`` resolves to
    **pypardiso**, which is multi-threaded: solving the SAME system twice in this
    process already gives answers that differ in the last two digits (measured
    while writing this test — ``rotor_od_growth_um`` 78.07044322854162 vs
    ...854570, a relative 1.6e-14).  So "bit-identical" cannot honestly be
    asserted about anything downstream of the solve, and asserting it would make
    a test that fails at random.  The honest bit-level claim is about the INPUT
    the new code path builds: if the eigenstrain arrays are equal byte for byte,
    the two requests are the same problem, and what the solver then does to it is
    not this feature's business.  The outputs are checked too, at solver noise.
    """
    real = rs.assemble_plane_stress
    seen: list = []

    def spy(mesh, C_elem, rho_elem, eps0_elem=None, order=2):
        seen.append(None if eps0_elem is None
                    else np.array(eps0_elem, copy=True))
        return real(mesh, C_elem, rho_elem, eps0_elem, order=order)

    rs.assemble_plane_stress = spy
    try:
        seen.clear()
        old = _spoke_solve(None, rotor_temp_c=ROTOR_C, sleeve_temp_c=SLEEVE_C)
        eps_old = list(seen)
        seen.clear()
        new = _spoke_solve(SAME_AS_OLD, rotor_temp_c=ROTOR_C,
                           sleeve_temp_c=SLEEVE_C)
        eps_new = list(seen)
    finally:
        rs.assemble_plane_stress = real
    return old, eps_old, new, eps_new


def test_a_the_eigenstrain_is_bit_identical_to_the_old_path(old_and_new):
    _old, eps_old, _new, eps_new = old_and_new
    assert eps_old and len(eps_old) == len(eps_new)
    for i, (a, b) in enumerate(zip(eps_old, eps_new)):
        assert (a is None) == (b is None), i
        if a is not None:
            assert np.array_equal(a, b), f"eigenstrain call {i} differs"


def test_a_the_reported_per_part_temperatures_are_identical(old_and_new):
    """Every number the temperature block carries — the same floats, not close
    ones: these are pure arithmetic on the inputs, so `==` is the right test."""
    old, _eo, new, _en = old_and_new
    to, tn = old["thermal"], new["thermal"]
    assert to["rotor_temp_c"] == tn["rotor_temp_c"] == ROTOR_C
    assert to["sleeve_temp_c"] == tn["sleeve_temp_c"] == SLEEVE_C
    # no band on this fixture, so under the band-fit rule (2026-09-09) the
    # temperatures loaded nothing — in BOTH spellings
    assert to["active"] is tn["active"] is False
    assert to["model"] == tn["model"] == "band_fit"
    assert to["applied_as"] == tn["applied_as"] == "none — no retaining band"
    assert to["part_temps_c"] == tn["part_temps_c"] == SAME_AS_OLD
    assert set(to["parts"]) == set(tn["parts"])
    for name in to["parts"]:
        a, b = dict(to["parts"][name]), dict(tn["parts"][name])
        # `temp_source` is the ONE key that must differ: it says whether the
        # number was named per part or inherited, which is the whole point.
        assert a.pop("temp_source") != b.pop("temp_source")
        assert a == b, name
    assert to["notes"] == tn["notes"]
    assert old["thermal_notes"] == new["thermal_notes"]
    # …and which of them the caller actually named.
    assert to["part_temps_given"] == {}
    assert tn["part_temps_given"] == SAME_AS_OLD


def test_a_the_solved_answer_is_the_same_answer(old_and_new):
    """The stresses too, at the multi-threaded solver's own reproducibility."""
    old, _eo, new, _en = old_and_new
    co = old["cases"][old["primary_case"]]
    cn = new["cases"][new["primary_case"]]
    assert cn["rotor_od_growth_um"] == pytest.approx(co["rotor_od_growth_um"],
                                                     rel=1e-9)
    assert cn["max_displacement_um"] == pytest.approx(co["max_displacement_um"],
                                                      rel=1e-9)
    for name, part in co["parts"].items():
        assert cn["parts"][name]["von_mises_max_mpa"] == pytest.approx(
            part["von_mises_max_mpa"], rel=1e-8), name
    jo, jn = _joint(old), _joint(new)
    assert jn["pressure_mean_mpa"] == pytest.approx(jo["pressure_mean_mpa"],
                                                    rel=1e-8, abs=1e-9)
    assert jn["open_fraction"] == pytest.approx(jo["open_fraction"], abs=1e-12)


def test_a_an_unknown_part_key_is_refused_by_name():
    """A typo'd part silently dropped would solve a cold magnet and report a
    hot one — the client-facing rule: name it, never substitute a default."""
    with pytest.raises(ValueError) as exc:
        _spoke_solve({"rotor": 150.0})          # the Thermal payload's own name
    assert "rotor" in str(exc.value) and "rotor_core" in str(exc.value)


# ---------------------------------------------------------------------------
# (b) the physics: a temperature is a load on the BAND and nowhere else
# ---------------------------------------------------------------------------
# The reference state (everything at 20 °C) is the machine as drawn: the pole
# faces touch their magnet exactly, at zero pressure.  Every case below is that
# state plus a temperature map.
#
#   no band, any map           -> the cold answer, and a note saying so.
#   band, iron hot, band cold  -> the fit tightens by the iron's free growth at
#                                 the band's bore; the band goes into hoop
#                                 tension; the magnets and iron carry NO thermal
#                                 strain of their own.
#   free_expansion             -> the 2026-09-07 per-part eigenstrain (a hot
#                                 magnet pressing into its cold pocket), kept
#                                 as the solver's verification model only.

DT = 100.0                                        # K above the reference
ALPHA_FE = 12e-6                                  # 1/K, the 20SW1200 card


@pytest.fixture(scope="module")
def cold():
    return _spoke_solve(None)                     # everything at the reference


@pytest.fixture(scope="module")
def hot_magnet():
    return _spoke_solve({"magnet": REF_TEMP_C + DT})


@pytest.fixture(scope="module")
def hot_core():
    return _spoke_solve({"rotor_core": REF_TEMP_C + DT})


@pytest.fixture(scope="module")
def hot_everything():
    return _spoke_solve({"rotor_core": REF_TEMP_C + DT, "magnet": REF_TEMP_C + DT,
                         "shaft": REF_TEMP_C + DT, "sleeve": REF_TEMP_C + DT})


def _spied(fn):
    """Run ``fn`` with every eigenstrain array that reaches the assembler
    captured — the one honest place to say what the solve carried (see the
    docstring of ``old_and_new``)."""
    real = rs.assemble_plane_stress
    seen: list = []

    def spy(mesh, C_elem, rho_elem, eps0_elem=None, order=2):
        seen.append(None if eps0_elem is None else np.array(eps0_elem, copy=True))
        return real(mesh, C_elem, rho_elem, eps0_elem, order=order)

    rs.assemble_plane_stress = spy
    try:
        out = fn()
    finally:
        rs.assemble_plane_stress = real
    return out, seen


def test_b_the_reference_state_is_stress_free_and_just_touching(cold):
    assert cold["thermal"]["active"] is False
    j = _joint(cold)
    assert j["n_facets"] > 20, "no magnet/iron interface to make a claim about"
    assert j["pressure_max_mpa"] == pytest.approx(0.0, abs=1e-6)
    assert j["gap_max_um"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("which", ["hot_magnet", "hot_core", "hot_everything"])
def test_b_on_a_sleeveless_rotor_no_temperature_map_is_a_load(request, cold, which):
    """THE RULE (user 2026-09-09).  Whatever the map — a magnet 100 K above its
    pocket, the pocket 100 K above its magnet, everything hot — the joint is
    the cold joint: just touching, at zero pressure, with no gap.  The answer
    still carries every temperature it was given and says, in words, that none
    of them was a load."""
    hot = request.getfixturevalue(which)
    th = hot["thermal"]
    assert th["active"] is False
    assert th["model"] == "band_fit"
    assert th["applied_as"] == "none — no retaining band"
    assert any("no retaining band" in n and "not a mechanical load" in n
               for n in th["notes"]), th["notes"]
    # the temperatures and the strains the parts WOULD take are still reported
    # — what the user typed or the Thermal tab sent is never hidden
    if which == "hot_magnet":
        assert th["part_temps_c"]["magnet"] == pytest.approx(REF_TEMP_C + DT)
        assert th["part_temps_c"]["rotor_core"] == pytest.approx(REF_TEMP_C)
        assert th["parts"]["magnet"]["thermal_strain_ppm_1"] == pytest.approx(
            5e-6 * DT * 1e6, rel=1e-9)
        assert th["parts"]["rotor"]["delta_t_c"] == 0.0

    before, after = _joint(cold), _joint(hot)
    assert after["pressure_max_mpa"] == pytest.approx(before["pressure_max_mpa"],
                                                      abs=1e-6)
    assert after["gap_max_um"] == pytest.approx(before["gap_max_um"], abs=1e-6)
    assert after["open_fraction"] == pytest.approx(before["open_fraction"],
                                                   abs=1e-9)
    co = cold["cases"][cold["primary_case"]]
    ch = hot["cases"][hot["primary_case"]]
    assert ch["rotor_od_growth_um"] == pytest.approx(co["rotor_od_growth_um"],
                                                     abs=1e-6)
    for name, part in co["parts"].items():
        assert ch["parts"][name]["von_mises_max_mpa"] == pytest.approx(
            part["von_mises_max_mpa"], abs=1e-6), name


def test_b_on_a_sleeveless_rotor_nothing_thermal_reaches_the_assembler():
    """The same claim at the one place it is exact: the assembler never sees
    an eigenstrain at all (not even a zero one)."""
    _out, seen = _spied(lambda: _spoke_solve({"rotor_core": REF_TEMP_C + DT,
                                              "magnet": REF_TEMP_C + DT}))
    assert seen, "no assembly happened"
    assert all(e is None for e in seen), [None if e is None else float(np.abs(e).max())
                                          for e in seen]


def test_b_with_a_band_the_temperature_enters_only_through_the_band():
    """The one place a temperature IS a load.

    Iron and magnets at 150 °C under a band left at 20 °C.  What the solve
    carries is the band's own hoop pre-stretch, sized by the FIT AT TEMPERATURE
    — the free growth of what sits under the band, alpha_Fe*dT*R_OD to within
    the magnets' share — and nothing on any other element.  The band goes into
    hoop tension; the magnets and the iron carry no thermal strain of their own.
    """
    hot = {"rotor_core": REF_TEMP_C + 130.0, "magnet": REF_TEMP_C + 130.0,
           "shaft": REF_TEMP_C + 130.0, "sleeve": REF_TEMP_C}
    out, seen = _spied(lambda: _spoke_solve(hot, sleeve=True))
    th = out["thermal"]
    assert th["active"] is True
    assert th["model"] == "band_fit"
    assert th["applied_as"].startswith("band fit only"), th["applied_as"]
    assert th["fit"]["free_growth_sleeve_bore_um"] == pytest.approx(0.0, abs=1e-9)
    eff = out["interference_effective_mm"]
    ana = ALPHA_FE * 130.0 * R_OD                 # mm — the iron alone
    assert 0.85 * ana < eff < 1.05 * ana, (eff, ana)   # the magnets grow less
    assert out["interference_mm"] == 0.0

    # what reached the assembler: the LAST array is the main solve's (the
    # earlier ones are the free-growth measurements), and it is the band's
    # hoop eigenstrain on sleeve elements ONLY
    main = seen[-1]
    assert main is not None
    rm = rs.build_rotor_mesh(_spoke_polys(True), mesh_size_mm=2.0)
    sleeve = rm.part_tri == rs.PART_SLEEVE
    assert sleeve.any() and (~sleeve).any()
    assert np.abs(main[~sleeve]).max() == 0.0, "a part other than the band was strained"
    hoop = np.abs(main[sleeve][:, :2]).sum(axis=1)          # e_xx + e_yy = e_t0
    assert np.allclose(hoop, eff * 1e-3 / rm.r_sleeve_mean_m, rtol=1e-9)

    # …and the physics that follows: the band is stretched, the pocket is not
    case = out["cases"][out["primary_case"]]
    assert case["parts"]["sleeve"]["hoop_max_mpa"] > 1.0
    assert _joint(out)["pressure_min_mpa"] >= 0.0


def test_b_free_expansion_is_still_the_solver_s_own_verification_model():
    """The 2026-09-07 physics is not deleted, it is off the routes: asked for
    by name, a magnet hotter than its pocket still presses into it — which is
    what the Lamé and seating suites rely on."""
    out = _spoke_solve({"magnet": REF_TEMP_C + DT}, thermal_model="free_expansion")
    th = out["thermal"]
    assert th["model"] == "free_expansion" and th["active"] is True
    assert th["applied_as"].startswith("per-part thermal eigenstrain")
    assert _joint(out)["pressure_max_mpa"] > 1.0
    with pytest.raises(ValueError):
        _spoke_solve(None, thermal_model="everywhere")


# ---------------------------------------------------------------------------
# (c) the route
# ---------------------------------------------------------------------------

CHEAP = {"loads": "centrifugal", "rpm": 8000, "cases": "single", "order": 1,
         "mesh_size_mm": 3.0, "lift_off_solves": 0, "field": False}


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_last_store(tmp_path, monkeypatch):
    """Keep this module's solves out of the shared last-result store and off the
    disk beside the sandbox config — the same bargain tests/test_mechanical_last
    strikes, for the same reason (that store is module state)."""
    from motor_ai_sim.routes import mechanical as mech

    monkeypatch.setattr(mech, "_LAST", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(mech, "_last_store_path",
                        lambda: str(tmp_path / ".last_mechanical.pkl"))
    yield


def test_c_the_route_takes_a_temperature_per_part_and_echoes_them(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={**CHEAP, "rotor_temp_c": 150, "sleeve_temp_c": 162,
                           "magnet_temp_c": 163, "rotor_core_temp_c": 161,
                           "shaft_temp_c": 158})
    assert r.status_code == 200, r.text[:600]
    th = r.json()["thermal"]
    # The line the panel and the Compare row print, straight off the answer.
    assert th["part_temps_c"] == {"magnet": 163.0, "rotor_core": 161.0,
                                  "shaft": 158.0, "sleeve": 162.0}
    assert th["part_temps_given"] == {"magnet": 163.0, "rotor_core": 161.0,
                                      "shaft": 158.0}
    # `rotor_temp_c` is still reported — it is the FALLBACK, and it was not used.
    assert th["rotor_temp_c"] == pytest.approx(150.0)
    for name, key in (("rotor", "rotor_core"), ("magnet", "magnet"),
                      ("shaft", "shaft")):
        if name in th["parts"]:
            assert th["parts"][name]["temp_c"] == pytest.approx(
                th["part_temps_c"][key]), name
            assert th["parts"][name]["temp_source"] == "part", name

    # …and the request that produced it, so /last restores the panel's fields.
    p = client.get("/api/mechanical/last", params={"field": False}).json()
    p = p["rotor_stress"]["params"]
    assert p["magnet_temp_c"] == pytest.approx(163.0)
    assert p["rotor_core_temp_c"] == pytest.approx(161.0)
    assert p["shaft_temp_c"] == pytest.approx(158.0)
    assert p["sleeve_temp_c"] == pytest.approx(162.0)


def test_c_a_request_naming_no_part_is_the_request_it_always_was(client):
    """The compatibility claim: nothing new appears in the persisted params, and
    every part reports the scalar it has always inherited."""
    r = client.get("/api/mechanical/rotor_stress",
                   params={**CHEAP, "rpm": 8100, "rotor_temp_c": 150,
                           "sleeve_temp_c": 80})
    assert r.status_code == 200, r.text[:600]
    th = r.json()["thermal"]
    assert th["part_temps_given"] == {}
    assert th["part_temps_c"] == {"rotor_core": 150.0, "magnet": 150.0,
                                  "shaft": 150.0, "sleeve": 80.0}
    for name, part in th["parts"].items():
        assert part["temp_source"] == ("sleeve_temp_c" if name == "sleeve"
                                       else "rotor_temp_c"), name

    p = client.get("/api/mechanical/last", params={"field": False}).json()
    p = p["rotor_stress"]["params"]
    for k in ("magnet_temp_c", "rotor_core_temp_c", "shaft_temp_c"):
        assert k not in p, k


def test_c_the_part_temperatures_key_the_cache_only_when_given():
    from motor_ai_sim.routes.mechanical import _cache_key

    base = dict(geo_ov=None, assign={"rotor_core": "20SW1200"}, rpm=8000.0,
                osf=1.0, interf=0.0, mesh_mm=3.0, order=1, contacts={},
                cases="single", loads="centrifugal", torque_nm=0.0,
                rotor_temp_c=150.0, sleeve_temp_c=80.0)
    plain = _cache_key(**base)
    # Absent, empty and None must ALL be the key this route has always built —
    # otherwise every entry already in the cache is orphaned by this change.
    assert _cache_key(**base, part_temps_c=None) == plain
    assert _cache_key(**base, part_temps_c={}) == plain

    hot_mag = _cache_key(**base, part_temps_c={"magnet": 163.0})
    assert hot_mag != plain
    assert _cache_key(**base, part_temps_c={"magnet": 164.0}) != hot_mag
    # …and it is the SET, not the order it was written in.
    assert (_cache_key(**base, part_temps_c={"magnet": 163.0, "shaft": 158.0})
            == _cache_key(**base, part_temps_c={"shaft": 158.0, "magnet": 163.0}))


def test_c_two_magnet_temperatures_are_two_answers(client):
    """The cache key claim, end to end: the second press must not be served the
    first one's stresses."""
    p = {**CHEAP, "rpm": 8200, "rotor_temp_c": 150}
    a = client.get("/api/mechanical/rotor_stress",
                   params={**p, "magnet_temp_c": 150}).json()
    b = client.get("/api/mechanical/rotor_stress",
                   params={**p, "magnet_temp_c": 260}).json()
    assert b["cached"] is False, "a hotter magnet was served from the cache"
    assert b["thermal"]["part_temps_c"]["magnet"] == pytest.approx(260.0)
    again = client.get("/api/mechanical/rotor_stress",
                       params={**p, "magnet_temp_c": 150}).json()
    assert again["cached"] is True
    assert again["thermal"]["part_temps_c"]["magnet"] == pytest.approx(
        a["thermal"]["part_temps_c"]["magnet"])


def test_c_an_impossible_part_temperature_is_refused_by_its_field(client):
    r = client.get("/api/mechanical/rotor_stress",
                   params={**CHEAP, "magnet_temp_c": -400})
    assert r.status_code == 422, r.text[:300]
    assert "magnet_temp_c" in r.text


# ---------------------------------------------------------------------------
# (d) the hook the orchestrator calls
# ---------------------------------------------------------------------------

# The magnet–rotor joint is BONDED here on purpose (2026-09-09): these tests
# are about the plumbing (which temperatures and which panel fields reach the
# solve), and they run on the sandbox copy of whatever machine is live.  With a
# `separation` joint the answer depends on whether THAT machine's pocket holds
# its magnet — the 40 mm's does not (a 0.1 mm gap above the magnet), and the
# solver now refuses such a solve by name (tests/test_mechanical_runaway.py).
PANEL = {"cases": "single", "loads": "centrifugal", "rpm": "20000",
         "rpm1": "8300", "osf": "1.25", "interf": "0", "meshMm": "3.0",
         "torque": "", "rotorTempC": "150", "sleeveTempC": "150",
         "contacts": {"magnet_rotor": {"type": "bonded", "mu": 0.0},
                      "shaft_rotor": {"type": "bonded", "mu": 0.0}}}


@pytest.fixture
def panel_saved(client, tmp_path, monkeypatch):
    """The user's saved Mechanical-panel fields, in a throwaway store."""
    from motor_ai_sim.routes import panel_settings as ps

    monkeypatch.setattr(ps, "_store_path",
                        lambda: str(tmp_path / ".panel_settings.json"))
    r = client.put("/api/panel_settings/mechanical", json={"settings": PANEL})
    assert r.status_code == 200, r.text[:300]
    return PANEL


def test_d_the_hook_solves_at_the_temperatures_it_is_given(panel_saved):
    """`run_rotor_stress_at` is the coupled entry point: the temperatures come
    from the caller, everything else from the panel the user actually set."""
    from motor_ai_sim.routes.mechanical import run_rotor_stress_at

    out = run_rotor_stress_at({"magnet": 163.0, "rotor_core": 161.0,
                               "shaft": 158.0, "sleeve": 162.0},
                              field=False)
    th = out["thermal"]
    assert th["part_temps_c"] == {"magnet": 163.0, "rotor_core": 161.0,
                                  "shaft": 158.0, "sleeve": 162.0}
    assert th["part_temps_given"] == {"magnet": 163.0, "rotor_core": 161.0,
                                      "shaft": 158.0}
    # …and the machine it solved is the one the PANEL describes: the proof rpm
    # (single-speed mode), that mesh size, that case table, those loads.
    assert out["rpm"] == pytest.approx(8300.0)
    assert out["case_mode"] == "single"
    assert out["loads"] == "centrifugal"
    assert out["mesh"]["mesh_size_mm"] == pytest.approx(3.0)
    assert out["interference_mm"] == pytest.approx(0.0)
    assert set(out["contacts"]) >= {"magnet_rotor"}
    assert out["contacts"]["magnet_rotor"]["type"] == "bonded"


def test_d_the_hook_leaves_its_answer_as_the_tabs_last_result(client, panel_saved):
    """The user opens Mechanical after a coupled run and must find it there."""
    from motor_ai_sim.routes.mechanical import run_rotor_stress_at

    run_rotor_stress_at({"magnet": 171.0, "rotor_core": 169.0}, field=False)
    entry = client.get("/api/mechanical/last",
                       params={"field": False}).json()["rotor_stress"]
    assert entry is not None
    assert entry["result"]["thermal"]["part_temps_c"]["magnet"] == pytest.approx(171.0)
    assert entry["params"]["magnet_temp_c"] == pytest.approx(171.0)
    assert entry["params"]["rpm"] == pytest.approx(8300.0)
    # A part nobody named inherits the ROUTE's own fallback, which is what
    # `rotor_temp_c` is for: the shaft was not given, so it takes the iron's
    # number (169) rather than a hand-typed field left over on the panel.  The
    # SLEEVE is the other side of that rule — nothing named it and no thermal
    # answer covered it, so it keeps the panel's own manual 150 °C.  Neither is
    # ever invented here.
    th = entry["result"]["thermal"]
    assert th["part_temps_c"]["shaft"] == pytest.approx(169.0)
    assert th["rotor_temp_c"] == pytest.approx(169.0)
    assert th["part_temps_c"]["sleeve"] == pytest.approx(150.0)
    assert th["sleeve_temp_c"] == pytest.approx(150.0)


def test_d_explicit_parameters_beat_the_panel(panel_saved):
    """An orchestrator that knows the speed it wants says so, and the panel does
    not override it.  `rotor` is accepted for the core — that is what the
    Thermal result's `components` calls the iron."""
    from motor_ai_sim.routes.mechanical import run_rotor_stress_at

    out = run_rotor_stress_at({"rotor": 155.0, "magnet": 157.0},
                              rpm=8400, mesh_size_mm=3.5, cases="single",
                              loads="centrifugal", field=False)
    assert out["rpm"] == pytest.approx(8400.0)
    assert out["mesh"]["mesh_size_mm"] == pytest.approx(3.5)
    assert out["thermal"]["part_temps_c"]["rotor_core"] == pytest.approx(155.0)
    assert out["thermal"]["part_temps_given"] == {"magnet": 157.0,
                                                  "rotor_core": 155.0}


def test_d_the_hook_refuses_a_part_it_does_not_know():
    from motor_ai_sim.routes.mechanical import run_rotor_stress_at

    with pytest.raises(ValueError) as exc:
        run_rotor_stress_at({"winding": 180.0})
    assert "winding" in str(exc.value)
    with pytest.raises(ValueError):
        run_rotor_stress_at({"magnet": float("nan")})
    with pytest.raises(ValueError):
        run_rotor_stress_at({"magnet": "hot"})
    # `rotor` and `rotor_core` are the same part under two names: two DIFFERENT
    # values for it is an ambiguity, and picking one quietly is how a coupled
    # run ends up solving a temperature nobody asked for.
    with pytest.raises(ValueError) as exc:
        run_rotor_stress_at({"rotor": 160.0, "rotor_core": 170.0})
    assert "same part" in str(exc.value)
