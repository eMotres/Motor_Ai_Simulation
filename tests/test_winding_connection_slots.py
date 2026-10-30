# -*- coding: utf-8 -*-
"""The winding CONNECTION follows the slot count (owner 2026-10-20).

A die grown from the 24s/28p M1 to 48s/56p (8 segments) and 60s/70p (10
segments) kept the `2S` it was born with, the Simulation panel kept the
previous machine's option list, and a `10P` the panel still held was solved on
a 48-slot machine (8 coils/phase) with a tenth of the coil MMF.  Pins:

  * the options, the error text and the re-pick rule (``motor_ai_sim.winding``);
  * GET /api/winding/config returns the options and coils/phase of the LIVE
    slot count (24 -> 60 -> 48) and REPORTS (never rewrites) a stored
    connection that slot count does not have: ``connection_valid`` false and
    the reason in ``connection_error`` (owner 2026-10-30: a read that re-picked
    the machine raced the machine load; the "set to 8S" notes are gone);
  * PATCH /api/simulation/config and the solver REFUSE a connection the
    machine does not have (never solve an impossible winding);
  * a configuration copied onto another lamination is re-picked
    (``family._repick_config_winding``);
  * the phase excitation stays balanced: the single-layer layout of 24, 48 and
    60 slots gives three equal back-EMF phasors 120 degrees apart.
"""
from __future__ import annotations

import cmath
import math
import os
from pathlib import Path

import pytest
import yaml

from motor_ai_sim import winding as W


# ── the rule ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("slots, labels", [
    (24, ["4S", "2S-2P", "4P"]),
    (48, ["8S", "4S-2P", "2S-4P", "8P"]),
    (60, ["10S", "5S-2P", "2S-5P", "10P"]),
    (12, ["2S", "2P"]),
])
def test_options_follow_the_slot_count(slots, labels):
    assert [o["label"] for o in W.connection_options(slots)] == labels
    assert W.coils_per_phase(slots) == slots // 6
    for lab in labels:
        assert W.connection_error(lab, slots) is None


@pytest.mark.parametrize("conn, slots", [("2S", 48), ("10P", 48), ("2S", 60),
                                         ("8S", 60), ("4S", 48), ("nonsense", 24)])
def test_an_impossible_connection_is_named(conn, slots):
    err = W.connection_error(conn, slots)
    assert err and "Valid" in err
    for o in W.connection_options(slots):
        assert o["label"] in err


def test_repick_keeps_the_parallel_paths_when_it_can():
    w, note = W.repick_connection({"connection": "2S", "n_parallel": 1, "n_series": 2,
                                   "n_coils_per_phase": 4, "layers": 1}, 48)
    assert (w["connection"], w["n_parallel"], w["n_series"], w["n_coils_per_phase"]) == ("8S", 1, 8, 8)
    assert w["layers"] == 1 and "2S" in note and "8S" in note
    w, note = W.repick_connection({"connection": "2S-2P", "n_parallel": 2}, 60)
    assert (w["connection"], w["n_parallel"]) == ("5S-2P", 2) and note
    # 10 paths do not divide 8 coils: all series
    w, note = W.repick_connection({"connection": "10P", "n_parallel": 10}, 48)
    assert (w["connection"], w["n_parallel"]) == ("8S", 1) and note
    # a valid one is left alone, byte for byte
    src = {"connection": "4S-2P", "n_parallel": 2, "n_series": 4, "n_coils_per_phase": 4}
    w, note = W.repick_connection(src, 48)
    assert note is None and w == src


