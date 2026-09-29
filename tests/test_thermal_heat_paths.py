"""THE HEAT-PATH MODEL — where the watts leave, on a machine you can draw.

``motor_ai_sim.thermal_heat_paths.heat_path_model`` computes no physics: it
reads the ``cooling`` block a thermal solve already wrote and says, per sink,
how many watts left, what share of what left that is, through what film or
conductance, and WHERE on the machine that surface sits.  So what is pinned here
is a CONTRACT, not a number the solver owns:

  (a) the Ø85 ROBOT JOINT (``CIANO28 85 20SW1200 / L13``, duty
      "rated 120С wire 80C NdFeB", solved 2026-09-14): mount 48.5 W, end faces
      5.9 W total, housing 1.1 W as 0.45 conv + 0.67 rad, bore 0.6 W — and the
      shares add to 100 % ± 1;
  (b) the Ø200 LIQUID-JACKETED motor (``CIANO10 200 opt / L155 motor``, duty
      "rated 1x9 mm"): the jacket is the housing sink and the bore air the only
      other one, every robotics path reports ``active: false`` rather than
      vanishing, and the shares still add to 100 % ± 1;
  (c) the GEOMETRY is recomputed from the primitives, never read off the die's
      own derived fields — ``die.yaml`` carries ``stator_inner_radius: 33.1``
      beside ``42.5 − 2.4 − 7.4 = 32.7``, and 32.7 is what the solver meshed;
  (d) the END-WINDING OVERHANG is the run's own k_end, as ℓ_end = (k_end − 1)·L/2
      — the identity ``routes.thermal`` builds the end-winding path on;
  (e) a machine-agnostic model: the same call serves both, and neither branch of
      the function names a machine.

THE COOLING BLOCKS BELOW ARE FROZEN COPIES of those two stored records
(``config/.duty_results.json``), trimmed to the keys the model reads.  Pinning
them inline rather than reading the user's store is deliberate: that file is a
live working document — the user re-solves duties all day — and a test that
moved with it would pin nothing.  ``test_matches_the_stored_records`` reads the
store when it is there and checks the frozen copies still describe it, so a
record that is re-solved to different watts is caught rather than ignored.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from motor_ai_sim.thermal_heat_paths import (
    HEAT_PATH_SCHEMA_VERSION, heat_path_model, machine_envelope)

CONFIG = Path(__file__).resolve().parents[1] / "config"


# ── (a) the Ø85 robot joint, still air + a bolted flange ────────────────────
L13_GEOMETRY = {
    "stator_diameter": 85, "core_thickness": 2.4, "slot_height": 7.4,
    "air_gap": 0.3, "magnet_height": 7, "rotor_house_height": 0.8,
    "shaft_height": 2, "motor_length": 13, "tooth_width": 5,
    "wire_width": 3.5, "wire_split": 1, "wire_spacing_x": 0.1,
    "insulation_thickness": 0.05, "num_slots": 24, "num_poles": 28,
    "sleeve_thickness": 0,
    # the STALE derived fields the die really carries — the model must ignore
    # them and recompute 32.7 from the primitives above
    "stator_outer_radius": 42.5, "stator_inner_radius": 33.1,
    "rotor_outer_radius": 32.8, "rotor_inner_radius": 25.0,
}

L13_THERMAL = {
    "P_loss_total_W": 55.3,
    "point": {"cooling_mode": "robotics", "ambient_temp": 40.0,
              "emissivity": 0.9, "mount_g_w_per_k": 2.0, "mount_temp_c": 40.0,
              "end_faces": "still", "end_face_sides": 2},
    "cooling": {
        "outer": {"mode": "robotics", "h_conv": 4.72, "t_sink_c": 40.0,
                  "heat_removed_W": 1.11, "area_m2": 0.003994, "h_rad": 7.018,
                  "h_total": 11.739, "t_wall_c": 63.75, "emissivity": 0.9,
                  "convection_W": 0.45, "radiation_W": 0.67,
                  "regime": "still air"},
        "inner": {"mode": "still", "h_conv": 5.39, "t_sink_c": 40.0,
                  "heat_removed_W": 0.59, "area_m2": 0.001846, "h_rad": 7.078,
                  "h_total": 12.468, "t_wall_c": 65.54, "convection_W": 0.25,
                  "radiation_W": 0.33, "regime": "still air in the bore"},
        "shaft_ends": {"mode": "off", "h_conv": 0.0, "t_sink_c": 40.0,
                       "heat_removed_W": 0.0, "length_each_side_mm": 0.0},
        "end_windings": {"mode": "housed", "h_conv": 0.0, "t_sink_c": 40.0,
                         "heat_removed_W": 0.0},
        "slot_channels": {"mode": "housed", "h_conv": 0.0, "t_sink_c": 40.0,
                          "heat_removed_W": 0.0},
        "mount": {"mode": "conduction", "G_W_per_K": 2.0, "t_sink_c": 40.0,
                  "t_sink_source": "given", "t_housing_mean_c": 64.27,
                  "heat_removed_W": 48.532, "attached_to": "stator"},
        "end_faces": {
            "mode": "still", "sides": 2, "emissivity": 0.9,
            "heat_removed_W": 5.947, "G_W_per_K": 0.20173, "k_end": 2.027,
            "winding": {"mode": "still", "area_m2": 0.00538953,
                        "h_total": 23.162, "n_faces": 2, "G_W_per_K": 0.12483,
                        "t_sink_c": 40.0, "t_mean_c": 72.25,
                        "heat_removed_W": 4.028},
            "stator": {"mode": "still", "area_m2": 0.0026691,
                       "h_total": 13.664, "n_faces": 2, "G_W_per_K": 0.03647,
                       "t_sink_c": 40.0, "t_mean_c": 64.27,
                       "heat_removed_W": 0.885},
            "rotor": {"mode": "still", "area_m2": 0.00136992,
                      "h_total": 14.48, "n_faces": 2, "G_W_per_K": 0.01984,
                      "t_sink_c": 40.0, "t_mean_c": 65.52,
                      "heat_removed_W": 0.507},
            "magnet": {"mode": "still", "area_m2": 0.00142695,
                       "h_total": 14.433, "n_faces": 2, "G_W_per_K": 0.02059,
                       "t_sink_c": 40.0, "t_mean_c": 65.52,
                       "heat_removed_W": 0.527},
        },
        "heat_budget": {
            "losses_W": 56.18, "housing_W": 1.11, "bore_W": 0.59,
            "gap_W": -0.13, "shaft_ends_W": 0.0, "end_windings_W": 0.0,
            "slot_channels_W": 0.0, "rotor_W": 1.496, "residual_W": 0.0,
            "residual_pct": 0.0, "mount_W": 48.532,
            "housing_convection_W": 0.45, "housing_radiation_W": 0.67,
            "end_faces_W": 5.947,
        },
    },
}

# ── (b) the Ø200 with a water jacket ───────────────────────────────────────
L155_GEOMETRY = {
    "stator_diameter": 200, "core_thickness": 14, "slot_height": 20.2,
    "air_gap": 3.2, "magnet_height": 31, "rotor_house_height": 6,
    "shaft_height": 5, "motor_length": 155, "tooth_width": 26,
    "wire_width": 9, "wire_split": 1, "wire_spacing_x": 0.1,
    "insulation_thickness": 0.25, "num_slots": 12, "num_poles": 10,
    "sleeve_thickness": 2.5,
}

L155_THERMAL = {
    "point": {"cooling_mode": "liquid", "ambient_temp": 30.0, "flow_lpm": 10.0},
    "cooling": {
        "outer": {"mode": "liquid", "h_conv": 100000.0, "t_sink_c": 68.09,
                  "t_in_c": 60.0, "t_out_c": 68.09, "flow_lpm": 10.0,
                  "fluid": "water", "heat_removed_W": 5644.14,
                  "area_m2": 0.081551, "regime": "turbulent"},
        "inner": {"mode": "air", "h_conv": 121.03, "t_sink_c": 32.31,
                  "air_speed_mps": 30.0, "heat_removed_W": 217.21,
                  "area_m2": 0.02005,
                  "regime": "turbulent + turbulent centrifugal convection"},
        "shaft_ends": {"mode": "off", "heat_removed_W": 0.0,
                       "length_each_side_mm": 0.0},
        "end_windings": {"mode": "housed", "heat_removed_W": 0.0},
        "slot_channels": {"mode": "housed", "heat_removed_W": 0.0},
        "mount": {"mode": "off", "G_W_per_K": 0.0, "t_sink_c": 30.0,
                  "heat_removed_W": 0.0, "attached_to": "stator"},
        "end_faces": {"mode": "off", "sides": 0, "heat_removed_W": 0.0,
                      "G_W_per_K": 0.0,
                      "winding": {"mode": "off", "heat_removed_W": 0.0},
                      "stator": {"mode": "off", "heat_removed_W": 0.0},
                      "rotor": {"mode": "off", "heat_removed_W": 0.0},
                      "magnet": {"mode": "off", "heat_removed_W": 0.0}},
        "heat_budget": {
            "losses_W": 5861.35, "housing_W": 5644.14, "bore_W": 217.21,
            "gap_W": 99.86, "shaft_ends_W": 0.0, "rotor_W": 317.071,
            "residual_W": -0.0, "residual_pct": 0.0, "mount_W": 0.0,
            "housing_convection_W": 5644.139, "housing_radiation_W": 0.0,
            "end_faces_W": 0.0,
        },
    },
}


def _by_id(model):
    return {s["id"]: s for s in model["sinks"]}


# ── (a) the robot joint ─────────────────────────────────────────────────────

def test_l13_sinks_carry_the_watts_the_solve_reported():
    m = heat_path_model(L13_THERMAL, L13_GEOMETRY)
    assert m["ok"] is True
    assert m["schema_version"] == HEAT_PATH_SCHEMA_VERSION
    assert m["cooling_mode"] == "robotics"
    assert m["ambient_c"] == pytest.approx(40.0)

    s = _by_id(m)
    assert s["mount"]["W"] == pytest.approx(48.532, abs=0.01)
    assert s["housing"]["W"] == pytest.approx(1.11, abs=0.01)
    assert s["bore"]["W"] == pytest.approx(0.59, abs=0.01)
    # the four axial faces, and their total — the block's own `heat_removed_W`
    ef = sum(s[k]["W"] for k in ("end_face_winding", "end_face_stator",
                                 "end_face_rotor", "end_face_magnet"))
    assert ef == pytest.approx(5.947, abs=0.01)
    assert s["end_face_winding"]["W"] == pytest.approx(4.028, abs=0.01)


def test_l13_housing_is_split_into_convection_and_radiation():
    """On a small machine in still air RADIATION carries more than convection.
    The split is the solver's own two lines, not an apportionment here."""
    s = _by_id(heat_path_model(L13_THERMAL, L13_GEOMETRY))["housing"]
    got = {d["label"]: d["W"] for d in s["detail"]}
    assert got["convection"] == pytest.approx(0.45, abs=0.005)
    assert got["radiation"] == pytest.approx(0.67, abs=0.005)
    # to the record's own rounding: the solver stores 1.114 W as 1.11 and its
    # two halves as 0.45 + 0.67 = 1.12.  The model must not reconcile them by
    # re-scaling one — the two lines are the solver's, printed as it wrote them.
    assert got["convection"] + got["radiation"] == pytest.approx(s["W"], abs=0.02)


