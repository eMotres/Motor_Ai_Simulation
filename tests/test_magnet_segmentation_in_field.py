"""Axial magnet segmentation is solved IN the field (sigma x factor), once.

Until 2026-11-25 the coupled sigma*dA/dt solve gave every magnet body the full
library conductivity and `magnet_lamination` only multiplied the REPORTED magnet
watts (and the loss map) by the Russell-Norsworthy factor afterwards.  The field
therefore carried the eddy currents, the screening and the drag of an axially
infinite magnet, and the Coulomb (rotor-side) torque held that drag, while the
reported loss was the scaled solid-block figure.  Now the magnet bodies are
solved with sigma_eff = factor * sigma and the watts are not scaled again.

This file is the small synthetic version of the three-variant experiment of the
audit (a: full sigma, watts x factor; b: sliced, sigma x factor in the field;
c: unsliced with a magnet card at sigma x factor), on the PUBLIC 30 mm 12s14p
fixture of tests/test_physics_regression.py with coupled eddy + rotor eddy and
the Coulomb torque, at 60 000 rpm so the magnet eddy is large enough to move
the torque, 12 steps per period (a fixture, not a final point).

Measured on the branch (job container, 2 cpus), factor 0.199994 (2.0542 mm
slices of the 10 mm stack):

    quantity            solid (a)    sliced (b)   card sigma x k (c)
    T_coulomb N*m       0.401099     0.403239     0.403239
    T_sv N*m            0.412316     0.411011     0.411011
    P_mag_solve W       21.211       4.327        4.327   (a reported 21.211 x k = 4.242)
    T_ripple_pp N*m     8.645e-4     1.0774e-3    1.0774e-3
    V_peak V            27.7214      27.7706      27.7706

(b) and (c) agree to about 1e-9 relative (the factor in the report is rounded
to 6 digits).  Directions as on the 200 mm audit point: the Coulomb torque
rises (less drag), the space-vector torque falls (less in-field loss), the
ripple rises (less eddy damping), and the sliced loss is ABOVE the scaled solid
one (sub-linear loss in sigma).  V_peak moves +0.18 % here, against -3 % on the
200 mm point: on this small machine the screening of the magnets barely
changes the gap flux, so it is only bounded, not signed.
"""
from __future__ import annotations

import dataclasses
import json
import math

import pytest

from tests import test_physics_regression as T
from motor_ai_sim.material_context import set_request_materials
from motor_ai_sim.simulation import fem_solver_2d as F
from motor_ai_sim.simulation.losses import magnet_segmentation_factor

RPM_HI = 60000.0
TARGET_FACTOR = 0.2


@pytest.fixture(autouse=True)
def _cold():
    F._SB_WARM_CACHE.clear()
    yield
    F._SB_WARM_CACHE.clear()


def _solve(geo, override):
    F._SB_WARM_CACHE.clear()
    kw = dict(T.CASES["p2_eddy"]); kw["torque_method"] = "coulomb"
    set_request_materials(override)
    tok = F._NO_WARM_CACHE_CTX.set(True)
    try:
        return F.fem_transient_sliding_band(
            geo_override=geo, rpm=RPM_HI, connection=T.CONNECTION,
            end_winding_factor=T.K_END, **kw)
    finally:
        F._NO_WARM_CACHE_CTX.reset(tok)
        set_request_materials(None)


def _card_override(scale: float):
    """T.OVERRIDE with the magnet replaced by a copy of its card at sigma x scale."""
    from motor_ai_sim.materials import get_material
    mag = get_material("magnet", T.MAGNET)
    card = {k: (list(map(list, v)) if k == "bh_curve" and v else v)
            for k, v in dataclasses.asdict(mag).items()}
    card["sigma"] = float(mag.sigma) * float(scale); card["category"] = "magnet"
    name = "SEGK_TEST_CARD"
    o = json.loads(json.dumps(T.OVERRIDE))
    o["assignment"]["magnet"] = name
    o["materials"] = {name: card}
    return o


def _slice_for(width_mm: float, stack_mm: float, target: float) -> float:
    lo, hi = 1e-3, stack_mm
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if magnet_segmentation_factor(mid, width_mm, stack_mm) < target:
            lo = mid
        else:
            hi = mid
    return round(0.5 * (lo + hi), 4)


def _field_magnet_W(d):
    """The magnet watts the FIELD dissipated, from the solver's power balance."""
    pb = d["power_balance"]
    return (float(pb["P_field_solved_loss_W"]) - float(d["P_cu_ac_solve_W"])
            - float(d["P_shaft_solve_W"]) - float(d["P_sleeve_solve_W"]))


