# SPDX-License-Identifier: Apache-2.0
"""THE FULL MASS OF A SIMULATED GEOMETRY (owner 2026-10-20: "the full mass everywhere").

A transient run weighs the EM parts + shaft tube of the geometry it solved (``mass_total_kg``).  The server stamps the
summary with the full mass of the loaded configuration = that active mass + the configuration's non-active parts
(``mass_summary``: ``full_kg - active_kg``) and the per-mass figures over it; a configuration without a CAD build is
``mass_partial`` with no full-mass key (the surfaces then print "Active mass (no CAD build)").  Closed-form, no solver."""
from __future__ import annotations

import pathlib

import pytest

from motor_ai_sim.modules import motor_mass as MM

DIE, CFG = "CIANO14 40 new", "L20"


def _summary_for(full_kg=0.1810, active_kg=0.1506, partial=False, reason=""):
    return {"full_kg": full_kg, "active_kg": active_kg, "rotating_kg": 0.06, "partial": partial, "partial_reason": reason,
            "scope": "full motor", "non_active_kg": full_kg - active_kg, "kit": False, "others": [], "unmassed": [],
            "source": "cad_bom"}


def _run(mass_total=0.165):
    # a run of a geometry a little longer than the configuration's saved build: 0.165 kg active (EM parts + shaft tube)
    return {"mass_total_kg": mass_total, "mass_active_kg": 0.14, "T_em_avg_Nm": 0.42, "P_mech_W": 132.0,
            "P_loss_total_W": 18.0, "Km_Nm_sqrtW": 0.021, "torque_per_mass_Nm_kg": 0.42 / mass_total,
            "power_per_mass_W_kg": 132.0 / mass_total, "Km_per_mass_Nm_sqrtW_kg": 0.021 / mass_total,
            "loss_density_W_kg": 18.0 / mass_total}


def test_the_full_mass_is_the_runs_active_mass_plus_the_configurations_non_active_parts(monkeypatch):
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: _summary_for())
    s = MM.stamp_run_summary(_run(), DIE, CFG)
    non_active = 0.1810 - 0.1506
    assert s["mass_partial"] is False and s["mass_basis"] == f"{DIE} / {CFG}" and s["mass_scope"] == "full motor"
    assert s["mass_non_active_kg"] == pytest.approx(non_active, abs=1e-5)
    assert s["mass_full_kg"] == pytest.approx(0.165 + non_active, abs=1e-5)         # the RUN's active mass, not the config's
    full = s["mass_full_kg"]
    assert s["torque_per_mass_full_Nm_kg"] == pytest.approx(0.42 / full, abs=1e-3)
    assert s["power_per_mass_full_W_kg"] == pytest.approx(132.0 / full, abs=0.1)
    assert s["loss_density_full_W_kg"] == pytest.approx(18.0 / full, abs=0.1)
    assert s["Km_per_mass_full_Nm_sqrtW_kg"] == pytest.approx(0.021 / full, abs=1e-4)


def test_the_active_numbers_are_untouched(monkeypatch):
    """The optimizer and every stored Compare point were built on the active mass: nothing there moves."""
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: _summary_for())
    before = _run()
    s = MM.stamp_run_summary(dict(before), DIE, CFG)
    for k, v in before.items():
        assert s[k] == v, k


def test_live_run_uses_the_unrounded_base_numbers(monkeypatch):
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: _summary_for())
    s = MM.stamp_run_summary(_run(), DIE, CFG, raw={"T_em_avg_Nm": 0.4234567, "P_mech_W": 131.987654,
                                                    "P_loss_total_W": 18.04, "Km_Nm_sqrtW": 0.0212345})
    assert s["torque_per_mass_full_Nm_kg"] == pytest.approx(0.4234567 / s["mass_full_kg"], abs=5e-4)
    assert s["Km_per_mass_full_Nm_sqrtW_kg"] == pytest.approx(0.0212345 / s["mass_full_kg"], abs=5e-5)


def test_no_cad_build_is_partial_with_no_full_mass_key(monkeypatch):
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: _summary_for(partial=True, reason="no CAD bill of materials"))
    s = MM.stamp_run_summary(_run(), DIE, CFG)
    assert s["mass_partial"] is True and "no CAD bill of materials" in s["mass_partial_reason"]
    for k in ("mass_full_kg", "mass_non_active_kg", "torque_per_mass_full_Nm_kg", "power_per_mass_full_W_kg",
              "loss_density_full_W_kg", "Km_per_mass_full_Nm_sqrtW_kg"):
        assert k not in s, k
    assert s["mass_total_kg"] == 0.165                       # the active mass stays, to be printed as "Active mass (no CAD build)"


def test_a_die_the_catalog_cannot_weigh_is_partial(monkeypatch):
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: None)
    s = MM.stamp_run_summary(_run(), DIE, CFG)
    assert s["mass_partial"] is True and s["mass_partial_reason"] and "mass_full_kg" not in s


