"""The per-duty FIELD store — ``motor_ai_sim.duty_fields``.

Added 2026-09-09 for the user's ask: *"давай сделаем сохранение всех полей
моделирования, как электромагнитных, так и тепловых и механических"*.  A
configuration has several duties and the report must show each duty's own maps
side by side; every field the product solves used to be stored ONCE PER MACHINE
(``.last_transient_field.pkl``, ``.last_thermal.pkl``, ``.last_mechanical.pkl``)
and the next solve overwrote it.

What is pinned here is the CONTRACT of the store, in the order it matters:

  (a) a solved field round-trips — the arrays come back equal to the solver's own
      within float32, and the meta (kind, duty, fingerprint, operating point,
      units, inventory) survives with them;
  (b) a re-solve REPLACES: one file per (duty, kind), never a growing pile —
      which is the whole size discipline;
  (c) ``have`` lists what exists and ``drop`` removes it, so a deleted duty
      leaves no graveyard;
  (d) nothing here can fail a solve: an absent file, a corrupt file and a writer
      that raises all come back as ``None``/no-op, and the solve routes' tails
      keep running;
  (e) the route ``GET /api/family/duty_fields/{die}/{cfg}`` lists the store and
      never the arrays.

The payloads are REAL: the 30 mm 12s/14p fixture the thermal and mechanical
suites are pinned on, solved once for the module through the product's own
seams — ``store_em_run`` for the electromagnetic snapshot, ``/api/thermal/field``
and ``/api/mechanical/rotor_stress`` for the other two — driven by ``?geo=`` so
nothing on disk is touched.  A hand-built dict of arrays would test this module's
idea of a field instead of the field.

The SIZES the module's docstring claims are printed by
``test_z_sizes_measured`` (run with ``-s``): the claim is a measurement, and it
is re-measured on every run.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
import time

import numpy as np
import pytest

from tests.test_thermal_routes import FAST, GEO_30MM, store_em_run

DIE = "FIELDSDIE 30"
CFG = "F30"
DUTY = "rated 30С"          # a Cyrillic С on purpose — the 2026-09-01 trap
DUTY2 = "peak"
EM_RUN_ID = "2026-09-09T09:00:00"

#: filled by `solved`, printed by the last test — the measurement behind the
#: size claim in `duty_fields`'s module docstring.
_SIZES: dict = {}


# ---------------------------------------------------------------------------
# Isolation: a throwaway catalogue root and empty machine-level stores
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def store_root():
    """Point the catalogue (and with it this store) at a throwaway tree.

    ``duty_fields`` derives its root from ``routes.family._DIES_DIR`` when the
    router is loaded — which is exactly so a suite that redirects the catalogue
    redirects the fields with it.  Nothing this module writes can reach
    ``config/dies``.
    """
    from motor_ai_sim.routes import family as fam

    mp = pytest.MonkeyPatch()
    root = pathlib.Path(tempfile.mkdtemp(prefix="duty_fields_")) / "dies"
    root.mkdir(parents=True)
    mp.setattr(fam, "_DIES_DIR", root)
    yield root
    mp.undo()
    shutil.rmtree(root.parent, ignore_errors=True)


@pytest.fixture(scope="module", autouse=True)
def _isolate_machine_stores():
    """Redirect the three machine-level stores this module's solves fill.

    They persist beside ``config/`` — a sandbox copy, but the pickles are the
    user's own last run and the suite promises not to touch them (the store
    clobber of 2026-09-08 is why).
    """
    from motor_ai_sim.routes import mechanical as me
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="duty_fields_stores_"))
    saved = {"th": dict(th._LAST), "th_loaded": th._LAST_LOADED,
             "me": dict(me._LAST), "me_loaded": me._LAST_LOADED,
             "snap": dict(sim._transient_field_snap)}
    mp.setattr(th, "_last_store_path", lambda: str(tmp / ".last_thermal.pkl"))
    mp.setattr(th, "_loss_maps_path", lambda: str(tmp / ".loss_maps.pkl"))
    mp.setattr(me, "_last_store_path", lambda: str(tmp / ".last_mechanical.pkl"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    th._LAST_LOADED = me._LAST_LOADED = True            # never load the disk
    for store in (th._LAST, me._LAST, sim._transient_field_snap):
        store.clear()
    th._LOSS_MAPS.clear()

    yield tmp

    th._LAST.clear(), th._LAST.update(saved["th"])
    me._LAST.clear(), me._LAST.update(saved["me"])
    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved["snap"])
    th._LAST_LOADED, me._LAST_LOADED = saved["th_loaded"], saved["me_loaded"]
    mp.undo()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def active():
    """Say which duty is loaded, without writing ``.family_context.json``.

    The real reader is ``duty_results.active_context``; pointing the module's own
    seam at the fixture duty is the same statement without a file the rest of the
    session shares.
    """
    from motor_ai_sim import duty_fields as df

    mp = pytest.MonkeyPatch()
    mp.setattr(df, "active_context", lambda: (DIE, CFG, DUTY))
    yield (DIE, CFG, DUTY)
    mp.undo()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# One electromagnetic run, one temperature map, one rotor stress solve
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def solved(client, store_root, active):
    """The three real payloads, solved once for the whole module.

    The thermal and mechanical solves go in through the ROUTES, so their tails
    (``_remember_last`` → ``duty_fields.save_active``) are the code under test:
    what lands on disk is what a real solve of a real duty lands there.
    """
    from motor_ai_sim.routes import simulation as sim

    geo_json = json.dumps(GEO_30MM)

    info = store_em_run(GEO_30MM, run_id=EM_RUN_ID)
    time.sleep(0.3)                       # the persist is a daemon thread
    snap = list(sim._transient_field_snap.values())[-1]

    th = client.get("/api/thermal/field",
                    params={**FAST, "geo": geo_json, "cooling_mode": "manual",
                            "ambient_temp": 25.0, "h_conv": 50.0})
    assert th.status_code == 200, th.text[:600]

    mech = client.get("/api/mechanical/rotor_stress",
                      params={"geo": geo_json, "loads": "centrifugal",
                              "rpm": FAST["rpm"], "mesh_size_mm": 1.2,
                              "order": 1, "lift_off_solves": 0,
                              "cases": "single"})
    assert mech.status_code == 200, mech.text[:600]

    return {"geo": geo_json, "em": snap, "solver": info["solver_result"],
            "thermal": th.json(), "mechanical": mech.json()}


# ---------------------------------------------------------------------------
# (a) round trip
# ---------------------------------------------------------------------------


def test_a_the_solve_routes_stored_the_fields(solved, store_root):
    """The hooks fired: a thermal and a rotor-stress solve of the ACTIVE duty
    left their maps under the duty's own folder, without being asked to."""
    from motor_ai_sim import duty_fields as df

    for kind in ("thermal", "rotor_stress"):
        p = df.field_path(DIE, CFG, DUTY, kind)
        assert p.is_file(), f"{kind}: nothing at {p}"
        _SIZES[kind] = p.stat().st_size

    # …and the layout is the runs sidecar's own, so a configuration rename
    # carries it (family._refile_config_runs moves runs/<cfg> whole).
    p = df.field_path(DIE, CFG, DUTY, "thermal")
    assert p.parent.name == "fields"
    assert p.parent.parent.parent.name == CFG
    assert p.parent.parent.parent.parent.name == "runs"
    # A Cyrillic duty name still gets a folder — and not the Latin one's.
    assert df._duty_stem(DUTY) != df._duty_stem("rated 30C")


