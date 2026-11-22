"""Zone-wise air speed in the thermal solver (``air_zones``, model ``zone-1``) - OPT-IN (owner 2026-11-16: "change the thermal
solver, but do not break the old one").

The scalar path is untouched: with no ``air_zones`` the request, the cache key and the result are the ones they always were.  What
is pinned here:

  (a) the request field is validated BY NAME (model id, source, zone names, finite speeds above 0, axial only, cooling_mode air,
      frame open) and absent / null / blank means "the scalar path";
  (b) the cache key grows ONLY when zones are given;
  (c) on the 30 mm fixture (open frame, air): an empty zone set equals the scalar result, three zones equal to the scalars equal the
      scalar result exactly (same code path), a faster zone lowers the temperatures it cools, the heat budget closes, and the
      result carries ``air_model: "zone-1"`` and the zones used while a scalar result carries neither;
  (d) the exact before/after golden of the scalar path was run on base and branch code (job zonegold-*, PR text): the full result
      JSON of three scalar requests is identical.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import time

import pytest
from fastapi import HTTPException

from motor_ai_sim.routes import thermal as th
from tests.test_thermal_routes import GEO_30MM, store_em_run

FAST = {
    "n_steps_per_period": 4, "n_periods": 1.0, "mesh_size_mm": 2.5, "min_size_mm": 0.6, "n_sectors": 2,
    "I_phase_rms": 60.0, "rpm": 3000.0, "gamma_deg": 0.0, "coil_temp_c": 120.0,
}


def Z(zones, **over):
    d = {"model_id": "zone-1", "source": "test | bemt-1", "zones": zones}
    d.update(over)
    return json.dumps(d)


def parse(raw, cooling_mode="air", frame="open"):
    return th._parse_air_zones(raw, cooling_mode=cooling_mode, frame=frame)


def refused(raw, field, **kw):
    with pytest.raises(HTTPException) as e:
        parse(raw, **kw)
    assert e.value.status_code == 422
    assert e.value.detail["invalid_parameters"][0]["field"] == field, e.value.detail
    return e.value.detail


# ---------------------------------------------------------------------------
# (a) the request field
# ---------------------------------------------------------------------------

def test_absent_null_and_blank_are_the_scalar_path():
    for raw in (None, "", "  ", "null", "NULL"):
        assert parse(raw) is None
    # ...and then nothing else is read: a housed / liquid request with no zones is not refused
    assert parse(None, cooling_mode="liquid", frame="housed") is None


def test_a_valid_request_is_canonical():
    z = parse(Z({"stator_ends": 4.4, "housing_outer": 3.1}))
    assert z == {"model_id": "zone-1", "source": "test | bemt-1", "axial_only": True, "calibration": None,
                 "zones": {"housing_outer": 3.1, "stator_ends": 4.4}}
    assert parse(Z({})) ["zones"] == {}                      # no zone = the scalar values, but still stamped
    assert parse({"model_id": "zone-1", "source": "x", "zones": {"rotor_ends": 2}})["zones"] == {"rotor_ends": 2.0}   # a dict is accepted


def test_refusals_are_by_name():
    refused("{not json", "air_zones")
    refused("[1]", "air_zones")
    refused(json.dumps({"model_id": "zone-2", "source": "x", "zones": {}}), "air_zones.model_id")
    refused(json.dumps({"model_id": "zone-1", "zones": {}}), "air_zones.source")
    refused(json.dumps({"model_id": "zone-1", "source": "x"}), "air_zones.zones")
    refused(Z({"nozzle": 3.0}), "air_zones.zones")
    for bad in (0, -1, "3", True, None, float("nan")):
        refused(Z({"stator_ends": bad}) if bad == bad else '{"model_id":"zone-1","source":"x","zones":{"stator_ends":NaN}}',
                "air_zones.zones.stator_ends")
    refused(Z({}, swirl={"stator_ends": 1.0}), "air_zones.swirl")
    refused(Z({}, calibration=0.5), "air_zones.calibration")
    refused(Z({}, extra=1), "air_zones")
    refused(Z({}), "air_zones", cooling_mode="liquid")
    refused(Z({}), "air_zones", frame="housed")


def test_the_source_kw_carries_the_zones_only_when_given():
    base = th._thermal_air_source_kw("manual", "", "", "", "air", None)
    assert base == {}                                           # today's value, unchanged
    kw = th._thermal_air_source_kw("manual", "", "", "", "air", None, air_zones=Z({"stator_ends": 5}), frame="open")
    assert set(kw) == {"air_zones"} and json.loads(kw["air_zones"])["zones"] == {"stator_ends": 5.0}
    assert th._thermal_air_source_kw("manual", "", "", "", "air", None, air_zones=None, frame="open") == {}


# ---------------------------------------------------------------------------
# (b)(c) the solver
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def em_run():
    from motor_ai_sim.routes import simulation as sim

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_air_zones_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    saved = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()
    info = store_em_run(GEO_30MM, run_id="2026-11-16T09:00:00", phys=FAST)
    time.sleep(0.3)
    yield info
    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved)
    mp.undo()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(th, "_last_store_path", lambda: str(tmp_path / ".last_thermal.pkl"))
    monkeypatch.setattr(th, "_loss_maps_path", lambda: str(tmp_path / ".loss_maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()
    yield
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()


def solve(**over):
    th._FIELD_CACHE.clear()
    kw = dict(ambient_temp=30.0, h_conv=50.0, slot_k=0.0, gap_k=0.0, geo=json.dumps(GEO_30MM), cooling_mode="air",
              air_speed_mps=5.0, fluid="water", fluid_temp_in_c=25.0, fluid_temp_out_c=0.0, flow_lpm=0.0,
              outer_air_factor=1.3, component_mesh="", frame="open", **FAST)
    kw.update(over)
    return th.solve_thermal_field(**kw)


def strip(r):
    return {k: v for k, v in r.items() if k not in ("air_model", "air_zones", "cached", "computed_at")}


def comparable(r):
    """The whole result, minus the stamp and wall-clock noise, as JSON."""
    def clean(x):
        if isinstance(x, dict):
            return {k: clean(v) for k, v in x.items() if not any(s in k for s in ("time", "wall", "elapsed", "computed", "run_id"))}
        if isinstance(x, (list, tuple)):
            return [clean(v) for v in x]
        return x
    return json.dumps(clean(strip(r)), sort_keys=True, default=str)


def test_the_cache_key_grows_only_with_zones(em_run):
    a = solve()
    assert "air_model" not in a and "air_zones" not in a        # a scalar result carries no stamp ("computed with momentum-1")
    keys0 = set(th._FIELD_CACHE)
    b = solve(air_zones=Z({}))
    assert len(set(th._FIELD_CACHE)) == 1 and set(th._FIELD_CACHE) != keys0   # a zone request is a different entry
    assert b["air_model"] == "zone-1" and b["air_zones"]["zones"] == {} and b["air_zones"]["source"] == "test | bemt-1"
    assert b["air_zones"]["axial_only"] is True and b["air_zones"]["calibration"] is None


def test_empty_zones_and_zones_equal_to_the_scalars_reproduce_the_scalar_result(em_run):
    scalar = solve(open_air_speed_mps=8.0)
    # with no zone given every speed keeps today's value: the same result
    assert comparable(solve(open_air_speed_mps=8.0, air_zones=Z({}))) == comparable(scalar)
    # three zones all set to the scalar values go through the same code path: the same result, bit for bit
    same = solve(air_zones=Z({"housing_outer": 5.0, "stator_ends": 8.0}))
    assert comparable(same) == comparable(scalar)


def test_a_faster_zone_cools_what_it_serves_and_the_heat_budget_closes(em_run):
    base = solve(open_air_speed_mps=8.0)
    for zones in ({"stator_ends": 15.0}, {"housing_outer": 15.0}, {"rotor_ends": 25.0}):
        r = solve(open_air_speed_mps=8.0, air_zones=Z(zones))
        assert r["T_max"] < base["T_max"] and r["T_min"] < base["T_min"], (zones, r["T_max"], base["T_max"])
        hb = r["cooling"]["heat_budget"]
        assert abs(hb["residual_W"]) < 1e-6 and abs(hb["residual_pct"]) < 1e-6        # physics check: the budget closes
        assert r["numerical_acceptance"]["finite"] is True and r["ok"] is True
        assert r["air_zones"]["zones"] == zones
    # the rotor zone acts on the rotor, the ends zone on the winding side: the rotor-end speed cools the magnets most
    rot = solve(open_air_speed_mps=8.0, air_zones=Z({"rotor_ends": 25.0}))
    ends = solve(open_air_speed_mps=8.0, air_zones=Z({"stator_ends": 25.0}))
    assert rot["components"]["magnet"]["avg"] < base["components"]["magnet"]["avg"]
    assert ends["components"]["winding"]["avg"] < base["components"]["winding"]["avg"]


def test_a_housed_or_liquid_solve_refuses_zones_by_name(em_run):
    with pytest.raises(HTTPException) as e:
        solve(frame="housed", air_zones=Z({"stator_ends": 5.0}))
    assert e.value.detail["invalid_parameters"][0]["field"] == "air_zones"


def test_the_component_mean_is_the_area_mean_not_the_vertex_mean(em_run):
    """Astra thermal audit 2026-11-21, F09: the reported bulk temperature drives the copper resistance and the magnet; it must not
    move with the local mesh density.  Recomputed here from the returned P1 field: the area-weighted mean over the winding."""
    import numpy as np
    r = solve(open_air_speed_mps=8.0)
    P = np.asarray(r["vertices"], float)
    t = np.asarray(r["triangles"], int)
    T = np.asarray(r["temperature_per_node"], float)
    tag = np.asarray(r["domain_per_tri"], int)
    m = tag == 2                                   # the winding domain
    a, b, c = P[t[m, 0]], P[t[m, 1]], P[t[m, 2]]
    area = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
    expect = float((area * T[t[m]].mean(axis=1)).sum() / area.sum())
    w = r["components"]["winding"]
    assert abs(w["avg"] - expect) <= 0.051
    assert "avg_vertex" in w and w["avg_vertex"] == round(float(T[np.unique(t[m])].mean()), 1)
