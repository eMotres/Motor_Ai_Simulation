"""A configuration's ``applications`` list (owner 2026-10-13).

Pins: the validator is loud, the tree and the Configure context carry the list, the cooling
registry is gated by it (a robot gets no propeller), a bad write is refused with 422, and the
migration script is a dry run by default, idempotent, backs up and never overwrites.
"""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from motor_ai_sim import applications as A
from motor_ai_sim import configure_limits as CL
from motor_ai_sim import cooling_options as CO
from motor_ai_sim.api import app

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
DIE, CFG, ADMIN = "TESTDIE APPS", "L12", "admin@example.com"
client = TestClient(app)


# -- validation ---------------------------------------------------------------

def test_valid_lists_pass_and_absent_is_not_set():
    assert A.validate_applications(["propeller"]) == ["propeller"]
    assert A.validate_applications(["robot", "propeller"]) == ["robot", "propeller"]
    assert A.validate_applications(None) == []


@pytest.mark.parametrize("bad", [
    "robot", ["Robot"], ["rocket"], [], ["robot", "robot"], [1], {"robot": 1}, [None]])
def test_invalid_values_are_errors(bad):
    with pytest.raises(A.ApplicationsError):
        A.validate_applications(bad)


def test_reader_reports_instead_of_raising():
    assert A.config_applications({"applications": ["marine"]}) == (["marine"], None)
    apps, err = A.config_applications({"applications": ["rocket"]})
    assert apps == [] and "rocket" in err
    assert A.config_applications({}) == ([], None)


# -- cooling gate ---------------------------------------------------------------

@pytest.fixture()
def registry(tmp_path, monkeypatch):
    f = tmp_path / "cooling_options.yaml"
    f.write_text(yaml.safe_dump({"version": 1, "dies": {
        "MIXED": {"cooling_options": ["robotics", "propeller_air"],
                  "propellers": ["p1", "p2"], "defaults": {"L13": "p1"}},
        "DRONE": {"cooling_options": ["propeller_air"], "propellers": ["p1"],
                  "defaults": {"L12": "p1"}},
    }}), encoding="utf-8")
    monkeypatch.setattr(CO, "_FILE", f)


def test_propeller_or_unset_keeps_the_registry_as_is(registry):
    for apps in (None, [], ["propeller"], ["robot", "propeller"]):
        out = CO.cooling_options("MIXED", "L13", applications=apps)
        assert out["cooling_options"] == ["robotics", "propeller_air"]
        assert out["propellers"] == ["p1", "p2"] and out["default_propeller"] == "p1"


def test_robot_loses_the_propeller_and_keeps_robotics(registry):
    out = CO.cooling_options("MIXED", "L13", applications=["robot"])
    assert out["cooling_options"] == ["robotics"] and out["restricted"] is True
    assert out["propellers"] == [] and out["default_propeller"] is None
    assert out["application"] == "robot" and out["propeller_hidden"] is True
    # a drone die handed to a robot: robotics replaces the propeller
    assert CO.cooling_options("DRONE", "L12", applications=["robot"])["cooling_options"] == ["robotics"]


@pytest.mark.parametrize("app_name", ["aviation", "marine"])
def test_liquid_cooled_applications_have_no_propeller(registry, app_name):
    out = CO.cooling_options("DRONE", "L12", applications=[app_name])
    assert out["cooling_options"] is None and out["restricted"] is False
    assert out["propellers"] == []
    out = CO.cooling_options("MIXED", "L13", applications=[app_name])
    assert out["cooling_options"] == ["robotics"]       # robotics is the registry's, not ours to drop


def test_the_configure_context_states_application_and_gates_cooling(registry):
    fam = {"die": "MIXED", "name": "L13", "applications": ["robot"]}
    ctx = CL.context(None, fam)
    assert ctx["applications"] == ["robot"] and ctx["application"] == "robot"
    assert ctx["cooling"]["propellers"] == [] and ctx["cooling"]["cooling_options"] == ["robotics"]
    assert CL.context(None, {"die": "MIXED", "name": "L13"})["application"] is None
    bad = CL.context(None, {"die": "MIXED", "name": "L13", "applications": "robot"})
    assert bad["applications"] == [] and "list" in bad["applications_error"]


def test_a_preset_carries_its_configurations_applications():
    pr = CL.preset_of({"name": "L13", "die": "D", "applications": ["robot", "propeller"]})
    assert pr["applications"] == ["robot", "propeller"] and pr["application"] == "robot"
    assert CL.preset_of({"name": "L13", "die": "D"})["application"] is None


# -- tree exposure + refused writes ----------------------------------------------