@pytest.mark.slow
def test_sliced_magnet_is_solved_with_scaled_sigma_and_not_scaled_twice():
    solid = _solve(dict(T.GEO_30MM), T.OVERRIDE)
    seg0 = solid["magnet_segmentation"]
    assert float(seg0["factor"]) == 1.0 and "applied_as" not in seg0, seg0
    lam = _slice_for(float(seg0["width_mm"]), float(T.GEO_30MM["motor_length"]),
                     TARGET_FACTOR)
    geo = dict(T.GEO_30MM); geo["magnet_lamination"] = lam
    sliced = _solve(geo, T.OVERRIDE)
    seg = sliced["magnet_segmentation"]
    k = float(seg["factor"])
    assert seg["applied_as"] == "field_sigma", seg
    assert k == pytest.approx(TARGET_FACTOR, abs=2e-3), seg
    assert float(seg["sigma_eff_S_per_m"]) == pytest.approx(
        k * float(seg["sigma_lib_S_per_m"]), rel=1e-5), seg
    card = _solve(dict(T.GEO_30MM), _card_override(k))

    # (b) == (c): the sliced run IS the unsliced machine with sigma x k
    for key in ("T_avg_Nm", "T_avg_coulomb_Nm", "T_avg_maxwell_Nm", "V_peak",
                "T_ripple_pp_Nm"):
        assert float(sliced[key]) == pytest.approx(float(card[key]), rel=1e-5), key
    assert float(sliced["P_mag_solve_W"]) == pytest.approx(
        float(card["P_mag_solve_W"]), abs=2e-3)
    assert float(sliced["P_mag_linear_W"]) == pytest.approx(
        float(card["P_mag_linear_W"]), abs=2e-3)

    # no second scaling: the reported magnet loss is the one the field solved
    assert float(sliced["P_mag_solve_W"]) == pytest.approx(
        _field_magnet_W(sliced), abs=5e-3)
    assert float(solid["P_mag_solve_W"]) == pytest.approx(
        _field_magnet_W(solid), abs=5e-3)

    # directions of the audit's (a) -> (b), with margins well inside the
    # measured moves (+2.0 %, +0.53 %, -0.32 %, +25 %)
    p_old = float(solid["P_mag_solve_W"]) * k       # what (a) reported
    p_new = float(sliced["P_mag_solve_W"])
    assert p_old * 1.005 < p_new < float(solid["P_mag_solve_W"]), (p_old, p_new)
    t0, t1 = float(solid["T_avg_coulomb_Nm"]), float(sliced["T_avg_coulomb_Nm"])
    assert t1 > t0 * 1.0025, (t0, t1)                # less drag in the rotor torque
    sv0 = float(solid["torque_method_diagnostics"]["space_vector_mean_candidate_Nm"])
    sv1 = float(sliced["torque_method_diagnostics"]["space_vector_mean_candidate_Nm"])
    assert sv1 < sv0 * 0.999, (sv0, sv1)             # less in-field loss in the gap power
    assert float(sliced["T_ripple_pp_Nm"]) > 1.1 * float(solid["T_ripple_pp_Nm"])
    assert float(sliced["V_peak"]) == pytest.approx(float(solid["V_peak"]), rel=0.01)
    # the shaft is one continuous conductor: its sigma is untouched
    assert float(sliced["P_shaft_solve_W"]) == pytest.approx(
        float(solid["P_shaft_solve_W"]), abs=0.02)


@pytest.mark.slow
def test_no_slicing_is_the_library_sigma_bit_for_bit():
    """lamination 0 and a magnet card that copies the library at sigma x 1.0
    give the SAME result to the last bit: the solid path never multiplies the
    factor in, and the card copy used above is faithful."""
    a = _solve(dict(T.GEO_30MM), T.OVERRIDE)
    b = _solve(dict(T.GEO_30MM), _card_override(1.0))
    for key in ("T_avg_Nm", "T_avg_coulomb_Nm", "T_avg_maxwell_Nm", "V_peak",
                "T_ripple_pp_Nm", "P_mag_solve_W", "P_mag_linear_W",
                "P_shaft_solve_W", "P_cu_ac_solve_W"):
        assert float(a[key]).hex() == float(b[key]).hex(), key
    assert a["power_balance"]["residual_W"] == b["power_balance"]["residual_W"]
    assert not math.isnan(float(a["T_avg_Nm"]))


# ── warm seeds and cache keys of SLICED machines (review B1, 2026-11-25) ─────

UNSLICED_WMETA_KEYS = {"nspp", "npd", "conn", "temp", "mscale", "wind", "ts"}


def test_warm_meta_gains_the_sigma_mode_only_when_sliced():
    base = {"nspp": 12, "npd": 1.0, "conn": "np1", "temp": 120.0,
            "mscale": 1.0, "wind": "abc", "ts": "bdf2"}
    m1 = F._wmeta_with_segmentation(dict(base), 1.0)
    assert m1 == base and json.dumps(m1, sort_keys=True) == json.dumps(base, sort_keys=True)
    m2 = F._wmeta_with_segmentation(dict(base), 0.1999937)
    assert m2["msig"] == 0.199994 and F._seed_field_identity(m2)["msig"] == 0.199994
    assert m2 != base      # the legacy meta comparison refuses a full-sigma seed


