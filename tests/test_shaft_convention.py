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


# ── the drag D: which shaft-side load the reported torque does not contain ──────────────────────────────────────────────
@pytest.mark.parametrize("method,eddy,rotor,expect,terms", [
    # rotor-side torque, rotor eddy solved IN the field: its drag is already in the torque -> only the core loss
    ("coulomb_virtual_work", True, True, 30.0, ("fe",)),
    ("maxwell_stress", True, True, 30.0, ("fe",)),
    # rotor-side torque, rotor eddy post-processed (or no eddy at all): the rotor losses are shaft loads too
    ("coulomb_virtual_work", False, True, 30.0 + 5.0 + 1.0 + 0.5, ("fe", "mag", "shaft", "sleeve")),
    ("coulomb_virtual_work", False, False, 36.5, ("fe", "mag", "shaft", "sleeve")),
    # copper eddy coupled but the rotor bodies not: rotor losses are post-processed -> shaft loads
    ("coulomb_virtual_work", True, False, 36.5, ("fe", "mag", "shaft", "sleeve")),
    # stator-side torque (power into the field): every in-field loss is inside it, the solved copper AC too
    ("energy_mean+maxwell_ripple", True, True, 30.0 + 7.0 + 6.5, ("fe", "cu_ac", "mag", "shaft", "sleeve")),
    ("terminal_work_mean+maxwell_ripple", False, False, 36.5, ("fe", "mag", "shaft", "sleeve")),
    (None, False, False, 36.5, ("fe", "mag", "shaft", "sleeve")),
])
def test_drag_table(method, eddy, rotor, expect, terms):
    d, t, side = SC.drag(torque_method=method, eddy_coupled=eddy, rotor_eddy_coupled=rotor,
                         p_fe=30.0, p_mag=5.0, p_shaft=1.0, p_sleeve=0.5, p_cu_ac_solved=7.0)
    assert d == pytest.approx(expect) and t == terms
    assert side == ("rotor" if method in ("coulomb_virtual_work", "maxwell_stress") else "stator")


def test_drag_of_result_reads_the_solver_flags():
    res = {"torque_method": "coulomb_virtual_work", "eddy_coupled": True, "rotor_eddy_solved": True, "P_cu_ac_solve_W": 9.0}
    assert SC.drag_of_result(res, p_fe=3.0, p_mag=1.0, p_shaft=0.1, p_sleeve=0.0)[0] == pytest.approx(3.0)
    res["torque_method"] = "energy_mean+maxwell_ripple"
    assert SC.drag_of_result(res, p_fe=3.0, p_mag=1.0, p_shaft=0.1, p_sleeve=0.0)[0] == pytest.approx(13.1)


# ── the balance ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_motor_balance_k_on_the_field_part_only():
    b = SC.balance(t_field_nm=10.0, rpm=1000.0, k=0.9, d_w=30.0, p_loss_w=100.0)
    p = 0.9 * 10.0 * W1000 - 30.0                   # NOT 0.9 * (10*w - 30): the factor never scales D
    assert b["op_mode"] == "motor" and b["P_mech_W"] == pytest.approx(p)
    assert b["P_elec_W"] == pytest.approx(p + 100.0) and b["efficiency"] == pytest.approx(p / (p + 100.0))
    assert b["T_shaft_Nm"] == pytest.approx(p / W1000) and b["T_field_k_Nm"] == pytest.approx(9.0)
    assert SC.per_mass_torque(b) == pytest.approx(p / W1000)


def test_generator_balance_input_is_field_plus_drag():
    b = SC.balance(t_field_nm=-10.0, rpm=1000.0, k=0.9, d_w=30.0, p_loss_w=100.0)
    pin = 0.9 * 10.0 * W1000 + 30.0
    assert b["op_mode"] == "generator" and b["P_mech_W"] == pytest.approx(pin)
    assert b["P_elec_W"] == pytest.approx(pin - 100.0) and b["efficiency"] == pytest.approx((pin - 100.0) / pin)
    assert b["T_shaft_Nm"] == pytest.approx(pin / W1000)
    # the comparison chart divides a generator's FIELD torque (x k) by the mass (owner Q2)
    assert SC.per_mass_torque(b) == pytest.approx(9.0)


