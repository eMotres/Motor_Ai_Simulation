"""Configure's physical limits (owner 2026-10-05): the server side.

* the hand-set stack-length maximum is admin-only, validated, stored on the
  catalogue card, clearable, and visible to every reader through the context;
* the phase-current ceiling is the machine's own inverter device x devices in
  parallel, read with the Controller's own card method — and "no controller
  set" is an explicit answer, never a made-up number;
* the slot-fit identity the web relies on (rows x (wire + spacing) + 2 x
  insulation <= slot height) is the SAME bound the solver's constraint uses.

No solver, no FEM: the catalogue is a temp file.
"""
from __future__ import annotations

import json
import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import configure_limits as cl
from motor_ai_sim import geometry_constraints as gc
from motor_ai_sim.inverter import devices as dv

SI = "IQE036N08NM6SC"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from motor_ai_sim.routes import catalog as cat_mod
    p = tmp_path / "motor_catalog.json"
    p.write_text(json.dumps({"tiers": [], "diameters_mm": [], "motors": [
        {"id": "cat_x", "name": "X die L12", "diameter_mm": 40, "passport": {}}]}),
        encoding="utf-8")
    monkeypatch.setattr(cat_mod, "_CATALOG_PATH", p)
    monkeypatch.setattr(cat_mod, "_family_doc_of_motor", lambda m, g=None: {
        "controller": {"device": SI, "devices_parallel": 2, "modulation_index": 0.95},
        "battery": {"v_min": 36.0, "v_nom": 44.4, "v_max": 50.4}})
    app = FastAPI()
    app.include_router(cat_mod.router)
    return TestClient(app), p


def test_l_max_is_admin_only_stored_and_clearable(client, monkeypatch):
    c, path = client
    from motor_ai_sim.routes import catalog as cat_mod
    monkeypatch.setattr(cat_mod, "_caller_identity", lambda a=None: {"id": "u", "is_admin": False})
    assert c.patch("/api/catalog/cat_x/configure_limits", json={"L_max_mm": 30}).status_code == 403
    monkeypatch.setattr(cat_mod, "_caller_identity", lambda a=None: {"id": "boss", "is_admin": True})
    assert c.patch("/api/catalog/cat_x/configure_limits", json={}).status_code == 422
    for bad in (0, -5, "abc", 1e9, True):
        assert c.patch("/api/catalog/cat_x/configure_limits", json={"L_max_mm": bad}).status_code == 422
    assert c.patch("/api/catalog/nope/configure_limits", json={"L_max_mm": 30}).status_code == 404
    r = c.patch("/api/catalog/cat_x/configure_limits", json={"L_max_mm": 30})
    assert r.status_code == 200 and r.json()["L_max_mm"] == 30.0
    stored = json.loads(path.read_text(encoding="utf-8"))["motors"][0]
    assert stored["configure_limits"]["L_max_mm"] == 30.0 and stored["configure_limits"]["set_by"] == "boss"
    assert stored["passport"] == {}                       # nothing else touched
    ctx = c.get("/api/catalog/cat_x/configure_context").json()
    assert ctx["limits"]["L_max_mm"] == 30.0
    c.patch("/api/catalog/cat_x/configure_limits", json={"L_max_mm": None})
    assert c.get("/api/catalog/cat_x/configure_context").json()["limits"]["L_max_mm"] is None


def test_context_carries_current_pack_and_modulation(client):
    c, _ = client
    ctx = c.get("/api/catalog/cat_x/configure_context").json()
    cur = ctx["current"]
    card = dv.get_device(SI)
    i_d = card.i_d_rating(100.0)["i_a"]
    assert cur["set"] and cur["device"] == SI and cur["devices_parallel"] == 2
    assert cur["i_phase_rms_max_A"] == pytest.approx(math.sqrt(2) * i_d * 2, abs=0.06)
    assert ctx["modulation"] == {"m": 0.95, "source": "controller"}
    assert ctx["battery"]["v_max"] == 50.4
    assert c.get("/api/catalog/nope/configure_context").status_code == 404


def test_no_controller_is_an_explicit_answer():
    assert cl.current_limit({})["set"] is False
    assert "no controller device" in cl.current_limit(None)["reason"]
    assert cl.current_limit({"device": "NOT_A_PART"})["set"] is False
    assert cl.modulation({}) == {"m": 0.89, "source": "default"}
    assert cl.pack(None) is None and cl.pack({"v_max": 50.4})["v_max"] == 50.4
    ctx = cl.context({"id": "x"}, None)
    assert ctx["limits"]["L_max_mm"] is None and ctx["current"]["set"] is False