def test_l13_shares_add_to_one_hundred_percent():
    m = heat_path_model(L13_THERMAL, L13_GEOMETRY)
    total = sum(s["pct"] for s in m["sinks"] if s["pct"] is not None)
    assert total == pytest.approx(100.0, abs=1.0)
    # …and the share is of what LEFT, so the mount is ~86 % of the outflow
    # (89 % of the STATOR side's balance, which is a different denominator).
    assert _by_id(m)["mount"]["pct"] == pytest.approx(86.4, abs=0.5)


def test_l13_balance_closes_against_the_generation():
    m = heat_path_model(L13_THERMAL, L13_GEOMETRY)
    t = m["totals"]
    assert t["generated_W"] == pytest.approx(56.18, abs=0.01)
    assert t["removed_W"] == pytest.approx(56.18, abs=0.05)
    assert abs(t["residual_W"]) < 0.05
    # which side of the machine has to be cooled
    assert t["stator_side_pct"] == pytest.approx(97.1, abs=0.5)
    assert t["rotor_side_pct"] == pytest.approx(2.9, abs=0.5)
    assert (t["stator_side_pct"] + t["rotor_side_pct"]) == pytest.approx(100.0, abs=0.2)


def test_l13_switched_off_paths_are_present_and_say_so():
    """A machine with nothing sticking out of the housing is an ANSWER.  A
    legend that simply dropped the row could not tell it from a path nobody
    asked about."""
    s = _by_id(heat_path_model(L13_THERMAL, L13_GEOMETRY))
    for sid in ("shaft_ends", "end_windings", "slot_channels"):
        assert sid in s, f"{sid} disappeared instead of reporting itself off"
        assert s[sid]["active"] is False
        assert s[sid]["W"] == pytest.approx(0.0)
        assert s[sid]["intensity"] == 0.0