@pytest.fixture()
def dies(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family as fam
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-10-13T10:00:00",
        "geometry": {"num_slots": 12, "num_poles": 10, "stator_diameter": 40.0,
                     "magnet_height": 3.0, "motor_length": 40.0},
    }, sort_keys=False), encoding="utf-8")
    base = {"die": DIE, "role": "motor",
            "geometry_overrides": {"motor_length": 12.0, "wire_height": 1.0},
            "winding": {"connection": "star"},
            "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
            "duties": [{"name": "rated", "mode": "motor", "current_arms": 40.0, "rpm": 6000.0}]}
    docs = {"L12": {"name": "L12", **base, "applications": ["robot"]},
            "L20": {"name": "L20", **base},
            "L30": {"name": "L30", **base, "applications": ["rocket"]}}
    for n, d in docs.items():
        (root / DIE / f"{n}.yaml").write_text(yaml.safe_dump(d, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(fam, "_DIES_DIR", root)
    return root


@pytest.fixture()
def granted(tmp_path, monkeypatch, dies):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U
    users_file = tmp_path / "users.json"
    if _REAL_USERS.exists():
        shutil.copy2(_REAL_USERS, users_file)
    monkeypatch.setattr(U, "_USERS_FILE", users_file)
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.delenv("CATALOG_GRANT_ALL_REGISTERED", raising=False)
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    return {"Authorization": f"Bearer {U.issue_token(ADMIN)}"}


def _cfgs(h):
    r = client.get("/api/family/tree", headers=h)
    assert r.status_code == 200, r.text
    die = next(d for d in r.json()["dies"] if d["name"] == DIE)
    return {c["name"]: c for c in die["configs"]}


def test_tree_exposes_the_list_the_default_and_the_flags(dies, granted):
    c = _cfgs(granted)
    assert c["L12"]["applications"] == ["robot"] and c["L12"]["application"] == "robot"
    assert c["L12"]["application_set"] is True and c["L12"]["applications_error"] is None
    # not set: today's behaviour, flagged
    assert c["L20"]["applications"] == [] and c["L20"]["application"] is None
    assert c["L20"]["application_set"] is False and c["L20"]["applications_error"] is None
    # a malformed file does not take the catalog down; it is named
    assert c["L30"]["application_set"] is False and "rocket" in c["L30"]["applications_error"]


def test_saving_an_unknown_application_is_a_422(dies):
    from motor_ai_sim.routes import family as fam
    p = dies / DIE / "L12.yaml"
    doc = yaml.safe_load(p.read_text("utf-8"))
    doc["applications"] = ["rocket"]
    with pytest.raises(HTTPException) as e:
        fam._save_yaml(p, doc)
    assert e.value.status_code == 422 and "rocket" in str(e.value.detail)
    doc["applications"] = ["robot", "propeller"]          # a second entry is a data edit
    fam._save_yaml(p, doc)
    assert yaml.safe_load(p.read_text("utf-8"))["applications"] == ["robot", "propeller"]


# -- migration script -----------------------------------------------------------

def _script():
    spec = importlib.util.spec_from_file_location("set_applications",
                                                  _ROOT / "scripts" / "set_applications.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def tree(tmp_path):
    root = tmp_path / "dies"

    def put(die, cfg, text):
        d = root / die
        d.mkdir(parents=True, exist_ok=True)
        (d / "die.yaml").write_text("name: x\n", encoding="utf-8")
        (d / f"{cfg}.yaml").write_bytes(text.encode("utf-8"))

    put("CIANO14 40 new", "L12", "name: L12\r\ndie: CIANO14 40 new\r\nrole: motor\r\nduties: []\r\n")
    put("CIANO14 40 new", "L99", "name: L99\ndie: CIANO14 40 new\n")        # not in the table
    put("CILN28", "M1-L220", "name: M1-L220\ndie: CILN28\napplications: [robot]\n")  # a value already set
    put("CIANO28 85 20SW1200", "L13", "name: L13\ndie: CIANO28 85 20SW1200\napplications: [robot]\n")
    put("CIANO 150_40", "L35", "name: L35\ndie: CIANO 150_40\n")  # no rule: untouched
    return root


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_script_dry_run_writes_nothing(tree, capsys):
    before = _snapshot(tree)
    assert _script().run([tree], apply=False) == 0
    assert _snapshot(tree) == before
    assert "dry run" in capsys.readouterr().out


def test_script_apply_sets_backs_up_is_idempotent_and_keeps_existing_values(tree):
    S = _script()
    assert S.run([tree], apply=True) == 0
    l12 = tree / "CIANO14 40 new" / "L12.yaml"
    raw = l12.read_bytes().decode()
    assert "applications: [propeller]\r\n" in raw and "\n" not in raw.replace("\r\n", "")  # CRLF kept
    assert yaml.safe_load(raw)["applications"] == ["propeller"]
    assert yaml.safe_load(raw)["duties"] == []                                  # rest intact
    baks = list((tree / "CIANO14 40 new").glob("L12.yaml.bak-applications-*"))
    assert len(baks) == 1 and b"applications" not in baks[0].read_bytes()
    # not in the table / a different value already there: untouched
    assert b"applications" not in (tree / "CIANO14 40 new" / "L99.yaml").read_bytes()
    assert b"applications" not in (tree / "CIANO 150_40" / "L35.yaml").read_bytes()
    assert yaml.safe_load((tree / "CILN28" / "M1-L220.yaml").read_text())["applications"] == ["robot"]
    # second run: nothing new written, no second backup
    after = _snapshot(tree)
    assert S.run([tree], apply=True) == 0
    assert _snapshot(tree) == after


def test_script_table_matches_the_owner_decision():
    R = _script().RULES
    assert R["CIANO14 30_10"] == (["propeller"], None)
    assert R["CIANO28 85 20SW1200"] == (["robot"], ["L13"])
    assert R["CIANO10 200 opt"][0] == ["aviation"] and R["CILN28"][0] == ["marine"]
    assert R["M1 850Nm"] == (["marine"], None)


# -- admin editor endpoint ---------------------------------------------------------

def _put(h, cfg, apps, die=DIE):
    return client.put(f"/api/family/config/{die}/{cfg}/applications", headers=h,
                      json={"applications": apps})


@pytest.fixture()
def audit(tmp_path, monkeypatch):
    f = tmp_path / "admin_audit.jsonl"
    monkeypatch.setenv("ADMIN_AUDIT_FILE", str(f))
    return f


def test_put_replaces_only_the_applications_line_and_audits(dies, granted, audit):
    p = dies / DIE / "L12.yaml"
    before = p.read_text("utf-8").replace("\n", "\r\n")          # a CRLF file
    p.write_bytes(before.encode("utf-8"))
    r = _put(granted, "L12", ["aviation", "propeller"])
    assert r.status_code == 200, r.text
    assert r.json()["applications"] == ["aviation", "propeller"] and r.json()["application"] == "aviation"
    after = p.read_bytes().decode("utf-8")
    assert before.replace("applications:\r\n- robot\r\n", "applications: [aviation, propeller]\r\n") == after
    assert "\n" not in after.replace("\r\n", "")                    # still CRLF only
    assert _cfgs(granted)["L12"]["applications"] == ["aviation", "propeller"]
    assert len(list((dies / DIE).glob("L12.yaml.bak-applications-*"))) == 1
    rec = [__import__("json").loads(x) for x in audit.read_text("utf-8").splitlines()]
    assert rec[-1]["action"] == "config.applications" and rec[-1]["target"] == f"{DIE}/L12"
    assert rec[-1]["details"]["from"] == ["robot"] and rec[-1]["details"]["to"] == ["aviation", "propeller"]


def test_put_inserts_when_unset_and_is_a_noop_when_unchanged(dies, granted, audit):
    assert _put(granted, "L20", ["marine"]).status_code == 200
    assert yaml.safe_load((dies / DIE / "L20.yaml").read_text("utf-8"))["applications"] == ["marine"]
    r = _put(granted, "L20", ["marine"])
    assert r.status_code == 200 and r.json()["changed"] is False
    assert len(list((dies / DIE).glob("L20.yaml.bak-applications-*"))) == 1


def test_put_rejects_bad_values_loudly(dies, granted, audit):
    before = (dies / DIE / "L12.yaml").read_bytes()
    for bad in (["rocket"], [], ["robot", "robot"]):
        r = _put(granted, "L12", bad)
        assert r.status_code == 422, bad
    assert _put(granted, "NOPE", ["robot"]).status_code in (404, 422)
    assert (dies / DIE / "L12.yaml").read_bytes() == before


def test_put_is_admin_only(dies):
    from motor_ai_sim.routes import family as fam
    with pytest.raises(HTTPException) as e:
        fam.set_config_applications(DIE, "L12", fam.ApplicationsPut(applications=["marine"]),
                                    _w={"is_admin": False, "user": {"email": "u@example.com"}})
    assert e.value.status_code == 403


@pytest.mark.skipif(__import__("os").name == "nt", reason="os.replace over a read-only file needs POSIX")
def test_put_keeps_the_mode_of_a_read_only_seeded_file(dies, granted, audit):
    import os
    import stat
    p = dies / DIE / "L12.yaml"
    os.chmod(p, 0o444)
    assert _put(granted, "L12", ["marine"]).status_code == 200
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o444
    assert yaml.safe_load(p.read_text("utf-8"))["applications"] == ["marine"]


def test_edit_line_handles_inline_block_and_missing_forms():
    f = A.edit_applications_line
    assert f("name: x\napplications: [robot]\nduties: []\n", ["marine"]) == "name: x\napplications: [marine]\nduties: []\n"
    assert f("name: x\napplications:\n  - robot\n  - marine\nduties: []\n", ["marine"]) == "name: x\napplications: [marine]\nduties: []\n"
    assert f("name: x\ndie: d\n", ["robot", "marine"]) == "name: x\ndie: d\napplications: [robot, marine]\n"
