"""Shaft skin layer: the skin-depth-driven structured wall mesh.

docs/CONDUCTIVE_BODY_MESH_CONVERGENCE_2026-09-24.md.  A 42CrMo4 shaft carries
its eddy current in δ = sqrt(2/(ωμσ)) ≈ 0.05-0.3 mm under its OD; the CDT
meshed the wall with 2-3 mm cells (1-2 across a 5 mm wall), which reads the
loss low.  `conductor_skin.shaft_skin_spec` sizes a layered wall on δ and
(the structured wall patch was built by the geometry-driven CDT mesher,
removed with `triangle` on 2026-09-29).

Asserted here:
  * the rule's arithmetic (δ, μ_r,max of a B-H curve, the reference frequency,
    the spec) — against closed forms, not restatements;
  * the layer radii (first layer h1, geometric growth, capped, no sliver);
  * the built mesh on two real rotors, full ring and half-model sector:
    the shaft region is still the CAD tube, the first layer is h1 thick, the
    mesh is conforming (no hanging node at the stitched arcs: every edge used
    by ONE triangle lies on the rotor OD or on a sector cut ray), the cut rays
    carry identical radii on both sides (anti-periodic pairing), and nothing
    outside the shaft changes but the iron cells next to the finer OD ring.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.simulation import conductor_skin as cs
from motor_ai_sim.simulation.sb_domains import DOM_SHAFT

MU0 = 4e-7 * math.pi
_ROOT = Path(__file__).resolve().parents[1]
_PRESETS = json.loads((_ROOT / "config" / "motor_presets.json")
                      .read_text(encoding="utf-8"))
G150 = {
    "stator_diameter": 150.0, "slot_height": 14.0, "core_thickness": 4.2,
    "num_seg": 4, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.5, "tooth_width": 9.2, "tooth2_width": 5.5, "cut_width": 6.0,
    "insulation_thickness": 0.15, "wire_width": 5.0, "wire_height": 0.6,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.13, "num_wires_per_slot": 14,
    "wire_split": 1, "slot_hs": 0.2, "magnet_height": 16.0,
    "rotor_house_height": 1.2, "shaft_height": 3.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.44, "magnet_fill_radius": 2.5, "magnet_up_gap": 2.0,
    "rotor_hole": 0.6, "magnet_down_height": 1.8, "magnet_lamination": 0,
    "stator_fillet_r": 3.5, "stator_fillet_r1": 1.2, "rotor_fill_r": 0.2,
    "motor_length": 35.0,
}
G40 = _PRESETS["my_40mm_last"]["geometry"]


# ── the rule ─────────────────────────────────────────────────────────────────
def test_skin_depth_closed_form():
    # copper at 50 Hz: 9.2 mm (textbook); steel sigma 4.4e6, mu_r 1000, 2840 Hz
    assert cs.skin_depth_m(50.0, 5.8e7, 1.0) == pytest.approx(9.35e-3, rel=5e-3)
    d = cs.skin_depth_m(2840.0, 4.4e6, 1000.0)
    assert d == pytest.approx(math.sqrt(2.0 / (2 * math.pi * 2840 * MU0 * 1000 * 4.4e6)))
    assert cs.skin_depth_m(0.0, 4.4e6, 1000.0) == math.inf
    assert cs.skin_depth_m(100.0, 0.0, 1.0) == math.inf


def test_mu_r_max_is_the_largest_secant_permeability():
    bh = [[0, 0.0], [200, 0.25], [1000, 1.0], [5000, 1.5], [50000, 2.0]]
    want = max(0.25 / (MU0 * 200), 1.0 / (MU0 * 1000), 1.5 / (MU0 * 5000),
               2.0 / (MU0 * 50000))
    assert cs.bh_mu_r_max(bh) == pytest.approx(want)
    assert cs.bh_mu_r_max(None) == 1.0
    assert cs.bh_mu_r_max([]) == 1.0


def test_reference_frequency_is_slot_passing_or_the_carrier():
    assert cs.rotor_frame_ref_hz(12, 14200.0) == pytest.approx(12 * 14200 / 60)
    assert cs.rotor_frame_ref_hz(12, 14200.0, 24000.0) == pytest.approx(24000.0)
    assert cs.rotor_frame_ref_hz(24, 1000.0, 5.0) == pytest.approx(400.0)


def test_spec_values(monkeypatch):
    for k in ("SB_SKIN_H1_FRAC", "SB_SKIN_GROWTH", "SB_SKIN_CELLS_PER_WL",
              "SB_SKIN_CHORD_MM"):
        monkeypatch.delenv(k, raising=False)
    sp = cs.shaft_skin_spec(4.4e6, 1000.0, 2840.0, 25.6, 12, 5)
    d_mm = cs.skin_depth_m(2840.0, 4.4e6, 1000.0) * 1e3
    assert sp["delta_mm"] == pytest.approx(d_mm)
    assert sp["h1_mm"] == pytest.approx(cs.SKIN_H1_FRAC * d_mm)
    lam = 2 * math.pi * 25.6 / 17
    assert sp["chord_mm"] == pytest.approx(lam / cs.SKIN_CELLS_PER_WAVELENGTH)
    assert sp["h_max_mm"] == pytest.approx(cs.SKIN_HMAX_CHORDS * sp["chord_mm"])
    assert sp["growth"] == cs.SKIN_GROWTH
    # nothing to resolve: no conductivity, no speed, no shaft
    assert cs.shaft_skin_spec(0.0, 1000.0, 2840.0, 25.6, 12, 5) is None
    assert cs.shaft_skin_spec(4.4e6, 1000.0, 0.0, 25.6, 12, 5) is None
    assert cs.shaft_skin_spec(4.4e6, 1000.0, 2840.0, 0.0, 12, 5) is None
    # the first layer never exceeds the chord (low-frequency / non-magnetic)
    sp2 = cs.shaft_skin_spec(3.5e7, 1.0, 10.0, 5.0, 12, 7)
    assert sp2["h1_mm"] <= sp2["chord_mm"] + 1e-12
