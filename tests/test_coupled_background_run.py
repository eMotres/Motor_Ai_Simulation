"""``record: false`` — a coupled run that is NOT the loaded duty's answer.

THE DEFECT THIS PINS (measured live, 2026-09-15)
================================================
The duty-cycle editor has an escape hatch out of ``no_electromagnetic_run``: the
Thermal tab offers to MAKE the missing run, at the CALIBRATION duty's stored
point — 14.7 A, 1 000 rpm, coil 30 °C — through ``POST /api/coupled/run`` with
``max_iter = 1``.  The duty the editor actually has loaded at that moment is a
different one (the L13 ``peak 200С wire 120C NdFeB``: 45.96 A at 200 °C), and
every solve route files its answer under whatever ``active_context()`` names.
So the calibration run was filed as the PEAK duty's answer: its ``em`` and
``thermal`` field sidecars came back as 14.7 A / 30 °C maps, and
``GET /api/thermal/last`` answered 59 °C / 47.6 W where the peak's own answer is
409 °C / 680 W.

WHAT THE FLAG IS, AND WHAT IT IS NOT
------------------------------------
``record: false`` suppresses the BOOKKEEPING and nothing else:

  * ``duty_fields.save_active`` — every kind, so no per-duty field sidecar;
  * ``duty_results.record`` / ``record_alt_carrier`` — the one choke point every
    ``note_*`` seam goes through, so no per-duty compact row;
  * ``routes.thermal._remember_last``, ``routes.mechanical._remember_last``,
    ``routes.coupled._remember_last`` — no ``_LAST``, no ``.last_thermal.pkl`` /
    ``.last_mech.pkl`` / ``.last_coupled.json``.

and DELIBERATELY does not touch the FIELD SNAPSHOT STORE, which is where the
duty-cycle route looks the loss map up by its exact physics key.  That store is
the whole reason the run is made; a flag that suppressed it too would turn a
six-minute run into six minutes of nothing.  Both halves of that sentence are
asserted here, because a fix that goes one step too far fails silently and looks
exactly like the feature working.

It is refused with ``max_iter > 1``: an errand is one pass.

COST.  Nothing in this file solves.  The seams are called directly and the two
halves of the loop are recorders (the same shape ``tests/test_coupled``'s
``faked_halves`` uses) — what is under test is the WIRING, and a real transient
would prove it most slowly and least clearly.

NOTHING THE USER OWNS IS TOUCHED: every store these tests could write to is
redirected into a tmp directory first (the no-silent-state rule).
"""
from __future__ import annotations

import json
import pathlib

import pytest

from motor_ai_sim import run_recording as rr


# ---------------------------------------------------------------------------
# the stores, all of them, pointed somewhere harmless
# ---------------------------------------------------------------------------

@pytest.fixture
def stores(monkeypatch, tmp_path):
    """``_LAST`` + on-disk path of all three routes, plus the snapshot store."""
    from motor_ai_sim import duty_fields as df
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.routes import coupled as cp
    from motor_ai_sim.routes import mechanical as me
    from motor_ai_sim.routes import simulation as sim
    from motor_ai_sim.routes import thermal as th

    # NOTHING THE USER OWNS.  The CONTROL half of these tests runs the real
    # `_remember_last`, whose tail files a per-duty row and a per-duty field
    # sidecar under whatever duty the editor has loaded — which on this machine
    # is a real one.  "No active duty" is the seams' own designed silence, so
    # the control writes only into the tmp stores this fixture owns.
    monkeypatch.setattr(df, "active_context", lambda: None, raising=True)
    monkeypatch.setattr(dr, "active_context", lambda: None, raising=True)

    for mod, name in ((th, ".last_thermal.pkl"), (me, ".last_mech.pkl"),
                      (cp, ".last_coupled.json")):
        monkeypatch.setattr(mod, "_LAST", {}, raising=True)
        monkeypatch.setattr(mod, "_LAST_LOADED", True, raising=True)
        monkeypatch.setattr(mod, "_last_store_path",
                            lambda p=str(tmp_path / name): p, raising=True)

    saved_snap = dict(sim._transient_field_snap)
    sim._transient_field_snap.clear()
    yield {"tmp": pathlib.Path(tmp_path), "th": th, "me": me, "cp": cp,
           "sim": sim}
    sim._transient_field_snap.clear()
    sim._transient_field_snap.update(saved_snap)


# ---------------------------------------------------------------------------
# (1) the per-duty seams
# ---------------------------------------------------------------------------

