from __future__ import annotations

import json
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
