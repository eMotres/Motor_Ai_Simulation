"""Unit tests of the passport v1 pilot library (no FEM)."""
from __future__ import annotations

import math

import pytest

from motor_ai_sim.passport_v1 import losses as LS
from motor_ai_sim.passport_v1 import psimap as PM
from motor_ai_sim.passport_v1 import snapshot as S


# ── M0 snapshot ─────────────────────────────────────────────────────────────

def test_canonical_hash_is_order_and_noise_free():
    a = {"b": 0.1 + 0.2, "a": [1, 2.0, {"z": -0.0}]}
    b = {"a": [1, 2.0, {"z": 0.0}], "b": 0.30000000000000004}
    assert S.sha256_of(a) == S.sha256_of(b)
    assert S.sha256_of({"x": 1.0}) != S.sha256_of({"x": 1.0000001})


def test_duty_mesh_settings_fail_closed():
    duty = {"name": "rated", "mesh": {"mesh.meshSize": 1, "mesh.minSize": 0.3,
                                      "mesh.outerAir": 1.2, "mesh.gapLayers": 1,
                                      "mesh.nSectors": 2, "mesh.componentMesh": {}}}
    m = S.duty_mesh_settings(duty)
    assert m["mesh_size_mm"] == 1.0 and m["n_sectors"] == 2
    assert m["structured_gap"] is True
    bad = {"name": "rated", "mesh": dict(duty["mesh"])}
    bad["mesh"].pop("mesh.gapLayers")
    with pytest.raises(S.SnapshotError):
        S.duty_mesh_settings(bad)


def test_resolve_materials_names_every_source():
    cfg = {"materials": {"magnet": "M1", "stator_core": "S", "rotor_core": "S"}}
    out = S.resolve_materials(cfg, fallback_parts={"shaft": "Sh", "sleeve": "Sl",
                                                   "slot": "copper"},
                              fallback_source="ws")
    assert out["slot_insulation"]["name"] == "Nomex"
    assert out["shaft"] == {"name": "Sh", "source": "ws"}
    assert out["magnet"]["source"] == "configuration"
    with pytest.raises(S.SnapshotError):
        S.resolve_materials(cfg, fallback_parts={}, fallback_source="ws")


# ── MTPA bracket ────────────────────────────────────────────────────────────

def test_parabola_vertex_and_bracket():
    g = [0.0, 5.0, 10.0]
    T = [1 - (x - 6.0) ** 2 * 1e-3 for x in g]
    gv, Tv, ok = PM.parabola_vertex(g, T)
    assert ok and gv == pytest.approx(6.0) and Tv == pytest.approx(1.0)
    assert PM.bracket_next([0, 5, 10], [1, 2, 3], 5.0) == 15.0
    assert PM.bracket_next([0, 5, 10], [3, 2, 1], 5.0) == -5.0
    assert PM.bracket_next([0, 5, 10], [1, 3, 2], 5.0) is None


# ── psi-map on a linear SPM model ───────────────────────────────────────────

P, PSI, LD, LQ = 7, 1.0e-3, 6.5e-6, 6.0e-6


def _lin(I, g):
    d, q = PM.id_iq(I, g)
    pd, pq = PSI + LD * d, LQ * q
    return {"i_d": d, "i_q": q, "psi_d": pd, "psi_q": pq,
            "T": 1.5 * P * (pd * q - pq * d), "ripple_pp": 0.0}


def _lin_map():
    pts = [_lin(0.0, 0.0)]
    for I in (10.0, 20.0, 40.0, 60.0, 80.0, 100.0):
        for g in (-5, 0, 5, 10, 20, 35, 50, 65, 80, 90):
            pts.append(_lin(I, g))
    return PM.PsiMap.build(pts, P, mtpa=[(I, 0.0, _lin(I, 0.0)["T"]) for I in (10, 40, 100)])


