"""WHICH POINT a ``/last`` answer was solved for (owner 2026-11-04).

«Why does the thermal tab draw the picture of a 400 mm motor when I loaded the 200 mm one?  Check the mechanics too.»
``/api/thermal/last`` and ``/api/mechanical/last`` hold ONE slot per workspace - the last solve of ANY motor - and the tabs
restored it under a "result is for a previous geometry" warning.  The tabs now adopt an entry only when two witnesses agree:
the backend's fingerprint verdict (``stale_geometry`` is False) AND the point (die / configuration / duty) stamped on the
entry when it was solved.  These tests pin the backend half: the stamp is written from the catalogue context the editor has
loaded (the same source ``duty_fields.save_active`` files the stored map under), travels through ``GET /last``, and is
``None`` - UNKNOWN, never a guess - when no catalogue point was loaded.

Nothing is solved: the stores are filled through ``_remember_last`` with the per-duty bookkeeping switched off.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """Empty thermal + mechanical last stores, pickles in tmp, no per-duty filing."""
    from motor_ai_sim import duty_fields, duty_results
    from motor_ai_sim.routes import mechanical as mech
    from motor_ai_sim.routes import thermal as th

    for mod, name in ((th, ".last_thermal.pkl"), (mech, ".last_mechanical.pkl")):
        monkeypatch.setattr(mod, "_LAST", {}, raising=True)
        monkeypatch.setattr(mod, "_LAST_LOADED", True, raising=True)
        monkeypatch.setattr(mod, "_last_store_path", lambda n=name: str(tmp_path / n))
    monkeypatch.setattr(duty_results, "note_thermal", lambda *a, **k: None)
    monkeypatch.setattr(duty_results, "note_mechanical", lambda *a, **k: None)
    monkeypatch.setattr(duty_fields, "save_active", lambda *a, **k: None)
    return th, mech, duty_fields


POINT = ("CILN28", "G2-L40", "optimal")


def test_thermal_entry_carries_the_loaded_point(stores, client, monkeypatch):
    th, _mech, df = stores
    monkeypatch.setattr(df, "active_context", lambda: POINT)
    th._remember_last("field", {"T_max": 90.0}, {"rpm": 3000.0}, "fp-a")
    assert th._LAST["field"]["context"] == {"die": POINT[0], "config": POINT[1], "duty": POINT[2]}

    out = client.get("/api/thermal/last", params={"field": "false"}).json()
    assert out["field"]["context"] == {"die": "CILN28", "config": "G2-L40", "duty": "optimal"}
    # the fingerprint witness is still reported beside it
    assert out["field"]["geometry_fingerprint"] == "fp-a"
    assert out["field"]["stale_geometry"] in (True, None)     # "fp-a" is not the sandbox machine


def test_thermal_entry_without_a_catalogue_point_says_unknown(stores, client, monkeypatch):
    th, _mech, df = stores
    monkeypatch.setattr(df, "active_context", lambda: None)
    th._remember_last("field", {"T_max": 90.0}, {}, "fp-a")
    assert th._LAST["field"]["context"] is None
    assert client.get("/api/thermal/last", params={"field": "false"}).json()["field"]["context"] is None


def test_thermal_duty_cycle_names_the_duty_it_was_told(stores, monkeypatch):
    """The duty-cycle route states which duty it solved; that wins over the editor's context."""
    th, _mech, df = stores
    monkeypatch.setattr(df, "active_context", lambda: POINT)
    monkeypatch.setattr("motor_ai_sim.duty_results.note_duty_cycle", lambda *a, **k: None)
    th._remember_last("duty_cycle", {"x": 1}, {}, "fp-a", die="OTHER", cfg="S1", duty="peak")
    assert th._LAST["duty_cycle"]["context"] == {"die": "OTHER", "config": "S1", "duty": "peak"}


def test_entry_pickled_before_the_stamp_reads_as_unknown(stores, client):
    """An old pickle has no ``context`` key: the route answers ``None``, which the tab does not adopt for a loaded point."""
    th, _mech, _df = stores
    th._LAST["field"] = {"result": {"T_max": 1.0}, "params": {}, "geometry_fingerprint": "fp-old",
                         "computed_at": "2026-10-28T10:00:00+00:00"}
    out = client.get("/api/thermal/last", params={"field": "false"}).json()
    assert out["field"]["context"] is None


def test_mechanical_entry_carries_the_loaded_point(stores, client, monkeypatch):
    _th, mech, df = stores
    monkeypatch.setattr(df, "active_context", lambda: POINT)
    mech._remember_last("rotor_stress", {"cases": {}}, {"rpm": 8000.0}, "fp-b")
    assert mech._LAST["rotor_stress"]["context"] == {"die": "CILN28", "config": "G2-L40", "duty": "optimal"}
    out = client.get("/api/mechanical/last", params={"field": "false"}).json()
    assert out["rotor_stress"]["context"] == {"die": "CILN28", "config": "G2-L40", "duty": "optimal"}
    # another kind of the same store is stamped by the same rule
    mech._remember_last("modes", {"modes": []}, {}, "fp-b")
    assert client.get("/api/mechanical/last", params={"field": "false"}).json()["modes"]["context"]["duty"] == "optimal"


def test_mechanical_entry_without_a_catalogue_point_says_unknown(stores, client, monkeypatch):
    _th, mech, df = stores
    monkeypatch.setattr(df, "active_context", lambda: None)
    mech._remember_last("rotor_stress", {"cases": {}}, {}, "fp-b")
    assert client.get("/api/mechanical/last", params={"field": "false"}).json()["rotor_stress"]["context"] is None


def test_stored_thermal_map_reports_its_sector_count():
    """A stored thermal answer is the sector the solver meshed; the payload carries the count so the tab tiles it the way
    the live tab does (1 for a full-ring record and for a record written before the count was kept)."""
    pytest.importorskip("numpy")
    import numpy as np

    from motor_ai_sim.development import motor_view as mv

    tri = np.array([[0, 1, 2]], dtype=np.int32)
    verts = np.array([[0.0, 0.0], [0.1, 0.0], [0.0, 0.1]], dtype=np.float32)
    base = {"vertices": verts, "triangles": tri, "temperature_per_node": np.array([20.0, 30.0, 40.0], np.float32),
            "domain_per_tri": np.array([1], dtype=np.int32)}
    assert mv._thermal_payload({**base, "meta": {"n_sectors": 4}})["n_sectors"] == 4
    assert mv._thermal_payload({**base, "meta": {}})["n_sectors"] == 1
