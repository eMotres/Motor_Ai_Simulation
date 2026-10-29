"""Six-phase current per set (owner 2026-10-29, option 1).

A ``winding.phases: 6`` machine is two in-phase 3-phase sets, each on its own
controller.  The stored duty and the solver keep ONE convention, unchanged:
the equivalent 3-phase current, both sets together.  What changes is what the
engineer reads and enters: the PER-SET current, with "per set" / "both sets"
said explicitly.  Pinned here for the datasheet and the catalogue tree; the web
input is pinned by web/src/lib/__tests__/perSetCurrent.test.mjs.

A 3-phase winding's parallel paths are NOT sets: a 4S-2P three-phase machine
reads exactly as before.
"""
from __future__ import annotations

import io

import pytest

openpyxl = pytest.importorskip("openpyxl")

from motor_ai_sim.datasheet import build_datasheet  # noqa: E402

DIE_DOC = {"geometry": {"stator_diameter": 150, "motor_length": 35,
                        "num_slots": 48, "num_poles": 56,
                        "rotor_outer_radius": 56.3, "air_gap": 0.5}}
DUTY = {"name": "cruise", "mode": "motor", "rpm": 850, "torque_nm": 1030.0,
        "current_arms": 199.6,
        "result": {"efficiency_pct": 95.0, "loss_w": 3000.0},
        "summary": {"P_mech_W": 91700.0, "T_ripple_pct": 3.3,
                    "ripple_mesh_limited": True, "ripple_self_check_rel": 0.52,
                    "gap_layers_effective": 1.0}}


def _rows(cfg_doc) -> dict:
    wb = openpyxl.load_workbook(io.BytesIO(build_datasheet(
        die="D", cfg="C", die_doc=DIE_DOC, cfg_doc=cfg_doc)))
    ws = wb["Motor card"]
    return {str(r[0].value): r[1].value for r in ws.iter_rows() if r[0].value}


def test_two_sets_read_per_set_and_both_sets():
    rows = _rows({"duties": [dict(DUTY)],
                  "winding": {"connection": "4S-2P", "n_parallel": 2, "phases": 6}})
    assert rows["Phase current per set (A rms)"] == pytest.approx(99.8, abs=0.05)
    assert rows["Phase current per set (A peak)"] == pytest.approx(99.8 * 2 ** 0.5, abs=0.1)
    assert rows["Phase current, both sets (A rms)"] == pytest.approx(199.6, abs=0.05)
    assert "Phase current (A rms)" not in rows


def test_three_phase_parallel_paths_are_not_sets():
    rows = _rows({"duties": [dict(DUTY)],
                  "winding": {"connection": "4S-2P", "n_parallel": 2, "phases": 3}})
    assert rows["Phase current (A rms)"] == pytest.approx(199.6, abs=0.05)
    assert not any("per set" in k or "both sets" in k for k in rows)


def test_datasheet_ripple_is_marked_or_refined():
    rows = _rows({"duties": [dict(DUTY)]})
    assert rows["Torque ripple (%)"] == "3.3 (not accurate)"
    refined = dict(DUTY, summary=dict(DUTY["summary"], ripple_refined={
        "T_ripple_pct": 1.9, "passed": True, "gap_layers_per_side": 3.0}))
    rows = _rows({"duties": [refined]})
    assert rows["Torque ripple (%)"] == pytest.approx(1.9)