def test_l13_intensity_is_against_the_biggest_path_not_the_share():
    s = _by_id(heat_path_model(L13_THERMAL, L13_GEOMETRY))
    assert s["mount"]["intensity"] == pytest.approx(1.0)
    # the end turns are 8 % of the mount — visible on a colour scale, which is
    # the whole point of normalising against the peak rather than the total
    assert s["end_face_winding"]["intensity"] == pytest.approx(4.028 / 48.532,
                                                               rel=0.02)
    for sink in s.values():
        assert 0.0 <= sink["intensity"] <= 1.0


# ── (c) + (d) the geometry the picture is drawn from ───────────────────────

def test_geometry_is_recomputed_from_the_primitives_not_the_stored_radii():
    """``die.yaml`` carries ``stator_inner_radius: 33.1`` beside primitives that
    say 42.5 − 2.4 − 7.4 = 32.7.  The solver meshes 32.7; so must the picture."""
    g = heat_path_model(L13_THERMAL, L13_GEOMETRY)["geometry"]
    assert g["known"] is True
    assert g["housing_r_mm"] == pytest.approx(42.5)
    assert g["slot_r_in_mm"] == pytest.approx(32.7)        # NOT the stored 33.1
    assert g["gap_r_in_mm"] == pytest.approx(32.4)         # NOT the stored 32.8
    assert g["rotor_iron_r_in_mm"] == pytest.approx(24.6)  # NOT the stored 25.0
    assert g["magnet_r_in_mm"] == pytest.approx(25.4)
    assert g["magnet_r_out_mm"] == pytest.approx(32.4)
    assert g["stack_length_mm"] == pytest.approx(13.0)