def test_slot_fit_identity_matches_the_solvers_bound():
    """The web fits rows as floor((slot - 2 ins) / (wire + dy)); the solver
    bounds wire_height by (slot - 2 ins) / N - dy.  Same inequality."""
    g = {"slot_height": 6.0, "insulation_thickness": 0.06, "wire_spacing_y": 0.1}
    avail = g["slot_height"] - 2 * g["insulation_thickness"]
    for n in range(1, 25):
        h_max = gc._wire_height_max({**g, "num_wires_per_slot": n})
        assert h_max == pytest.approx(avail / n - g["wire_spacing_y"])
        for h in (0.2, 0.3, 0.6, 0.9, 1.4):
            fits_rows = n <= math.floor(avail / (h + g["wire_spacing_y"]) + 1e-9)
            fits_solver = h <= h_max + 1e-9
            assert fits_rows == fits_solver, (n, h)


# ---------------------------------------------------------------------------
# The phase-current ceiling — the formula, pinned (review 2026-10-05)
# ---------------------------------------------------------------------------
#
# A switch carries its leg's sine current for HALF the period, so its rms is
# I_leg,rms / sqrt(2) (peak / 2).  The datasheet's continuous drain current I_D
# is an rms-type rating, so the ceiling is  I_leg,rms,max = sqrt(2) * I_D * n_par.
# The PEAK current sqrt(2) * I_leg,rms = 2 * I_D is then judged against the
# pulsed rating I_DM (712 A here), a different and much larger limit.  The
# alternative I_D * n / sqrt(2) would hold the PEAK to the CONTINUOUS rating and
# use only half of what the part allows (the Controller tab would show that
# machine at 50 % device utilisation).  This is the very rule the Controller's
# "Continuous current per device" row applies, so the two cannot disagree.

L12_DEVICE = "IQE018N06NM6SC"          # the CIANO14 40/60V controller part


@pytest.mark.parametrize("n_par", [1, 2, 3])
def test_current_ceiling_formula_is_sqrt2_times_rating_times_parallel(n_par):
    card = dv.get_device(L12_DEVICE)
    assert card.i_d_rating(cl.RATING_T_CASE_C)["i_a"] == pytest.approx(126.0)   # Table 2, T_C = 100 degC
    cur = cl.current_limit({"device": L12_DEVICE, "devices_parallel": n_par})
    assert cur["set"] and cur["i_d_rating_A"] == pytest.approx(126.0)
    assert cur["i_phase_rms_max_A"] == pytest.approx(math.sqrt(2) * 126.0 * n_par, abs=0.06)
    assert cur["i_phase_rms_max_A"] == pytest.approx([178.2, 356.4, 534.6][n_par - 1], abs=0.06)


def test_current_ceiling_is_where_the_controller_puts_the_device_at_its_rating():
    """At the ceiling the Controller solve's per-device rms current equals the
    card's 100 degC continuous rating, and the peak is far inside I_DM."""
    from motor_ai_sim.inverter.losses import solve_controller
    card = dv.get_device(L12_DEVICE)
    i_d = card.i_d_rating(cl.RATING_T_CASE_C)["i_a"]
    for n_par in (1, 2):
        i_max = cl.current_limit({"device": L12_DEVICE, "devices_parallel": n_par})["i_phase_rms_max_A"]
        out = solve_controller({
            "standalone": True, "lean": True, "device": L12_DEVICE,
            "devices_parallel": n_par, "v_dc_V": 22.2, "i_phase_rms_A": i_max,
            "f_elec_hz": 1000.0, "f_carrier_hz": 48000.0, "modulation_index": 0.8,
            "power_factor": 0.9, "dead_time_us": 0.1, "cooling": {"mode": "liquid"}})
        rows = {r["name"]: r for r in out["limits"]}
        cont = rows["Continuous current per device"]
        peak = rows["Peak current per device"]
        assert cont["value"] == pytest.approx(i_d, rel=0.005) and cont["verdict"] == "pass"
        assert peak["value"] == pytest.approx(2.0 * i_d, rel=0.005) and peak["verdict"] == "pass"
        assert peak["limit"] == pytest.approx(card.i_d_pulsed_A)