def test_a_em_round_trip(solved, active):
    """The electromagnetic snapshot: mesh, |B|, loss density, A_z."""
    from motor_ai_sim import duty_fields as df

    path = df.save_active("em", solved["em"],
                          geometry_fingerprint="fp-em",
                          computed_at=EM_RUN_ID)
    assert path, "the EM field was not stored"
    _SIZES["em"] = pathlib.Path(path).stat().st_size

    got = df.load(DIE, CFG, DUTY, "em")
    assert got is not None
    fld = solved["em"]["field"]

    np.testing.assert_allclose(got["vertices"], np.asarray(fld["P_mm"]),
                               rtol=1e-6, atol=1e-3)
    np.testing.assert_array_equal(got["triangles"], np.asarray(fld["T"]))
    np.testing.assert_array_equal(got["tags"], np.asarray(fld["tags"], int))
    b = np.hypot(np.asarray(fld["Bx"], float), np.asarray(fld["By"], float))
    np.testing.assert_allclose(got["b_mag_per_tri"], b, rtol=2e-6, atol=1e-6)
    assert "loss_dens_per_tri" in got, "the run's loss map was not kept"
    np.testing.assert_allclose(got["loss_dens_per_tri"],
                               np.asarray(fld["loss_dens"], float),
                               rtol=2e-6, atol=1e-3)
    np.testing.assert_allclose(got["a_z_per_node"], np.asarray(fld["A"], float),
                               rtol=2e-6, atol=1e-9)

    m = got["meta"]
    assert m["kind"] == "em" and m["duty"] == DUTY and m["die"] == DIE
    assert m["geometry_fingerprint"] == "fp-em"
    assert m["computed_at"] == EM_RUN_ID
    # the point is the RUN's own scalars, not the caller's idea of them
    scal = solved["em"]["scalars"]
    assert m["point"]["rpm"] == pytest.approx(float(scal["rpm"]))
    assert m["point"]["T_avg_Nm"] == pytest.approx(float(scal["T_avg_Nm"]),
                                                   rel=1e-9)
    assert m["units"]["vertices"] == "mm"
    # the inventory names every array in the file, and nothing else
    assert set(m["arrays"]) == {k for k in got if k != "meta"}


