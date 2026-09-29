"""Where the rotor's heat goes: out across the gap, or in down the shaft.

User 2026-09-10: *"в термоанализе ещё нужно считать два числа: сколько тепла от
ротора уходит через внешний диаметр, а сколько через внутренний"*, and then
plainly: *"то есть через зазор и через вал"*.

Every watt made inside the slip radius has exactly three ways out of this
cross-section — across the gap into the stator, off the bore surface, and
axially down the exposed shaft stubs — so what is pinned here is that the block
IS that balance and not a second opinion assembled from other lines:

  * it adds up: gap + shaft side = what the rotor makes, and `closure_W` says
    by how much it misses;
  * both numbers are 2-D — surface integrals on the same solved section — and
    the axial shaft-stub path is reported beside them, never folded in;
  * every share is that watt over the rotor's own generation;
  * and the numbers are the SAME ones the budget already carried one per line —
    a split that disagreed with `gap_W` or `bore_W` would be worse than none.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import time

import pytest

from tests.test_thermal_routes import GEO_30MM, store_em_run

#: The cheapest honest solve on the pinned 30 mm fixture — a half-wedge with a
#: water jacket, the same one `test_thermal_wedge_cuts` runs.  BORE COOLING ON:
#: without it the rotor has only the gap to give its heat to and the split under
#: test would be 100 / 0 by construction.
FAST = {
    "n_steps_per_period": 4, "n_periods": 1.0,
    "mesh_size_mm": 2.0, "min_size_mm": 0.5, "n_sectors": 2,
    "I_phase_rms": 60.0, "rpm": 3000.0, "gamma_deg": 0.0,
    "coil_temp_c": 120.0,
}
COOL = {"cooling_mode": "liquid", "fluid": "water", "fluid_temp_in_c": 30.0,
        "flow_lpm": 8.0, "ambient_temp": 30.0,
        "bore_mode": "air", "bore_air_speed_mps": 20.0}
RUN_ID = "2026-09-10T09:00:00"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture(scope="module")
def em_run():
    from motor_ai_sim.routes import simulation as sim

    mp = pytest.MonkeyPatch()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thermal_split_"))
    mp.setattr(sim, "_transient_field_store_path", lambda: str(tmp / ".snap.pkl"))
    sim._transient_field_snap.clear()
    info = store_em_run(GEO_30MM, run_id=RUN_ID, phys=FAST)
    time.sleep(0.3)
    yield info
    sim._transient_field_snap.clear()
    mp.undo()


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Never through the persisting store — the standing rule."""
    from motor_ai_sim.routes import thermal as th

    monkeypatch.setattr(th, "_LAST", {}, raising=True)
    monkeypatch.setattr(th, "_LAST_LOADED", True, raising=True)
    monkeypatch.setattr(th, "_last_store_path",
                        lambda: str(tmp_path / ".last_thermal.pkl"))
    monkeypatch.setattr(th, "_loss_maps_path",
                        lambda: str(tmp_path / ".loss_maps.pkl"))
    monkeypatch.setattr(th, "_LOSS_MAPS_LOADED", True, raising=True)
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()
    yield
    th._LOSS_MAPS.clear()
    th._FIELD_CACHE.clear()
    th._COUPLED_CACHE.clear()


@pytest.fixture(scope="module")
def thermal_result(client, em_run):
    r = client.get("/api/thermal/field",
                   params={**FAST, **COOL, "geo": json.dumps(GEO_30MM)})
    assert r.status_code == 200, r.text[:900]
    return r.json()


def _split(budget):
    sp = budget.get("rotor_heat_split")
    assert isinstance(sp, dict), "no rotor_heat_split in the heat budget"
    return sp


def test_a_the_block_is_the_rotor_balance(thermal_result):
    b = (thermal_result.get("cooling") or {}).get("heat_budget") or {}
    sp = _split(b)
    assert sp["rotor_W"] == pytest.approx(b["rotor_W"], rel=1e-6)
    # The budget rounds its own lines to 2 decimals and the split to 3, so the
    # two can differ by half a centiwatt and by nothing else.
    assert sp["gap_W"] == pytest.approx(b["gap_W"], abs=0.01)
    assert sp["bore_W"] == pytest.approx(b["bore_W"], abs=0.01)
    assert sp["axial_shaft_ends_W"] == pytest.approx(b["shaft_ends_W"], abs=0.01)
    assert sp["dimensionality"] == "2D"


def test_b_the_axial_path_is_kept_out_of_the_two(thermal_result):
    """User 2026-09-10: "делай только в двумерном варианте пока".

    The gap and the bore are surface integrals on the SAME solved section; the
    shaft stubs are a lumped conductance out of the page.  Folding the third
    into the second would inflate "through the shaft" with a term this section
    never drew, so it is reported on its own line and the two 2-D numbers stay
    comparable.
    """
    sp = _split((thermal_result.get("cooling") or {}).get("heat_budget") or {})
    assert "shaft_side_W" not in sp
    assert "axial_shaft_ends_W" in sp


def test_c_the_two_ways_out_add_back_to_what_the_rotor_makes(thermal_result):
    sp = _split((thermal_result.get("cooling") or {}).get("heat_budget") or {})
    assert sp["closure_W"] == pytest.approx(
        sp["rotor_W"] - sp["gap_W"] - sp["bore_W"] - sp["axial_shaft_ends_W"],
        abs=5e-3)
    # …and it really does close, on a solve that converged
    assert abs(sp["closure_W"]) <= max(0.02 * abs(sp["rotor_W"]), 0.5), sp


def test_d_every_share_is_of_the_rotors_own_generation(thermal_result):
    sp = _split((thermal_result.get("cooling") or {}).get("heat_budget") or {})
    if abs(sp["rotor_W"]) <= 1e-9:
        pytest.skip("this machine's rotor makes no measurable loss")
    for w, p in (("gap_W", "gap_pct"), ("bore_W", "bore_pct"),
                 ("axial_shaft_ends_W", "axial_shaft_ends_pct")):
        assert sp[p] == pytest.approx(
            100.0 * sp[w] / sp["rotor_W"], abs=0.15), (w, sp[w], sp[p])
    # the two 2-D paths carry everything the axial one does not
    assert (sp["gap_pct"] + sp["bore_pct"]
            + (sp["axial_shaft_ends_pct"] or 0.0)) == pytest.approx(100.0, abs=2.0)
