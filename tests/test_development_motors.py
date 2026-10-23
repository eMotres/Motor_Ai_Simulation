from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from motor_ai_sim.development import motors, store


def _project(project_id="alpha", members=None):
    return {"id": project_id, "linked_motors": [], "members": members or [], "status": "spec"}


def _request():
    return Request({"type": "http", "headers": []})


def test_link_needs_project_and_motor_authority_and_is_exclusive(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project(_project())
    store.create_project(_project("beta"))
    monkeypatch.setattr(motors, "_motor", lambda *args: (Path("."), Path("."), {"is_admin": True}, {}))
    linked = motors.link_motor("alpha", "DIE", "A", _request())
    assert linked == {"die": "DIE", "config": "A"}
    with pytest.raises(HTTPException) as error:
        motors.link_motor("beta", "DIE", "A", _request())
    assert error.value.status_code == 409
    assert store.load_index()["motors"][motors._key("DIE", "A")]["project_id"] == "alpha"


def test_customer_grants_preserve_manual_and_other_motor_grants(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    root = tmp_path / "projects"; root.mkdir()
    store.create_project(_project(members=[{"email": "c@example.test", "role": "customer"}]))
    saved = {"all": False, "dies": ["OTHER"], "default": None, "configs": ["MANUAL/B"]}
    monkeypatch.setattr(motors.users, "get_user", lambda email: {"email": email})
    monkeypatch.setattr(motors.users, "get_stored_motor_grants", lambda email: dict(saved))
    def setter(email, *, all_motors, dies, default, configs):
        saved.update({"all": all_motors, "dies": dies, "default": default, "configs": configs})
        return saved
    monkeypatch.setattr(motors.users, "set_motor_grants", setter)
    monkeypatch.setattr(motors, "_motor", lambda *args: (Path("."), Path("."), {"is_admin": True}, {}))
    motors.link_motor("alpha", "DIE", "A", _request())
    assert saved["configs"] == ["DIE/A", "MANUAL/B"]
    assert saved["dies"] == ["OTHER"]
    project = store.get_project("alpha")
    assert project["project_motor_grants"][motors._key("DIE", "A")]["emails"] == ["c@example.test"]
    # Simulate the real admin grant editor removing and then explicitly
    # reissuing this exact permission after the project inserted it.
    saved["configs"] = ["MANUAL/B"]
    saved["configs"] = ["DIE/A", "MANUAL/B"]
    motors.unlink_motor("alpha", "die", "a")
    assert saved["configs"] == ["DIE/A", "MANUAL/B"]
    project = store.get_project("alpha")
    assert project["grant_removal_candidates"] == [
        {"email": "c@example.test", "die": "DIE", "config": "A", "reason": "motor_unlinked"}
    ]


def test_preexisting_grant_is_not_a_project_removal_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project(_project(members=[{"email": "c@example.test", "role": "customer"}]))
    saved = {"all": False, "dies": [], "default": None, "configs": ["DIE/A"]}
    monkeypatch.setattr(motors.users, "get_user", lambda email: {"email": email})
    monkeypatch.setattr(motors.users, "get_stored_motor_grants", lambda email: dict(saved))
    monkeypatch.setattr(motors.users, "set_motor_grants", lambda *args, **kwargs: pytest.fail("existing grant rewritten"))
    monkeypatch.setattr(motors, "_motor", lambda *args: (Path("."), Path("."), {"is_admin": True}, {}))
    motors.link_motor("alpha", "DIE", "A", _request())
    motors.unlink_motor("alpha", "DIE", "A")
    assert saved["configs"] == ["DIE/A"]
    assert "grant_removal_candidates" not in store.get_project("alpha")


def test_membership_index_updates_without_project_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    store.create_project(_project(members=[{"email": "c@example.test", "role": "customer"}]))
    assert store.membership_projects("C@example.test") == ["alpha"]
    monkeypatch.setattr(store, "list_projects", lambda: pytest.fail("membership read scanned project documents"))
    assert store.membership_projects("c@example.test") == ["alpha"]


def test_failed_link_keeps_add_only_grant_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    root = tmp_path / "projects"; root.mkdir()
    store.create_project(_project(members=[{"email": "c@example.test", "role": "customer"}]))
    saved = {"all": False, "dies": [], "default": None, "configs": []}
    monkeypatch.setattr(motors.users, "get_user", lambda email: {"email": email})
    monkeypatch.setattr(motors.users, "get_stored_motor_grants", lambda email: dict(saved))
    def setter(email, *, all_motors, dies, default, configs):
        saved.update({"all": all_motors, "dies": dies, "default": default, "configs": configs})
    monkeypatch.setattr(motors.users, "set_motor_grants", setter)
    monkeypatch.setattr(motors, "_motor", lambda *args: (Path("."), Path("."), {"is_admin": True}, {}))
    from motor_ai_sim import json_store
    original = json_store.mutate_json
    def fail_index(path, mutator, **kwargs):
        if Path(path) == store.index_path():
            raise OSError("injected index failure")
        return original(path, mutator, **kwargs)
    monkeypatch.setattr(json_store, "mutate_json", fail_index)
    with pytest.raises(OSError, match="injected"):
        motors.link_motor("alpha", "DIE", "A", _request())
    project = store.get_project("alpha")
    assert "DIE/A" in saved["configs"]
    assert project["linked_motors"] == []
    assert project["project_motor_grants"][motors._key("DIE", "A")]["emails"] == ["c@example.test"]
    assert project["grant_removal_candidates"][0]["reason"] == "link_failed"


def test_reads_do_not_create_project_store(tmp_path, monkeypatch):
    missing = tmp_path / "readonly-projects"
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(missing))
    assert store.load_index() == {"motors": {}}
    assert not missing.exists()
    with motors.transaction():
        assert missing.is_dir()
    assert not (missing / "index.json").exists()


def test_results_allowlist_keeps_outer_metrics_and_duty_order(monkeypatch):
    cfg = {"duties": [{"name": "rated", "rpm": 1000,
                       "result": {"T_em_Nm": 800, "comparison": {"T_em_Nm": 12}}},
                      {"name": "peak", "rpm": 1800}]}
    monkeypatch.setattr("motor_ai_sim.duty_results.get", lambda die, config: {
        "rated": {"thermal": {"P_out_W": 50, "eta_shaft": .9, "P_loss_total_incl_mech_W": 6,
                                "coil_temp_C": 77, "geometry_fingerprint": "private"}}})
    rows = motors._results("DIE", "A", cfg)
    assert [row["duty"] for row in rows] == ["rated", "peak"]
    assert rows[0]["electromagnetic"]["t_em_nm"] == 800
    assert rows[0]["stored_results"]["thermal"]["p_out_w"] == 50
    assert "geometry_fingerprint" not in json.dumps(rows)


def test_comparison_projection_uses_stored_duty_metrics_and_omits_missing_values(monkeypatch):
    cfg = {"duties": [{"name": "rated", "rpm": 1000, "current_arms": 8,
                       "gamma_deg": 12, "torque_nm": 15, "power_kw": 0.9,
                       "result": {"t_em_avg_nm": 8, "p_mech_w": 700,
                                  "efficiency_pct": 0.5, "loss_w": 42,
                                  "loss_mech_w": 7, "p_stranded_w": 20,
                                  "p_core_w": 12, "p_solid_w": 5,
                                  "ripple_pct": 4.2, "v_ll_peak_v": 36,
                                  "j_coil_a_mm2": 2.7,
                                  "mass_kg": 3},
                       "summary": {"mass_full_kg": 2.5, "mass_partial": False,
                                   "demag": {"loss_pct": 0, "br_kept_vol_pct": 100},
                                   "runs": {"pwm_voltage": {"summary": {
                                       "T_em_avg_Nm": 999, "efficiency_pct": 999}}}}}]}
    rows = motors._results("SYNTHETIC-DIE", "A", cfg, {
        "rated": {
            "thermal": {"components": {"winding": {"max": 77},
                                         "magnet": {"max": 82},
                                         "stator": {"max": 73},
                                         "rotor": {"max": 79}}},
            "coupled": {"coil_temp_c": 70, "magnet_temp_c": 75,
                        "bearing_temp_c": 68,
                        "em": {"T_em_avg_Nm": 1000, "P_mag_W": 3,
                               "P_solid_W": 999, "efficiency": 0.99},
                        "inverter": {"v_dc_V": 48},
                        "controller": {"P_mech_W": 9999},
                        "reference_sine": {"T_em_avg_Nm": 9999},
                        "constants_20c": {"T_em_avg_Nm": 9999}},
        }
    })
    assert rows[0]["operating_point"] == {"rpm": 1000, "current_arms": 8, "gamma_deg": 12}
    assert rows[0]["comparison"] == {
        "torque_nm": 8, "power_w": 700, "efficiency_pct": 0.5,
        "p_loss_total_w": 49, "p_cu_w": 20, "p_fe_w": 12, "p_mag_w": 3, "p_solid_w": 5,
        "mass_kg": 2.5, "torque_to_mass_nm_kg": 3.2,
        "mass_partial": False, "p_mech_total_w": 7,
        "ripple_pct": 4.2, "v_line_peak_v": 36, "line_peak_physical": True,
        "line_peak_provenance": "physical_terminal_peak",
        "line_peak_source": "result",
        "current_density_a_mm2": 2.7, "coil_temp_c": 77, "magnet_temp_c": 82,
        "stator_temp_c": 73, "rotor_temp_c": 79, "bearing_temp_c": 68,
        "temperatures_c": {"winding": 77, "magnet": 82, "stator": 73, "rotor": 79},
        "demag": False,
    }
    missing = motors._results("SYNTHETIC-DIE", "A", {"duties": [{"name": "new"}]}, {})[0]
    assert missing["comparison"] == {}


def test_comparison_projection_marks_old_active_mass_and_partial_total(monkeypatch):
    row = motors._results("SYNTHETIC-DIE", "A", {"duties": [{
        "name": "old", "torque_nm": 10,
        "result": {"t_em_avg_nm": 10, "loss_w": 25, "mass_kg": 2, "efficiency_pct": 88},
    }]}, {})[0]["comparison"]
    assert row == {"torque_nm": 10, "efficiency_pct": 88,
                   "p_loss_total_w": 25, "loss_total_partial": True,
                   "mass_kg": 2, "mass_partial": True,
                   "torque_to_mass_nm_kg": 5}


def test_comparison_projection_ignores_target_only_and_prefers_solved_values():
    target_only = motors._results("D", "A", {"duties": [{
        "name": "target", "torque_nm": 12, "power_kw": 1.2,
    }]}, {})[0]
    assert target_only["has_result"] is False
    assert target_only["comparison"] == {}

    conflicting = motors._results("D", "A", {"duties": [{
        "name": "solved", "torque_nm": 12, "power_kw": 1.2,
        "result": {"t_em_avg_nm": 6, "p_mech_w": 400, "mass_kg": 2},
    }]}, {})[0]["comparison"]
    assert conflicting["torque_nm"] == 6
    assert conflicting["power_w"] == 400
    assert conflicting["torque_to_mass_nm_kg"] == 3


def test_comparison_projection_uses_only_explicit_needed_bus_voltage():
    supplied = motors._comparison_projection({
        "result": {"v_dc_v": 48, "dc_bus_v": 52, "v_bus_v": 54},
        "summary": {"v_dc_v": 60, "dc_bus_v": 62, "v_bus_v": 64},
    }, {
        "coupled": {"v_dc_v": 48, "dc_bus_v": 52, "inverter": {"v_dc_V": 54}},
    })
    needed_zero = motors._comparison_projection({"result": {"v_dc_required_v": 0}}, {})
    needed = motors._comparison_projection({}, {"coupled": {"v_dc_required_v": 62}})
    line_peak = motors._comparison_projection({"result": {"v_ll_peak_v": 37},
                                               "summary": {"V_line_peak_solved_V": 36}}, {
        "coupled": {"em": {"V_line_peak_V": 37}, "inverter": {"v_dc_V": 48}},
    })
    assert supplied == {}
    assert needed_zero["v_dc_required_v"] == 0
    assert needed["v_dc_required_v"] == 62
    assert "v_dc_required_v" not in line_peak
    assert line_peak["v_line_peak_v"] == 36


def test_comparison_projection_converts_only_named_efficiency_ratios(monkeypatch):
    percent = motors._comparison_projection({"result": {"efficiency_pct": 0.8}}, {})
    ratio = motors._comparison_projection({"summary": {"efficiency_shaft": 0.8}}, {})
    assert percent["efficiency_pct"] == 0.8
    assert ratio["efficiency_pct"] == 80
    assert motors._comparison_projection({"result": {"demag": False}}, {})["demag"] is False


def test_comparison_projection_does_not_join_mass_and_basis_from_different_sources(monkeypatch):
    projected = motors._comparison_projection({
        "torque_nm": 12,
        "result": {"t_em_avg_nm": 12, "mass_kg": 2, "mass_partial": False},
        "summary": {"mass_full_kg": 3, "torque_per_mass_full_Nm_kg": 999},
    }, {})
    assert projected["mass_kg"] == 2
    assert projected["mass_partial"] is True
    assert projected["torque_to_mass_nm_kg"] == 6


def test_project_motor_results_load_stored_index_once_per_die(monkeypatch):
    from motor_ai_sim import duty_results
    calls = []
    expected = {"A": {"rated": {"thermal": {"P_out_W": 10}}}}
    monkeypatch.setattr(duty_results, "index", lambda die: calls.append(die) or expected)
    cache = {}
    first = motors._stored_results_index("SYNTHETIC-DIE", cache)
    second = motors._stored_results_index("SYNTHETIC-DIE", cache)
    assert first is second is expected
    assert calls == ["SYNTHETIC-DIE"]


def test_link_authority_is_bound_to_exact_resolved_directory_when_names_collide(tmp_path, monkeypatch):
    shared_die = tmp_path / "shared" / "dies" / "D"
    published_die = tmp_path / "published" / "owner" / "dies" / "D"
    for path in (shared_die, published_die):
        path.mkdir(parents=True)
        (path / "die.yaml").write_text("name: D\n", encoding="utf-8")
        (path / "A.yaml").write_text("name: A\nvisibility: private\n", encoding="utf-8")
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("SHARED_ROOT", str(tmp_path / "shared"))
    (tmp_path / "projects").mkdir()
    store.create_project(_project(members=[{"email": "customer@example.test", "role": "customer"}]))
    from motor_ai_sim.routes import family
    from motor_ai_sim import motor_access
    monkeypatch.setattr(family, "_require_die_access", lambda *args: None)
    monkeypatch.setattr(family, "_die_dir", lambda die: shared_die)
    monkeypatch.setattr(family, "_cfg_file", lambda die, config: shared_die / f"{config}.yaml")
    # The published collision appears first and has the same raw die field,
    # but is not the source selected by the normal bare-name resolver.
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [
        {"name": "D ? by Owner", "die": "D", "dir": published_die,
         "layer": "published", "owner": "owner@example.test", "owner_id": "owner-ws"},
        {"name": "D", "die": "D", "dir": shared_die,
         "layer": "shared", "owner": "", "owner_id": ""},
    ])
    monkeypatch.setattr(motor_access, "catalog_access", lambda authorization: {"is_admin": True})
    monkeypatch.setattr(motor_access, "config_visible", lambda *args, **kwargs: True)
    admin = _request()
    monkeypatch.setattr(motors, "_identity", lambda request: {"id": "admin@example.test", "is_admin": True})
    _die_dir, _cfg_path, _ident, resolved = motors._motor(admin, "D", "A")
    assert resolved["layer"] == "shared"
    assert resolved["canonical_die"] == "D"
    saved = {"all": False, "dies": [], "default": None, "configs": []}
    monkeypatch.setattr(motors.users, "get_user", lambda email: {"email": email})
    monkeypatch.setattr(motors.users, "get_stored_motor_grants", lambda email: dict(saved))
    def set_grants(email, *, all_motors, dies, default, configs):
        saved.update({"all": all_motors, "dies": dies, "default": default, "configs": configs})
    monkeypatch.setattr(motors.users, "set_motor_grants", set_grants)
    linked = motors.link_motor("alpha", "D", "A", admin)
    assert linked == {"die": "D", "config": "A"}
    assert "D/A" in saved["configs"]
    assert "D ? by Owner/A" not in saved["configs"]
    assert store.load_index()["motors"][motors._key("D", "A")]["project_id"] == "alpha"

    # A published owner cannot claim share authority over the shared motor
    # merely because their published entry has the same underlying die name.
    monkeypatch.setattr(motors, "_identity", lambda request: {"id": "owner@example.test", "is_admin": False})
    with pytest.raises(HTTPException) as error:
        motors._motor(_request(), "D", "A")
    assert error.value.status_code == 403



