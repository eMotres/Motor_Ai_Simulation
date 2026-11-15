"""A1 of the propeller module: schema ``propeller-2`` (a superset of ``propeller-1``) and the blade block.

Pins: propeller-1 files and propeller-2 files without a ``blade`` behave exactly as before; a complete blade
is parsed into SI units; every malformed blade is refused with the field named.
"""
from __future__ import annotations

import copy

import pytest
import yaml

from motor_ai_sim import propeller as pp
from motor_ai_sim.propeller_blade import MIN_STATIONS, parse_blade

D_M = 0.254


def _stations(n=10, tip_mm=127.0, hub_mm=12.0):
    out = []
    for i in range(n):
        r = hub_mm + (tip_mm - hub_mm) * (i + 1) / n
        out.append({"r_mm": round(r, 3), "chord_mm": 20.0 - 0.1 * i, "twist_deg": 40.0 - 3.0 * i,
                    "airfoil": "clarky", "thickness_rel": 0.08})
    return out


def _blade(**kw):
    b = {"geometry_source": "published", "source_ref": "test fixture", "hub_radius_mm": 12.0,
         "stations": _stations()}
    b.update(kw)
    return b


def _raw(schema="propeller-2", blade=None, name="v2test"):
    rho = 1.225
    rows = []
    for rpm in (3000, 4000, 5000, 6000, 7000, 8000):
        n = rpm / 60.0
        T = 0.1 * rho * n ** 2 * D_M ** 4
        P = 0.04 * rho * n ** 3 * D_M ** 5
        rows.append({"thr": 50, "V": 20.0, "I": 5.0, "Pel": 100.0, "rpm": rpm,
                     "thrust_g": T / 9.80665 * 1000.0, "torque_Nm": P / (2 * 3.141592653589793 * n)})
    raw = {"schema": schema, "id": name, "vendor": "TEST", "model": name, "status": "current",
           "geometry": {"diameter_in": 10.0, "diameter_mm": 254.0, "blades": 2},
           "performance": {"data_quality": "torque_measured", "test_density_kg_m3": rho,
                           "tables": [{"id": "t", "use_in_fit": True, "torque_resolution_Nm": 0.0001, "rows": rows}]}}
    if blade is not None:
        raw["blade"] = blade
    return raw


def test_v1_and_v2_without_blade_give_identical_models():
    a = pp._build(_raw("propeller-1"), "a")
    b = pp._build(_raw("propeller-2"), "b")
    assert a.blade is None and b.blade is None and not a.bemt_ready and not b.bemt_ready
    for rpm in (4000.0, 6500.0):
        assert pp.thrust_N(a, rpm) == pp.thrust_N(b, rpm)
        assert pp.torque_Nm(a, rpm) == pp.torque_Nm(b, rpm)
        assert pp.cooling_air_speed_ms(a, rpm) == pp.cooling_air_speed_ms(b, rpm)


def test_existing_catalogue_is_untouched_by_the_new_schema():
    props = pp.list_propellers()
    assert props and all(p.blade is None and not p.bemt_ready for p in props)
    assert all(p.raw.get("schema") == "propeller-1" for p in props)


def test_unknown_schema_is_still_refused():
    with pytest.raises(pp.PropellerDataError):
        pp._build(_raw("propeller-3"), "x")


def test_complete_blade_is_parsed_in_si_units():
    p = pp._build(_raw(blade=_blade(interpolation="linear", pitch_axis=0.3, rotation="cw_from_front",
                                    sections={"clarky": "airfoils/clarky.dat"},
                                    polars={"source": "xfoil_table", "re_ref": 80000})), "x")
    b = p.blade
    assert p.bemt_ready
    assert b.hub_radius_m == 0.012 and b.root_cutout_m == 0.012 and b.tip_radius_m == 0.127
    assert len(b.stations) == 10 and b.stations[0].r_m < b.stations[-1].r_m
    assert b.stations[-1].r_m == pytest.approx(0.127)
    assert (b.interpolation, b.pitch_axis, b.rotation) == ("linear", 0.3, "cw_from_front")
    assert b.polars_source == "xfoil_table" and b.polars_re_ref == 80000.0
    assert b.sections == {"clarky": "airfoils/clarky.dat"}
    assert not b.estimated and b.method is None