def test_bore_radius_is_inverted_from_the_area_the_solver_measured():
    """``area = 2πrL`` on the stored 0.001846 m² over 13 mm gives 22.6 mm, which
    is also ``r_rotor_in − shaft_height``.  The measured one wins: it is what
    the mesh actually had."""
    g = heat_path_model(L13_THERMAL, L13_GEOMETRY)["geometry"]
    r = 0.001846 / (2 * math.pi * 0.013) * 1e3
    assert g["bore_r_mm"] == pytest.approx(r, abs=0.05)
    assert g["bore_r_mm"] == pytest.approx(22.6, abs=0.05)


def test_end_winding_overhang_follows_the_runs_own_k_end():
    """ℓ_end = (k_end − 1)·L/2 — the identity routes.thermal builds the
    end-winding path on.  k_end 2.027 over a 13 mm stack is 6.68 mm a side, so
    the end turns are LONGER than the core is deep."""
    g = heat_path_model(L13_THERMAL, L13_GEOMETRY)["geometry"]
    assert g["k_end"] == pytest.approx(2.027, abs=1e-3)
    assert g["end_winding_overhang_mm"] == pytest.approx((2.027 - 1) * 13 / 2,
                                                         abs=1e-3)
    assert g["end_winding_r_in_mm"] == pytest.approx(32.7)
    assert g["end_winding_r_out_mm"] == pytest.approx(40.1)
    # the drawn extent has to contain them
    assert g["z_extent_mm"][1] == pytest.approx(6.5 + g["end_winding_overhang_mm"],
                                                abs=1e-3)


