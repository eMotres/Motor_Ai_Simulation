"""A Sweep / Descent candidate carries the SWEPT keys only; every other key of the loaded configuration - the opt-in
ones included (magnet_flat, magnet_pocket_clearance, wire_omit, wire_bore_margin, k_end_by_turns) - is inherited
(BUG_sweep_magnet_flat.md: a flat-magnet die was solved with arc-topped magnets).  A whole-lamination override that is
silent on them is another machine and still does not inherit them."""
import pytest

from motor_ai_sim import config as C
from motor_ai_sim.cadquery_geometry import CadQueryMotor
from motor_ai_sim.geometry.motor_geometry import MotorGeometryParams

from test_magnet_flat import FLAT, G, _magnet0


@pytest.fixture
def flat_config(monkeypatch):
    """The loaded configuration is the 12 mm die with magnet_flat 1 (the config the candidates are cut from)."""
    geo = {**G, **FLAT, "wire_omit": "0"}
    monkeypatch.setattr(C, "get_geometry_params", lambda *a, **k: MotorGeometryParams(dict(geo)))
    return geo


def _magnet(params):
    m = CadQueryMotor()
    m.set_parameters(params)
    return m, _magnet0(m.get_2d_polygons(rotor_angle_deg=0.0))


def test_a_grid_point_with_the_swept_key_only_is_the_full_solve(flat_config):
    _, full = _magnet(dict(flat_config))
    m, cand = _magnet({"tooth_width": flat_config["tooth_width"]})
    assert m.parameters.get("magnet_flat") == 1 and m.parameters.get("magnet_pocket_clearance") == 0.025
    assert cand.area == pytest.approx(full.area, rel=1e-12)
    assert cand.hausdorff_distance(full) < 1e-9


def test_the_candidate_is_not_the_arc_topped_magnet(flat_config):
    arc_cfg = {k: v for k, v in flat_config.items() if k not in FLAT}
    _, arc = _magnet(arc_cfg)
    _, cand = _magnet({"tooth_width": 1.0})
    assert abs(cand.area - arc.area) / arc.area > 0.01


@pytest.mark.parametrize("key", ["magnet_flat", "magnet_pocket_clearance", "wire_omit", "wire_bore_margin"])
def test_every_opt_in_key_of_the_configuration_survives_a_partial_override(flat_config, key):
    m = CadQueryMotor()
    m.set_parameters({"wire_height": flat_config["wire_height"]})
    assert m.parameters.get(key) == flat_config[key]


def test_a_whole_lamination_override_silent_on_the_opt_in_keys_does_not_inherit_them(flat_config):
    other = {k: v for k, v in flat_config.items() if k not in (*FLAT, "wire_omit", "wire_bore_margin")}
    m = CadQueryMotor()
    m.set_parameters(other)
    for k in ("magnet_flat", "magnet_pocket_clearance", "wire_omit", "wire_bore_margin"):
        assert k not in m.parameters


def test_a_candidate_that_states_an_opt_in_key_wins_over_the_configuration(flat_config):
    m = CadQueryMotor()
    m.set_parameters({"magnet_flat": 0})
    assert not m.parameters.get("magnet_flat")