def test_defaults_are_stated_not_silent():
    b = parse_blade(_blade(), D_M, "x")
    assert (b.interpolation, b.pitch_axis, b.rotation, b.polars_source) == ("pchip", 0.25, None, None)


def test_bemt_ready_needs_the_blade_count():
    raw = _raw(blade=_blade())
    raw["geometry"]["blades"] = None
    assert pp._build(raw, "x").blade is not None and not pp._build(raw, "x").bemt_ready


def test_inferred_geometry_must_be_marked_estimated_with_a_method():
    ok = parse_blade(_blade(geometry_source="inferred", method="generic planform"), D_M, "x")
    assert ok.estimated and ok.method == "generic planform"
    with pytest.raises(pp.PropellerDataError, match="method"):
        parse_blade(_blade(geometry_source="inferred"), D_M, "x")
    with pytest.raises(pp.PropellerDataError, match="estimated: true"):
        parse_blade(_blade(geometry_source="inferred", estimated=False, method="m"), D_M, "x")


def _bad(mutate, match):
    b = copy.deepcopy(_blade())
    mutate(b)
    with pytest.raises(pp.PropellerDataError, match=match):
        parse_blade(b, D_M, "x")


@pytest.mark.parametrize("mutate,match", [
    (lambda b: b.pop("source_ref"), "source_ref"),
    (lambda b: b.update(geometry_source="guessed"), "geometry_source"),
    (lambda b: b.pop("hub_radius_mm"), "hub_radius_mm"),
    (lambda b: b.update(hub_radius_mm=-1), "hub_radius_mm"),
    (lambda b: b.update(hub_radius_mm=100), "hub_radius_mm"),
    (lambda b: b.update(root_cutout_mm=5.0), "root_cutout_mm"),
    (lambda b: b.update(pitch_axis=1.5), "pitch_axis"),
    (lambda b: b.update(interpolation="spline"), "interpolation"),
    (lambda b: b.update(rotation="left"), "rotation"),
    (lambda b: b.update(stations=b["stations"][:MIN_STATIONS - 1]), "at least"),
    (lambda b: b["stations"].reverse(), "increase"),
    (lambda b: b["stations"][3].update(r_mm=b["stations"][2]["r_mm"]), "increase"),
    (lambda b: b["stations"][0].update(r_mm=5.0), "inside the hub"),
    (lambda b: b["stations"][2].update(chord_mm=0), "chord_mm"),
    (lambda b: b["stations"][2].update(chord_mm=float("nan")), "chord_mm"),
    (lambda b: b["stations"][2].update(twist_deg="40"), "twist_deg"),
    (lambda b: b["stations"][2].pop("airfoil"), "airfoil"),
    (lambda b: b["stations"][2].update(thickness_rel=0.9), "thickness_rel"),
    (lambda b: b.update(stations=_stations(tip_mm=100.0)), "90 %"),
    (lambda b: b["stations"].__setitem__(-1, dict(b["stations"][-1], r_mm=140.0)), "r_mm"),
    (lambda b: b.update(sections={"naca0012": "f.dat"}), "no station uses"),
    (lambda b: b.update(polars={"source": "guess"}), "polars.source"),
])
def test_malformed_blade_is_refused_with_the_field_named(mutate, match):
    _bad(mutate, match)


def test_yaml_round_trip_through_the_catalogue_loader(tmp_path):
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "p.yaml").write_text(yaml.safe_dump(_raw(blade=_blade())))
    old = pp._DIR
    pp._DIR = tmp_path
    try:
        cat = pp.load_catalog()
        assert cat["v2test"].bemt_ready and cat["v2test"].selectable
    finally:
        pp._DIR = old
        pp._cache.clear()