def test_a_thermal_round_trip(solved):
    """The temperature map: the arrays ``report._thermal_map`` draws."""
    from motor_ai_sim import duty_fields as df

    got = df.load(DIE, CFG, DUTY, "thermal")
    assert got is not None
    res = solved["thermal"]

    np.testing.assert_allclose(got["vertices"], np.asarray(res["vertices"], float),
                               rtol=1e-5, atol=1e-9)
    np.testing.assert_array_equal(got["triangles"],
                                  np.asarray(res["triangles"], int))
    np.testing.assert_array_equal(got["domain_per_tri"],
                                  np.asarray(res["domain_per_tri"], int))
    np.testing.assert_allclose(got["temperature_per_node"],
                               np.asarray(res["temperature_per_node"], float),
                               rtol=1e-5, atol=1e-3)
    np.testing.assert_allclose(got["heat_flux_per_tri"],
                               np.asarray(res["heat_flux_per_tri"], float),
                               rtol=1e-4, atol=1e-2)

    m = got["meta"]
    assert m["kind"] == "thermal" and m["duty"] == DUTY
    # the part names ride along (the thermal vocabulary's own, in which the
    # winding is 'coil'), so a map can be labelled without a second table
    assert m["part_names"] and "coil" in set(m["part_names"].values())
    assert m["point"]["T_max"] == pytest.approx(res["T_max"], rel=1e-6)
    assert m["units"]["temperature_per_node"] == "degC"


def test_a_rotor_stress_round_trip(solved):
    """The stress map: the PRIMARY case's von Mises, principal, SF and
    displacement, plus the contact segments while they are cheap."""
    from motor_ai_sim import duty_fields as df

    got = df.load(DIE, CFG, DUTY, "rotor_stress")
    assert got is not None
    res = solved["mechanical"]
    fld = res["field"]
    case = res.get("primary_case") or next(iter(fld["cases"]))
    cf = fld["cases"][case]

    np.testing.assert_allclose(got["vertices"], np.asarray(fld["vertices"], float),
                               rtol=1e-5, atol=1e-4)
    np.testing.assert_array_equal(got["triangles"],
                                  np.asarray(fld["triangles"], int))
    np.testing.assert_allclose(got["vm_per_tri"],
                               np.asarray(cf["vm_per_tri"], float),
                               rtol=1e-5, atol=1e-3)
    for key in ("s_p1_per_tri", "sf_per_tri", "u_per_node", "u_mag_per_node"):
        assert key in got, f"{key} was not kept"
        np.testing.assert_allclose(got[key], np.asarray(cf[key], float),
                                   rtol=1e-5, atol=1e-3)

    m = got["meta"]
    assert m["kind"] == "rotor_stress" and m["case"] == case
    assert m["point"]["rpm"] == pytest.approx(res["cases"][case]["rpm"])
    assert m["units"]["vm_per_tri"] == "MPa"
    # contact segments: kept as `contact_seg::<pair>` when the solve had any
    pairs = m.get("contact_pairs") or []
    for label in pairs:
        assert f"contact_seg::{label}" in got


# ---------------------------------------------------------------------------
# (b) a re-solve replaces, it does not accumulate
# ---------------------------------------------------------------------------


def test_b_resave_replaces(solved, active):
    from motor_ai_sim import duty_fields as df

    d = df.fields_dir(DIE, CFG, DUTY)
    before = sorted(p.name for p in d.glob("*"))
    first = df.load(DIE, CFG, DUTY, "thermal")["meta"]["saved_at"]

    # the same duty solved again — a different fingerprint, the same file
    time.sleep(1.1)                       # the stamp has one-second resolution
    assert df.save(DIE, CFG, DUTY, "thermal", solved["thermal"],
                   geometry_fingerprint="fp-second")

    after = sorted(p.name for p in d.glob("*"))
    assert after == before, f"the folder grew: {before} -> {after}"
    assert not list(d.glob("*.tmp*")), "a temp file was left behind"
    m = df.load(DIE, CFG, DUTY, "thermal")["meta"]
    assert m["geometry_fingerprint"] == "fp-second"
    assert m["saved_at"] != first, "the replacement kept the old stamp"