def test_comparison_projection_blocks_delta_pwm_model_branch_line_peak():
    delta_pwm = motors._comparison_projection({
        "result": {"v_ll_peak_v": 55.426},
        "summary": {
            "V_line_peak_solved_V": 55.426,
            "V_line_peak_V": 55.426,
            "v_dc_V": 48,
            "delta_equivalent_star": {"applied": True, "drive": "pwm_voltage"},
        },
    }, {"coupled": {"em": {"V_line_peak_V": 55.426},
                   "inverter": {"equivalent_star": True}}})
    assert delta_pwm["line_peak_physical"] is False
    assert delta_pwm["line_peak_provenance"] == "delta_equivalent_star"
    assert delta_pwm["line_peak_reason"] == "model_branch_peak"
    assert delta_pwm["line_peak_source"] == "summary"
    assert "v_line_peak_v" not in delta_pwm

    coupled_only = motors._comparison_projection({}, {"coupled": {
        "em": {"V_line_peak_V": 55.426},
        "inverter": {"equivalent_star": True},
    }})
    assert coupled_only["line_peak_physical"] is False
    assert coupled_only["line_peak_source"] == "coupled.em"
    assert "v_line_peak_v" not in coupled_only


def test_comparison_projection_accepts_only_qualified_physical_delta_peak():
    physical_override = motors._comparison_projection({"summary": {
        "V_line_peak_solved_V": 55.426,
        "V_line_peak_physical_V": 48,
        "delta_equivalent_star": {"applied": True, "drive": "pwm_voltage"},
    }}, {})
    assert physical_override["v_line_peak_v"] == 48
    assert physical_override["line_peak_physical"] is True
    assert physical_override["line_peak_provenance"] == "physical_terminal_peak"
    assert physical_override["line_peak_source"] == "summary"

    contradictory = motors._comparison_projection({"summary": {
        "V_line_peak_physical_V": 48,
        "line_peak_physical": False,
        "delta_equivalent_star": {"applied": True},
    }}, {})
    assert contradictory["line_peak_physical"] is False
    assert "v_line_peak_v" not in contradictory


