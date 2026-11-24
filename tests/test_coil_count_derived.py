# -*- coding: utf-8 -*-
"""``n_coils_per_phase`` is DERIVED (slots, layers); the stored copy can lag behind the machine.

A 24-slot single-layer die stored 8 (coil SIDES, slots / 3) where the application derives 4 (slots / 6).  Pins:

  * ``winding.coils_per_phase(slots, layers)``: single layer slots / 6, double layer slots / 3 (= coil_count / 3);
    the default keeps the old behaviour, so the connection validators are unchanged;
  * the readers that built something from the stored copy derive (``agent_designs._winding_of``, the BOM text,
    the passport fingerprint);
  * ``repick_connection``, the configuration save sites and ``PATCH /api/winding/config`` correct a stale count,
    return / log the correction, add the key nowhere, and leave a six-phase block alone;
  * a build stamp written while the copy was stale still reads as the current build after the heal.
"""
from __future__ import annotations

import pytest

from motor_ai_sim import winding as W
from test_winding_connection_slots import _set_machine, client  # noqa: F401 - the fixture and the machine setter


# ── the rule ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("slots, layers, want", [
    (24, 1, 4), (24, 2, 8), (12, 1, 2), (12, 2, 4), (48, 1, 8), (48, 2, 16), (60, 1, 10), (9, 2, 3),
])
def test_coils_per_phase_follows_layers(slots, layers, want):
    assert W.coils_per_phase(slots, layers) == want
    assert W.derived_n_coils_per_phase({"layers": layers}, slots) == want


def test_it_is_coil_count_over_three_when_the_slots_divide():
    for slots in (12, 24, 36, 48):
        for layers in (1, 2):
            assert W.coils_per_phase(slots, layers) == W.coil_count(slots, layers) // 3


def test_default_and_validators_are_unchanged():
    for slots in (12, 24, 36, 48, 60):
        assert W.coils_per_phase(slots) == W.coils_per_phase(slots, 1) == max(1, round(slots / 6))
    assert [o["label"] for o in W.connection_options(24)] == ["4S", "2S-2P", "4P"]
    assert W.derived_n_coils_per_phase({}, 24) == 4
    assert W.derived_n_coils_per_phase({"layers": "x"}, 24) == 4


def test_with_derived_coil_count():
    w, old = W.with_derived_coil_count({"n_coils_per_phase": 8, "layers": 1, "connection": "4P"}, 24)
    assert (w["n_coils_per_phase"], old) == (4, 8)
    w, old = W.with_derived_coil_count({"n_coils_per_phase": 4, "layers": 1}, 24)
    assert old is None and w["n_coils_per_phase"] == 4
    w, old = W.with_derived_coil_count({"n_coils_per_phase": 8, "layers": 2}, 24)     # double layer: 8 is right
    assert old is None and w["n_coils_per_phase"] == 8
    w, old = W.with_derived_coil_count({"connection": "4P"}, 24)                      # no key: none added
    assert old is None and "n_coils_per_phase" not in w
    w, old = W.with_derived_coil_count({"n_coils_per_phase": 8, "phases": 6}, 48)     # six-phase: as stored
    assert old is None and w["n_coils_per_phase"] == 8
    src = {"n_coils_per_phase": 8}
    W.with_derived_coil_count(src, 24)
    assert src["n_coils_per_phase"] == 8                                              # the input is not touched


def test_repick_heals_a_stale_count_with_a_valid_label():
    stale = {"connection": "4P", "n_parallel": 4, "n_series": 1, "n_coils_per_phase": 8, "layers": 1}
    w, note = W.repick_connection(stale, 24)
    assert note is None and w["n_coils_per_phase"] == 4
    assert (w["connection"], w["n_parallel"], w["n_series"]) == ("4P", 4, 1)          # the machine is the same
    assert stale["n_coils_per_phase"] == 8
    ok = {"connection": "4S", "n_parallel": 1, "n_series": 4, "n_coils_per_phase": 4, "layers": 1}
    assert W.repick_connection(ok, 24) == (ok, None)                                  # nothing to heal: identical
    nokey = {"connection": "4S", "n_parallel": 1, "n_series": 4}
    assert W.repick_connection(nokey, 24) == (nokey, None)