# ---------------------------------------------------------------------------
# (c) have() and drop()
# ---------------------------------------------------------------------------


def test_c_have_lists_what_exists(solved, active):
    from motor_ai_sim import duty_fields as df

    # a second duty with one kind only — so "which duty has which map" has
    # something to say beyond "all of them"
    assert df.save(DIE, CFG, DUTY2, "thermal", solved["thermal"])

    have = df.have(DIE, CFG)
    assert set(have) == {DUTY, DUTY2}, have
    assert [r["kind"] for r in have[DUTY]] == ["em", "thermal", "rotor_stress"]
    assert [r["kind"] for r in have[DUTY2]] == ["thermal"]
    for row in have[DUTY]:
        assert row["bytes"] > 0 and row["saved_at"]
        assert "vertices" in row["arrays"]
    assert df.kinds_present(DIE, CFG)[DUTY2] == ["thermal"]

    # …and an unknown configuration is empty, not an error
    assert df.have(DIE, "NOSUCH") == {}


def test_c_drop_removes_one_duty(solved, active):
    from motor_ai_sim import duty_fields as df

    n = df.drop(DIE, CFG, DUTY2)
    assert n == 1
    assert set(df.have(DIE, CFG)) == {DUTY}
    assert df.load(DIE, CFG, DUTY2, "thermal") is None
    # dropping again is a no-op, not a failure
    assert df.drop(DIE, CFG, DUTY2) == 0


def test_c_a_configuration_rename_carries_the_fields(solved, active, store_root):
    """Renaming a configuration moves its whole ``runs/<cfg>`` folder — and the
    fields live inside it, which is the entire reason they were put there."""
    from motor_ai_sim import duty_fields as df
    from motor_ai_sim.routes import family as fam

    assert df.save(DIE, "TOMOVE", DUTY, "thermal", solved["thermal"])
    doc = {"duties": [{"name": DUTY, "runs": {}}]}
    fam._refile_config_runs(DIE, doc, "TOMOVE", "MOVED", copy=False)

    assert df.have(DIE, "TOMOVE") == {}, "the old name still has fields"
    moved = df.have(DIE, "MOVED")
    assert [r["kind"] for r in moved[DUTY]] == ["thermal"]
    assert df.load(DIE, "MOVED", DUTY, "thermal") is not None
    df.drop(DIE, "MOVED")


def test_c_delete_duty_route_drops_the_fields(client, store_root, solved, active,
                                              monkeypatch):
    """The catalogue's own delete takes the maps with it."""
    import yaml

    from motor_ai_sim import duty_fields as df
    from motor_ai_sim.routes import family as fam

    (store_root / DIE).mkdir(parents=True, exist_ok=True)
    (store_root / DIE / "die.yaml").write_text(
        yaml.safe_dump({"name": DIE, "geometry": dict(GEO_30MM)}),
        encoding="utf-8")
    (store_root / DIE / f"{CFG}.yaml").write_text(
        yaml.safe_dump({"name": CFG, "die": DIE,
                        "duties": [{"name": DUTY}, {"name": DUTY2}]},
                       allow_unicode=True), encoding="utf-8")
    assert df.save(DIE, CFG, DUTY2, "thermal", solved["thermal"])
    monkeypatch.setattr(fam, "require_admin", lambda: {"admin": True})

    from motor_ai_sim.api import app
    app.dependency_overrides[fam.require_admin] = lambda: {"email": "t@t"}
    try:
        r = client.delete(f"/api/family/duty/{DIE}/{CFG}/{DUTY2}")
        assert r.status_code == 200, r.text[:400]
        assert r.json()["fields_deleted"] == 1
    finally:
        app.dependency_overrides.pop(fam.require_admin, None)
    assert set(df.have(DIE, CFG)) == {DUTY}


# ---------------------------------------------------------------------------
# (d) nothing here may fail a solve
# ---------------------------------------------------------------------------


def test_d_absent_and_corrupt_read_as_none(solved, active, store_root):
    from motor_ai_sim import duty_fields as df

    assert df.load("NO DIE", CFG, DUTY, "thermal") is None
    assert df.load(DIE, CFG, "never solved", "thermal") is None
    assert df.load(DIE, CFG, DUTY, "not_a_kind") is None

    p = df.field_path(DIE, CFG, DUTY2, "em")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"PK\x03\x04 this is not an npz")
    assert df.load(DIE, CFG, DUTY2, "em") is None, "a corrupt file must not raise"
    # …and it does not poison the listing of the duties around it
    assert DUTY in df.have(DIE, CFG)
    df.drop(DIE, CFG, DUTY2)


