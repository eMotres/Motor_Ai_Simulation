"""Test / validation duties (``kind: test``, owner 2026-10-28): the flag, the API that writes it, and the public
surfaces that leave such a duty out.  The bench-test comparison (``motor_tests``) keeps reading them from the
same configuration yaml.

No solver: the die folder is a temp directory.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.duty_kind import duty_kind, is_test_duty, operating_duties
from motor_ai_sim.routes.family import config_default_duty, config_rated_duty

_ROOT = Path(__file__).resolve().parents[1]
_REAL_USERS = _ROOT / "config" / "users.json"
DIE, CFG, ADMIN = "TESTDIE KIND", "L50", "admin@example.com"
client = TestClient(app)


def test_the_flag_the_legacy_name_and_the_explicit_override():
    assert is_test_duty({"name": "x", "kind": "test"}) and duty_kind({"name": "x", "kind": " TEST "}) == "test"
    assert not is_test_duty({"name": "rated"}) and duty_kind({"name": "rated"}) == "operating"
    # legacy bench free-run reproductions, saved before the flag existed
    assert is_test_duty({"name": "noload_3021"}) and is_test_duty({"name": "NoLoad_1031"})
    assert not is_test_duty({"name": "no-load 4000 rpm"})            # only the exact legacy family
    # an explicit kind (any value) outranks the name; a non-duty is never a test duty
    assert not is_test_duty({"name": "noload_3021", "kind": "operating"})
    assert not is_test_duty({"name": "noload_3021", "kind": "rated"})
    assert not is_test_duty(None) and not is_test_duty("noload_1")
    assert operating_duties([{"name": "a"}, {"name": "noload_1"}, {"name": "b", "kind": "test"}]) == [{"name": "a"}]


def test_a_test_duty_is_never_the_default_or_the_rated_duty():
    c = {"duties": [{"name": "noload_3021"}, {"name": "rated"}, {"name": "peak"}]}
    assert config_default_duty(c) == "rated"
    only_first = {"duties": [{"name": "noload_3021"}, {"name": "continuous"}]}
    assert config_default_duty(only_first) == "continuous"            # not the first duty, which is a bench point
    assert config_rated_duty(only_first) == "continuous"
    # a flag that points AT a test duty is not honoured as the default either
    assert config_default_duty({**c, "default_duty": "noload_3021"}) == "rated"
    assert config_default_duty({"duties": [{"name": "noload_1"}]}) is None


@pytest.fixture()
def dies(tmp_path, monkeypatch):
    from motor_ai_sim.routes import family as fam
    root = tmp_path / "dies"
    (root / DIE).mkdir(parents=True)
    (root / DIE / "die.yaml").write_text(yaml.safe_dump({
        "name": DIE, "locked": False, "created": "2026-10-13T10:00:00",
        "geometry": {"num_slots": 24, "num_poles": 20, "stator_diameter": 200.0,
                     "magnet_height": 3.0, "motor_length": 50.0},
    }, sort_keys=False), encoding="utf-8")
    (root / DIE / f"{CFG}.yaml").write_text(yaml.safe_dump({
        "name": CFG, "die": DIE, "role": "motor",
        "geometry_overrides": {"motor_length": 50.0, "wire_height": 1.0},
        "winding": {"connection": "star"},
        "materials": {"magnet": "N42SH", "stator_core": "20SW1200"},
        "duties": [{"name": "noload_3021", "mode": "motor", "current_arms": 0.1, "rpm": 3021.0},
                   {"name": "continuous", "mode": "motor", "current_arms": 40.0, "rpm": 3000.0},
                   {"name": "peak", "mode": "motor", "current_arms": 80.0, "rpm": 3500.0}],
    }, sort_keys=False), encoding="utf-8")
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


def _stored(dies):
    return yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text("utf-8"))["duties"]


def _tree_cfg(h):
    r = client.get("/api/family/tree", headers=h)
    assert r.status_code == 200, r.text
    die = next(d for d in r.json()["dies"] if d["name"] == DIE)
    return next(c for c in die["configs"] if c["name"] == CFG)


def test_the_tree_keeps_the_test_duty_marked_and_the_default_skips_it(dies, granted):
    c = _tree_cfg(granted)
    assert [d["name"] for d in c["duties"]] == ["noload_3021", "continuous", "peak"]      # engineers still see it
    assert [d.get("kind") for d in c["duties"]] == ["test", None, None]
    assert c["default_duty"] == "continuous"                         # the first duty is a bench point: skipped


def test_the_duty_api_accepts_persists_and_clears_the_flag(dies, granted):
    body = {"die": DIE, "config": CFG,
            "duty": {"name": "bench 4000", "mode": "motor", "rpm": 4000.0, "current_arms": 0.1, "gamma_deg": 0.0,
                     "kind": "test"}}
    r = client.post("/api/family/duty", headers=granted, json=body)
    assert r.status_code == 200, r.text
    assert next(d for d in _stored(dies) if d["name"] == "bench 4000")["kind"] == "test"
    # re-saving the operating point without `kind` keeps the flag
    body["duty"].pop("kind")
    body["duty"]["rpm"] = 4100.0
    assert client.post("/api/family/duty", headers=granted, json=body).status_code == 200
    again = next(d for d in _stored(dies) if d["name"] == "bench 4000")
    assert again["kind"] == "test" and again["rpm"] == 4100.0
    # an ordinary duty carries no kind key; an unknown kind is refused
    plain = {"die": DIE, "config": CFG, "duty": {"name": "cruise", "mode": "motor", "rpm": 2000.0,
                                                  "current_arms": 20.0, "gamma_deg": 0.0}}
    assert client.post("/api/family/duty", headers=granted, json=plain).status_code == 200
    assert "kind" not in next(d for d in _stored(dies) if d["name"] == "cruise")
    plain["duty"]["kind"] = "secret"
    assert client.post("/api/family/duty", headers=granted, json=plain).status_code == 422
    # "operating" is explicit (outranks the legacy noload_ name rule); "" drops the key again
    body["duty"].update(name="noload_3021", kind="operating")
    assert client.post("/api/family/duty", headers=granted, json=body).status_code == 200
    assert next(d for d in _stored(dies) if d["name"] == "noload_3021")["kind"] == "operating"
    body["duty"]["kind"] = ""
    assert client.post("/api/family/duty", headers=granted, json=body).status_code == 200
    assert "kind" not in next(d for d in _stored(dies) if d["name"] == "noload_3021")


def test_the_motor_tests_comparison_still_reads_the_test_duties_from_the_configuration(dies):
    """The model side of the bench comparison reads the yaml directly: nothing above hides it from motor_tests."""
    from motor_ai_sim.motor_tests import model_source
    doc = yaml.safe_load((dies / DIE / f"{CFG}.yaml").read_text("utf-8"))
    assert any(d["name"] == "noload_3021" for d in doc["duties"])
    assert model_source._NOLOAD_NAME.search("noload_3021")


def test_the_comparison_creates_its_missing_bench_points_as_test_duties():
    from motor_ai_sim.motor_tests import comparison
    body = comparison._create_duty("D", "C", rpm=4000, current=0.0)["body"]
    assert body["duty"]["kind"] == "test"


def test_public_lists_leave_the_test_duties_out():
    from motor_ai_sim import public_motors, support_catalog
    cfg = {"name": "L50", "default_duty": "noload_3021", "card_date": "2026-10-28", "duties": [
        {"name": "noload_3021", "kind": "test", "rpm": 3021.0, "current_arms": 0.1},
        {"name": "continuous", "rpm": 3000.0, "current_arms": 40.0, "torque_nm": 5.0, "power_kw": 1.5,
         "result": {"efficiency_pct": 95.0}}]}
    # the assistant / public Motors row quote the rating from a real duty
    assert support_catalog._pick_rated(cfg)["name"] == "continuous"
    assert support_catalog._pick_rated({"duties": [cfg["duties"][0]]}) is None
    assert support_catalog._other_duties(cfg, cfg["duties"][1]) == []
    row = public_motors.build_row({"name": "CIANO20 200_50"}, cfg)
    assert row is not None and row["rated"]["speed_rpm"] == 3000