def test_k_end_falls_back_to_the_geometry_when_the_record_has_none():
    """A liquid machine's `end_faces` block is off and carries no k_end, and the
    end turns are still there — so the estimator (masses.end_winding_factor's
    span: tooth + the whole wire column) has to supply one."""
    g = machine_envelope(L155_GEOMETRY)
    assert g["k_end"] is not None and g["k_end"] > 1.0
    span = 26 + 9                     # tooth_width + wire column at split 1
    assert g["k_end"] == pytest.approx((math.pi * span / 2 + 155) / 155, abs=1e-3)
    assert g["end_winding_overhang_mm"] == pytest.approx(
        (g["k_end"] - 1) * 155 / 2, abs=1e-3)


def test_placements_are_inside_the_machine_and_ordered():
    g = heat_path_model(L13_THERMAL, L13_GEOMETRY)["geometry"]
    assert (g["bore_r_mm"] < g["shaft_r_out_mm"] <= g["magnet_r_in_mm"]
            < g["magnet_r_out_mm"] < g["slot_r_in_mm"] < g["slot_r_out_mm"]
            <= g["housing_r_mm"])
    for s in heat_path_model(L13_THERMAL, L13_GEOMETRY)["sinks"]:
        p = s["placement"]
        assert p["kind"] in ("cylinder", "annulus", "band", "stub")
        for k in ("r_mm", "r_in_mm", "r_out_mm"):
            if p.get(k) is not None:
                assert 0.0 <= p[k] <= g["housing_r_mm"] + 1e-6, (s["id"], k)
        if p["kind"] == "annulus":
            assert p["r_in_mm"] <= p["r_out_mm"], s["id"]


def test_the_mount_is_one_end_annulus_and_the_end_faces_are_both_sides():
    """The flange conductance is LUMPED — the solve never says which end the arm
    is on — so the picture draws one side and must not claim two.  The end
    faces DO know: `sides: 2` is both."""
    s = _by_id(heat_path_model(L13_THERMAL, L13_GEOMETRY))
    assert s["mount"]["placement"]["sides"] == [-1]
    assert s["end_face_winding"]["placement"]["sides"] == [-1, 1]
    assert s["end_face_stator"]["placement"]["sides"] == [-1, 1]
    # the end turns are a BAND that stands proud of the core, not a flat annulus
    assert s["end_face_winding"]["placement"]["kind"] == "band"
    assert s["end_face_winding"]["placement"]["length_mm"] == pytest.approx(
        6.676, abs=1e-2)


# ── (b) the jacketed machine ───────────────────────────────────────────────

def test_l155_jacket_and_bore_are_the_only_two_paths():
    m = heat_path_model(L155_THERMAL, L155_GEOMETRY)
    assert m["ok"] is True
    assert m["cooling_mode"] == "liquid"
    s = _by_id(m)
    assert s["housing"]["W"] == pytest.approx(5644.14, abs=0.1)
    assert s["housing"]["label"].endswith("liquid jacket")
    assert s["bore"]["W"] == pytest.approx(217.21, abs=0.1)
    active = sorted(k for k, v in s.items() if v["active"])
    assert active == ["bore", "housing"]
    # every robotics path is present and says off — not missing
    for sid in ("mount", "end_face_winding", "end_face_stator",
                "end_face_rotor", "end_face_magnet", "shaft_ends"):
        assert s[sid]["active"] is False


def test_l155_shares_add_to_one_hundred_percent():
    m = heat_path_model(L155_THERMAL, L155_GEOMETRY)
    total = sum(s["pct"] for s in m["sinks"] if s["pct"] is not None)
    assert total == pytest.approx(100.0, abs=1.0)
    s = _by_id(m)
    assert s["housing"]["pct"] == pytest.approx(96.3, abs=0.5)
    assert s["bore"]["pct"] == pytest.approx(3.7, abs=0.5)
    t = m["totals"]
    assert t["removed_W"] == pytest.approx(5861.35, abs=0.5)
    assert abs(t["residual_W"]) < 0.5


