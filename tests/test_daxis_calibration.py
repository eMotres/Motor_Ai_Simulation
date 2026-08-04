"""The d-axis calibration: the same machine must calibrate to the same number.

DAXIS is the electrical angle that puts gamma=0 on the true q-axis, so every
torque, ripple and cogging number downstream is quoted at the operating point it
picks. It is MEASURED, per machine, by a no-load solve — which makes its
repeatability a physics result, not a style question. Measured sensitivity, on
the 30 mm regression machine: +-0.3 deg of DAXIS moves the pinned mean torque by
~2 %, the ripple by ~16 % and the cogging by ~50 %.

It used to wander. ``config/.daxis_cache.json`` still carries the evidence: the
12s14p entries scatter over 59.87...61.32 deg where the topology's exact answer
is 60.000 (pole pitch + slot pitch, see fem_solver_2d.DAXIS_SHIFT_DEG). Two
causes, both fixed before this file existed — the calibration accepted the
answer of a solve that had not converged, and it cached that answer under a key
that named only the topology, so one cross-section's angle was served to a
different machine.

Re-measured 2026-08-04, after the static Picard convergence fix (b15448a), five
COLD runs per machine in five fresh interpreters with the disk cache disabled:

    30 mm regression machine   DAXIS = 60.01988468898403   x5, bit-identical
    40 mm (preset my_40mm_last) DAXIS = 59.99836646437093  x5, bit-identical

Spread: 0.0 deg on both, down from ~0.5 deg. So this file pins the property
rather than chasing it: the estimator is exact and pure, the convergence gate
refuses an unconverged solve outright, and (slow) two cold calibrations of one
machine agree to well inside the tolerance the pins can feel.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

import motor_ai_sim.simulation.fem_solver_2d as F

# Tolerance on a repeated calibration, in ELECTRICAL degrees. 0.01 deg is two
# orders below the +-0.3 deg that moves the pinned numbers, and the measurement
# above says the honest spread is zero, so a failure here is a real regression
# and not a re-tuning invitation.
SPREAD_TOL_DEG = 0.01

# The 30 mm 12s14p spoke machine the physics regression suite is pinned on.
GEO_30MM = {
    "stator_diameter": 30.0, "slot_height": 4.3, "core_thickness": 1.5,
    "num_seg": 2, "num_slots_per_segment": 6, "num_poles_per_segment": 7,
    "air_gap": 0.2, "tooth_width": 2.6, "tooth2_width": 1.4, "cut_width": 1.5,
    "insulation_thickness": 0.05, "wire_width": 2.0, "wire_height": 0.5,
    "wire_spacing_x": 0.1, "wire_spacing_y": 0.1, "num_wires_per_slot": 6,
    "wire_split": 1, "slot_hs": 0.267, "magnet_height": 4.5,
    "rotor_house_height": 0.8, "shaft_height": 2.0, "magnet_fill_down": 0.9,
    "magnet_fill_up": 0.3, "magnet_fill_radius": 0.1, "magnet_up_gap": 0.1,
    "rotor_hole": 0.7, "magnet_down_height": 1.4, "magnet_lamination": 0,
    "stator_fillet_r": 1.2, "stator_fillet_r1": 0.0, "rotor_fill_r": 0.2,
    "motor_length": 10.0,
}


# --------------------------------------------------------------------------
# helpers: drive _resolve_daxis_shift with a SYNTHETIC no-load run, so the
# estimator and the gate can be checked without paying for a FEM solve.
# --------------------------------------------------------------------------

def _args(poles: int = 14, slots: int = 12):
    p = SimpleNamespace(num_poles=poles)
    geo = {"num_slots": slots, "stator_diameter": 30.0}
    wind = {"layers": 1, "connection": "2S"}
    return p, geo, wind, poles // 2


def _series(poles: int = 14, n: int = 24):
    """The angle grid the calibration actually runs on: one electrical period
    of mechanical rotation, n frames, endpoint excluded."""
    return np.linspace(0.0, 360.0 / (poles // 2), n, endpoint=False)


def _fake_run(monkeypatch, *, psi, ang, converged=True, unconv=(), tol=1e-3,
              resid=1e-8, fallback=()):
    calls = []

    def _fake(**kw):
        calls.append(kw)
        return {"psi_A_Wb": list(psi), "rotor_angle_deg": list(ang),
                "picard_converged": bool(converged),
                "picard_unconverged_frames": list(unconv),
                "picard_fallback_frames": list(fallback),
                "picard_tol": float(tol), "picard_resid_max": float(resid)}

    monkeypatch.setattr(F, "em_transient_eval", _fake)
    monkeypatch.setattr(F, "_daxis_disk_path", lambda: None)
    F._DAXIS_CACHE.clear()
    return calls


@pytest.fixture(autouse=True)
def _clean_cache():
    """Never let one case's answer be served to another, and never leave a
    synthetic angle behind in the process-global cache."""
    F._DAXIS_CACHE.clear()
    yield
    F._DAXIS_CACHE.clear()


# --------------------------------------------------------------------------
# the estimator
# --------------------------------------------------------------------------

class TestPeakEstimator:
    """theta* is a parabolic vertex through three samples; it must be exact,
    and it must be a pure function of the series."""

    def test_a_symmetric_peak_lands_on_the_sample(self, monkeypatch):
        """Neighbours equal -> the vertex IS the sample, frac = 0 exactly.

        k0 = 5 on a 24-frame grid over 360/7 deg mech: theta* = 5 * 2.142857 =
        10.714286 deg mech, x7 = 75.0 deg elec, so DAXIS = 90 - 75 = 15.0.
        """
        ang = _series()
        psi = -np.abs(np.arange(24) - 5.0)
        _fake_run(monkeypatch, psi=psi, ang=ang)
        got = F._resolve_daxis_shift(*_args(), None, 2)
        assert got == pytest.approx(15.0, abs=1e-12)

    def test_an_asymmetric_peak_interpolates_the_vertex(self, monkeypatch):
        """ym1, y0, yp1 = 0.8, 1.0, 0.9 -> frac = 0.5*(0.8-0.9)/(-0.3) = 1/6.

        theta* = (5 + 1/6) * 2.142857 = 11.0714286 deg mech = 77.5 deg elec,
        DAXIS = 12.5. Hand-computed; the point is that the sub-step is a
        CLOSED FORM, not an iterate whose stopping point can drift.
        """
        ang = _series()
        psi = np.full(24, -10.0)
        psi[4], psi[5], psi[6] = 0.8, 1.0, 0.9
        _fake_run(monkeypatch, psi=psi, ang=ang)
        got = F._resolve_daxis_shift(*_args(), None, 2)
        assert got == pytest.approx(12.5, abs=1e-12)

    def test_the_same_series_gives_the_same_answer_bit_for_bit(self, monkeypatch):
        ang = _series()
        psi = np.cos(np.radians(np.arange(24) * 15.0 - 37.0))
        out = []
        for _ in range(5):
            _fake_run(monkeypatch, psi=psi, ang=ang)
            out.append(F._resolve_daxis_shift(*_args(), None, 2))
        assert len(set(out)) == 1, out

    def test_the_peak_wraps_around_the_grid(self, monkeypatch):
        """A machine whose psi_A peaks in the first frame must not read the
        vertex off a one-sided difference: the series is PERIODIC, so index
        -1 is the last frame."""
        ang = _series()
        psi = np.full(24, -10.0)
        psi[23], psi[0], psi[1] = 0.9, 1.0, 0.9
        _fake_run(monkeypatch, psi=psi, ang=ang)
        # symmetric about frame 0 -> theta* = 0 -> DAXIS = 90
        assert F._resolve_daxis_shift(*_args(), None, 2) == pytest.approx(90.0)


# --------------------------------------------------------------------------
# the convergence gate
# --------------------------------------------------------------------------

class TestConvergenceGate:
    """theta* read off an unconverged field is not theta*. The calibration must
    RAISE rather than hand back a number, because the only alternative on offer
    is the legacy 108 deg constant, which is ~48 deg wrong for this topology."""

    def test_an_unconverged_solve_is_refused(self, monkeypatch):
        ang = _series()
        psi = np.cos(np.radians(np.arange(24) * 15.0))
        _fake_run(monkeypatch, psi=psi, ang=ang, converged=False,
                  unconv=[3, 7], tol=1e-3, resid=2.8e-1)
        with pytest.raises(RuntimeError) as e:
            F._resolve_daxis_shift(*_args(), None, 2)
        msg = str(e.value)
        assert "did NOT converge" in msg
        assert "0.28" in msg and "0.001" in msg, msg     # residual AND tolerance
        assert "[3, 7]" in msg, msg                      # ...and WHICH frames
        assert not F._DAXIS_CACHE, "a refused calibration was cached anyway"

    def test_a_missing_convergence_flag_is_failure_not_consent(self, monkeypatch):
        ang = _series()
        psi = np.cos(np.radians(np.arange(24) * 15.0))

        def _fake(**kw):
            return {"psi_A_Wb": list(psi), "rotor_angle_deg": list(ang)}

        monkeypatch.setattr(F, "em_transient_eval", _fake)
        monkeypatch.setattr(F, "_daxis_disk_path", lambda: None)
        with pytest.raises(RuntimeError):
            F._resolve_daxis_shift(*_args(), None, 2)

    def test_a_run_with_no_usable_psi_is_refused(self, monkeypatch):
        _fake_run(monkeypatch, psi=[], ang=[])
        with pytest.raises(RuntimeError) as e:
            F._resolve_daxis_shift(*_args(), None, 2)
        assert "no usable" in str(e.value)

    def test_the_legacy_constant_is_never_returned_as_a_fallback(self, monkeypatch):
        """108 deg is the 20p/24s value (and that machine calibrates to 120).
        Silently answering with it runs the whole simulation at a load angle the
        user never asked for."""
        _fake_run(monkeypatch, psi=[], ang=[])
        with pytest.raises(RuntimeError):
            got = F._resolve_daxis_shift(*_args(), None, 2)
            assert got != F.DAXIS_SHIFT_DEG

    def test_the_solve_runs_at_the_calibrations_OWN_fixed_mesh(self, monkeypatch):
        """The cache key names the MACHINE, not the mesh, so the calibration
        must not inherit the caller's mesh settings — a value measured under one
        caller's mesh would otherwise be handed to another's."""
        ang = _series()
        psi = -np.abs(np.arange(24) - 5.0)
        calls = _fake_run(monkeypatch, psi=psi, ang=ang)
        F._resolve_daxis_shift(*_args(), None, 2)
        kw = calls[0]
        assert kw["I_phase_rms"] == 0.0 and kw["gamma_deg"] == 0.0
        assert kw["element_order"] == 2
        assert kw["structured_gap"] and kw["iron_template"] and kw["geo_mesh"]
        assert kw["gap_layers"] == 2.0
        # 1.4 * D / 150, clamped to [0.5, 2.0], on a 30 mm machine
        assert kw["mesh_size_mm"] == pytest.approx(0.5)


# --------------------------------------------------------------------------
# the property the whole file is about
# --------------------------------------------------------------------------

@pytest.mark.slow
def test_repeated_cold_calibration_of_one_machine_lands_on_one_number(monkeypatch):
    """TWO cold calibrations of the SAME machine, two real no-load FEM solves.

    This is the measurement, not a proxy for it: the in-memory cache is cleared
    between them and the disk cache is switched off, so each run pays for its
    own CAD, its own mesh and its own 24 frames. Measured 2026-08-04 over five
    fresh interpreters per machine: bit-identical, spread 0.0 deg.

    The calibration's OWN convergence is asserted too, numerically. The solver
    already refuses a run whose ``picard_converged`` is False, but a boolean is
    a promise; the residual is the evidence, and a theta* measured at a residual
    above tolerance is exactly the noisy answer this file exists to forbid.
    """
    from motor_ai_sim.material_context import set_request_materials
    from motor_ai_sim.simulation.geometry_2d import merge_geo_override
    from motor_ai_sim.config import get_config
    from motor_ai_sim.winding import parse_connection

    real = F.em_transient_eval
    seen = []

    def _watched(**kw):
        out = real(**kw)
        seen.append((float(out.get("picard_resid_max") or 0.0),
                     float(out.get("picard_tol") or 0.0),
                     bool(out.get("picard_converged"))))
        return out

    monkeypatch.setattr(F, "em_transient_eval", _watched)
    monkeypatch.setattr(F, "_daxis_disk_path", lambda: None)
    set_request_materials({"assignment": {"magnet": "F45SH_120C"}, "materials": {}})
    try:
        cfg = get_config()
        geo = merge_geo_override(dict(cfg.get("geometry", {})), dict(GEO_30MM))
        p = F._params_from_geo_dict(geo)
        wind = dict(cfg.get("winding", {}) or {})
        npar, nser = parse_connection("2S")
        wind.update(connection="2S", n_series=nser, n_parallel=npar)
        pp = p.num_poles // 2

        got = []
        for _ in range(2):
            F._DAXIS_CACHE.clear()
            got.append(F._resolve_daxis_shift(p, geo, wind, pp,
                                              dict(GEO_30MM), 2))
    finally:
        set_request_materials(None)

    assert len(seen) == 2, "the cache short-circuited one of the two runs"
    for resid, tol, conv in seen:
        assert conv, "the calibration solve did not converge"
        assert resid <= tol, (
            f"calibration residual {resid:.3g} above tolerance {tol:.1g} — "
            f"theta* read off that field is not theta*")

    spread = abs(got[0] - got[1])
    assert spread <= SPREAD_TOL_DEG, (
        f"the same machine calibrated to {got[0]:.6f} and {got[1]:.6f} deg "
        f"(spread {spread:.4f} deg): +-0.3 deg moves the pinned torque ~2 %, "
        f"ripple ~16 %, cogging ~50 %")
    # ...and this topology's exact answer is pole pitch + slot pitch = 60 deg.
    assert got[0] == pytest.approx(60.0, abs=0.1), got