def test_no_load_motor_has_a_negative_shaft_power_and_no_efficiency():
    b = SC.balance(t_field_nm=0.01, rpm=1000.0, k=None, d_w=30.0, p_loss_w=31.0)
    assert b["P_mech_W"] == pytest.approx(0.01 * W1000 - 30.0) and b["P_mech_W"] < 0
    assert b["efficiency"] == 0.0 and b["op_mode"] == "motor"


def test_without_a_factor_k_is_one():
    b = SC.balance(t_field_nm=2.0, rpm=1000.0, k=None, d_w=1.0, p_loss_w=10.0)
    assert b["P_field_k_W"] == pytest.approx(b["P_field_W"]) and b["k"] is None


def test_rederive_rebuilds_a_marked_summary_and_leaves_an_unmarked_one_alone():
    s = {"shaft_convention": SC.SHAFT_CONVENTION, "op_mode": "motor", "rpm": 1000.0, "T_em_avg_Nm": 10.0,
         "shaft_k3d": 0.9, "P_loss_total_W": 100.0, "P_core_W": 30.0, "P_mag_W": 5.0,
         "P_shaft_drag_terms": ["fe"], "P_mech_W": -1.0}
    assert SC.rederive(s) is True
    assert s["P_mech_W"] == pytest.approx(0.9 * 10.0 * W1000 - 30.0) and s["P_shaft_drag_W"] == pytest.approx(30.0)
    old = {"op_mode": "motor", "rpm": 1000.0, "T_em_avg_Nm": 10.0, "P_mech_W": 5.0}
    assert SC.rederive(old) is False and old["P_mech_W"] == 5.0


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
NEW = {"shaft_convention", "P_field_W", "T_field_k_Nm", "T_shaft_Nm", "P_shaft_drag_W", "P_shaft_drag_terms",
       "P_cu_ac_field_W", "torque_side", "P_elec_W", "shaft_k3d", "shaft_basis_note"}


def test_motor_summary_under_the_marker_and_its_legacy_twin(build):
    old, new = build(_sb(0.40), None), build(_sb(0.40))
    w = 2 * math.pi * 15000.0 / 60.0
    assert "shaft_convention" not in old and new["shaft_convention"] == SC.SHAFT_CONVENTION
    # the earlier convention: T*w 2-D, every loss on the electrical side
    assert old["P_mech_W"] == round(0.40 * w, 1)
    L = 110.0 + 4.6 + 1.3 + 0.01
    p = 0.9 * 0.40 * w - 4.6                     # Coulomb + coupled rotor eddy: D = Fe only
    assert new["P_mech_W"] == pytest.approx(p, abs=0.06)
    assert new["efficiency"] == pytest.approx(p / (p + L), abs=1e-4)
    assert new["P_shaft_drag_W"] == pytest.approx(4.6) and new["P_shaft_drag_terms"] == ["fe"]
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
    pin = 0.9 * 0.40 * w + 4.6
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
    assert new["P_shaft_drag_terms"] == ["fe", "cu_ac", "mag", "shaft", "sleeve"]
    assert new["P_shaft_drag_W"] == pytest.approx(4.6 + 3.3 + 1.3 + 0.01, abs=0.06)
    assert new["P_cu_ac_field_W"] == pytest.approx(3.3)
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
          "P_shaft_drag_W": 30.0, "P_shaft_drag_terms": ["fe"], "end3d": {"k_flux": 0.9}}


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
    assert out["P_mech_W"] == pytest.approx(p) and out["Kt_Nm_per_Arms"] == pytest.approx(0.9 * 11.0 / 20.0)
    assert out["torque_per_mass_Nm_kg"] == pytest.approx(p / W1000 / 2.0)
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
    assert '"shaft_convention": _SC.SHAFT_CONVENTION' in text and '"shaft_k3d": None' in text