def test_l155_sleeve_sits_between_the_magnets_and_the_gap():
    g = heat_path_model(L155_THERMAL, L155_GEOMETRY)["geometry"]
    assert g["sleeve_thickness_mm"] == pytest.approx(2.5)
    assert g["sleeve_r_out_mm"] == pytest.approx(g["gap_r_in_mm"])
    assert g["sleeve_r_in_mm"] == pytest.approx(g["magnet_r_out_mm"])
    assert g["magnet_r_out_mm"] == pytest.approx(62.6 - 2.5)


def test_l155_bore_radius_from_its_own_measured_area():
    g = heat_path_model(L155_THERMAL, L155_GEOMETRY)["geometry"]
    assert g["bore_r_mm"] == pytest.approx(
        0.02005 / (2 * math.pi * 0.155) * 1e3, abs=0.05)


# ── (e) machine-agnostic, and the refusals ─────────────────────────────────

def test_the_same_call_serves_both_machines():
    """Nothing in the model branches on a machine.  The two answers differ only
    in which sinks are active — the sink LIST is the same, in the same order."""
    a = heat_path_model(L13_THERMAL, L13_GEOMETRY)
    b = heat_path_model(L155_THERMAL, L155_GEOMETRY)
    assert [s["id"] for s in a["sinks"]] == [s["id"] for s in b["sinks"]]


def test_no_cooling_block_is_refused_by_name_not_drawn_empty():
    out = heat_path_model({"T_max": 90.0})
    assert out["ok"] is False
    assert "cooling" in out["reason"]


def test_a_result_with_no_geometry_still_lists_the_watts():
    """The watts are the answer; the drawing is the illustration.  A model asked
    without geometry must still report every sink, with the placement radii
    absent rather than invented."""
    m = heat_path_model(L13_THERMAL, None)
    assert m["ok"] is True
    assert m["geometry"]["known"] is False
    assert _by_id(m)["mount"]["W"] == pytest.approx(48.532, abs=0.01)
    total = sum(s["pct"] for s in m["sinks"] if s["pct"] is not None)
    assert total == pytest.approx(100.0, abs=1.0)


# ── the frozen copies against the user's live store ────────────────────────

@pytest.mark.parametrize("machine,config,duty_prefix,expect", [
    ("CIANO28 85 20SW1200", "L13", "rated",
     {"mount_W": 48.532, "end_faces_W": 5.947, "housing_W": 1.11, "bore_W": 0.59}),
    ("CIANO10 200 opt", "L155 motor", "rated 1x9",
     {"mount_W": 0.0, "end_faces_W": 0.0, "housing_W": 5644.14,
      "bore_W": 217.21}),
])
def test_matches_the_stored_records(machine, config, duty_prefix, expect):
    """The frozen blocks above still describe the records they were copied from.

    Skipped when the store is absent (a clean checkout, CI) — this is the user's
    own working file, and its absence is not a failure.  When it IS there and a
    duty has been re-solved to different watts, this is what says so.
    """
    store = CONFIG / ".duty_results.json"
    if not store.exists():
        pytest.skip("no config/.duty_results.json in this checkout")
    data = json.loads(store.read_text(encoding="utf-8")).get("results") or {}
    duties = ((data.get(machine) or {}).get(config) or {})
    rec = next((v for k, v in duties.items() if k.startswith(duty_prefix)), None)
    if not isinstance(rec, dict) or not isinstance(rec.get("thermal"), dict):
        pytest.skip(f"{machine} / {config} / {duty_prefix}* not solved here")
    budget = ((rec["thermal"].get("cooling") or {}).get("heat_budget") or {})
    for key, want in expect.items():
        assert float(budget.get(key) or 0.0) == pytest.approx(want, rel=2e-3,
                                                              abs=0.01), key
