"""The Thermal tab must find a GENERATOR run of the Electromagnetic tab.

Generator mode drives the panel's load angle shifted by 180° el, and the
transient folds that shift in before it builds its keys — so a generator run at
panel γ = −15° is stored under 165°.  The thermal probe used to key on the panel
value and never matched: "no Electromagnetic run of this machine at … γ = −15°"
on every generator point (user, 2026-09-08 22:0x, after a coupled Run).  These
tests pin the key arithmetic without solving anything.
"""
from __future__ import annotations

import pytest

from motor_ai_sim.routes import thermal as th
from motor_ai_sim.routes.simulation import (_config_physics_fingerprint,
                                            _field_snap_key_fields,
                                            _get_request_materials_safe,
                                            _parse_component_mesh)


def _run_key(gamma_solved: float, **over):
    """A stored run's key fields, exactly as the transient writes them."""
    base = dict(
        gamma_deg=gamma_solved, I_phase_rms=687.31,
        mesh_size_mm=4.0, min_size_mm=0.3, outer_air_factor=1.3, n_sectors=2,
        stator_fillet_mm=0.0, gap_layers=1.0, coil_temp_c=106.8,
        comp_mesh=_parse_component_mesh(""),
        pole_copy=False, iron_template=True, geo_mesh=True,
        structured_gap=True, airgap_macro=False,
        n_steps_per_period=36, n_periods=1.0,
        eddy=True, rotor_eddy=True, demag=True,
        drive="current", element_order=2,
        cfg_fingerprint=_config_physics_fingerprint(with_request_materials=False),
        geo_ov=None, mat_ov=_get_request_materials_safe(),
        magnet_temp_c=150.0, rotor_angle0_deg=0.0)
    base.update(over)
    return _field_snap_key_fields(**base)


def _probe(gamma_panel: float, op_mode):
    return th._loss_snapshot_probe(
        gamma_deg=gamma_panel, I_phase_rms=687.31, mesh_size_mm=4.0,
        min_size_mm=0.3, outer_air_factor=1.3, n_sectors=2, coil_temp_c=106.8,
        component_mesh="", n_steps_per_period=36, n_periods=1.0, geo_ov=None,
        magnet_temp_c=150.0, op_mode=op_mode)


def test_a_generator_run_is_found_under_the_panel_angle():
    run = th._physics_identity(_run_key(165.0))          # solved at −15° + 180°
    assert th._physics_identity(_probe(-15.0, "generator")) == run
    # …and NOT by a motor-mode probe at the same panel angle: the two are
    # different operating points and must never share a loss map.
    assert th._physics_identity(_probe(-15.0, "motor")) != run


def test_a_motor_run_keys_on_the_panel_angle_unchanged():
    run = th._physics_identity(_run_key(16.0))
    assert th._physics_identity(_probe(16.0, "motor")) == run
    assert th._physics_identity(_probe(16.0, None)) == run or \
        th._effective_op_mode(None) == "generator"


@pytest.mark.parametrize("raw,expect", [
    ("generator", "generator"), ("GENERATOR ", "generator"), ("motor", "motor"),
    ("", "motor"), ("nonsense", "motor")])
def test_the_mode_word_is_normalised_like_the_transient_does(raw, expect):
    assert th._effective_op_mode(raw) == expect


def test_the_solved_angle_shifts_only_in_generator_mode():
    assert th._solved_gamma_deg(-15.0, "generator") == pytest.approx(165.0)
    assert th._solved_gamma_deg(-15.0, "motor") == pytest.approx(-15.0)
