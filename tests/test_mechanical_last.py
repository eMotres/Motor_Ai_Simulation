"""What the Mechanical tab comes back to — /api/mechanical/last and /mesh.

Added 2026-09-06 for the user's report: "когда я захожу и выхожу в Mechanical,
графики пропадают.  Нужно, чтобы по умолчанию: если нет расчётов — рисуется
просто геометрия; если есть — подгружается последний расчёт; если были изменения
текущей геометрии — нужно подсвечивать неактуальность текущего расчёта."

Three claims are worth a test, and they are the three the panel now trusts:

  (a) before anything is solved, /last says so — the panel then draws the bare
      geometry rather than a blank page;
  (b) after a solve it hands the SAME answer back, stamped with the fingerprint
      it was solved for, and that fingerprint is the sandbox machine's own
      ``routes.simulation._geometry_fingerprint`` — the staleness badge is only
      as good as that comparison;
  (c) /mesh returns a finite, connected-looking cross-section without solving
      anything, because that is what an unsolved tab draws.

The module is ordered: (a) MUST run before any solve in this process, since the
last-result store is module state.  ``_reset_last`` makes that explicit instead
of relying on collection order across the whole suite.
"""
from __future__ import annotations

import math

import pytest


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_last_store(tmp_path, monkeypatch):
    """Empty the last-result store, and send its pickle to a tmp file.

    Both halves matter.  The in-memory dict is module state that a rotor_stress
    test elsewhere in the session may already have filled, and the pickle would
    otherwise land next to the sandbox config and be restored by the lazy loader
    — either one would make "nothing solved yet" untestable after the first
    solve anywhere in the suite.
    """
    from motor_ai_sim.routes import mechanical as mech

    monkeypatch.setattr(mech, "_LAST", {}, raising=True)
    monkeypatch.setattr(mech, "_LAST_LOADED", True, raising=True)   # skip the disk
    monkeypatch.setattr(mech, "_last_store_path",
                        lambda: str(tmp_path / ".last_mechanical.pkl"))
    yield


def test_last_is_empty_before_anything_is_solved(client):
    r = client.get("/api/mechanical/last")
    assert r.status_code == 200, r.text[:400]
    out = r.json()
    # 200 + has_result:false, NOT a 404: an unsolved tab is the normal first
    # state, and it draws the geometry — an error would be a lie about it.
    assert out["has_result"] is False
    for kind in ("rotor_stress", "modes", "critical_speeds"):
        assert out[kind] is None, kind
    # The live fingerprint is always answered, even with nothing stored: it is
    # what a later result will be compared against.
    assert isinstance(out["live_geometry_fingerprint"], str)


def test_last_hands_the_solved_result_back_with_its_fingerprint(client):
    solved = client.get("/api/mechanical/rotor_stress",
                        params={"loads": "centrifugal", "rpm": 8000, "mesh_size_mm": 3.0, "order": 1,
                                "lift_off_solves": 0})
    assert solved.status_code == 200, solved.text[:600]
    sol = solved.json()

    r = client.get("/api/mechanical/last")
    assert r.status_code == 200, r.text[:400]
    out = r.json()
    assert out["has_result"] is True
    entry = out["rotor_stress"]
    assert entry is not None

    # The SAME answer, not a re-solve: the headline numbers must match exactly.
    got = entry["result"]
    assert got["cases"]["rated"]["rpm"] == pytest.approx(sol["cases"]["rated"]["rpm"])
    assert got["cases"]["rated"]["rotor_od_growth_um"] == pytest.approx(
        sol["cases"]["rated"]["rotor_od_growth_um"])
    # …with its field, which is the whole point of restoring it (the panel draws
    # the picture, not just the table).
    assert "field" in got and got["field"]["triangles"]

    # The request that produced it, so the panel can restore its input fields.
    assert entry["params"]["rpm"] == pytest.approx(8000.0)
    assert entry["params"]["mesh_size_mm"] == pytest.approx(3.0)
    assert entry["computed_at"]

    # THE staleness comparison: the stored fingerprint is the sandbox config's
    # own, and the backend agrees the result is current.
    from motor_ai_sim.routes.simulation import _geometry_fingerprint
    assert entry["geometry_fingerprint"] == _geometry_fingerprint(None)
    assert out["live_geometry_fingerprint"] == entry["geometry_fingerprint"]
    assert entry["stale_geometry"] is False