def test_two_set_48_slot_4s_2p_is_never_repicked():
    """Owner 2026-10-29 (Winding panel, 48 slots, 2x3ph, stored 4S-2P,
    n_parallel 2, one path per set): the panel said "'4S' is not valid for 48
    slots - set to 8S".  The per-set label "4S" reached the 3-phase re-pick;
    8S would double the series turns.  The label is normalised, the machine
    is not touched, and nothing is announced."""
    six = {"connection": "4S", "n_parallel": 2, "n_series": 4, "phases": 6,
           "set1_paths": "1", "set_neutrals": "isolated", "layers": 1}
    w, note = W.repick_connection(six, 48)
    assert note is None
    assert (w["connection"], w["n_parallel"], w["n_series"]) == ("4S-2P", 2, 4)
    assert (w["phases"], w["set1_paths"], w["set_neutrals"]) == (6, "1", "isolated")
    # the per-set label alone (no counts stored) reads as two sets of it
    w, note = W.repick_connection({"connection": "4S", "phases": 6}, 48)
    assert note is None and (w["connection"], w["n_parallel"]) == ("4S-2P", 2)
    # the canonical two-set label is left alone, byte for byte
    src = {"connection": "4S-2P", "n_parallel": 2, "n_series": 4, "phases": 6}
    assert W.repick_connection(src, 48) == (src, None)
    # a two-set winding that resolves neither way is never re-picked to all series
    bad = {"connection": "3S", "n_parallel": 1, "n_series": 3, "phases": 6}
    assert W.repick_connection(bad, 48) == (bad, None)
    # a 3-phase label that only disagrees with valid stored counts: relabel only
    w, note = W.repick_connection({"connection": "4S", "n_parallel": 2, "n_series": 4}, 48)
    assert note is None and (w["connection"], w["n_parallel"], w["n_series"]) == ("4S-2P", 2, 4)


def test_two_set_configuration_payload_keeps_its_connection():
    """The duty payload's re-pick (family._repick_config_winding) is the one
    that raised the note on load: it must hand the frontend 4S-2P."""
    from motor_ai_sim.routes.family import _repick_config_winding
    c = {"winding": {"connection": "4S", "n_parallel": 2, "n_series": 4, "phases": 6,
                     "set1_paths": "1", "set_neutrals": "isolated"}}
    assert _repick_config_winding(c, {"num_seg": 8, "num_slots_per_segment": 6,
                                      "num_poles_per_segment": 7}, "test") is None
    assert c["winding"]["connection"] == "4S-2P" and c["winding"]["n_series"] == 4


def test_slot_count_comes_from_the_segment_form():
    # the stored num_slots is a derived copy that may lag the segment count
    assert W.slot_pole_counts({"num_seg": 8, "num_slots_per_segment": 6,
                               "num_poles_per_segment": 7, "num_slots": 60,
                               "num_poles": 70}) == (48, 56)
    assert W.slot_pole_counts({"num_slots": 36, "num_poles": 42}) == (36, 42)


# ── balanced excitation for every slot count on the way ─────────────────────