def test_comparison_projection_preserves_ordinary_zero_line_peak():
    ordinary = motors._comparison_projection({"summary": {
        "V_line_peak_solved_V": 0, "star_delta": "star", "drive": "current",
    }}, {})
    assert ordinary["v_line_peak_v"] == 0
    assert ordinary["line_peak_physical"] is True


def test_comparison_projection_keeps_physical_delta_sine_peak():
    sine = motors._comparison_projection({"summary": {
        "V_line_peak_solved_V": 48,
        "delta_equivalent_star": {
            "applied": True, "drive": "voltage", "v_bus_model_V": 0,
        },
    }}, {})
    assert sine["v_line_peak_v"] == 48
    assert sine["line_peak_physical"] is True
    assert sine["line_peak_source"] == "summary"


def test_comparison_projection_does_not_borrow_physical_peak_from_other_source():
    projected = motors._comparison_projection({
        "result": {"V_line_peak_physical_V": 48},
        "summary": {
            "V_line_peak_solved_V": 55.426,
            "V_line_peak_V": 48,
            "delta_equivalent_star": {
                "applied": True, "drive": "pwm_voltage", "v_bus_model_V": 83.138,
            },
        },
    }, {})
    assert projected["line_peak_physical"] is False
    assert projected["line_peak_source"] == "summary"
    assert "v_line_peak_v" not in projected