def test_last_flags_a_result_from_a_different_machine(client, monkeypatch):
    """A result whose fingerprint is not the live one comes back FLAGGED.

    The badge the user asked for ("нужно подсвечивать неактуальность текущего
    расчёта") is only as good as this bit, and the frontend cannot compute it —
    it does not have the backend's fingerprint of the live machine.
    """
    assert client.get("/api/mechanical/rotor_stress",
                      params={"loads": "centrifugal", "rpm": 8000, "mesh_size_mm": 3.0, "order": 1,
                              "lift_off_solves": 0}).status_code == 200

    from motor_ai_sim.routes import mechanical as mech
    mech._LAST["rotor_stress"]["geometry_fingerprint"] = "not-this-machine"

    out = client.get("/api/mechanical/last", params={"field": False}).json()
    assert out["rotor_stress"]["stale_geometry"] is True
    # `field=false` drops the heavy payload — the panel asks for it that way
    # when it only wants to know whether anything is there.
    assert "field" not in out["rotor_stress"]["result"]


def test_last_is_unknown_not_fine_when_a_fingerprint_is_missing(client):
    """No fingerprint on either side = UNKNOWN, reported as null.

    A staleness check that cannot prove a mismatch must never claim one — and
    must never claim the opposite either, which is why this is `None` and not
    `False`.
    """
    assert client.get("/api/mechanical/rotor_stress",
                      params={"loads": "centrifugal", "rpm": 8000, "mesh_size_mm": 3.0, "order": 1,
                              "lift_off_solves": 0}).status_code == 200

    from motor_ai_sim.routes import mechanical as mech
    mech._LAST["rotor_stress"]["geometry_fingerprint"] = None

    out = client.get("/api/mechanical/last", params={"field": False}).json()
    assert out["rotor_stress"]["stale_geometry"] is None


def test_the_last_result_survives_a_restart(client, tmp_path):
    """Solve, drop the in-memory store, reload from disk — the answer is back.

    This is the reload / API-restart half of the user's ask.  The persist runs
    off-thread (a viewer convenience must never sit in a solve's critical path),
    so the write is waited for rather than assumed.
    """
    import time

    from motor_ai_sim.routes import mechanical as mech

    assert client.get("/api/mechanical/rotor_stress",
                      params={"loads": "centrifugal", "rpm": 8000, "mesh_size_mm": 3.0, "order": 1,
                              "lift_off_solves": 0}).status_code == 200

    p = tmp_path / ".last_mechanical.pkl"
    for _ in range(100):
        if p.exists():
            break
        time.sleep(0.1)
    assert p.exists(), "the last mechanical result was never persisted"

    mech._LAST.clear()
    mech._LAST_LOADED = False          # what a fresh process starts from
    out = client.get("/api/mechanical/last", params={"field": False}).json()
    assert out["has_result"] is True
    assert out["rotor_stress"]["result"]["cases"]["rated"]["rpm"] == pytest.approx(8000.0)


def test_modes_and_criticals_are_remembered_too(client):
    m = client.get("/api/mechanical/modes",
                   params={"body": "rotor", "n": 4, "mesh_size_mm": 4.0,
                           "order": 1, "rpm": 8000})
    assert m.status_code == 200, m.text[:600]
    c = client.get("/api/mechanical/critical_speeds",
                   params={"rpm": 8000, "n_modes": 4, "mesh_size_mm": 4.0})
    assert c.status_code == 200, c.text[:600]

    out = client.get("/api/mechanical/last", params={"field": False}).json()
    assert out["modes"] is not None
    assert out["modes"]["result"]["modes"], "no mode rows restored"
    assert out["modes"]["params"]["n"] == 4
    assert out["critical_speeds"] is not None
    assert out["critical_speeds"]["result"]["critical_speeds"] is not None
    # `has_result` is about the tab as a whole: a modal-only session counts.
    assert out["has_result"] is True


# ---------------------------------------------------------------------------
# The bare cross-section
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mesh_payload(client):
    r = client.get("/api/mechanical/mesh", params={"mesh_size_mm": 3.0})
    assert r.status_code == 200, r.text[:600]
    return r.json()


def test_mesh_returns_a_finite_cross_section(mesh_payload):
    m = mesh_payload
    assert m["n_triangles"] > 100 and m["n_nodes"] > 100
    assert len(m["triangles"]) == m["n_triangles"]
    assert len(m["vertices"]) == m["n_nodes"]
    assert len(m["domain_per_tri"]) == m["n_triangles"]
    for x, y in m["vertices"]:
        assert math.isfinite(x) and math.isfinite(y)
    # mm, and the rotor of the sandbox machine is centimetres, not metres:
    # this is the assertion that catches a unit slip in the m -> mm conversion.
    assert 1.0 < m["extent"] < 2000.0
    r = max(math.hypot(x, y) for x, y in m["vertices"])
    assert r == pytest.approx(m["extent"], rel=0.35)