@pytest.mark.parametrize("seg", [4, 10, 8])            # 24 -> 60 -> 48 slots
def test_single_layer_layout_is_a_balanced_three_phase_winding(seg):
    from motor_ai_sim.simulation.geometry_2d import build_winding_layout
    slots, poles = 6 * seg, 7 * seg
    lay = build_winding_layout(slots, poles // 2, single_layer=True)
    assert len(lay) == slots
    # back-EMF phasor of each phase: sum of its slot-conductor phasors
    a = (poles // 2) * 2 * math.pi / slots
    ph = {k: sum(d * cmath.exp(1j * a * i) for i, (p, d) in enumerate(lay) if p == k)
          for k in "ABC"}
    mags = [abs(ph[k]) for k in "ABC"]
    assert min(mags) > 0 and max(mags) / min(mags) == pytest.approx(1.0, abs=1e-9)
    # A -> B -> C 120 degrees apart (either sequence)
    dab = math.degrees(cmath.phase(ph["B"] / ph["A"])) % 360
    dac = math.degrees(cmath.phase(ph["C"] / ph["A"])) % 360
    assert {round(dab), round(dac)} == {120, 240}
    # every phase holds C coils' worth of conductors (2 per coil)
    for k in "ABC":
        assert sum(1 for p, _ in lay if p == k) == 2 * W.coils_per_phase(slots)


# ── the live config: GET re-picks, PATCH and the solver refuse ──────────────

def _cfg_path() -> Path:
    return Path(os.environ["MOTOR_AI_SIM_CONFIG"])


def _set_machine(seg: int, connection: str, n_parallel: int, n_series: int) -> None:
    from motor_ai_sim.config import clear_config_cache
    p = _cfg_path()
    c = yaml.safe_load(p.read_text(encoding="utf-8"))
    g = c["geometry"]
    g["num_seg"] = seg
    g["num_slots_per_segment"] = 6
    g["num_poles_per_segment"] = 7
    for k in ("num_slots", "num_poles"):
        if k in g and not isinstance(g[k], str):
            g[k] = seg * (6 if k == "num_slots" else 7)
    c["winding"].update({"connection": connection, "n_parallel": n_parallel,
                         "n_series": n_series, "n_coils_per_phase": 4, "layers": 1})
    c["winding"].pop("phases", None)
    p.write_text(yaml.safe_dump(c, sort_keys=False, allow_unicode=True), encoding="utf-8")
    clear_config_cache()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from motor_ai_sim.api import app
    p = _cfg_path()
    before = p.read_text(encoding="utf-8")
    yield TestClient(app)
    p.write_text(before, encoding="utf-8")
    from motor_ai_sim.config import clear_config_cache
    clear_config_cache()


def test_get_follows_24_60_48_and_reports_without_rewriting(client):
    _set_machine(4, "2S-2P", 2, 2)                       # 24 slots, valid
    g = client.get("/api/winding/config").json()
    assert [c["label"] for c in g["connections"]] == ["4S", "2S-2P", "4P"]
    assert g["connection"] == "2S-2P" and g["connection_valid"] and not g["connection_error"]
    assert g["n_coils_per_phase"] == 4
    assert "connection_note" not in g

    _set_machine(10, "2S-2P", 2, 2)                      # 60 slots: 2S-2P is not one
    g = client.get("/api/winding/config").json()
    assert [c["label"] for c in g["connections"]] == ["10S", "5S-2P", "2S-5P", "10P"]
    assert g["n_coils_per_phase"] == 10
    # reported, not re-picked: the stored winding is exactly what it was
    assert g["connection"] == "2S-2P" and g["connection_valid"] is False
    assert "60 slots" in g["connection_error"] and "5S-2P" in g["connection_error"]
    w = yaml.safe_load(_cfg_path().read_text(encoding="utf-8"))["winding"]
    assert (w["connection"], w["n_parallel"], w["n_series"]) == ("2S-2P", 2, 2)

    _set_machine(8, "2S", 1, 2)                          # 48 slots, the owner's L80
    g = client.get("/api/winding/config").json()
    assert [c["label"] for c in g["connections"]] == ["8S", "4S-2P", "2S-4P", "8P"]
    assert g["connection"] == "2S" and g["connection_valid"] is False
    # an option of ANOTHER slot count is refused by the winding route, with the
    # one-line reason - the stored winding is untouched
    r = client.patch("/api/winding/config", json={"connection": "10P"})
    assert r.status_code == 400 and "8S" in r.json()["detail"]
    w = yaml.safe_load(_cfg_path().read_text(encoding="utf-8"))["winding"]
    assert w["connection"] == "2S"


# ── a machine LOAD is one change: no snap, no note (owner 2026-10-30) ───────

def _geo_48() -> dict:
    return {"num_seg": 8, "num_slots_per_segment": 6, "num_poles_per_segment": 7}


def test_machine_load_writes_geometry_and_winding_together(client):
    """CILN28 (24 slots, 3-phase 4S) -> Erium (48 slots, six-phase 4S-2P): the
    old winding must never be validated against the 48-slot geometry."""
    _set_machine(4, "4S", 1, 4)
    r = client.put("/api/geometry/load", json={
        "geometry": _geo_48(),
        "winding": {"connection": "4S-2P", "phases": 6, "set1_paths": "1",
                    "set_neutrals": "isolated"}})
    assert r.status_code == 200, r.text
    assert "winding_note" not in r.json()
    w = yaml.safe_load(_cfg_path().read_text(encoding="utf-8"))["winding"]
    assert (w["connection"], w["n_parallel"], w["n_series"], w["phases"]) == ("4S-2P", 2, 4, 6)
    g = client.get("/api/winding/config").json()
    assert g["connection"] == "4S-2P" and g["connection_valid"] and g["phases"] == 6
    assert "connection_note" not in g and g["num_slots"] == 48


def test_machine_load_never_snaps_the_incoming_winding(monkeypatch, client):
    """The geometry write of a load does not re-pick - not even internally."""
    import motor_ai_sim.api as api
    calls = []
    real = api.reconcile_live_winding
    monkeypatch.setattr(api, "reconcile_live_winding",
                        lambda: calls.append(1) or real())
    _set_machine(4, "4S", 1, 4)
    r = client.put("/api/geometry/load", json={
        "geometry": _geo_48(), "winding": {"connection": "8S", "phases": 3}})
    assert r.status_code == 200, r.text
    assert calls == []


def test_machine_load_refuses_a_winding_the_incoming_machine_lacks(client):
    """Refused with a one-line reason, NOTHING written (geometry included)."""
    _set_machine(4, "4S", 1, 4)
    before = _cfg_path().read_text(encoding="utf-8")
    r = client.put("/api/geometry/load", json={
        "geometry": _geo_48(), "winding": {"connection": "10P", "phases": 3}})
    assert r.status_code == 400
    d = r.json()["detail"]
    assert "10P" in d and "48 slots" in d and "8S" in d
    assert _cfg_path().read_text(encoding="utf-8") == before


def test_a_users_own_geometry_edit_still_repicks_but_announces_nothing(client):
    _set_machine(4, "2S-2P", 2, 2)
    r = client.put("/api/geometry", json=_geo_48())
    assert r.status_code == 200, r.text
    assert "winding_note" not in r.json()
    assert "connection_note" not in client.get("/api/winding/config").json()


def test_simulation_config_refuses_a_connection_the_machine_has_not(client):
    _set_machine(8, "8S", 1, 8)
    r = client.patch("/api/simulation/config", json={"connection": "10P"})
    assert r.status_code == 422 and "48 slots" in r.json()["detail"]
    w = yaml.safe_load(_cfg_path().read_text(encoding="utf-8"))["winding"]
    assert w["connection"] == "8S" and w["n_parallel"] == 1


def test_solver_refuses_an_impossible_winding(client):
    from motor_ai_sim.simulation.fem_solver_2d import fem_transient_sliding_band
    _set_machine(8, "8S", 1, 8)
    with pytest.raises(ValueError, match="impossible winding"):
        fem_transient_sliding_band(n_steps_per_period=4, I_phase_rms=0.0,
                                   connection="10P", n_sectors=8, daxis_deg=60.0)
    # the stored label is checked too, when the request names none
    _set_machine(8, "2S", 1, 2)
    with pytest.raises(ValueError, match="impossible winding"):
        fem_transient_sliding_band(n_steps_per_period=4, I_phase_rms=0.0,
                                   n_sectors=8, daxis_deg=60.0)


def test_a_copied_configuration_is_repicked_for_its_lamination():
    from motor_ai_sim.routes.family import _repick_config_winding
    c = {"winding": {"connection": "2S", "n_parallel": 1, "n_series": 2,
                     "n_coils_per_phase": 4, "layers": 1}}
    note = _repick_config_winding(c, {"num_seg": 8, "num_slots_per_segment": 6,
                                      "num_poles_per_segment": 7}, "test")   # logged, not shown
    assert note and c["winding"]["connection"] == "8S" and c["winding"]["n_coils_per_phase"] == 8
    c2 = {"winding": {"connection": "4S", "n_parallel": 1, "n_series": 4}}
    assert _repick_config_winding(c2, {"num_slots": 24, "num_poles": 28}, "test") is None
    assert c2["winding"]["connection"] == "4S"