def test_map_reproduces_a_linear_machine():
    m = _lin_map()
    for I, g in ((33.0, 12.0), (70.0, 57.0), (95.0, 3.0)):
        a, b = m.at_Ig(I, g), _lin(I, g)
        psi = math.hypot(b["psi_d"], b["psi_q"])
        # Clough–Tocher reproduces a linear field to its gradient estimate
        assert abs(a["psi_d"] - b["psi_d"]) / psi < 1e-4
        assert abs(a["psi_q"] - b["psi_q"]) / psi < 1e-4
        assert a["T_psi"] == pytest.approx(b["T"], rel=1e-4)
    dl = m.diff_L(*PM.id_iq(50.0, 30.0))
    assert dl["Ld_diff_H"] == pytest.approx(LD, rel=1e-2)
    assert dl["Lq_diff_H"] == pytest.approx(LQ, rel=1e-2)
    assert dl["reciprocity_asym"] < 1e-2
    assert not m.at_Ig(150.0, 10.0)["inside"]          # outside the hull: refused


def test_voltage_limit_and_field_weakening():
    m = _lin_map()
    R = 0.009
    vl = PM.v_phase_limit(22.2, 0.95)
    assert vl == pytest.approx(0.95 * 22.2 / math.sqrt(3.0))
    # low speed: MTPA (the q-axis for this linear SPM table)
    g, how = m.operating_gamma(40.0, 3000.0, R, vl)
    assert how == "mtpa" and g == pytest.approx(0.0)
    # high speed: FW, the returned angle sits on the limit
    g, how = m.operating_gamma(40.0, 19500.0, R, vl)
    assert how == "field weakening"
    assert m.v_phase(40.0, g, 19500.0, R) == pytest.approx(vl, rel=1e-6)
    # beyond the characterised arm: refused
    g, how = m.operating_gamma(10.0, 60000.0, R, vl)
    assert g is None and "deeper field weakening" in how
    n = m.max_speed(R, vl, 40.0)
    gl, _ = m.operating_gamma(40.0, n * 0.999, R, vl)
    assert gl is not None and m.operating_gamma(40.0, n * 1.01, R, vl)[0] is None


def test_short_circuit_limit():
    s = PM.short_circuit_steady(PSI, LD, LQ, 1e-9, 50000.0, P)
    assert s["i_d_A"] == pytest.approx(-PSI / LD, rel=1e-6)
    assert s["I_char_rms_A"] == pytest.approx(PSI / LD / math.sqrt(2.0))


# ── loss trajectory ─────────────────────────────────────────────────────────

def test_loss_interp_power_law_exact_and_never_extrapolates():
    rows = {}
    for I in (20.0, 40.0):
        rows[I] = [{"rpm": n, "I": I, "P_cu_ac_W": 1e-8 * I * n ** 2,
                    "P_fe_stator_W": 1e-3 * n ** 1.5, "P_fe_rotor_W": 0.0,
                    "P_mag_W": 1e-7 * n ** 2, "P_shaft_W": 0.0, "P_sleeve_W": 0.0}
                   for n in (3000.0, 6000.0, 13000.0)]
    out = LS.interp_loss(9000.0, 30.0, rows, R_dc_hot=0.01)
    assert out["groups"]["P_cu_ac_W"] == pytest.approx(1e-8 * 30 * 9000 ** 2, rel=1e-9)
    assert out["groups"]["P_fe_stator_W"] == pytest.approx(1e-3 * 9000 ** 1.5, rel=1e-9)
    assert out["groups"]["P_cu_dc_W"] == pytest.approx(3 * 30 ** 2 * 0.01)
    assert LS.interp_loss(20000.0, 30.0, rows, 0.01)["P_total_W"] is None
    assert LS.interp_loss(9000.0, 50.0, rows, 0.01)["P_total_W"] is None


def test_efficiency_at_the_shaft_matches_the_route_convention():
    # L12 rated duty summary: P_mech 848.6 W (T·w), loss 65.9 W, mech 0.11 W
    w = 2 * math.pi * 13000 / 60
    e = LS.efficiency_shaft(848.6 / w, 13000.0, 65.9, 0.11)
    assert e["eta_shaft"] == pytest.approx(0.9278, abs=5e-5)