def test_mesh_indices_and_part_tags_are_in_range(mesh_payload):
    m = mesh_payload
    n = m["n_nodes"]
    for tri in m["triangles"]:
        assert len(tri) == 3
        for i in tri:
            assert 0 <= i < n
    names = m["part_names"]
    assert names, "the mesh must name its parts — the viewer's Part menu reads them"
    for tag in set(m["domain_per_tri"]):
        assert str(tag) in names, tag
    # No sleeve in the sandbox config (conftest zeroes sleeve_thickness), so the
    # cross-section must not carry sleeve elements.
    assert not any(names[str(t)] == "sleeve" for t in set(m["domain_per_tri"]))
    assert m["outlines"], "no outlines — the picture would have no part boundaries"


def test_mesh_is_cached_by_geometry_and_size(client, mesh_payload):
    again = client.get("/api/mechanical/mesh", params={"mesh_size_mm": 3.0})
    assert again.status_code == 200
    j = again.json()
    assert j["cached"] is True
    assert j["n_triangles"] == mesh_payload["n_triangles"]


def test_mesh_does_not_solve_anything(client):
    """The unsolved tab draws this — it must not fill the last-result store."""
    from motor_ai_sim.routes import mechanical as mech

    mech._LAST.clear()
    assert client.get("/api/mechanical/mesh",
                      params={"mesh_size_mm": 4.0}).status_code == 200
    assert client.get("/api/mechanical/last").json()["has_result"] is False


# ---------------------------------------------------------------------------
# How long it took, and the mesh as a thing you control
# ---------------------------------------------------------------------------
# User 2026-09-06: "нужно добавить ещё индикатор времени расчёта, и по поводу
# сетки — как я понял, она строится отдельно, и ей тоже нужно как-то управлять".
#
# Both halves are backend claims before they are UI: a panel can time its own
# fetch, but that number includes the network and a 40 MB field payload, and a
# Build mesh button is only worth pressing if the Solve after it does NOT mesh
# the same rotor again.

def test_every_solve_reports_the_seconds_it_took(client):
    """`elapsed_s` is present, positive, and measured around the real work."""
    # Speeds nothing else in the module has solved at, so these are real solves
    # and not cache hits — the claim is about a FRESH solve.
    s = client.get("/api/mechanical/rotor_stress",
                   params={"loads": "centrifugal", "rpm": 8123, "mesh_size_mm": 3.0, "order": 1,
                           "lift_off_solves": 0, "field": False}).json()
    assert s["cached"] is False
    assert s["elapsed_s"] > 0
    # the mesh is part of it, and is reported separately
    assert s["mesh"]["mesh_s"] >= 0

    m = client.get("/api/mechanical/modes",
                   params={"body": "rotor", "n": 4, "mesh_size_mm": 4.0,
                           "order": 1, "rpm": 8123, "shapes": False}).json()
    assert m["cached"] is False
    assert m["elapsed_s"] > 0
    assert m["mesh"]["mesh_s"] >= 0

    c = client.get("/api/mechanical/critical_speeds",
                   params={"rpm": 8123, "n_modes": 4, "mesh_size_mm": 4.0}).json()
    assert c["cached"] is False
    assert c["elapsed_s"] > 0


def test_a_cache_hit_keeps_the_seconds_the_solve_actually_cost(client):
    """A repeat press says "cached" and still reports the ORIGINAL duration.

    The panel prints "cached · solved in 48 s earlier" from exactly these two
    fields; a hit that reset `elapsed_s` to its own microseconds would turn the
    estimate the timer shows next time into nonsense.
    """
    p = {"loads": "centrifugal", "rpm": 8321, "mesh_size_mm": 3.0, "order": 1,
         "lift_off_solves": 0, "field": False}
    first = client.get("/api/mechanical/rotor_stress", params=p).json()
    assert first["cached"] is False
    again = client.get("/api/mechanical/rotor_stress", params=p).json()
    assert again["cached"] is True
    assert again["elapsed_s"] == pytest.approx(first["elapsed_s"])


def test_mesh_reports_what_it_built(client):
    """The mesh line in the panel: N tri · P2 · size X mm · built in Y s."""
    r = client.get("/api/mechanical/mesh",
                   params={"mesh_size_mm": 3.7, "order": 2})
    assert r.status_code == 200, r.text[:400]
    m = r.json()
    assert m["cached"] is False
    assert m["n_vertices"] == m["n_nodes"] > 100
    assert m["n_triangles"] > 100
    assert m["mesh_size_mm"] == pytest.approx(3.7)
    assert m["element_order"] == 2
    assert m["mesh_s"] > 0, "a fresh build must report the gmsh seconds it cost"
    assert m["mesh_reused"] is False
    assert m["elapsed_s"] >= m["mesh_s"]
    # The edges are the OUTCOME of the size setting, not the setting itself:
    # gmsh clamps at MeshSizeMin and refines on curvature, so the smallest edge
    # is normally well under the target and the largest at or below it.
    assert 0 < m["min_edge_mm"] <= m["max_edge_mm"]
    assert m["max_edge_mm"] < 3.7 * 3