def test_comparison_projection_keeps_primary_peak_when_coupled_is_equivalent():
    projected = motors._comparison_projection({"summary": {
        "V_line_peak_solved_V": 48,
    }}, {"coupled": {
        "em": {"V_line_peak_V": 55.426},
        "inverter": {"equivalent_star": True},
    }})
    assert projected["v_line_peak_v"] == 48
    assert projected["line_peak_physical"] is True
    assert projected["line_peak_source"] == "summary"


def test_comparison_projection_blocks_primary_pwm_equivalent_marker():
    projected = motors._comparison_projection({"result": {
        "V_line_peak_solved_V": 55.426,
        "pwm": {"equivalent_star": True},
    }}, {})
    assert projected["line_peak_physical"] is False
    assert projected["line_peak_reason"] == "model_branch_peak"
    assert "v_line_peak_v" not in projected


def test_comparison_projection_prefers_solved_peak_over_generic_alias():
    projected = motors._comparison_projection({
        "summary": {"V_line_peak_V": 80},
        "result": {"V_line_peak_solved_V": 36},
    }, {})
    assert projected["v_line_peak_v"] == 36
    assert projected["line_peak_source"] == "result"


def test_comparison_projection_accepts_physical_peak_without_equivalent_marker():
    projected = motors._comparison_projection({"summary": {
        "V_line_peak_physical_V": 0,
    }}, {})
    assert projected["v_line_peak_v"] == 0
    assert projected["line_peak_physical"] is True


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_line_peak_tooltip_locale_resolves_at_runtime_key(locale):
    locale_root = Path(os.environ.get("ROUND3_LOCALE_ROOT", Path.cwd() / "web" / "src" / "locales"))
    data = json.loads((locale_root / locale / "development.json").read_text(encoding="utf-8"))
    value = data
    for part in "comparison.linePeakUnavailable".split("."):
        value = value[part]
    assert isinstance(value, str) and value.strip()
    assert value != "comparison.linePeakUnavailable"
