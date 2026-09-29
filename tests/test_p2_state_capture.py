from contextvars import Context
from pathlib import Path

import numpy as np
import pytest

from motor_ai_sim.simulation.p2_state_capture import (
    capture_p2_states,
    current_p2_state_capture,
    emit_selected_p2_state,
)


def test_no_capture_context_and_unselected_frame_do_not_build_snapshot():
    assert current_p2_state_capture() is None
    built = []
    received = []
    with capture_p2_states(lambda meta: False, received.append):
        target = current_p2_state_capture()
        assert target is not None
        assert not emit_selected_p2_state(
            target, {"frame_index": 4}, lambda: built.append(True) or {})
    assert built == []
    assert received == []


def test_capture_context_is_context_local_and_restored():
    consumer = lambda snapshot: None
    with capture_p2_states(lambda meta: True, consumer):
        outer = current_p2_state_capture()
        assert outer is not None
        with capture_p2_states(lambda meta: False, consumer):
            assert current_p2_state_capture() is not outer
        assert current_p2_state_capture() is outer
        assert Context().run(current_p2_state_capture) is None
    assert current_p2_state_capture() is None


def test_consumer_exception_propagates_and_context_is_restored():
    def fail(snapshot):
        raise RuntimeError("diagnostic sink failed")

    with pytest.raises(RuntimeError, match="diagnostic sink failed"):
        with capture_p2_states(lambda meta: True, fail):
            target = current_p2_state_capture()
            emit_selected_p2_state(target, {"frame_index": 1}, lambda: {})
    assert current_p2_state_capture() is None


def test_selected_snapshot_arrays_are_independent_read_only_copies():
    source = np.array([1.0, 2.0])
    received = []
    with capture_p2_states(lambda meta: meta["frame_index"] == 7,
                           received.append):
        target = current_p2_state_capture()
        assert emit_selected_p2_state(
            target, {"frame_index": 7},
            lambda: {"A_z": source, "nested": {"curve": source}})

    source[:] = 9.0
    saved = received[0]["state"]
    np.testing.assert_array_equal(saved["A_z"], [1.0, 2.0])
    np.testing.assert_array_equal(saved["nested"]["curve"], [1.0, 2.0])
    with pytest.raises(ValueError):
        saved["A_z"][0] = 3.0


def test_solver_wiring_captures_after_linkage_and_currents_before_later_work():
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/motor_ai_sim/simulation/fem_solver_2d.py").read_text(
        encoding="utf-8")
    psi = source.index("_pa, _pb, _pc = _psi2(A2)")
    currents = source.index("_IA.append(Ist['A']); _IB.append(Ist['B']); _IC.append(Ist['C'])", psi)
    hook = source.index("_p2_capture = _current_p2_state_capture()", currents)
    later = source.index("# ── INCREMENTAL d-q INDUCTANCES", hook)
    assert psi < currents < hook < later
    assert "if _p2_capture is not None:" in source[hook:later]
    for field in ("A_z", "nu_base2", "saturable_materials",
                  "magnet_source_vector", "coil_source_vectors_per_A",
                  "Bx_quad_T", "By_quad_T", "mechanical_angle_rad",
                  "picard_unconverged"):
        assert field in source