# ── the save sites ──────────────────────────────────────────────────────────
def test_config_save_heals_the_live_winding():
    from motor_ai_sim.routes.family import _live_winding_healed
    live = {"geometry": {"num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7},
            "winding": {"connection": "4P", "n_parallel": 4, "n_series": 1, "n_coils_per_phase": 8, "layers": 1}}
    w = _live_winding_healed(live)
    assert w["n_coils_per_phase"] == 4 and w["connection"] == "4P"
    assert live["winding"]["n_coils_per_phase"] == 8
    assert _live_winding_healed({"geometry": live["geometry"], "winding": {"connection": "4P"}}) == {"connection": "4P"}
    assert _live_winding_healed({}) == {}


def test_repick_config_winding_heals_and_reports_nothing():
    from motor_ai_sim.routes.family import _repick_config_winding
    c = {"winding": {"connection": "4P", "n_parallel": 4, "n_series": 1, "n_coils_per_phase": 8, "layers": 1}}
    assert _repick_config_winding(c, {"num_slots": 24, "num_poles": 28}, "test") is None
    assert c["winding"]["n_coils_per_phase"] == 4


def test_a_stamp_written_with_the_stale_count_is_still_current():
    from motor_ai_sim.routes.family import _build_sig_of, _build_sigs, build_sig_moved
    die = {"geometry": {"num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7, "wire_width": 7}}
    stale = {"geometry_overrides": {}, "materials": {"magnet": "N52"},
             "winding": {"connection": "4P", "n_parallel": 4, "n_series": 1, "n_coils_per_phase": 8, "layers": 1}}
    stamp = _build_sig_of(die, stale, stale["materials"])
    healed = {**stale, "winding": {**stale["winding"], "n_coils_per_phase": 4}}
    assert not build_sig_moved(stamp, _build_sigs(die, healed))
    other = {**healed, "winding": {**healed["winding"], "connection": "2S-2P", "n_parallel": 2, "n_series": 2}}
    assert build_sig_moved(stamp, _build_sigs(die, other))                            # a real change still moves it
    assert not build_sig_moved(stamp, _build_sigs(die, stale))


# ── PATCH / GET ─────────────────────────────────────────────────────────────
def test_patch_corrects_a_wrong_count_and_says_so(client):                            # noqa: F811
    _set_machine(4, "4S", 1, 4)                                                       # 24 slots
    r = client.patch("/api/winding/config", json={"n_coils_per_phase": 8})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"]["n_coils_per_phase"] == "4"
    assert body["n_coils_per_phase_corrected"]["requested"] == 8
    assert body["n_coils_per_phase_corrected"]["stored"] == 4
    assert client.get("/api/winding/config").json()["n_coils_per_phase"] == 4
    r = client.patch("/api/winding/config", json={"n_coils_per_phase": 4})
    assert "n_coils_per_phase_corrected" not in r.json()


def test_get_is_layer_aware(client):                                                  # noqa: F811
    _set_machine(4, "4S", 1, 4)
    assert client.get("/api/winding/config").json()["n_coils_per_phase"] == 4


# ── readers ─────────────────────────────────────────────────────────────────
def test_agent_design_winding_ignores_the_stale_copy(monkeypatch):
    import motor_ai_sim.agent_designs as AD
    pay = {"geometry": {"num_seg": 4, "num_slots_per_segment": 6, "num_wires_per_slot": 9, "wire_parallel": 1},
           "winding": {"n_coils_per_phase": 8, "n_parallel": 4, "connection": "4P"}, "sim": {}}
    monkeypatch.setattr(AD, "_payload", lambda *a, **k: pay)
    assert AD._winding_of("d", "c", "x")["coils_per_phase"] == 4
    pay["winding"]["n_coils_per_phase"] = 4
    assert AD._winding_of("d", "c", "x")["coils_per_phase"] == 4
    pay["geometry"].pop("num_seg")                                                    # no slot count: stored copy
    assert AD._winding_of("d", "c", "x")["coils_per_phase"] == 4


def test_passport_fingerprint_coils_are_derived():
    from motor_ai_sim.passport_v1.stage3 import _coils_of
    g = {"num_slots": 24}
    assert _coils_of(g, {"n_coils_per_phase": 8, "layers": 1}) == 4
    assert _coils_of(g, {"n_coils_per_phase": 8, "layers": 2}) == 8
    assert _coils_of({}, {"n_coils_per_phase": 8}) == 8