def test_a_second_mesh_at_the_same_size_is_a_cache_hit(client):
    """Nothing is re-meshed, and the reported build seconds do not change."""
    first = client.get("/api/mechanical/mesh",
                       params={"mesh_size_mm": 3.7, "order": 2}).json()
    again = client.get("/api/mechanical/mesh",
                       params={"mesh_size_mm": 3.7, "order": 2}).json()
    assert again["cached"] is True
    assert again["n_triangles"] == first["n_triangles"]
    # `mesh_s` stays the cost of the build that happened — a cache hit did not
    # make the mesher faster, it skipped it.
    assert again["mesh_s"] == pytest.approx(first["mesh_s"])


def test_build_mesh_then_solve_does_not_mesh_twice(client):
    """THE point of the Build mesh button (user 2026-09-06).

    A press of Build mesh followed by a Solve at the same size must reuse the
    built mesh: otherwise the button costs the user seconds of gmsh instead of
    saving them.
    """
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    rsm.clear_mesh_memo()
    built = client.get("/api/mechanical/mesh", params={"mesh_size_mm": 3.3}).json()
    assert built["mesh_s"] > 0 and built["mesh_reused"] is False

    solved = client.get("/api/mechanical/rotor_stress",
                        params={"loads": "centrifugal", "rpm": 8000, "mesh_size_mm": 3.3, "order": 1,
                                "lift_off_solves": 0, "field": False}).json()
    assert solved["mesh"]["mesh_reused"] is True, "the solve re-meshed the rotor"
    # …the SAME mesh, still carrying what it cost to build (that cost is a
    # property of the mesh, not of the press that got it).
    assert solved["mesh"]["mesh_s"] == pytest.approx(built["mesh_s"])
    assert solved["mesh"]["n_triangles"] == built["n_triangles"]


def test_the_mesh_memo_is_keyed_on_the_geometry_not_on_a_fingerprint(client):
    """A different cross-section misses, rather than being handed the old mesh.

    The memo hashes the polygons themselves precisely so it cannot serve one
    machine's mesh for another — the failure mode a caller-supplied key has.
    """
    from motor_ai_sim.services.geometry_service import get_current_geometry
    from motor_ai_sim.simulation.mechanical import rotor_stress as rsm

    polys_a = _rotor_polys(get_current_geometry().to_dict())
    rsm.clear_mesh_memo()
    a = rsm.build_rotor_mesh(polys_a, mesh_size_mm=3.9)
    assert a.from_memo is False and a.build_s > 0
    assert rsm.build_rotor_mesh(polys_a, mesh_size_mm=3.9).from_memo is True
    # same solids, different size -> a real build
    assert rsm.build_rotor_mesh(polys_a, mesh_size_mm=2.9).from_memo is False

    params = dict(get_current_geometry().to_dict())
    params["magnet_height"] = float(params["magnet_height"]) * 0.9
    b = rsm.build_rotor_mesh(_rotor_polys(params), mesh_size_mm=3.9)
    assert b.from_memo is False, "a different rotor was handed a cached mesh"


def _rotor_polys(params: dict):
    from motor_ai_sim.cadquery_geometry import CadQueryMotor

    motor = CadQueryMotor()
    motor.set_parameters(params)
    return motor.get_2d_polygons(0.0)


def test_last_round_trips_a_single_speed_result(client):
    """One case, named by its speed, comes back as itself — mode included.

    User 2026-09-06: "давай будем рассчитывать только на 23 000 оборотов".  The
    panel restores its toolbar from `params`, so the case-table choice has to
    ride along with the numbers: a single-speed answer displayed under a
    three-case toggle would read as two columns that failed to arrive.
    """
    solved = client.get("/api/mechanical/rotor_stress",
                        params={"loads": "centrifugal", "rpm": 23000, "mesh_size_mm": 3.0, "order": 1,
                                "cases": "single", "lift_off_solves": 0})
    assert solved.status_code == 200, solved.text[:600]
    sol = solved.json()
    assert list(sol["cases"]) == ["23,000 rpm"], list(sol["cases"])

    entry = client.get("/api/mechanical/last").json()["rotor_stress"]
    assert entry is not None
    got = entry["result"]
    assert list(got["cases"]) == ["23,000 rpm"], list(got["cases"])
    assert got["case_mode"] == "single"
    assert got["cases"]["23,000 rpm"]["rotor_od_growth_um"] == pytest.approx(
        sol["cases"]["23,000 rpm"]["rotor_od_growth_um"])
    # The request that produced it — the mode among the inputs to restore.
    assert entry["params"]["cases"] == "single"
    assert entry["params"]["rpm"] == pytest.approx(23000.0)