def test_cache_tag_only_for_sliced_or_unreadable_lamination():
    tag = F.MAGNET_SEGMENTATION_PHYSICS
    for g in ({}, None, {"magnet_lamination": 0}, {"magnet_lamination": 0.0},
              {"magnet_lamination": None}):
        assert F.magnet_segmentation_cache_tag(g) is None, g
    for g in ({"magnet_lamination": 5}, {"magnet_lamination": "2.5"},
              {"magnet_lamination": float("nan")}, {"magnet_lamination": "x"}):
        assert F.magnet_segmentation_cache_tag(g) == tag, g


def test_eval_cache_key_moves_only_for_a_sliced_machine(monkeypatch):
    from motor_ai_sim.routes import optimization as O
    cfg = {"geometry": dict(T.GEO_30MM)}
    monkeypatch.setattr(O, "get_config", lambda: cfg)
    args = (12, 120.0, 1.0, 0.0, 1.4, 0.35, 2, False, False, "fp")

    def key(ov):
        return O._eval_cache_key(ov, 60.0, *args, rotor_eddy=True)
    k_solid = key({})
    k_sliced = key({"magnet_lamination": 2.0})
    k_solid_ov = key({"magnet_lamination": 0.0})
    assert k_sliced != k_solid
    # the solid machine's key has no segmentation term at all: same payload
    # as before the change (a lamination-0 override is a no-op of the config)
    assert k_solid_ov == k_solid
    real_dumps = json.dumps
    seen = []
    monkeypatch.setattr(O.json, "dumps", lambda o, **kw: (seen.append(o), real_dumps(o, **kw))[1])
    key({}); key({"magnet_lamination": 2.0})
    payloads = [p for p in seen if isinstance(p, dict) and "v" in p]
    assert "mseg" not in payloads[0] and payloads[1]["mseg"] == F.MAGNET_SEGMENTATION_PHYSICS


@pytest.mark.slow
def test_warm_library_key_separates_sliced_states_and_keeps_solid_keys(monkeypatch):
    """The library machine key of a SLICED machine carries `msig`; an unsliced
    machine's key inputs (field identity, op digest fields) have exactly the
    pre-change terms.  The library is switched on with no stored states and
    nothing written, so the solve itself is a cold one."""
    rec = {"mk": [], "op": []}
    real_mk, real_op = F._wlib.machine_key, F._wlib.op_digest
    monkeypatch.setattr(F._wlib, "enabled", lambda: True)
    monkeypatch.setattr(F._wlib, "catalog_enabled", lambda: False)
    monkeypatch.setattr(F._wlib, "candidates", lambda key: [])
    monkeypatch.setattr(F._wlib, "store", lambda *a, **k: None)
    monkeypatch.setattr(F, "_warm_cache_load", lambda *a, **k: None)
    monkeypatch.setattr(F, "_warm_cache_store", lambda *a, **k: None)
    monkeypatch.setattr(F._wlib, "machine_key",
                        lambda *a, **k: (rec["mk"].append((a, k)), real_mk(*a, **k))[1])
    monkeypatch.setattr(F._wlib, "op_digest",
                        lambda f: (rec["op"].append(dict(f)), real_op(f))[1])

    def solve(geo):
        F._SB_WARM_CACHE.clear()
        kw = dict(T.CASES["p2_eddy"]); kw["torque_method"] = "coulomb"
        set_request_materials(T.OVERRIDE)
        try:
            return F.fem_transient_sliding_band(
                geo_override=geo, rpm=RPM_HI, connection=T.CONNECTION,
                end_winding_factor=T.K_END, **kw)
        finally:
            set_request_materials(None)

    d0 = solve(dict(T.GEO_30MM))
    geo = dict(T.GEO_30MM); geo["magnet_lamination"] = 2.0542
    d1 = solve(geo)
    assert len(rec["mk"]) == 2 and len(rec["op"]) == 2, rec
    id0, id1 = rec["mk"][0][0][2], rec["mk"][1][0][2]
    assert set(id0) == UNSLICED_WMETA_KEYS - {"temp"}, id0
    assert "msig" not in id0
    k = float(d1["magnet_segmentation"]["factor"])
    assert id1["msig"] == float("%.6g" % k)
    assert {x: v for x, v in id1.items() if x != "msig"} == id0
    assert real_mk(*rec["mk"][0][0], **rec["mk"][0][1]) != \
        real_mk(*rec["mk"][1][0], **rec["mk"][1][1])
    assert rec["op"][0] == rec["op"][1]          # the op digest is unchanged
    assert d0["magnet_segmentation"].get("applied_as") is None
