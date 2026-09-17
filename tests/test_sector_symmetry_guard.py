"""Sector admissibility is checked before any calibration or mesh solve."""
import copy
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from motor_ai_sim import config
from motor_ai_sim.simulation import fem_solver_2d as fem
from motor_ai_sim.simulation.geometry_2d import (
    build_winding_layout, parse_winding_layout, validate_sector_symmetry)


# Valid phase tokens, opposite paired coil sides and equal phase counts, but
# the first and third coils are swapped in only one half of the full machine.
ASYMMETRIC = "B|b|c|C|A|a|a|A|C|c|b|B|A|a|c|C|B|b|a|A|C|c|b|B"


@pytest.mark.parametrize("slots,poles,sectors", [
    (12, 14, 1), (12, 14, 2), (24, 28, 1), (24, 28, 2), (24, 28, 4)])
@pytest.mark.parametrize("single_layer", [True, False])
def test_standard_resolved_phase_bases_are_admissible(slots, poles, sectors, single_layer):
    layout = build_winding_layout(slots, poles//2, single_layer=single_layer)
    validate_sector_symmetry(slots, poles, sectors, layout, paired_stator=True)
    # Explicit layouts follow the same rule as generated ones, also at layers=2.
    text = "|".join(phase if sign == 1 else phase.lower() for phase, sign in layout)
    explicit = build_winding_layout(slots, poles//2, single_layer=False, layout_str=text)
    validate_sector_symmetry(slots, poles, sectors, explicit, paired_stator=True)


@pytest.mark.parametrize("sectors", [-1, 0, 1])
def test_full_aliases_do_not_require_a_repeated_winding(sectors):
    validate_sector_symmetry(24, 28, sectors, parse_winding_layout(ASYMMETRIC), paired_stator=True)


@pytest.mark.parametrize("sectors", [2, 4])
def test_custom_balanced_paired_winding_need_not_have_sector_symmetry(sectors):
    with pytest.raises(ValueError, match="resolved winding.*slot.*same phase"):
        validate_sector_symmetry(24, 28, sectors, parse_winding_layout(ASYMMETRIC), paired_stator=True)


def test_count_divisibility_is_necessary_but_not_sufficient():
    with pytest.raises(ValueError, match="both be divisible"):
        validate_sector_symmetry(12, 14, 4, build_winding_layout(12, 7), paired_stator=True)
    with pytest.raises(ValueError, match="paired-stator CAD/template.*whole two-slot"):
        validate_sector_symmetry(12, 8, 4, build_winding_layout(12, 4), paired_stator=True)


def test_pair_requirement_is_a_named_geometry_family_constraint():
    # A hypothetical one-slot-repeating stator can use this periodic phase
    # basis at shift3. It must not be mistaken for the current paired CAD.
    layout = [("A", 1), ("B", 1), ("C", 1)]*4
    validate_sector_symmetry(12, 8, 4, layout, paired_stator=False)
    with pytest.raises(ValueError, match="paired-stator"):
        validate_sector_symmetry(12, 8, 4, layout, paired_stator=True)


@pytest.mark.parametrize("slots,poles,wind,expected", [
    (12, 14, {"layers": 1}, 2),
    (24, 28, {"layers": 1}, 4),
    (24, 28, {"layers": 2}, 4),
    (12, 8, {"layers": 1}, 2),
    (24, 28, {"layers": 1, "layout": ASYMMETRIC}, 1)])
def test_private_probe_chooses_largest_certified_sector(slots, poles, wind, expected):
    before = copy.deepcopy(wind)
    assert fem._calibration_sector_count(slots, poles, wind) == expected
    assert wind == before


class ProbeIntercepted(BaseException):
    """Escape calibration's ordinary failure/fallback policy without solving."""


@pytest.mark.parametrize("probe", ["daxis", "psi_pm"])
@pytest.mark.parametrize("slots,poles,layout,expected", [(12, 8, "", 2), (24, 28, ASYMMETRIC, -1)])
def test_both_private_probes_use_certified_sector(monkeypatch, probe, slots, poles, layout, expected):
    calls = []
    def intercept(**kwargs):
        calls.append(kwargs)
        raise ProbeIntercepted
    monkeypatch.setattr(fem, "em_transient_eval", intercept)
    monkeypatch.setattr(fem, "_daxis_disk_path", lambda: None)
    geo = {"num_slots": slots, "num_poles": poles, "stator_diameter": 30.}
    wind = {"layers": 1, "layout": layout, "connection": "2S"}
    with pytest.raises(ProbeIntercepted):
        if probe == "psi_pm":
            fem.noload_psi_pm(geo, wind, poles//2, 1, 60., geo_override=geo)
        else:
            fem._calibrate_daxis(SimpleNamespace(num_poles=poles), geo, wind, poles//2,
                                 geo, 1, ("sector-guard-test",), "test")
    assert len(calls) == 1 and calls[0]["n_sectors"] == expected
    assert calls[0]["I_phase_rms"] == 0.


@pytest.mark.parametrize("entry", ["fem_transient_sliding_band", "em_transient_eval"])
@pytest.mark.parametrize("current", [0., 46.])
@pytest.mark.parametrize("slots,poles,sectors,layout,message,equal_widths", [
    (12, 8, 4, "", "paired-stator", False),
    (12, 8, 4, "", "paired-stator", True),
    (24, 28, 4, ASYMMETRIC, "resolved winding", False),
    (24, 28, 2, ASYMMETRIC, "resolved winding", False),
    (12, 14, 4, "", "both be divisible", False)])
def test_public_entry_rejects_before_calibration_or_mesh(monkeypatch, entry, current,
                                                       slots, poles, sectors, layout, message, equal_widths):
    regression = runpy.run_path(str(Path(__file__).with_name("test_physics_regression.py")))
    geo = dict(regression["GEO_30MM"], num_slots=slots, num_poles=poles,
               num_seg=sectors, num_slots_per_segment=slots//sectors,
               num_poles_per_segment=poles//sectors)
    if equal_widths:
        geo["tooth2_width"] = geo["tooth_width"]
    original_get = config.get_config
    def isolated_config(*args, **kwargs):
        value = copy.deepcopy(original_get(*args, **kwargs))
        value["winding"] = dict(value.get("winding", {}), layers=1, layout=layout,
                                n_parallel=1, connection="2S")
        return value
    monkeypatch.setattr(config, "get_config", isolated_config)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid sector reached calibration or mesh construction")
    monkeypatch.setattr(fem, "_resolve_daxis_shift", forbidden)
    monkeypatch.setattr(fem, "_build_sliding_band_meshes", forbidden)
    with pytest.raises(ValueError, match=message):
        getattr(fem, entry)(geo_override=geo, I_phase_rms=current, rpm=3800.,
                            gamma_deg=0., n_steps_per_period=12, n_periods=1.,
                            n_sectors=sectors, daxis_deg=None, geo_mesh=False,
                            iron_template=True, eddy=False, rotor_eddy=False)


@pytest.mark.parametrize("slots,poles,sectors,layout", [
    (12, 14, 1, ""), (12, 14, 2, ""),
    (24, 28, 1, ""), (24, 28, 2, ""), (24, 28, 4, ""),
    (24, 28, -1, ASYMMETRIC), (24, 28, 1, ASYMMETRIC)])
def test_valid_public_request_reaches_unchanged_calibration_boundary(monkeypatch, slots, poles,
                                                                   sectors, layout):
    regression = runpy.run_path(str(Path(__file__).with_name("test_physics_regression.py")))
    geo = dict(regression["GEO_30MM"], num_slots=slots, num_poles=poles,
               num_seg=2, num_slots_per_segment=slots//2, num_poles_per_segment=poles//2)
    original_get = config.get_config
    def isolated_config(*args, **kwargs):
        value = copy.deepcopy(original_get(*args, **kwargs))
        value["winding"] = dict(value.get("winding", {}), layers=1, layout=layout,
                                n_parallel=1, connection="2S")
        return value
    monkeypatch.setattr(config, "get_config", isolated_config)
    def intercept(*args, **kwargs):
        assert args[5] == sectors  # the requested sector was not auto-adjusted
        raise ProbeIntercepted
    monkeypatch.setattr(fem, "_resolve_daxis_shift", intercept)
    with pytest.raises(ProbeIntercepted):
        fem.fem_transient_sliding_band(geo_override=geo, I_phase_rms=0., rpm=3800.,
                                      gamma_deg=0., n_steps_per_period=12, n_periods=1.,
                                      n_sectors=sectors, daxis_deg=None, geo_mesh=False,
                                      iron_template=True, eddy=False, rotor_eddy=False)