def test_d_a_writer_that_raises_does_not_fail_the_caller(solved, active,
                                                         monkeypatch):
    from motor_ai_sim import duty_fields as df

    def _boom(*a, **kw):
        raise OSError("the virus scanner has the file")

    monkeypatch.setattr(np, "savez_compressed", _boom)
    assert df.save(DIE, CFG, DUTY, "thermal", solved["thermal"]) is None
    assert df.save_active("thermal", solved["thermal"]) is None

    # …and the solve route's tail runs to the end anyway: _remember_last is what
    # a finished solve calls, and it must return normally with the store broken.
    from motor_ai_sim.routes import thermal as th
    th._remember_last("field", solved["thermal"], {"rpm": FAST["rpm"]}, "fp-x")
    assert th._LAST["field"]["result"] is solved["thermal"]

    # the previous good file is still there — a failed write replaced nothing
    monkeypatch.undo()
    assert df.load(DIE, CFG, DUTY, "thermal") is not None


def test_d_no_active_duty_stores_nothing(solved, monkeypatch):
    from motor_ai_sim import duty_fields as df

    monkeypatch.setattr(df, "active_context", lambda: None)
    assert df.save_active("thermal", solved["thermal"]) is None
    monkeypatch.undo()


def test_d_junk_payloads_are_declined(active):
    from motor_ai_sim import duty_fields as df

    for kind in df.KINDS:
        assert df.save(DIE, CFG, "junk", kind, {"nothing": "useful"}) is None
        assert df.save(DIE, CFG, "junk", kind, None) is None
    assert df.have(DIE, CFG).get("junk") is None


# ---------------------------------------------------------------------------
# (e) the route
# ---------------------------------------------------------------------------


def test_e_route_lists_the_store_and_not_the_arrays(client, store_root, solved,
                                                    active):
    import yaml

    from motor_ai_sim import duty_fields as df

    (store_root / DIE).mkdir(parents=True, exist_ok=True)
    (store_root / DIE / "die.yaml").write_text(
        yaml.safe_dump({"name": DIE, "geometry": dict(GEO_30MM)}),
        encoding="utf-8")
    (store_root / DIE / f"{CFG}.yaml").write_text(
        yaml.safe_dump({"name": CFG, "die": DIE, "duties": [{"name": DUTY}]},
                       allow_unicode=True), encoding="utf-8")
    r = client.get(f"/api/family/duty_fields/{DIE}/{CFG}")
    assert r.status_code == 200, r.text[:400]
    d = r.json()
    assert d["die"] == DIE and d["config"] == CFG
    assert d["kinds"] == list(df.KINDS)
    row = next(x for x in d["duties"] if x["duty"] == DUTY)
    assert row["kinds"] == ["em", "thermal", "rotor_stress"]
    assert row["bytes"] == sum(f["bytes"] for f in row["fields"]) > 0
    # a LISTING: not one number of any field is in the response
    blob = json.dumps(d)
    assert "vertices" in blob                      # named in the inventory…
    assert len(blob) < 20000, "the route is shipping arrays"
    # a name that names nothing is a 404, the same way every catalogue read is
    # — never an empty listing, which would read as "solve it again"
    assert client.get("/api/family/duty_fields/NOPE/NOPE").status_code == 404
    assert client.get(f"/api/family/duty_fields/{DIE}/NOSUCH").status_code == 404


# ---------------------------------------------------------------------------
# The measurement behind the docstring's size claim
# ---------------------------------------------------------------------------


def test_z_sizes_measured(solved):
    """Print what one duty of the fixture costs, and hold the ~2 MB ceiling.

    The 30 mm fixture on a coarse mesh is much smaller than the user's 200 mm
    machine (measured 2026-09-09 straight out of the live pickles: em 314 949 B,
    thermal 317 360 B, rotor_stress 317 943 B — 950 252 B for the duty, against
    6 821 500 B of machine-level stores).  What this asserts is the RULE the
    docstring states, on whatever machine the suite is run against.
    """
    total = sum(_SIZES.values())
    print("\nper-duty field store, 30 mm fixture:")
    for kind in ("em", "thermal", "rotor_stress"):
        print(f"  {kind:14s} {_SIZES.get(kind, 0):9d} B")
    print(f"  {'TOTAL':14s} {total:9d} B")
    assert set(_SIZES) == {"em", "thermal", "rotor_stress"}
    assert 0 < total < 2_000_000, f"one duty costs {total} B — over the ceiling"