def test_the_per_duty_field_sidecar_is_not_written_under_no_record(monkeypatch):
    """``duty_fields.save_active`` — the sidecar the L13 peak duty lost.

    Both directions in one test, because the claim is a DIFFERENCE: the same
    call with a live active duty writes, and writes nothing under the flag.
    """
    from motor_ai_sim import duty_fields as df

    wrote = []
    monkeypatch.setattr(df, "active_context",
                        lambda: ("die", "cfg", "peak 200С wire 120C NdFeB"),
                        raising=True)
    monkeypatch.setattr(df, "save",
                        lambda *a, **k: (wrote.append(a[3]), "path")[1],
                        raising=True)

    assert df.save_active("em", {"mesh": [1]}) == "path"
    assert wrote == ["em"]

    with rr.no_record():
        assert df.save_active("em", {"mesh": [1]}) is None
        assert df.save_active("thermal", {"mesh": [1]}) is None
        assert df.save_active("rotor_stress", {"mesh": [1]}) is None
    # NOT "one more": no kind got through.
    assert wrote == ["em"]


def test_every_note_seam_goes_silent_under_no_record(monkeypatch):
    """``duty_results.record`` is the choke point ALL of them go through.

    Asserted through the public ``note_*`` functions rather than the private
    one, so a future seam that is added beside them is covered by construction —
    which is the reason the guard was put there and not in six places.
    """
    from motor_ai_sim import duty_results as dr

    wrote = []
    monkeypatch.setattr(dr, "active_context", lambda: ("die", "cfg", "peak"),
                        raising=True)
    monkeypatch.setattr(dr, "read_all", lambda: {"results": {}}, raising=True)
    monkeypatch.setattr(dr, "_write_all",
                        lambda doc: (wrote.append(doc), True)[1], raising=True)

    assert dr.note_thermal({"ok": True}, {}, "fp") is True
    assert len(wrote) == 1

    with rr.no_record():
        assert dr.note_thermal({"ok": True}, {}, "fp") is False
        assert dr.note_coupled({"coupling": {}}) is False
        assert dr.note_mechanical("rotor_stress", {"ok": True}, {}, "fp") is False
        assert dr.note_pwm({"ok": True}, die="die", cfg="cfg", duty="peak") is False
        assert dr.note_em_pointer("die", "cfg", "peak") is False
        assert dr.record_alt_carrier("die", "cfg", "peak", "coupled",
                                     {"inverter": {"f_carrier_hz": 48000}}) is False
    assert len(wrote) == 1


# ---------------------------------------------------------------------------
# (2) the three "last result" stores
# ---------------------------------------------------------------------------

def test_the_thermal_last_result_is_not_replaced(stores):
    """The store behind ``GET /api/thermal/last`` — 59 °C where 409 °C belonged."""
    th = stores["th"]
    with rr.no_record():
        th._remember_last("field", {"ok": True, "components": {}}, {"rpm": 1000},
                          "fp")
    assert th._LAST == {}
    assert not (stores["tmp"] / ".last_thermal.pkl").exists()

    th._remember_last("field", {"ok": True, "components": {}}, {"rpm": 1000}, "fp")
    assert th._LAST["field"]["result"]["ok"] is True


def test_the_mechanical_last_result_is_not_replaced(stores):
    me = stores["me"]
    with rr.no_record():
        me._remember_last("rotor_stress", {"ok": True}, {"rpm": 1000}, "fp")
    assert me._LAST == {}
    assert not (stores["tmp"] / ".last_mech.pkl").exists()

    me._remember_last("rotor_stress", {"ok": True}, {"rpm": 1000}, "fp")
    assert me._LAST["rotor_stress"]["result"]["ok"] is True


def test_the_coupled_last_result_is_not_replaced(stores):
    cp = stores["cp"]
    with rr.no_record():
        cp._remember_last({"ok": True, "coupling": {"coil_temp_c": 30.0}})
    assert cp._LAST == {}
    assert not (stores["tmp"] / ".last_coupled.json").exists()

    cp._remember_last({"ok": True, "coupling": {"coil_temp_c": 200.0}})
    assert cp._LAST["coupling"]["coil_temp_c"] == 200.0
    assert json.loads((stores["tmp"] / ".last_coupled.json")
                      .read_text(encoding="utf-8"))["ok"] is True


# ---------------------------------------------------------------------------
# (3) …and the ONE store that must still be written
# ---------------------------------------------------------------------------

def test_the_field_snapshot_store_is_still_written_under_no_record(stores):
    """The whole point of the run.

    The duty-cycle route finds the loss map in ``_transient_field_snap`` by an
    exact physics key (current, angle, speed, coil temperature, frame count).
    Suppressing this store as well would make ``record: false`` a flag that
    spends six minutes and answers nothing — a failure that looks exactly like
    success from the outside, which is why it is pinned as its own test.
    """
    sim = stores["sim"]
    key = ("i", 14.7, "rpm", 1000.0, "coil", 30.0)
    with rr.no_record():
        sim._store_transient_field_snapshot(
            key, field={"tri": [1, 2, 3]}, sbres={"P_cu_W": 12.0, "rpm": 1000.0},
            eddy=True, n_steps_per_period=12, n_periods=1.0, solve_time_s=1.0)
    assert key in sim._transient_field_snap
    assert sim._transient_field_snap[key]["field"] == {"tri": [1, 2, 3]}
    assert sim._transient_field_snap[key]["scalars"]["P_cu_W"] == 12.0


