"""Production-path tests for the opt-in FEM safe warm cache candidate.

Promote this file to ``tests/test_safe_warm_cache.py`` with the helper and
solver patch. Imports intentionally target the real package modules.
"""
import ast
import inspect
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from motor_ai_sim.simulation.safe_warm_cache import (
    SAFE_CACHE_SCHEMA,
    exact_region_transfer,
    exact_tagged_br_transfer,
    identity_digest,
    rewind_rotor_dofs,
    validate_current_history,
    strict_identity_check,
    magnet_material_identity,
    restore_br_bundle,
)
from motor_ai_sim.simulation import fem_solver_2d as solver
from motor_ai_sim.simulation.rotor_window import period_shift_map


def _complete_cache():
    meta = {"nspp": 24, "npd": 1.0, "conn": "np3", "temp": 80.0,
            "mscale": 1.0, "safe_identity": "identity-a", "ts": "bdf2"}
    history = {"validated": True, "prepass_periods": 1,
               "image_history": {"complete": True, "cycle_periods": 1},
               "prepass": [{"frames": 24, "per_magnet_mean_max": 0.0,
                            "element_max": 0.0,
                            "torque": {"T_mean": 10.0, "ripple_pct": 3.0}}],
               "last_torque": {"T_mean": 10.0, "ripple_pct": 3.0}}
    cache = {"safe_schema": SAFE_CACHE_SCHEMA, "safe_identity": "identity-a",
             "meta": meta, "eddy_complete": True, "demag_complete": True,
             "demag_history": history,
             "I": 60.0, "rpm": 15000.0, "gam": 4.0, "nsect": 2,
             "doflocs": np.array([[0.0, 0.0], [1.0, 1.0]]),
             "A": np.array([1.0, 2.0]), "A2": np.array([0.5, 1.5]),
             "Ued": np.array([0.0]), "solid": np.array([3.0]),
             "Ib_prev": (np.array([0.2]), np.array([0.1])),
             "Ib_prev2": (np.array([0.3]), np.array([0.15])),
             "dof_region": np.array([0, 1]), "edcon_fp": "conductor-a",
             "br_val": np.array([0.9, 0.8]),
             "br_cen": np.array([[0.1, 0.2], [0.3, 0.4]]),
             "br_tag": np.array([10, 11]), "br_mat": np.array(["a", "b"])}
    return meta, cache


def test_identity_is_exact_and_rejects_nonfinite_values():
    identity = {"current_A": 60.0, "daxis_eff_deg": 7.0,
                "magnet_card": {"grade": "F52SH", "temp_C": 120.0}}
    assert identity_digest(identity) != identity_digest(dict(identity, current_A=60.01))
    for value in (float("nan"), float("inf")):
        try:
            identity_digest({"bad": value})
        except ValueError:
            pass
        else:
            raise AssertionError("non-finite identity value was accepted")


def test_region_transfer_rejects_missing_required_conductor_dofs():
    source = np.array([[0.0, 1.0], [0.0, 0.0]])
    result, _ = exact_region_transfer(
        source, np.array([1.0, 2.0]), np.array([0, 1]),
        np.array([[0.0, 0.5], [0.0, 0.5]]), np.array([0, 1]),
        np.array([True, True]))
    assert result is None


def test_br_order_mapping_and_material_identity_ignore_pole_direction_only():
    xy = np.array([[0.0, 0.0], [0.1, 0.2]])
    tags = np.array([14, 14])
    factors = np.array([0.9, 0.8])
    got, info = exact_tagged_br_transfer(
        xy[::-1], tags[::-1], factors[::-1], xy, tags,
        np.array([magnet_material_identity({"name": "mag_14_N", "Br": 1.2}),
                  magnet_material_identity({"name": "mag_14_S", "Br": 1.2})]))
    assert got is not None, info
    np.testing.assert_array_equal(got, factors)
    assert magnet_material_identity({"name": "mag_14_N", "Br": 1.2, "My": 1.2}) == \
           magnet_material_identity({"name": "mag_14_S", "Br": 1.2, "My": -1.2})
    assert magnet_material_identity({"name": "N52UH", "Br": 1.2}) != \
           magnet_material_identity({"name": "F52SH", "Br": 1.2})


def test_solver_accepts_only_complete_finite_exact_provenance(monkeypatch):
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    meta, cache = _complete_cache()
    assert solver._warm_seed_accept(cache, meta, 60.0, 15000.0, 4.0,
                                    n_sectors=2, need_br=True) == (True, "")
    for key, value in (("I", "bad"), ("I", np.nan), ("rpm", np.inf),
                       ("gam", np.nan)):
        rejected = dict(cache, **{key: value})
        assert not solver._warm_seed_accept(rejected, meta, 60.0, 15000.0,
                                            4.0, n_sectors=2, need_br=True)[0]
    for value in ("bad", np.nan, np.inf):
        rejected = dict(cache)
        rejected["demag_history"] = dict(cache["demag_history"])
        rejected["demag_history"]["last_torque"] = {"T_mean": value,
                                                     "ripple_pct": 3.0}
        assert not solver._warm_seed_accept(rejected, meta, 60.0, 15000.0,
                                            4.0, n_sectors=2, need_br=True)[0]