def test_a_restamp_never_leaves_the_previous_configurations_numbers(monkeypatch):
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: _summary_for())
    s = MM.stamp_run_summary(_run(), DIE, CFG)
    assert "mass_full_kg" in s
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: _summary_for(partial=True, reason="gone"))
    s = MM.stamp_run_summary(s, DIE, "L21")
    assert s["mass_partial"] is True and s["mass_basis"] == f"{DIE} / L21"
    assert "mass_full_kg" not in s and "torque_per_mass_full_Nm_kg" not in s


def test_a_frameless_kit_may_have_a_negative_non_active_part(monkeypatch):
    """The kit has no shaft tube: its non-active part is board + enamel - tube and can be negative; the sum still holds."""
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: {**_summary_for(full_kg=0.0420, active_kg=0.0450), "kit": True,
                                                                     "scope": "frameless kit"})
    s = MM.stamp_run_summary(_run(0.046), DIE, CFG)
    assert s["mass_scope"] == "frameless kit"
    assert s["mass_full_kg"] == pytest.approx(0.046 + (0.0420 - 0.0450), abs=1e-5)


def test_a_summary_without_a_mass_of_its_own_is_partial(monkeypatch):
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: _summary_for())
    s = MM.stamp_run_summary({"T_em_avg_Nm": 1.0}, DIE, CFG)
    assert s["mass_partial"] is True and "mass_full_kg" not in s


def test_the_live_stamp_follows_the_loaded_context(monkeypatch):
    from motor_ai_sim.routes import family as fam
    from motor_ai_sim.routes import simulation as sim
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: _summary_for())
    # a loaded configuration: stamped
    monkeypatch.setattr(fam, "_read_ctx", lambda: {"die": DIE, "config": CFG})
    s = _run()
    sim._stamp_full_mass(s, T=0.42, p_mech=132.0, p_cu_dc=4.0, p_loss=18.0)
    assert s["mass_partial"] is False and s["mass_full_kg"] == pytest.approx(0.165 + 0.0304, abs=1e-5)
    assert s["Km_per_mass_full_Nm_sqrtW_kg"] == pytest.approx(0.42 / 2.0 / s["mass_full_kg"], abs=1e-4)      # Km = T / sqrt(P_cu_dc)
    # nothing loaded: the active mass, said so
    monkeypatch.setattr(fam, "_read_ctx", lambda: None)
    s = _run()
    sim._stamp_full_mass(s, T=0.42, p_mech=132.0, p_cu_dc=4.0, p_loss=18.0)
    assert s["mass_partial"] is True and "mass_full_kg" not in s and "no die" in s["mass_partial_reason"]


def test_a_mass_hiccup_never_fails_a_run(monkeypatch):
    from motor_ai_sim.routes import family as fam
    from motor_ai_sim.routes import simulation as sim

    def boom():
        raise RuntimeError("disk")
    monkeypatch.setattr(fam, "_read_ctx", boom)
    s = _run()
    sim._stamp_full_mass(s, T=0.42, p_mech=132.0, p_cu_dc=4.0, p_loss=18.0)
    assert s["mass_partial"] is True and "disk" in s["mass_partial_reason"]


def test_a_stored_duty_summary_is_stamped_on_a_copy(monkeypatch):
    from motor_ai_sim.routes import family as fam
    monkeypatch.setattr(MM, "mass_summary", lambda die, cfg, **k: _summary_for())
    monkeypatch.setattr(fam, "_die_dir", lambda die: pathlib.Path("/nonexistent/dies") / die)
    stored = _run()
    out = fam._summary_with_full_mass(stored, DIE, CFG)
    assert out is not stored and "mass_full_kg" not in stored and out["mass_full_kg"] > out["mass_total_kg"]
    assert fam._summary_with_full_mass(None, DIE, CFG) is None


def test_rebuilding_a_cached_summary_keeps_the_stamp_it_carries(monkeypatch):
    """_refresh_summary_shape must not move a stored run onto whatever configuration is loaded NOW."""
    from motor_ai_sim.routes import simulation as sim
    stamped = {"summary_shape_v": 1, "mass_basis": "OLD die / OLD cfg", "mass_partial": False, "mass_full_kg": 0.2,
               "mass_non_active_kg": 0.05, "mass_scope": "full motor"}
    res = {"summary": stamped, "_summary_args": {"I_phase_rms": 1.0, "gamma_deg": 0.0, "coil_temp_c": 20.0}}
    monkeypatch.setattr(sim, "_build_transient_summary", lambda *a, **k: {"summary_shape_v": sim._SUMMARY_SHAPE_V,
                                                                          "mass_total_kg": 0.15, "mass_basis": "NEW / NEW"})
    out = sim._refresh_summary_shape(res)
    assert out["summary"]["mass_basis"] == "OLD die / OLD cfg" and out["summary"]["mass_full_kg"] == 0.2