# ---------------------------------------------------------------------------
# (4) the route
# ---------------------------------------------------------------------------

#: The Thermal tab's cooling, in the panel's own field names — valid, so
#: `cooling_issue` lets the request through to the part under test.
COOLING = {"coolMode": "air", "ambientT": "30", "airSpeed": "10",
           "boreMode": "air", "boreAirSpeed": "40", "shaftExtMm": "0",
           "maxIter": "6"}

#: What the duty-cycle editor's escape hatch posts, minus the machine: the
#: point is another duty's, the loop is one pass, and no rotor stress is asked
#: for.  `_preflight` is stubbed in the fixture below — every check in it is
#: about the machine's configuration and none of them is what this file tests.
ERRAND = {"I_phase_rms": 14.7, "gamma_deg": 0.0, "coil_temp_c": 30.0,
          "n_steps_per_period": 12, "eddy": True, "rotor_eddy": True,
          "mechanical": False, "max_iter": 1, "record": False,
          "for_duty": "cal 1000rpm", "thermal_settings": COOLING}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from motor_ai_sim.api import app
    return TestClient(app)


@pytest.fixture
def faked_halves(monkeypatch):
    """Both halves of the loop replaced by recorders — nothing solves.

    Same shape as ``tests/test_coupled.faked_halves``; ``_remember_last`` is
    deliberately NOT stubbed here, because it is the thing under test.
    """
    from motor_ai_sim.routes import coupled as cp

    calls = {"em": 0, "th": 0}

    def _em(body, *, coil_temp_c, magnet_temp_c, **_k):
        calls["em"] += 1
        return {"summary": {"P_loss_total_W": 100.0, "T_em_avg_Nm": 5.0,
                            "coil_temp_C": coil_temp_c}}

    def _th(body, cooling, *, coil_temp_c, magnet_temp_c, rpm, **_k):
        calls["th"] += 1
        return {"ok": True,
                "components": {"winding": {"avg": 31.0, "max": 33.0},
                               "magnet": {"avg": 30.5, "max": 31.0},
                               "shaft": {"avg": 30.0, "max": 30.2}}}

    monkeypatch.setattr(cp, "_em_run", _em, raising=True)
    monkeypatch.setattr(cp, "_thermal_solve", _th, raising=True)
    monkeypatch.setattr(cp, "_attach_coupling", lambda em, block: False,
                        raising=True)
    monkeypatch.setattr(cp, "_preflight", lambda body, **k: None, raising=True)
    from motor_ai_sim import jobs as _JOBS
    _JOBS.clear_cancelled()      # Stage 4: one run-id map, not a one-slot dict
    return calls


def test_a_record_false_run_answers_but_files_nothing(client, stores,
                                                      faked_halves):
    r = client.post("/api/coupled/run", json=dict(ERRAND))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert faked_halves["em"] == 1 and faked_halves["th"] == 1
    # …and the machine on screen still has whatever it had before.
    assert stores["cp"]._LAST == {}
    assert not (stores["tmp"] / ".last_coupled.json").exists()


def test_without_the_flag_the_very_same_run_is_still_filed(client, stores,
                                                           faked_halves):
    """The control.  A fix that suppressed recording for everybody would pass
    every assertion above and break the product."""
    body = {k: v for k, v in ERRAND.items() if k not in ("record", "for_duty")}
    r = client.post("/api/coupled/run", json=body)
    assert r.status_code == 200, r.text
    assert stores["cp"]._LAST.get("ok") is True
    assert (stores["tmp"] / ".last_coupled.json").exists()


@pytest.mark.parametrize("falsy", [False, "false", 0])
def test_the_flag_is_read_the_way_a_json_body_spells_it(falsy, client, stores,
                                                        faked_halves):
    r = client.post("/api/coupled/run", json=dict(ERRAND, record=falsy))
    assert r.status_code == 200, r.text
    assert stores["cp"]._LAST == {}


def test_an_errand_that_asks_for_a_loop_is_refused_by_name(client, stores,
                                                           faked_halves):
    """One pass or nothing.

    Iterating this machine's temperatures onto another duty's point is not a
    calibration run; and an answer nobody may file is an answer nobody can read.
    Refused BEFORE anything is solved — the recorders stay at zero.
    """
    r = client.post("/api/coupled/run", json=dict(ERRAND, max_iter=3))
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["error_code"] == "record_false_needs_single_pass"
    assert {"max_iter", "record"} <= {p["field"] for p in d["invalid_parameters"]}
    assert faked_halves["em"] == 0


def test_the_suppression_does_not_outlive_the_request(client, stores,
                                                      faked_halves):
    """A ContextVar that is never reset is a backend that stops recording
    anything after the first duty-cycle offer."""
    assert rr.recording() is True
    client.post("/api/coupled/run", json=dict(ERRAND))
    assert rr.recording() is True
    # …and the refusal path unwinds it too.
    client.post("/api/coupled/run", json=dict(ERRAND, max_iter=3))
    assert rr.recording() is True