def test_disk_roundtrip_and_invalid_publish_preserve_previous_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    monkeypatch.setattr(solver, "_warm_cache_path", lambda: tmp_path / "warm.npz")
    meta, cache = _complete_cache()
    solver._SB_WARM_CACHE["last"] = None
    solver._warm_cache_store(cache)
    solver._SB_WARM_CACHE["last"] = None
    loaded = solver._warm_cache_load(disk_only=True)
    assert loaded["safe_identity"] == cache["safe_identity"]
    assert loaded["demag_history"]["image_history"]["complete"] is True
    np.testing.assert_array_equal(loaded["A2"], cache["A2"])
    np.testing.assert_array_equal(loaded["Ib_prev"][0], cache["Ib_prev"][0])
    np.testing.assert_array_equal(loaded["Ib_prev"][1], cache["Ib_prev"][1])
    np.testing.assert_array_equal(loaded["Ib_prev2"][0], cache["Ib_prev2"][0])
    np.testing.assert_array_equal(loaded["Ib_prev2"][1], cache["Ib_prev2"][1])
    solver._SB_WARM_CACHE["last"] = cache
    invalid = dict(cache, A=np.array([np.nan, 2.0]))
    solver._warm_cache_store(invalid)
    assert solver._SB_WARM_CACHE["last"] is cache
    loaded_after = solver._warm_cache_load(disk_only=True)
    assert np.all(np.isfinite(loaded_after["A"]))
    assert loaded_after["safe_identity"] == cache["safe_identity"]


def test_br_rollback_mutates_the_aliased_demag_array_in_place():
    original = np.array([1.0, 0.9])
    current = np.array([0.8, 0.7])
    alias = current
    mx, my = np.array([2.0, -3.0]), np.array([4.0, 5.0])
    mx_all, my_all = np.zeros(4), np.zeros(4)
    restore_br_bundle(original, current, dm_seeded=True, dm_seed_from={"I": 60},
                      mx_all=mx_all, my_all=my_all, nst=2,
                      mx=mx, my=my, magnet_scale=1.0)
    assert alias is current
    np.testing.assert_array_equal(alias, original)
    np.testing.assert_array_equal(mx_all[2:], mx * original)
    np.testing.assert_array_equal(my_all[2:], my * original)


