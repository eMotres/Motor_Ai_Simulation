"""The shaft-side convention (owner 2026-11-25, motor_ai_sim.shaft_convention): the core loss is a SHAFT load, the 3-D
torque factor goes into the shaft-side numbers (field part only), every loss stays 2-D, and a summary without the marker is
the earlier convention, never converted.  Pure-function tests first, then the twin tests of every site under the marker."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

from motor_ai_sim import shaft_convention as SC

W1000 = 2.0 * math.pi * 1000.0 / 60.0


# ── where each loss sits ─────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("method,eddy,rotor,rot_emb,st_emb", [
    # rotor-side torque, rotor eddy solved IN the field: its drag is inside the force on the rotor
    ("coulomb_virtual_work", True, True, 6.5, 0.0),
    ("maxwell_stress", True, True, 6.5, 0.0),
    # rotor-side torque, rotor eddy post-processed (or none): nothing embedded
    ("coulomb_virtual_work", False, True, 0.0, 0.0),
    ("coulomb_virtual_work", False, False, 0.0, 0.0),
    ("coulomb_virtual_work", True, False, 0.0, 0.0),
    # stator-side torque (power into the field): the solved copper AC is inside it
    ("energy_mean+maxwell_ripple", True, True, 0.0, 7.0),
    ("terminal_work_mean+maxwell_ripple", False, False, 0.0, 0.0),
    (None, False, False, 0.0, 0.0),
])
def test_loss_placement(method, eddy, rotor, rot_emb, st_emb):
    d = SC.drag(torque_method=method, eddy_coupled=eddy, rotor_eddy_coupled=rotor,
                p_fe=30.0, p_mag=5.0, p_shaft=1.0, p_sleeve=0.5, p_cu_ac_solved=7.0)
    # the shaft supplies the core loss and every rotor loss, wherever they were computed
    assert d["D_W"] == pytest.approx(36.5)
    assert d["rotor_embedded_W"] == pytest.approx(rot_emb) and d["stator_embedded_W"] == pytest.approx(st_emb)
    assert d["side"] == ("rotor" if method in ("coulomb_virtual_work", "maxwell_stress") else "stator")


W100 = 100.0                         # rad/s
RPM100 = W100 * 60.0 / (2.0 * math.pi)


def _hand(*, mode, G, R, F, cu, cuac, k, side):
    """The balance written out by hand from the physics (not from the helper): G = converted air-gap power,
    R = rotor eddy (in the field), F = core loss, cu = copper incl. AC, cuac = its solved AC part."""
    L = cu + F + R
    if mode == "motor":
        P = k * G - R - F
        return P, P + L
    P = k * G + R + F
    return P, P - L


@pytest.mark.parametrize("mode", ["motor", "generator"])
@pytest.mark.parametrize("k", [1.0, 0.9])
def test_rotor_side_coupled_balance_scales_only_the_converted_power(mode, k):
    G, R, F, cu = 1000.0, 60.0, 30.0, 10.0
    s = 1.0 if mode == "motor" else -1.0
    t = s * (G - s * R) / W100        # what a rotor-side torque reports: the force on the rotor, drag inside
    b = SC.balance(t_field_nm=t, rpm=RPM100, k=(None if k == 1.0 else k), d_w=F + R, p_loss_w=cu + F + R,
                   rotor_embedded_w=R)
    P, E = _hand(mode=mode, G=G, R=R, F=F, cu=cu, cuac=0.0, k=k, side="rotor")
    assert b["P_mech_W"] == pytest.approx(P) and b["P_elec_W"] == pytest.approx(E)
    assert b["op_mode"] == mode
    if mode == "motor":
        assert b["efficiency"] == pytest.approx(P / E)
    else:
        assert b["efficiency"] == pytest.approx(E / P)


@pytest.mark.parametrize("mode", ["motor", "generator"])
def test_stator_side_balance(mode):
    G, R, F, cu, cuac, k = 1000.0, 60.0, 30.0, 10.0, 4.0, 0.9
    s = 1.0 if mode == "motor" else -1.0
    t = s * (G + s * cuac) / W100     # power into (motor) / out of (generator) the field, copper AC inside
    b = SC.balance(t_field_nm=t, rpm=RPM100, k=k, d_w=F + R, p_loss_w=cu + F + R, stator_embedded_w=cuac)
    P, E = _hand(mode=mode, G=G, R=R, F=F, cu=cu, cuac=cuac, k=k, side="stator")
    assert b["P_mech_W"] == pytest.approx(P) and b["P_elec_W"] == pytest.approx(E)


def test_zero_current_closes_with_any_factor():
    # Astra B1: omega 100 rad/s, rotor torque -0.1 N*m = 10 W of in-field eddy drag, core 30 W, no copper, k 0.9
    b = SC.balance(t_field_nm=-0.1, rpm=RPM100, k=0.9, d_w=30.0 + 10.0, p_loss_w=40.0, rotor_embedded_w=10.0)
    assert b["P_mech_W"] == pytest.approx(40.0)            # the shaft supplies every loss
    assert b["P_elec_W"] == pytest.approx(0.0, abs=1e-12)  # and the terminals get nothing (zero current)
    assert b["efficiency"] == 0.0


def test_motor_and_generator_no_embedded_loss():
    b = SC.balance(t_field_nm=10.0, rpm=1000.0, k=0.9, d_w=30.0, p_loss_w=100.0)
    p = 0.9 * 10.0 * W1000 - 30.0
    assert b["P_mech_W"] == pytest.approx(p) and b["efficiency"] == pytest.approx(p / (p + 100.0))
    assert b["T_shaft_Nm"] == pytest.approx(p / W1000) and b["T_field_k_Nm"] == pytest.approx(9.0)
    g = SC.balance(t_field_nm=-10.0, rpm=1000.0, k=0.9, d_w=30.0, p_loss_w=100.0)
    pin = 0.9 * 10.0 * W1000 + 30.0
    assert g["P_mech_W"] == pytest.approx(pin) and g["efficiency"] == pytest.approx((pin - 100.0) / pin)
    assert SC.per_mass_torque(g) == pytest.approx(9.0) and SC.per_mass_torque(b) == pytest.approx(p / W1000)


def test_no_load_motor_has_a_negative_shaft_power_and_no_efficiency():
    b = SC.balance(t_field_nm=0.01, rpm=1000.0, k=None, d_w=30.0, p_loss_w=31.0)
    assert b["P_mech_W"] == pytest.approx(0.01 * W1000 - 30.0) and b["P_mech_W"] < 0
    assert b["efficiency"] == 0.0 and b["op_mode"] == "motor"


def _marked(**over):
    s = {"shaft_convention": SC.SHAFT_CONVENTION, "op_mode": "motor", "rpm": 1000.0, "T_em_avg_Nm": 10.0,
         "shaft_k3d": 0.9, "torque_side": "rotor", "shaft_rotor_in_field": True,
         "P_loss_total_W": 100.0, "P_core_W": 30.0, "P_mag_W": 5.0, "P_shaft_W": 0.0, "P_sleeve_W": 0.0}
    s.update(over)
    return s


def test_rederive_rebuilds_from_the_summary_s_own_losses():
    s = _marked()
    assert SC.rederive(s) is True
    # rotor-side, in field: G = T*w + R; P = k*G - R - F
    assert s["P_mech_W"] == pytest.approx(0.9 * (10.0 * W1000 + 5.0) - 5.0 - 30.0, abs=0.06)
    assert s["P_shaft_drag_W"] == pytest.approx(35.0)
    # a loss change moves D, the shaft power and the efficiency together (Astra B3)
    s2 = _marked(P_core_W=20.0, P_loss_total_W=90.0)
    SC.rederive(s2)
    assert s2["P_mech_W"] - s["P_mech_W"] == pytest.approx(10.0, abs=0.11)
    assert s2["P_elec_W"] == pytest.approx(s2["P_mech_W"] + 90.0, abs=0.11)
    old = {"op_mode": "motor", "rpm": 1000.0, "T_em_avg_Nm": 10.0, "P_mech_W": 5.0}
    assert SC.rederive(old) is False and old["P_mech_W"] == 5.0


def test_overlay_is_all_or_nothing():
    base = _marked()
    SC.rederive(base)
    unmarked = {"T_em_avg_Nm": 9.0, "P_core_W": 31.0}
    SC.overlay(base, unmarked)
    assert not SC.has_marker(base) and "T_shaft_Nm" not in base and "shaft_k3d" not in base
    b2 = {"T_em_avg_Nm": 1.0}
    m = _marked(); SC.rederive(m)
    SC.overlay(b2, m)
    assert SC.has_marker(b2) and b2["torque_side"] == "rotor" and b2["shaft_k3d"] == 0.9


def test_comparable_refuses_a_mix():
    assert SC.comparable([{"shaft_conv": 1}, {"shaft_conv": 1}])
    assert SC.comparable([{}, {"efficiency": 0.9}])
    assert not SC.comparable([{"shaft_conv": 1}, {}])


# ── the summary: twin of the earlier convention, bit for bit where nothing moves ──────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _sb(t: float, *, method: str = "coulomb_virtual_work", eddy: bool = True) -> dict:
    n = 12
    return {"rpm": 15000.0, "T_avg_Nm": t, "P_cu_W": [110.0] * n, "P_fe_W": [4.6] * n,
            "P_mag_eddy_W": [1.3] * n, "P_shaft_eddy_W": [0.01] * n, "P_sleeve_eddy_W": [0.0] * n,
            "P_cu_dc_W": 107.0, "P_cu_ac_solve_W": 3.3, "V_peak": 7.3, "torque_method": method,
            "eddy_coupled": eddy, "rotor_eddy_solved": eddy, "end_winding_factor": 1.72,
            "star_delta": "star", "drive": "current", "P_elec_in_W": 700.0, "P_mech_avg_W": 600.0}


@pytest.fixture()
def build(monkeypatch):
    from motor_ai_sim.routes import simulation as S
    import test_physics_regression as T
    from motor_ai_sim.material_context import set_request_materials
    monkeypatch.setattr(S, "_end3d_lookup", lambda *a, **k: {"k_flux": 0.9, "k_torque": 0.9})

    def _b(sb, conv="current"):
        set_request_materials(T.OVERRIDE)
        try:
            return S._build_transient_summary(dict(sb), I_phase_rms=32.0, gamma_deg=0.0, coil_temp_c=120.0,
                                              geo_override=dict(T.GEO_30MM), skip_psi_pm_probe=True,
                                              shaft_convention=conv)
        finally:
            set_request_materials(None)
    return _b


#: the summary fields this convention moves (everything else of a marked summary equals the earlier one)
MOVED = {"P_mech_W", "efficiency", "Kt_Nm_per_Arms", "Kt_Nm_per_A_line", "Km_Nm_sqrtW", "Km_per_mass_Nm_sqrtW_kg",
         "torque_per_mass_Nm_kg", "power_per_mass_W_kg", "torque_per_mass_full_Nm_kg", "power_per_mass_full_W_kg",
         "Km_per_mass_full_Nm_sqrtW_kg", "P_shaft_net_W", "efficiency_shaft", "battery_charge"}
NEW = set(SC.CONVENTION_KEYS)


def test_motor_summary_under_the_marker_and_its_legacy_twin(build):
    old, new = build(_sb(0.40), None), build(_sb(0.40))
    w = 2 * math.pi * 15000.0 / 60.0
    assert "shaft_convention" not in old and new["shaft_convention"] == SC.SHAFT_CONVENTION
    # the earlier convention: T*w 2-D, every loss on the electrical side
    assert old["P_mech_W"] == round(0.40 * w, 1)
    L = 110.0 + 4.6 + 1.3 + 0.01
    R = 1.3 + 0.01
    # Coulomb + coupled rotor eddy: the drag R is inside the torque, so G = T*w + R and P = k*G - R - Fe
    p = 0.9 * (0.40 * w + R) - R - 4.6
    assert new["P_mech_W"] == pytest.approx(p, abs=0.06)
    assert new["efficiency"] == pytest.approx(p / (p + L), abs=1e-4)
    assert new["P_shaft_drag_W"] == pytest.approx(4.6 + R) and new["torque_side"] == "rotor"
    assert new["shaft_rotor_in_field"] is True and new["P_torque_embedded_W"] == pytest.approx(R)
    assert new["T_shaft_Nm"] == pytest.approx(p / w, abs=1e-3) and new["T_field_k_Nm"] == pytest.approx(0.36)
    # Kt / Km: the FIELD torque x k, no core-loss subtraction (owner Q1)
    assert new["Kt_Nm_per_Arms"] == pytest.approx(round(0.9 * 0.40 / 32.0, 4))
    assert old["Kt_Nm_per_Arms"] == pytest.approx(round(0.40 / 32.0, 4))
    assert new["torque_per_mass_Nm_kg"] == pytest.approx(p / w / old["mass_total_kg"], abs=2e-3)
    # the 2-D field torque, the losses and the voltages do not move
    for k in ("T_em_avg_Nm", "P_loss_total_W", "P_core_W", "V_line_peak_V", "mass_total_kg", "end3d"):
        assert new[k] == old[k], k
    # everything else is bit for bit the earlier summary
    for k in set(old) | set(new):
        if k in MOVED or k in NEW:
            continue
        assert new.get(k) == old.get(k), k
    assert set(new) - set(old) <= NEW


def test_generator_summary_input_power_and_field_torque_per_mass(build):
    new = build(_sb(-0.40))
    w = 2 * math.pi * 15000.0 / 60.0
    R = 1.3 + 0.01
    pin = 0.9 * (0.40 * w - R) + R + 4.6
    L = 110.0 + 4.6 + 1.3 + 0.01
    assert new["op_mode"] == "generator" and new["T_em_avg_Nm"] == pytest.approx(0.40)
    assert new["P_mech_W"] == pytest.approx(pin, abs=0.06)
    assert new["efficiency"] == pytest.approx((pin - L) / pin, abs=1e-4)
    assert new["torque_per_mass_Nm_kg"] == pytest.approx(0.36 / new["mass_total_kg"], abs=2e-3)


def test_no_load_summary_is_honest(build):
    new = build(_sb(1e-4))
    assert new["P_mech_W"] < 0 and new["efficiency"] == 0.0
    assert new.get("P_shaft_net_W") is None and new.get("efficiency_shaft") is None


def test_stator_side_torque_with_eddy_counts_the_copper_ac_once(build):
    new = build(_sb(0.40, method="energy_mean+maxwell_ripple"))
    w = 2 * math.pi * 15000.0 / 60.0
    assert new["torque_side"] == "stator" and new["P_cu_ac_field_W"] == pytest.approx(3.3)
    assert new["P_shaft_drag_W"] == pytest.approx(4.6 + 1.3 + 0.01, abs=0.06)
    # G = T*w - Cu_ac (the power into the field holds the solved copper AC); P = k*G - D
    assert new["P_mech_W"] == pytest.approx(0.9 * (0.40 * w - 3.3) - (4.6 + 1.31), abs=0.06)
    # P_elec closes on the 2-D losses: no watt twice
    assert new["P_elec_W"] == pytest.approx(new["P_mech_W"] + new["P_loss_total_W"], abs=0.2)


def test_a_restored_summary_keeps_its_convention(build, monkeypatch):
    from motor_ai_sim.routes import simulation as S
    args = {"I_phase_rms": 32.0, "gamma_deg": 0.0, "coil_temp_c": 120.0, "geo_override": None, "mode_requested": None}
    calls = []
    real = S._build_transient_summary
    monkeypatch.setattr(S, "_build_transient_summary", lambda *a, **k: calls.append(k.get("shaft_convention")) or {"x": 1})
    S._refresh_summary_shape({"summary": {"summary_shape_v": 1}, "_summary_args": args})
    S._refresh_summary_shape({"summary": {"summary_shape_v": 1, "shaft_convention": SC.SHAFT_CONVENTION},
                              "_summary_args": args})
    monkeypatch.setattr(S, "_build_transient_summary", real)
    assert calls == [None, "current"]


# ── the readers ──────────────────────────────────────────────────────────────────────────────────────────────────────
MARKED = {"shaft_convention": SC.SHAFT_CONVENTION, "op_mode": "motor", "rpm": 1000.0, "T_em_avg_Nm": 10.0,
          "T_field_k_Nm": 9.0, "T_shaft_Nm": (9.0 * W1000 - 30.0) / W1000, "P_field_W": 10.0 * W1000,
          "P_mech_W": 9.0 * W1000 - 30.0, "P_loss_total_W": 100.0, "P_core_W": 30.0, "shaft_k3d": 0.9,
          "torque_side": "rotor", "shaft_rotor_in_field": False,
          "P_shaft_drag_W": 30.0, "P_shaft_drag_terms": ["fe", "mag", "shaft", "sleeve"], "end3d": {"k_flux": 0.9}}


def test_report_shaft_view_applies_no_second_factor():
    from motor_ai_sim import report
    sv = report.shaft_view(MARKED, {"has_bearings": True, "P_mech_extra_W": 10.0}, "motor")
    p = MARKED["P_mech_W"]
    assert sv["k"] is None and sv["P_rotor_W"] == pytest.approx(p)
    assert sv["P_elec_W"] == pytest.approx(p + 100.0) and sv["P_shaft_W"] == pytest.approx(p - 10.0)
    assert sv["eta_shaft"] == pytest.approx((p - 10.0) / (p + 100.0))
    old = report.shaft_view({k: v for k, v in MARKED.items() if k != "shaft_convention"}, None, "motor")
    assert old["P_rotor_W"] == pytest.approx(p * 0.9)            # the earlier convention: |P| x k, unchanged
    idle = report.shaft_view({**MARKED, "P_mech_W": -20.0}, None, "motor")
    assert idle["P_rotor_W"] == pytest.approx(-20.0) and idle["eta_em"] is None


def test_report_recompute_keeps_the_convention():
    from motor_ai_sim import report
    out = dict(MARKED, T_em_avg_Nm=11.0, I_winding_rms_A=20.0, mass_total_kg=2.0)
    report._em_recompute_derived(out)
    p = 0.9 * 11.0 * W1000 - 30.0
    assert out["P_mech_W"] == pytest.approx(p, abs=0.06) and out["Kt_Nm_per_Arms"] == pytest.approx(0.9 * 11.0 / 20.0)
    assert out["torque_per_mass_Nm_kg"] == pytest.approx(p / W1000 / 2.0, rel=1e-3)
    old = {"T_em_avg_Nm": 11.0, "rpm": 1000.0, "I_winding_rms_A": 20.0, "mass_total_kg": 2.0}
    report._em_recompute_derived(old)
    assert old["P_mech_W"] == 11.0 * 2.0 * math.pi * 1000.0 / 60.0 and old["Kt_Nm_per_Arms"] == 11.0 / 20.0


def test_report_compare_rows_name_a_mixed_convention():
    from motor_ai_sim import report
    assert report.SHAFT_CONV_WORDS_NEW and report.SHAFT_CONV_WORDS_OLD
    assert report._sc_marked(MARKED) and not report._sc_marked({"P_mech_W": 1.0})


def test_coupled_hold_uses_the_shaft_torque_of_a_motor_and_the_field_torque_of_a_generator():
    from motor_ai_sim.routes import coupled
    t, note = coupled._shaft_torque_nm(MARKED)
    assert t == pytest.approx(MARKED["T_shaft_Nm"]) and "core loss" in note
    g = dict(MARKED, op_mode="generator", T_shaft_Nm=12.0)
    assert coupled._shaft_torque_nm(g)[0] == pytest.approx(9.0)
    # an unmarked summary: the field torque x k, as before
    assert coupled._shaft_torque_nm({"T_em_avg_Nm": 10.0, "end3d": {"k_flux": 0.9}})[0] == pytest.approx(9.0)


def test_optimiser_gate_and_cache_version():
    from motor_ai_sim.routes import optimization as O
    pt = {"shaft_convention": SC.SHAFT_CONVENTION, "op_mode": "motor", "T_em_Nm": 1.0, "T_shaft_Nm": 0.95}
    assert O._cogging_limits_ok(pt, 0.0, 0.0, 0.97) is False          # shaft torque below the floor
    assert O._cogging_limits_ok(dict(pt, op_mode="generator"), 0.0, 0.0, 0.97) is True
    assert O._cogging_limits_ok({"T_em_Nm": 1.0}, 0.0, 0.0, 0.97) is True
    src = Path(O.__file__).read_text(encoding="utf-8")
    assert '"v": 8,' in src and "if p.get(\"shaft_convention\") != _SHAFT_CONVENTION:" in src


def test_presets_card_power_is_never_the_diagnostic_balance():
    from motor_ai_sim.routes import presets
    e: dict = {}
    presets._enrich_card_entry(e, {}, {"rpm": 1000.0},
                               {"T_avg_Nm": 10.0, "P_mech_avg_W": -500.0, "P_mech_W": -1047.2, "efficiency": 0.9})
    assert e["power_w"] == pytest.approx(1047.2)
    m = presets._applied_metrics({"P_mech_W": 5.0, "P_mech_avg_W": 9.0, "shaft_convention": SC.SHAFT_CONVENTION})
    assert m["P_mech_W"] == 5.0 and m["shaft_conv"] == 1
    assert "shaft_conv" not in presets._applied_metrics({"P_mech_avg_W": 9.0}) and \
        "P_mech_W" not in presets._applied_metrics({"P_mech_avg_W": 9.0})


def test_refine_proc_row_is_on_the_convention():
    src = Path(__file__).resolve().parent.parent / "src" / "motor_ai_sim" / "optimization" / "refine_proc.py"
    text = src.read_text(encoding="utf-8")
    assert "**_SC.fields(_bal, _dr, None)" in text        # the row's convention fields, k = 1 (no passport)


# ── Astra review of PR #900: B2-B7 ───────────────────────────────────────────────────────────────────────────────────
def test_b3_pwm_iron_selection_moves_the_shaft_side_with_the_losses():
    from motor_ai_sim import coupled_pwm_final as PF
    s = _marked(torque_side="rotor", shaft_rotor_in_field=False, P_mag_W=0.0, P_core_rotor_W=10.0,
                P_loss_rotor_W=10.0, P_loss_total_W=100.0, efficiency=0.5)
    SC.rederive(s)
    out = PF.apply_to_summary(s, {"raw_W": 10.0, "selected_W": 0.0})        # rotor iron 10 W -> 0: core 30 -> 20
    p = 0.9 * 10.0 * W1000 - 20.0
    assert out["P_core_W"] == pytest.approx(20.0) and out["P_shaft_drag_W"] == pytest.approx(20.0)
    assert out["P_mech_W"] == pytest.approx(p, abs=0.06)
    assert out["P_elec_W"] == pytest.approx(out["P_mech_W"] + out["P_loss_total_W"], abs=0.11)
    assert out["efficiency"] == pytest.approx(p / (p + 90.0), abs=1e-4)


def test_b3_s1_linear_rating_rebuilds_d_from_the_same_losses():
    from motor_ai_sim import coupled_continuous_rating as CR
    src = Path(CR.__file__).read_text(encoding="utf-8")
    # D = every held non-copper loss of THIS point minus the bearing/windage watts the thermal nodes carry
    # (the behaviour itself: test_r2_b3_rating_does_not_count_bearings_as_em_drag)
    assert "d_w=_p_other_em, p_loss_w=p_loss_em" in src


def test_b2_report_record_overlay_keeps_one_convention():
    from motor_ai_sim import report
    sine = _marked(); SC.rederive(sine)
    old_rec = {"em": {"T_em_avg_Nm": 10.0, "P_loss_total_W": 100.0, "P_core_W": 30.0, "efficiency": 0.9}}
    out = report._em_from_coupled(dict(sine), old_rec)
    assert not SC.has_marker(out)                              # an unmarked record: the earlier convention, whole
    assert out["P_mech_W"] == pytest.approx(10.0 * W1000)
    new_rec = {"em": {k: sine[k] for k in report._PWM_EM_KEYS if k in sine}}
    out2 = report._em_from_coupled({"T_em_avg_Nm": 10.0, "rpm": 1000.0}, new_rec)
    assert SC.has_marker(out2) and out2["torque_side"] == "rotor"


def test_b2_coupled_record_carries_the_convention():
    from motor_ai_sim.routes import coupled
    for k in SC.CONVENTION_KEYS + ("P_mech_W", "op_mode"):
        assert k in coupled._EM_SHAFT_KEYS


def test_b4_headless_memo_and_warm_start_isolate_the_convention():
    from motor_ai_sim import optimize_headless as H
    assert '["shaft_convention", SHAFT_CONVENTION]' in Path(H.__file__).read_text(encoding="utf-8")
    from motor_ai_sim.routes import optimization as O
    m = O._msum({"shaft_convention": SC.SHAFT_CONVENTION, "T_shaft_Nm": 1.0, "op_mode": "motor"})
    assert m["shaft_convention"] == SC.SHAFT_CONVENTION and m["T_shaft_Nm"] == 1.0
    a = O._auto_result_metrics({"shaft_convention": SC.SHAFT_CONVENTION, "efficiency": 0.9}, 1000.0, "g")
    assert a["shaft_conv"] == 1
    p = O._pt({"ok": True, "res": {"shaft_convention": SC.SHAFT_CONVENTION, "T_em_Nm": 1.0}}, "x")
    assert p["shaft_conv"] == 1
    old = {"cfg_fp": "f", "overrides": {"a": 1.0}, "current_a": 10.0, "gamma_deg": 0.0, "ripple": 1.0,
           "td": 1.0, "eff": 0.9}
    base = {"torque_per_mass_Nm_kg": 1.0, "efficiency": 0.9}
    # an entirely earlier-convention history keeps its earlier warm start
    assert O._auto_warm_start([old] * 5, [{"name": "a", "x0": 1.0}], "f", 10.0, 0.0, 10.0, base)["n"] == 5
    marked = {**old, "overrides": {"a": 2.0}, "shaft_conv": 1, "td": 4.35, "eff": 870 / 970}
    marked_base = {"torque_per_mass_Nm_kg": 4.35, "efficiency": 870 / 970,
                   "shaft_convention": SC.SHAFT_CONVENTION,
                   "_bline": {"shaft_conv": 1, "td_a": 4.35, "eff_a": 870 / 970,
                              "w_td": 0.01, "w_eff": 0.5, "norm": math.hypot(0.01, 0.5)}}
    # Only matching marked rows are ranked, and one row is still too few to seed.
    assert O._auto_warm_start([old] * 5 + [marked], [{"name": "a", "x0": 1.0}], "f", 10.0, 0.0, 10.0, marked_base) is None
    s3 = O._auto_warm_start([old] * 5 + [marked] * 3, [{"name": "a", "x0": 1.0}], "f", 10.0, 0.0, 10.0, marked_base)
    assert s3["n"] == 3 and s3["x"] == [2.0]
    assert s3["F"] == pytest.approx(0.0)
    # a marked baseline never ranks earlier-convention rows
    assert O._auto_warm_start([old] * 5, [{"name": "a", "x0": 1.0}], "f", 10.0, 0.0, 10.0,
                              {**base, "_bline": {"shaft_conv": 1}}) is None


def test_b6_restamp_uses_the_live_numerator(monkeypatch):
    from motor_ai_sim.modules import motor_mass as MM
    monkeypatch.setattr(MM, "run_mass_fields", lambda *a, **k: {"mass_full_kg": 2.0})
    s = _marked(T_shaft_Nm=8.7, T_field_k_Nm=9.0, mass_total_kg=1.0, P_mech_W=870.0, Km_Nm_sqrtW=0.9)
    MM.stamp_run_summary(s, "d", "c")
    assert s["torque_per_mass_full_Nm_kg"] == pytest.approx(4.35)
    g = _marked(op_mode="generator", T_shaft_Nm=9.3, T_field_k_Nm=9.0, mass_total_kg=1.0, P_mech_W=930.0)
    MM.stamp_run_summary(g, "d", "c")
    assert g["torque_per_mass_full_Nm_kg"] == pytest.approx(4.5)
    old = {"T_em_avg_Nm": 10.0, "mass_total_kg": 1.0}
    MM.stamp_run_summary(old, "d", "c")
    assert old["torque_per_mass_full_Nm_kg"] == pytest.approx(5.0)


def test_b7_mechanical_and_customer_match_use_the_shaft_torque():
    from motor_ai_sim.routes import mechanical as ME
    from motor_ai_sim import report_customer as RC
    s = _marked(T_shaft_Nm=8.7, T_field_k_Nm=9.0)
    assert ME._torque_of_block(s) == pytest.approx(8.7)
    assert ME._torque_of_block({"T_em_avg_Nm": 10.0, "end3d": {"k_flux": 0.9}}) == pytest.approx(9.0)
    # Astra r2: a shaft requirement is matched at the coupling - motor 8.7 (bearings unknown), 8.5 with X = 20 W;
    # a generator's INPUT 9.3, not its field torque 9 (omega = 100 rad/s)
    m = _marked(rpm=RPM100, P_mech_W=870.0, T_shaft_Nm=8.7, T_field_k_Nm=9.0)
    assert RC._torque({"em": m}) == pytest.approx(8.7)
    assert RC._torque({"em": dict(m, P_mech_extra_W=20.0)}) == pytest.approx(8.5)
    g = _marked(rpm=RPM100, op_mode="generator", P_mech_W=930.0, T_shaft_Nm=9.3, T_field_k_Nm=9.0)
    assert RC._torque({"em": g}) == pytest.approx(9.3)
    assert RC._torque({"em": dict(g, P_mech_extra_W=20.0)}) == pytest.approx(9.5)



# ── Astra review round 2 ─────────────────────────────────────────────────────────────────────────────────────────────
def _plain_marked(**over):
    """omega 100 rad/s, |T| 10, k 0.9, Fe 30, L 100, nothing embedded (Astra r2's common case)."""
    s = {"shaft_convention": SC.SHAFT_CONVENTION, "op_mode": "motor", "rpm": RPM100, "T_em_avg_Nm": 10.0,
         "shaft_k3d": 0.9, "torque_side": "rotor", "shaft_rotor_in_field": False, "P_loss_total_W": 100.0,
         "P_core_W": 30.0, "P_mag_W": 0.0, "P_shaft_W": 0.0, "P_sleeve_W": 0.0}
    s.update(over)
    SC.rederive(s)
    return s


def test_r2_b2_overlay_carries_mode_and_speed():
    from motor_ai_sim.development import motor_view as MV
    base = _plain_marked(mass_total_kg=2.0)
    before = dict(base)
    gen = _plain_marked(op_mode="generator", rpm=2.0 * RPM100)     # a DIFFERENT speed: it must travel with the run
    assert gen["P_mech_W"] == pytest.approx(0.9 * 10.0 * 200.0 + 30.0, abs=0.06)
    SC.overlay(base, gen)
    base["T_em_avg_Nm"] = gen["T_em_avg_Nm"]
    MV._rescale_to_loop(base, before)
    assert base["op_mode"] == "generator" and base["rpm"] == pytest.approx(2.0 * RPM100)
    assert base["P_mech_W"] == pytest.approx(1830.0, abs=0.06)
    assert base["T_shaft_Nm"] == pytest.approx(9.15, abs=1e-3)


def test_r2_b2_density_rebuilt_from_the_selected_torque():
    from motor_ai_sim.development import motor_view as MV
    # an earlier-convention saved run with its own losses (a real saved summary carries them; the loop overlay
    # replaces them with the loop's, here the same)
    base = {"op_mode": "motor", "rpm": RPM100, "T_em_avg_Nm": 10.0, "P_mech_W": 1000.0, "mass_total_kg": 2.0,
            "torque_per_mass_Nm_kg": 5.0, "power_per_mass_W_kg": 500.0, "P_core_W": 30.0, "P_mag_W": 0.0,
            "P_shaft_W": 0.0, "P_sleeve_W": 0.0, "P_loss_total_W": 100.0}
    before = dict(base)
    SC.overlay(base, _plain_marked())
    MV._rescale_to_loop(base, before)
    assert base["torque_per_mass_Nm_kg"] == pytest.approx(4.35, abs=1e-3)
    assert base["power_per_mass_W_kg"] == pytest.approx(435.0, abs=0.05)


def test_r2_b2_light_panel_keeps_the_convention():
    from motor_ai_sim.development import motor_view as MV
    s = _plain_marked()
    lp = MV.light_panel({"summary": s, "source": {}})["summary"]
    for k in ("shaft_convention", "torque_side", "shaft_rotor_in_field", "op_mode"):
        assert lp.get(k) == s.get(k), k


def test_r2_b3_rating_does_not_count_bearings_as_em_drag():
    from motor_ai_sim import coupled_continuous_rating as CR
    ref = {"T_em_avg_Nm": 10.0, "rpm": RPM100, "k_flux": 0.9, "shaft_k3d": 0.9,
           "shaft_convention": SC.SHAFT_CONVENTION, "torque_side": "rotor", "shaft_rotor_in_field": False,
           "op_mode": "motor", "P_mech_extra_W": 10.0}
    loss = {"winding": 70.0, "stator": 30.0, "rotor": 10.0, "magnet": 0.0, "coil_ref_c": 20.0,
            "split": {"rotor_solid_W": 0.0}, "mech_in_nodes_W": 10.0, "rpm": RPM100}
    out = CR._power_block(ref, loss, 70.0, 1.0, 20.0, mode="motor")
    assert out["P_mech_W"] == pytest.approx(870.0, abs=0.01)          # k*T*w - Fe
    assert out["P_shaft_W"] == pytest.approx(860.0, abs=0.01)         # minus the bearings once
    assert out["P_elec_W"] == pytest.approx(970.0, abs=0.01)          # + Cu + Fe


def test_r2_b3_pwm_updates_net_and_full_mass():
    from motor_ai_sim import coupled_pwm_final as PF
    s = _plain_marked(P_core_rotor_W=10.0, P_loss_rotor_W=10.0, efficiency=0.5, P_mech_extra_W=20.0,
                      P_shaft_net_W=850.0, efficiency_shaft=0.8, mass_total_kg=1.0, mass_full_kg=2.0)
    out = PF.apply_to_summary(s, {"raw_W": 10.0, "selected_W": 0.0})
    assert out["P_mech_W"] == pytest.approx(880.0, abs=0.06)
    assert out["P_shaft_net_W"] == pytest.approx(860.0, abs=0.06)
    assert out["power_per_mass_full_W_kg"] == pytest.approx(440.0, abs=0.06)
    assert out["torque_per_mass_full_Nm_kg"] == pytest.approx(4.40, abs=1e-3)


def test_r2_thresholds_of_the_earlier_convention():
    # 0.5 W of shaft output beside 0.25 W of loss: no efficiency from sub-watt powers, as before
    b = SC.balance(t_field_nm=0.75 / W1000, rpm=1000.0, k=None, d_w=0.25, p_loss_w=0.25)
    assert b["P_mech_W"] == pytest.approx(0.5) and b["efficiency"] == 0.0


def test_r2_negative_power_is_not_made_positive():
    from motor_ai_sim.routes import presets
    e: dict = {}
    presets._enrich_card_entry(e, {}, {"rpm": 1000.0}, {"T_avg_Nm": 0.01, "P_mech_W": -30.0, "efficiency": 0.0,
                                                       "shaft_convention": SC.SHAFT_CONVENTION, "op_mode": "motor"})
    assert e["power_w"] == pytest.approx(-30.0)
    src = (Path(__file__).resolve().parent.parent / "src" / "motor_ai_sim" / "datasheet.py").read_text(encoding="utf-8")
    assert 'if _g(d, "summary.shaft_convention") == "fe_on_shaft_k3d_v1":\n            return float(w) / 1000.0' in src


def test_r2_journal_and_passport_keep_provenance():
    root = Path(__file__).resolve().parent.parent / "src" / "motor_ai_sim"
    sim = (root / "routes" / "simulation.py").read_text(encoding="utf-8")
    assert '**({"shaft_conv": 1, "shaft_basis":' in sim
    fam = (root / "routes" / "family.py").read_text(encoding="utf-8")
    assert 'a 3-D passport measured after this run is not applied to its numbers' in fam


def test_r2_rederive_uses_the_full_precision_torque():
    s = _plain_marked(T_em_avg_Nm=0.128, T_field_Nm=0.12784, rpm=1900.0, P_core_W=3.2, P_loss_total_W=4.4,
                      op_mode="generator")
    w = 2 * math.pi * 1900.0 / 60.0
    assert s["P_mech_W"] == pytest.approx(0.9 * 0.12784 * w + 3.2, abs=0.06)
    # the rounded torque (0.128) would give 0.1152: the stored field torque x k tells the two apart
    assert s["T_field_k_Nm"] == pytest.approx(0.9 * 0.12784, abs=5e-5)



# ── Astra review round 3 (each example with its numbers) ──────────────────────────────────────────────────────────────
def test_r3_b2_project_table_takes_the_marked_numbers_as_they_are():
    from motor_ai_sim.development import motors as DM
    m = _plain_marked(mass_total_kg=2.0, efficiency_shaft=None, end3d={"k_flux": 0.9})
    c = DM._comparison_projection({"summary": m}, {})
    assert c["power_w"] == pytest.approx(870.0, abs=0.06) and c["torque_nm"] == pytest.approx(8.7, abs=1e-3)
    assert c["torque_to_mass_nm_kg"] == pytest.approx(4.35, abs=1e-3) and c["shaft_conv"] == 1
    g = _plain_marked(op_mode="generator", mass_total_kg=2.0, end3d={"k_flux": 0.9})
    cg = DM._comparison_projection({"summary": g}, {})
    assert cg["power_w"] == pytest.approx(930.0, abs=0.06) and cg["torque_nm"] == pytest.approx(9.3, abs=1e-3)
    # a marked result block alone keeps its own numbers and its code
    cr = DM._comparison_projection({"result": {"shaft_conv": 1, "power_w": 870.0, "efficiency_pct": 89.69}}, {})
    assert cr["shaft_conv"] == 1 and cr["power_w"] == 870.0 and cr["efficiency_pct"] == 89.69
    # an OLD result beside a marked summary: the row takes the summary's numbers, never the old efficiency
    co = DM._comparison_projection({"summary": m, "result": {"efficiency_pct": 95.0}}, {})
    assert co["efficiency_pct"] == pytest.approx(100.0 * m["efficiency"], abs=1e-6)
    # an unmarked row is the earlier convention, unchanged and unstamped
    cu = DM._comparison_projection({"summary": {"T_em_avg_Nm": 10.0, "P_mech_W": 1000.0, "end3d": {"k_flux": 0.9}}}, {})
    assert "shaft_conv" not in cu and cu["power_w"] == pytest.approx(900.0)


def test_r3_b2_reverse_overlay_keeps_the_old_run_s_mode_and_speed():
    from motor_ai_sim.development import motor_view as MV
    base = _plain_marked(mass_total_kg=2.0)                      # marked motor at 100 rad/s
    before = dict(base)
    old_gen = {"T_em_avg_Nm": 10.0, "op_mode": "generator", "rpm": 2.0 * RPM100}
    SC.overlay(base, old_gen)
    base["T_em_avg_Nm"] = 10.0
    MV._rescale_to_loop(base, before)
    assert not SC.has_marker(base) and base["op_mode"] == "generator"
    assert base["rpm"] == pytest.approx(2.0 * RPM100) and base["P_mech_W"] == pytest.approx(2000.0, abs=1e-6)


def test_r3_b3_summary_shaft_net_is_signed(monkeypatch):
    from motor_ai_sim.routes import simulation as S
    from motor_ai_sim import mech_losses as ML, bearings as BR
    monkeypatch.setattr(ML, "machine_bearings", lambda: ({"x": 1}, "d", "c"))
    monkeypatch.setattr(BR, "has_bearings", lambda a: True)
    monkeypatch.setattr(ML, "rotor_mass_from_summary", lambda s: 1.0)
    monkeypatch.setattr(ML, "machine_mech_losses", lambda **k: {"P_bearings_W": 15.0, "P_windage_W": 5.0,
                                                                "P_mech_extra_W": 20.0})
    monkeypatch.setattr(ML, "summary_block", lambda m: {})
    out = S._mech_loss_fields({}, geo_override=None, rpm=1000.0, p_mech_w=-30.0, p_loss_w=40.0, efficiency=0.0,
                              op_mode="motor", signed=True)
    assert out["P_shaft_net_W"] == pytest.approx(-50.0) and out["efficiency_shaft"] == 0.0
    pos = S._mech_loss_fields({}, geo_override=None, rpm=1000.0, p_mech_w=870.0, p_loss_w=100.0,
                              efficiency=870.0 / 970.0, op_mode="motor", signed=True)
    assert pos["P_shaft_net_W"] == pytest.approx(850.0) and pos["efficiency_shaft"] == pytest.approx(850.0 / 970.0, abs=1e-6)


def test_r3_b3_pwm_net_goes_through_zero_signed():
    from motor_ai_sim import coupled_pwm_final as PF
    # k*T*w = 36 W (k 0.9, T 0.4 N*m at 100 rad/s), core 30 -> 40 W, X 20 W: P = 36 - 40 = -4 W, net -24 W
    s = _plain_marked(T_em_avg_Nm=0.4, P_core_W=30.0, P_core_rotor_W=10.0, P_loss_rotor_W=10.0, P_loss_total_W=31.0,
                      efficiency=0.0, P_mech_extra_W=20.0, P_shaft_net_W=-14.0, efficiency_shaft=0.0)
    out = PF.apply_to_summary(s, {"raw_W": 10.0, "selected_W": 20.0})
    assert out["P_core_W"] == pytest.approx(40.0)
    assert out["P_mech_W"] == pytest.approx(-4.0, abs=0.06) and out["P_shaft_net_W"] == pytest.approx(-24.0, abs=0.06)
    assert out["efficiency_shaft"] == 0.0


def test_r3_b7_driven_shaft_never_meets_an_output_requirement():
    from motor_ai_sim import report_customer as RC
    idle = _plain_marked(T_em_avg_Nm=0.0, P_mech_W=-30.0)
    idle["P_mech_W"] = -30.0
    idle["P_mech_extra_W"] = 20.0
    t = RC._torque({"em": idle})
    assert t == pytest.approx(-0.5)
    assert not (t >= 0.4 * (1.0 - RC.TORQUE_TOL_REL))


def test_r3_b7_mechanical_fallback_is_the_coupling_load():
    from motor_ai_sim.routes import mechanical as ME
    m = _plain_marked(P_mech_extra_W=20.0)
    assert ME._torque_of_block(m) == pytest.approx(8.5, abs=1e-3)
    g = _plain_marked(op_mode="generator", P_mech_extra_W=20.0)
    assert ME._torque_of_block(g) == pytest.approx(9.5, abs=1e-3)


def test_r3_b3_bearing_heat_through_the_real_loss_adapter():
    from motor_ai_sim import coupled_continuous_rating as CR, thermal_duty_cycle as TD
    summ = {"P_stranded_W": 70.0, "P_core_W": 30.0, "P_solid_W": 0.0, "coil_temp_C": 20.0, "rpm": RPM100}
    th = {"cooling": {"mech_losses": {"P_bearings_W": 10.0, "shaft_ends_open": True}}, "P_mag_eddy_W": 0.0}
    loss = TD.losses_by_node(summ, th)
    assert loss["mech_in_nodes_W"] == pytest.approx(10.0)
    ref = {"T_em_avg_Nm": 10.0, "rpm": RPM100, "k_flux": 0.9, "shaft_k3d": 0.9, "shaft_convention": SC.SHAFT_CONVENTION,
           "torque_side": "rotor", "shaft_rotor_in_field": False, "op_mode": "motor", "P_mech_extra_W": 10.0}
    out = CR._power_block(ref, loss, 70.0, 1.0, 20.0, mode="motor")
    assert out["P_mech_W"] == pytest.approx(870.0, abs=0.01) and out["P_shaft_W"] == pytest.approx(860.0, abs=0.01)
    assert out["P_elec_W"] == pytest.approx(970.0, abs=0.01)


def test_r3_provenance_in_scalar_exports():
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim.digest import readable as RD
    row = DR.compact_em_run({"summary": _plain_marked()})
    assert row["numbers"]["shaft_conv"] == 1.0
    mech = RD.mechanical_numbers({"em_run": row})
    assert mech["basis"] == "shaft" and "core loss on the shaft" in RD.headline({"em_run": row})
    old = DR.compact_em_run({"summary": {"P_mech_W": 1000.0, "T_em_avg_Nm": 10.0}})
    assert "shaft_conv" not in old["numbers"] and RD.mechanical_numbers({"em_run": old})["basis"] == "earlier"


# ── coordinator 2026-11-26: the bench comparison and the controller tab ───────────────────────────────────────────────
def _bench_doc(summary):
    return {"winding": {"star_delta": "star"}, "duties": [{"name": "rated", "rpm": summary["rpm"], "current_arms": 100,
                                                           "summary": {"I_phase_rms_A": 100, **summary}}]}


def test_bench_comparison_takes_a_marked_duty_as_stored():
    from motor_ai_sim.motor_tests import comparison, model_source, models
    s = _plain_marked(mass_total_kg=2.0)
    assert s["P_mech_W"] == pytest.approx(870.0, abs=0.06)
    m = model_source.from_docs({}, _bench_doc(s), {}, {}, end3d={"k_flux": 0.9})
    d = m["duties"][0]
    assert d["shaft_conv"] == 1 and d["torque_nm"] == pytest.approx(8.7, abs=1e-3) and d["torque_kind"] == "shaft"
    rec = models.normalize({"kind": "load", "date": "2026-01-15",
                            "rows": [{"rpm": RPM100, "i_phase_a": 100, "torque_nm": 8.6, "eff_pct": 89.0}]},
                           who="eng@example.test")
    g = comparison.compare_record(rec, m, "D", "C")["groups"][0]
    rows = {r["key"]: r for r in g["rows"]}
    # 8.7 N*m (870 W at 100 rad/s) and 870/970 - never x0.9 again (7.83 N*m / 783 W)
    assert rows["torque"]["model"] == pytest.approx(8.7, abs=1e-3)
    assert rows["eff"]["model"] == pytest.approx(100.0 * 870.0 / 970.0, abs=0.01)
    assert rows["torque"]["extra"]["corrected"] is True and g["basis_key"] == "load_3d"


def test_controller_torque_on_a_marked_record_is_the_coupling_torque(monkeypatch):
    from motor_ai_sim import duty_results as dr
    from motor_ai_sim.routes import controller as rc, family as fam
    em = _plain_marked(P_mech_extra_W=20.0)
    node = {"coupled": {"mode": "steady", "em": em, "P_mech_extra_W": 20.0,
                        "inverter": {"I_phase_rms_solved_A": 50.0, "star_delta": "star", "v_dc_V": 400.0}},
            "thermal": {"point": {"rpm": RPM100}}}
    monkeypatch.setattr(rc, "_DR", dr)
    monkeypatch.setattr(dr, "get", lambda die, cfg: {"rated": node})
    monkeypatch.setattr(fam, "config_doc", lambda die, cfg: {})
    monkeypatch.setattr(fam, "duty_entry", lambda die, cfg, duty: {})
    out = rc._duty_defaults("DIE", "CFG", "rated")
    assert out["torque_Nm"] == pytest.approx(8.5, abs=1e-3)          # (870 - 20) W / 100 rad/s, not the 10 N*m field torque
    assert out["shaft_conv"] == 1
    em.pop("P_mech_W")
    assert "torque_Nm" not in rc._duty_defaults("DIE", "CFG", "rated")
    old = {"T_em_avg_Nm": 10.0, "P_loss_total_W": 100.0}
    node["coupled"]["em"] = old
    assert rc._duty_defaults("DIE", "CFG", "rated")["torque_Nm"] == pytest.approx(10.0)


# ── Astra review round 4 (each example with its numbers) ──────────────────────────────────────────────────────────────
def test_r4_c1_compact_record_keeps_the_convention_context():
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim.motor_tests import model_source
    for mode, t_exp in (("motor", 8.7), ("generator", 9.3)):
        s = _plain_marked(op_mode=mode, mass_total_kg=2.0)
        row = DR.compact_em_run({"summary": s})
        ctx = row["shaft_context"]
        assert ctx["shaft_convention"] == SC.SHAFT_CONVENTION and ctx["shaft_rotor_in_field"] is False
        assert ctx["op_mode"] == mode and isinstance(ctx["torque_side"], str)
        back = SC.summary_from_compact(row)
        assert SC.coupling_power(back) == pytest.approx(SC.coupling_power(s))
        doc = {"winding": {"star_delta": "star"}, "duties": [{"name": "rated", "rpm": RPM100, "current_arms": 100}]}
        m = model_source.from_docs({}, doc, {"rated": {"em_run": row}}, {}, end3d={"k_flux": 0.9})
        d = m["duties"][0]
        if mode == "motor":
            assert d["torque_nm"] == pytest.approx(t_exp, abs=1e-3)      # not 9 N*m (a second k on 870 W)
            assert d["eff_pct"] == pytest.approx(100.0 * 870.0 / 970.0, abs=0.01)   # not 88.67 %
        assert d["shaft_conv"] == 1 and d["shaft_k3d"] == pytest.approx(0.9)
    # an unmarked compact row is filed exactly as before (no context key)
    assert "shaft_context" not in DR.compact_em_run({"summary": {"T_em_avg_Nm": 10.0, "P_mech_W": 1000.0}})
    # a marked compact row WITHOUT the context is unreadable, never "old"
    bare = {"numbers": {"T_em_avg_Nm": 10.0, "P_mech_W": 870.0, "shaft_conv": 1.0}}
    assert SC.summary_from_compact(bare) == {}
    doc = {"winding": {"star_delta": "star"}, "duties": [{"name": "rated", "rpm": RPM100, "current_arms": 100}]}
    m2 = model_source.from_docs({}, doc, {"rated": {"em_run": bare}}, {}, end3d={"k_flux": 0.9})
    assert all(x.get("torque_nm") is None for x in m2.get("duties") or [])


def test_r4_c1_light_panel_from_a_compact_record_is_marked():
    from motor_ai_sim import duty_results as DR
    from motor_ai_sim.development import motor_view as MV
    row = DR.compact_em_run({"summary": _plain_marked(mass_total_kg=2.0)})
    blk = MV.em_run_block({"em_run": row})
    assert blk["shaft_context"]["shaft_convention"] == SC.SHAFT_CONVENTION
    panel = MV.build_panel(None, {}, None, blk)
    summ = panel["summary"]
    assert SC.has_marker(summ) and summ["T_shaft_Nm"] == pytest.approx(8.7, abs=1e-3)
    assert MV.em_run_block({"em_run": {"numbers": {"T_em_avg_Nm": 10.0, "shaft_conv": 1.0}}}) is None


def test_r4_c2_zero_shaft_efficiency_stays_zero():
    from motor_ai_sim.motor_tests import model_source
    s = {"shaft_convention": SC.SHAFT_CONVENTION, "op_mode": "motor", "rpm": RPM100, "I_phase_rms_A": 100,
         "T_em_avg_Nm": 1.0, "P_mech_W": 6.0, "P_loss_total_W": 31.0, "P_mech_extra_W": 20.0, "P_shaft_net_W": -14.0,
         "efficiency_shaft": 0.0, "efficiency": 6.0 / 37.0}
    doc = {"winding": {"star_delta": "star"}, "duties": [{"name": "rated", "rpm": RPM100, "current_arms": 100,
                                                           "summary": s}]}
    d = model_source.from_docs({}, doc, {}, {})["duties"][0]
    assert d["eff_pct"] == 0.0                                              # not 16.22 %
    assert d["torque_nm"] == pytest.approx(-0.14, abs=1e-6)


def test_r4_c3_projection_one_source_missing_stays_missing():
    from motor_ai_sim.development import motors as DM
    em = _plain_marked(mass_total_kg=2.0)
    c = DM._comparison_projection({}, {"coupled": {"em": em, "P_mech_extra_W": 20.0, "efficiency_shaft": 850.0 / 970.0}})
    assert c["power_w"] == pytest.approx(850.0, abs=0.06) and c["torque_nm"] == pytest.approx(8.5, abs=1e-3)
    assert c["efficiency_pct"] == pytest.approx(87.628866, abs=1e-4) and c["shaft_conv"] == 1
    # a marked source without an efficiency (friction known): no efficiency, never the old result's 95 %
    m = _plain_marked(P_mech_extra_W=20.0)
    m.pop("efficiency_shaft", None)
    c2 = DM._comparison_projection({"summary": m, "result": {"efficiency_pct": 95.0}}, {})
    assert "efficiency_pct" not in c2
    # a marked result without torque density: none, never the old summary's 5 N*m/kg
    c3 = DM._comparison_projection({"result": {"shaft_conv": 1, "power_w": 870.0},
                                    "summary": {"T_em_avg_Nm": 10.0, "mass_total_kg": 2.0}}, {})
    assert "torque_to_mass_nm_kg" not in c3 and c3["power_w"] == 870.0


def test_r4_c4_digest_headline_from_one_record():
    from motor_ai_sim.digest import readable as RD
    kinds = {"em_run": {"numbers": {"P_mech_W": 1000.0, "T_em_avg_Nm": 10.0}},
             "coupled": {"em": _plain_marked(), "efficiency_shaft": 0.876289}}
    mech = RD.mechanical_numbers(kinds)
    assert mech["T"] == 10.0 and mech["P"] == 1000.0 and mech["eta"] is None and mech["basis"] == "earlier"
    h = RD.headline(kinds)
    assert "87.6" not in h and "shaft basis" not in h                     # unmarked on its own: its earlier text
    assert "earlier shaft basis" in RD.headline(kinds, mixed=True)          # listed with marked lines: named
    k2 = {"coupled": {"em": _plain_marked(), "efficiency_shaft": 0.876289}}
    m2 = RD.mechanical_numbers(k2)
    assert m2["basis"] == "shaft" and m2["eta"] == pytest.approx(87.6289, abs=1e-3)


def test_r4_c6_agent_headline_keeps_the_convention():
    from motor_ai_sim import agent_designs as AD
    d = {"params": {"speed_rpm": RPM100, "mode": "generator", "current_a_rms": 100.0}}
    out = AD.headline({"em": _plain_marked(op_mode="generator"), "P_mech_extra_W": 20.0}, d, "coupled")
    assert out["torque_nm"] == pytest.approx(10.0) and out["torque_basis"] == "2-D field"
    assert out["power_kw"] == pytest.approx(0.93, abs=1e-3) and out["shaft_conv"] == 1
    assert out["efficiency_shaft_pct"] == pytest.approx(87.37, abs=0.01)    # not 91.21 %


def test_r4_snapshot_carries_the_convention_only_when_marked():
    import inspect
    from motor_ai_sim.passport_v1 import snapshot as SN
    src = inspect.getsource(SN)
    assert '("shaft_convention", "shaft_k3d", "op_mode")' in src


def test_r5_r1_old_primary_never_borrows_marked_efficiency():
    from motor_ai_sim.development.motors import _comparison_projection
    cp = {"coupled": {"em": _plain_marked(), "P_mech_extra_W": 20, "efficiency_shaft": 850 / 970}}
    for duty in ({"summary": {"T_em_avg_Nm": 10, "P_mech_W": 1000, "mass_total_kg": 2}},
                 {"result": {"torque_nm": 10, "power_w": 1000}}):
        out = _comparison_projection(duty, cp)
        assert out["torque_nm"] == 10 and out["power_w"] == 1000
        assert "efficiency_pct" not in out and "shaft_conv" not in out
    old = {"summary": {"T_em_avg_Nm": 10, "P_mech_W": 1000, "efficiency": .9}}
    assert _comparison_projection(old, cp)["efficiency_pct"] == 90


@pytest.mark.parametrize("mode,power,torque", [("motor", 850, 8.5), ("generator", 950, 9.5)])
def test_r5_r2_bilingual_result_uses_coupling_power(mode, power, torque):
    from motor_ai_sim.digest.readable import mechanical_numbers, result_line
    from motor_ai_sim.duty_results import compact_em_run
    em = _plain_marked(op_mode=mode)
    eta = 850 / 970 if mode == "motor" else 830 / 950
    cp = {"em": em, "P_mech_extra_W": 20, "efficiency_shaft": eta,
          "coil_temp_c": 60, "magnet_temp_c": 50, "converged": True,
          "hold_torque": {"basis": "held_torque", "torque_nm": torque, "target_nm": torque, "held": True}}
    m = mechanical_numbers({"coupled": cp})
    assert m["P"] == pytest.approx(power) and m["T"] == pytest.approx(torque)
    assert m["eta"] == pytest.approx(eta * 100)
    # An old standalone EM row must not override the selected marked held record.
    en, zh = result_line("rated", {"em_run": {"numbers": {"P_mech_W": 1000, "T_em_avg_Nm": 10}}, "coupled": cp})
    assert str(torque) in en and str(torque) in zh
    assert "core loss on the shaft" in en and "铁损计入轴侧" in zh
    if mode == "motor":
        assert "850 W" in en and "850 W" in zh and "740 W" not in en
    else:
        assert "shaft torque 9.5" in en and "轴转矩 9.5" in zh
    compact = mechanical_numbers({"em_run": compact_em_run({"summary": em})})
    assert compact["T"] == pytest.approx(8.7 if mode == "motor" else 9.3, abs=.001)


def test_r5_r2_digest_missing_context_and_legacy_omission():
    from motor_ai_sim.digest.readable import mechanical_numbers, result_line
    assert mechanical_numbers({"em_run": {"numbers": {"P_mech_W": 870, "shaft_conv": 1}}})["P"] is None
    old = {"em_run": {"numbers": {"P_mech_W": 1000, "T_em_avg_Nm": 10}},
           "coupled": {"em": {"efficiency": .9}, "efficiency_shaft": .88}}
    assert mechanical_numbers(old)["eta"] is None
    en, zh = result_line("rated", {"em_run": {"numbers": {"T_em_avg_Nm": 10, "P_elec_in_W": -800}}})
    # an unmarked generator on its own keeps its earlier text exactly
    assert en == "rated: generator 800 W out, shaft torque 10 N·m" and zh == "rated：发电 800 W，轴转矩 10 N·m"


def test_r7_digest_unmarked_alone_keeps_text_marked_and_mixed_name_the_basis():
    from motor_ai_sim.digest import readable as RD
    old = {"em_run": {"numbers": {"T_avg_Nm": 12.34, "efficiency_pct": 95.1, "P_mech_W": 4100}}}
    en, zh = RD.result_line("cruise", old)
    assert en == "cruise: 12.3 N·m at 4.1 kW, efficiency 95.1 %" and "口径" not in zh
    assert RD.headline(old) == "T 12.3 N·m, P 4.1 kW, η 95.1 %"
    assert RD.line_basis(old) == "earlier"
    en_m, zh_m = RD.result_line("cruise", old, mixed=True)
    assert en_m == "cruise: 12.3 N·m at 4.1 kW, efficiency 95.1 %, earlier shaft basis" and "较早轴功率口径" in zh_m
    assert RD.headline(old, mixed=True).endswith("earlier shaft basis")
    marked = {"coupled": {"em": _plain_marked(), "P_mech_extra_W": 20, "efficiency_shaft": 850 / 970}}
    assert RD.line_basis(marked) == "shaft"
    for mixed in (False, True):
        en2, zh2 = RD.result_line("rated", marked, mixed=mixed)
        assert en2.startswith("rated: shaft torque ") and "core loss on the shaft" in en2 and "铁损计入轴侧" in zh2
        assert "core loss on the shaft" in RD.headline(marked, mixed=mixed)
    assert RD.line_basis({"rotor_stress": {"sf_min": 2.0}}) is None


def test_r7_digest_collector_names_the_basis_only_in_a_mixed_variant(tmp_path, monkeypatch):
    from motor_ai_sim.digest import collect as C
    from motor_ai_sim.development import store as PS
    monkeypatch.setattr(PS, "path_for", lambda pid: tmp_path / str(pid))    # no project files: results only
    old = {"em_run": {"computed_at": "2026-11-26T10:00:00Z",
                      "numbers": {"T_avg_Nm": 12.34, "efficiency_pct": 95.1, "P_mech_W": 4100}}}
    marked = {"coupled": {"computed_at": "2026-11-26T10:00:00Z", "em": _plain_marked(), "P_mech_extra_W": 20,
                          "efficiency_shaft": 850 / 970}}
    proj = {"id": "p_r7_none", "linked_motors": [{"die": "D", "config": "C"}]}

    def lines(by_duty):
        items = C.collect_project(proj, 0.0, 4.0e9, results=lambda die, cfg: by_duty)
        return sorted(i.en for i in items if i.kind == "results")
    alone = lines({"cruise": old})
    assert alone and alone[0] == "cruise: 12.3 N·m at 4.1 kW, efficiency 95.1 %"
    both = lines({"cruise": old, "rated": marked})
    assert any(x == "cruise: 12.3 N·m at 4.1 kW, efficiency 95.1 %, earlier shaft basis" for x in both)
    assert any(x.startswith("rated: shaft torque ") and "core loss on the shaft" in x for x in both)


def test_r5_r3_rebuild_keeps_marker_and_refuses_wrong_cache():
    from motor_ai_sim.sweep_rebuild import build_result, _metric_key
    row = {"status": "ok", "current_a": 10, "torque": 10, "eff": .8969,
           "mass": 2, "td": 4.35, "p_loss": 100, "shaft_conv": 1}
    key = _metric_key(10, .8969, 2, 100)
    for cache in ({}, {key: {"P_mech_W": 1000, "efficiency": .8969}}):
        result = build_result("synthetic", [row], cache)
        p = result["points"][0]
        assert p["shaft_convention"] == SC.SHAFT_CONVENTION
        assert p["torque_per_mass_Nm_kg"] == 4.35 and p["efficiency"] == .8969
        assert "P_mech_W" not in p
        assert result["rebuilt"]["points_with_full_result"] == 0
    old = {k: v for k, v in row.items() if k != "shaft_conv"}
    assert "shaft_convention" not in build_result("synthetic", [old])["points"][0]


def test_r5_r4_optimizer_compact_power_is_stored_not_synthesised():
    from motor_ai_sim.routes.optimization import _pt
    r = {"shaft_convention": SC.SHAFT_CONVENTION, "T_em_Nm": 10, "T_shaft_Nm": 8.7, "P_mech_W": 870}
    out = _pt({"ok": True, "res": r}, "test")
    assert out["P_mech_W"] == 870 and out["torque"] == 10 and out["shaft_conv"] == 1
    r.pop("P_mech_W")
    assert _pt({"ok": True, "res": r}, "test")["P_mech_W"] is None
    r.pop("shaft_convention")
    assert "P_mech_W" not in _pt({"ok": True, "res": r}, "test")


def test_r5_r5_point_metadata_and_parent_mechanical_losses():
    from types import SimpleNamespace
    from motor_ai_sim.development.sim_view import _duty_row
    from motor_ai_sim.agent_designs import headline
    family = SimpleNamespace(_primary_drive=lambda d: "current", _RUN_DRIVES=[], build_sig_moved=lambda a,b: False)
    for marked in (False, True):
        res = {"efficiency_pct": 89.69, **({"shaft_conv": 1} if marked else {})}
        projected = _duty_row({"result": res}, family, None)["result"]
        assert (projected.get("shaft_conv") == 1) is marked
        if not marked:
            assert projected == {"efficiency_pct": 89.69, "v_ll_peak_v": None}
    d = {"params": {"speed_rpm": RPM100, "mode": "generator", "current_a_rms": 100}}
    h = headline({"em": _plain_marked(op_mode="generator"), "P_mech_extra_W": 20}, d, "coupled")
    assert h["mechanical_losses_included"] is True and h["losses_w"]["mechanical"] == 20
    assert h["efficiency_shaft_pct"] == pytest.approx(87.37, abs=.01)
