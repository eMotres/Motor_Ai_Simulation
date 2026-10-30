"""Apply of a Sweep point after a page reload (owner 2026-10-30:
"after the reload the Apply button disappeared, and where it is there it does
not work").

Two causes, both fixed:

* the panel kept Apply disabled while a sweep ran, and the server answered an
  Apply only from the FINISHED result - ``_scan_state["result"]`` is empty for
  the whole run, the computed points live in ``_scan_state["points"]``.  A
  reload in the middle of a long sweep therefore had a chart full of points and
  nothing to do with them;
* a refused Apply said only "Machine/configuration changed" - whether another
  motor was loaded or the same motor edited was not told.
"""
from pathlib import Path

import pytest
from fastapi import HTTPException

from motor_ai_sim.routes import optimization as O

PANEL = Path(__file__).resolve().parents[1] / "web" / "src" / "components" / "sweep" / "SweepStudyPanel.tsx"

MACHINE = {"fingerprint": "machinefp", "swept": ["tooth_width"],
           "die": "CIANO56 400_80 vadim 6", "config": "L90-2x3ph-Al2O3-15t", "duty": "S1"}


def _points():
    return [
        {"geom_id": 0, "op_index": 0, "feasible": True, "overrides": {"tooth_width": 3.1},
         "current_a": 50.0, "gamma_deg": 0.0, "source_cfg_fp": "pointfp", "T_em_Nm": 1.5},
        {"geom_id": 1, "op_index": 0, "feasible": True, "overrides": {"tooth_width": 3.4},
         "current_a": 50.0, "gamma_deg": 0.0, "source_cfg_fp": "pointfp", "T_em_Nm": 1.7},
    ]


def _running_state(**kw):
    st = {"running": True, "run_id": "sweep_32_1793348291936", "result": None,
          "points": _points(), "machine": dict(MACHINE)}
    st.update(kw)
    return st


@pytest.fixture
def server(monkeypatch):
    fp = {"point": "pointfp", "machine": "machinefp"}
    now = {"die": MACHINE["die"], "config": MACHINE["config"], "duty": "S1"}
    monkeypatch.setattr(O, "_config_fingerprint",
                        lambda exclude=(): fp["point"] if exclude else "fullfp")
    monkeypatch.setattr(O, "_machine_stamp",
                        lambda exclude=(): {"fingerprint": fp["machine"], **now})

    def _install(state):
        monkeypatch.setattr(O, "_scan_state", state)
    return _install, fp, now


def _req(run="sweep_32_1793348291936", geom=1, op=0):
    return O.ScanValidateRequest(run_id=run, geom_id=geom, op_index=op)


def test_a_point_of_the_running_sweep_can_be_applied(server):
    install, _, _ = server
    install(_running_state())
    got = O.scan_apply_point(_req())
    assert got["point"]["overrides"] == {"tooth_width": 3.4}
    assert got["point"]["T_em_Nm"] == 1.7
    assert got["provenance"] == "pinned"


def test_another_run_is_still_refused_while_one_runs(server):
    install, _, _ = server
    install(_running_state())
    with pytest.raises(HTTPException) as e:
        O.scan_apply_point(_req(run="sweep_8_1"))
    assert e.value.status_code == 409 and "run changed" in e.value.detail


def test_a_point_not_yet_computed_is_refused(server):
    install, _, _ = server
    install(_running_state())
    with pytest.raises(HTTPException) as e:
        O.scan_apply_point(_req(geom=7))
    assert e.value.status_code == 422


def test_a_running_sweep_without_its_machine_stamp_is_refused(server):
    install, _, _ = server
    install(_running_state(machine=None))
    with pytest.raises(HTTPException) as e:
        O.scan_apply_point(_req())
    assert e.value.status_code == 422 and "machine stamp" in e.value.detail


def test_a_restored_finished_sweep_applies_on_the_same_machine(server):
    install, _, _ = server
    result = {"run_id": "sweep_32_1793348291936", "restored_from_disk": True,
              "machine": dict(MACHINE), "points": _points(),
              "scan_params": {"validation_provenance_version": 1},
              "variables": [{"name": "tooth_width"}]}
    install({"running": False, "run_id": "sweep_32_1793348291936", "result": result, "points": []})
    assert O.scan_apply_point(_req())["point"]["overrides"] == {"tooth_width": 3.4}


def test_the_finished_result_wins_once_the_run_is_over(server):
    install, _, _ = server
    result = {"run_id": "sweep_32_1793348291936", "machine": dict(MACHINE),
              "points": [dict(_points()[1], T_em_Nm=9.9)],
              "scan_params": {"validation_provenance_version": 1}, "variables": []}
    install({"running": False, "run_id": "sweep_32_1793348291936", "result": result,
             "points": _points()})
    assert O.scan_apply_point(_req())["point"]["T_em_Nm"] == 9.9


def test_another_motor_loaded_says_which_two_motors(server):
    install, fp, now = server
    install(_running_state())
    now.update(die="G2", config="L40")
    fp["machine"] = "otherfp"
    with pytest.raises(HTTPException) as e:
        O.scan_apply_point(_req())
    assert e.value.status_code == 409
    d = e.value.detail
    assert "CIANO56 400_80 vadim 6 / L90-2x3ph-Al2O3-15t / S1" in d and "G2 / L40" in d
    assert "Load CIANO56" in d


def test_the_same_motor_edited_says_it_was_edited(server):
    install, fp, _ = server
    install(_running_state())
    fp["machine"] = "editedfp"
    with pytest.raises(HTTPException) as e:
        O.scan_apply_point(_req())
    assert e.value.status_code == 409
    assert "was edited after this sweep ran" in e.value.detail
    assert "CIANO56 400_80 vadim 6" in e.value.detail


def test_a_point_solved_on_other_parameters_says_it_was_edited(server):
    install, fp, _ = server
    install(_running_state())
    fp["point"] = "changedfp"
    with pytest.raises(HTTPException) as e:
        O.scan_apply_point(_req())
    assert e.value.status_code == 409 and "was edited" in e.value.detail


def test_the_panel_does_not_switch_apply_off_while_a_sweep_runs():
    src = PANEL.read_text(encoding="utf-8")
    i = src.index("⤵ Apply")
    button = src[src.rindex("<Button", 0, i):i]
    assert "disabled={applying}" in button and "running" not in button