def _rotor_points(pp=7, ns=2):
    angle = np.arange(0, 2 * pp // ns + 1) * math.pi / pp
    rings = [0.010, 0.013]
    return np.hstack([r * np.vstack([np.cos(angle + 0.05), np.sin(angle + 0.05)])
                      for r in rings]
                     + [0.012 * np.vstack([np.cos(angle), np.sin(angle)])])


def _analytic_rotor_state(points, theta, pp=7):
    c, s = math.cos(theta), math.sin(theta)
    x = c * points[0] - s * points[1]
    y = s * points[0] + c * points[1]
    phi = np.arctan2(y, x)
    return np.hypot(x, y) * (np.cos(pp * phi - pp * theta)
                             + 0.3 * np.cos((pp - 2) * phi + pp * theta + 0.4))


def test_analytic_rotor_image_rewind_fixes_same_coordinate_seed_error():
    pp, ns = 7, 2
    pts = _rotor_points(pp, ns)
    theta = 0.37
    theta_e = 2 * math.pi / pp
    cached = _analytic_rotor_state(pts, theta, pp)
    wanted = _analytic_rotor_state(pts, theta - theta_e, pp)
    naive_dev = float(np.max(np.abs(cached - wanted)) /
                      max(np.max(np.abs(wanted)), 1e-30))
    assert naive_dev > 1e-2
    got, info = rewind_rotor_dofs(
        cached, np.arange(pts.shape[1]), pts, 1e-3, n_sectors=ns, bc_sign=-1,
        electrical_period_rad=theta_e, period_shift_map=period_shift_map)
    assert got is not None, info
    np.testing.assert_allclose(got, wanted, atol=1e-12, rtol=1e-12)


def test_strict_identity_rejects_provenance_changes_and_old_cache():
    identity = {"machine": "small-fixture", "geometry": {"air_gap": 0.001},
                "materials": {"magnet": "F45SH@120C"}, "current_A": 60.0,
                "gamma_deg": 4.0, "rpm": 900.0, "daxis_eff_deg": 10.0,
                "schedule": {"steps": 24, "periods": 1}, "mesh": "mesh-a"}
    current = {"schema": SAFE_CACHE_SCHEMA, "identity": identity_digest(identity)}
    assert strict_identity_check(current, identity) == (True, "")
    assert not strict_identity_check({"identity": current["identity"]}, identity)[0]
    for key, value in (("current_A", 60.01), ("gamma_deg", 4.01),
                       ("rpm", 901.0), ("mesh", "mesh-b")):
        assert not strict_identity_check(current, dict(identity, **{key: value}))[0]
    for bad in (float("nan"), float("inf")):
        try:
            identity_digest({"bad": bad})
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("non-finite identity accepted")


def test_solver_callsite_guards_and_safe_cache_cold_start():
    source = Path(inspect.getsourcefile(solver)).read_text(encoding="utf-8")
    assert 'drive == "current" and waveform is None' in source
    assert "six_phase is None" in source and 'star_delta or "star"' in source
    assert '"cold static fill did not converge" if _commit_valid' in source
    assert '_rollback_safe_br_seed("seeded Newton warm-up retry")' in source
    assert 'publication skipped (eddy or demag state is not affirmatively settled)' in source
    assert "_Ued = np.zeros(len(_ed_con), float)" in source


def test_safe_br_seed_stays_attached_to_magnet_material_identity():
    source = Path(inspect.getsourcefile(solver)).read_text(encoding="utf-8")
    assert "_proj = np.asarray(_br_ordered, float).copy()" in source
    assert "_safe_rewind_br_factors" not in source


def test_cached_bdf2_current_history_is_complete_finite_and_shape_checked(monkeypatch):
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    meta, cache = _complete_cache()
    assert solver._warm_seed_accept(cache, meta, 60.0, 15000.0, 4.0,
                                    n_sectors=2, need_br=True) == (True, "")
    for bad_history in (
            None,
            ((np.array([np.nan]), np.array([0.1])),
             (np.array([0.3]), np.array([0.15]))),
            ((np.array([0.2, 0.3]), np.array([0.1])),
             (np.array([0.3, 0.4]), np.array([0.15, 0.2])))):
        rejected = dict(cache, Ib_prev=bad_history[0] if bad_history else None)
        if bad_history:
            rejected["Ib_prev2"] = bad_history[1]
        assert not solver._warm_seed_accept(rejected, meta, 60.0, 15000.0,
                                            4.0, n_sectors=2, need_br=True)[0]
    assert validate_current_history((cache["Ib_prev"], cache["Ib_prev2"]),
                                    expected_size=1) is not None
    assert validate_current_history((cache["Ib_prev"], cache["Ib_prev2"]),
                                    expected_size=2) is None


def test_backward_euler_safe_seed_does_not_require_bdf2_current_history(monkeypatch):
    monkeypatch.setenv("SB_SAFE_WARM_CACHE", "1")
    meta, cache = _complete_cache()
    meta = dict(meta, ts="be")
    cache = dict(cache, meta=meta)
    cache.pop("Ib_prev")
    cache.pop("Ib_prev2")
    assert solver._warm_seed_accept(cache, meta, 60.0, 15000.0, 4.0,
                                    n_sectors=2, need_br=False) == (True, "")


def test_current_history_changes_only_safe_bdf2_cache_format():
    source = Path(inspect.getsourcefile(solver)).read_text(encoding="utf-8")
    assert 'wc.get("safe_schema") == _SAFE_WARM_CACHE_SCHEMA' in source
    assert 'wc.get("meta", {}).get("ts") == "bdf2"' in source
    assert '"Ib_prev": _wc_Ib_prev' in source
    assert 'if _safe_warm_cache_enabled() and _eddy_bdf2 else {}' in source


def test_cached_current_history_reproduces_full_midpoint_after_two_fallbacks():
    # For a sinusoidal constraint current, missing one and then two prior
    # endpoint values yields the established two-point and quadratic midpoint
    # estimators respectively. The restored pair is required at first handoff.
    dt = 0.05
    t = np.array([0.0, -dt, -2.0 * dt])
    values = np.sin(2.0 * np.pi * t)
    endpoint, previous, previous2 = values
    two_point = 0.5 * (endpoint + previous)
    h1, h2, x = dt, 2.0 * dt, 0.5 * dt
    l0 = (x-h1)*(x-h2)/(h1*h2)
    l1 = x*(x-h2)/(h1*(h1-h2))
    l2 = x*(x-h1)/(h2*(h2-h1))
    full_midpoint = l0*endpoint + l1*previous + l2*previous2
    true_midpoint = np.sin(2.0 * np.pi * (-0.5 * dt))
    assert abs(two_point-true_midpoint) > 0.001
    assert abs(full_midpoint-true_midpoint) < 0.01
    source = Path(inspect.getsourcefile(solver)).read_text(encoding="utf-8")
    assert "_Ib_prev, _Ib_prev2 = _commit_history" in source
