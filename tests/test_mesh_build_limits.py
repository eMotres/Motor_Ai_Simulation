"""CadQuery builds behind GET /api/geometry/mesh: counted per account, staff not counted, and the disk cache has a byte cap.

No CAD runs here: a stand-in builder counts the builds."""
from __future__ import annotations

import json
import sys
import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

from motor_ai_sim.api import app
from motor_ai_sim.routes import geometry as G

CUSTOMER = "customer@example.com"
OTHER = "other@example.com"
ENGINEER = "engineer@example.com"
ADMIN = "admin@example.com"

client = TestClient(app)


def _geo(i: int) -> dict:
    return {"geo": json.dumps({"motor_length": 40.0 + i / 10.0})}


@pytest.fixture
def env(tmp_path, monkeypatch):
    from motor_ai_sim import auth
    from motor_ai_sim import users as U

    builds = []

    class FakeMotor:
        def set_parameters(self, params):
            self.params = params

        def build_all(self):
            builds.append(self.params.get("motor_length"))

        def get_all_mesh_data(self):
            return {"stator_core": {"vertices": [[0.0, 0.0, 0.0]], "faces": []}}

    fake = types.ModuleType("motor_ai_sim.cadquery_geometry")
    fake.CadQueryMotor = FakeMotor
    monkeypatch.setitem(sys.modules, "motor_ai_sim.cadquery_geometry", fake)
    monkeypatch.setitem(G.__dict__, "_MESH_DISK_DIR", tmp_path / "mesh_cache")
    monkeypatch.setattr(U, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-the-real-one")
    monkeypatch.setattr(auth, "_ADMIN_EMAILS", {ADMIN})
    monkeypatch.setattr(auth, "AUTH_ENFORCE", False)
    U.create_user(CUSTOMER, "password-customer", role="user", name="Customer")
    U.create_user(OTHER, "password-other", role="user", name="Other")
    U.create_user(ENGINEER, "password-engineer", role="engineer", name="Engineer")
    U.create_user(ADMIN, "password-admin", role="admin", name="Admin")
    if hasattr(G, "_MESH_BUILD_WINDOW"):
        G._MESH_BUILD_WINDOW.reset_all()
        G._MESH_BUILD_RUNNING.clear()
    hdr = lambda e: {"Authorization": f"Bearer {U.issue_token(e)}"}   # noqa: E731
    yield {"builds": builds, "customer": hdr(CUSTOMER), "other": hdr(OTHER), "engineer": hdr(ENGINEER), "admin": hdr(ADMIN),
           "dir": tmp_path / "mesh_cache"}


def _spend_the_window(env, who: str) -> None:
    for i in range(G.MESH_BUILD_MAX_IN_WINDOW):
        assert client.get("/api/geometry/mesh", params=_geo(i), headers=env[who]).status_code == 200


def test_an_account_past_the_limit_gets_429_and_no_build_starts(env):
    _spend_the_window(env, "customer")
    done = len(env["builds"])
    r = client.get("/api/geometry/mesh", params=_geo(999), headers=env["customer"])
    assert r.status_code == 429
    assert r.headers["retry-after"]
    assert len(env["builds"]) == done


def test_the_limit_is_per_account(env):
    _spend_the_window(env, "customer")
    assert client.get("/api/geometry/mesh", params=_geo(999), headers=env["other"]).status_code == 200


def test_a_cached_machine_answers_after_the_limit(env):
    """Reopening a machine already built costs nothing: only real builds count."""
    _spend_the_window(env, "customer")
    assert client.get("/api/geometry/mesh", params=_geo(0), headers=env["customer"]).status_code == 200


def test_a_repeated_view_of_one_machine_is_one_build(env):
    for _ in range(G.MESH_BUILD_MAX_IN_WINDOW + 5):
        assert client.get("/api/geometry/mesh", params=_geo(1), headers=env["customer"]).status_code == 200
    assert len(env["builds"]) == 1


@pytest.mark.parametrize("staff", ["engineer", "admin"])
def test_staff_are_not_counted(env, staff):
    for i in range(G.MESH_BUILD_MAX_IN_WINDOW + 3):
        assert client.get("/api/geometry/mesh", params=_geo(i), headers=env[staff]).status_code == 200


def test_overlapping_builds_of_one_account_are_capped(env):
    """An edit made while an earlier build still runs is fine, a pile of them is not; the turn comes back after the build."""
    from fastapi import HTTPException
    from starlette.requests import Request

    request = Request({"type": "http", "headers": [(b"authorization", env["customer"]["Authorization"].encode())]})
    release, entered = threading.Event(), threading.Barrier(G.MESH_BUILD_MAX_RUNNING + 1)

    def build():
        with G._mesh_build_turn(request):
            entered.wait(5)
            release.wait(5)

    threads = [threading.Thread(target=build) for _ in range(G.MESH_BUILD_MAX_RUNNING)]
    for t in threads:
        t.start()
    entered.wait(5)
    with pytest.raises(HTTPException) as refused:
        with G._mesh_build_turn(request):
            pass
    assert refused.value.status_code == 429
    release.set()
    for t in threads:
        t.join(5)
    assert not G._MESH_BUILD_RUNNING
    with G._mesh_build_turn(request):
        pass


def test_the_boot_warm_up_is_not_counted(env):
    """The warm-up calls the route as a plain function, with no request."""
    for i in range(G.MESH_BUILD_MAX_IN_WINDOW + 1):
        G.get_geometry_mesh(_geo(i)["geo"])
    assert not G._MESH_BUILD_WINDOW._hits


def test_the_disk_cache_stays_under_its_byte_cap(env, monkeypatch):
    payload = {"blob": "".join(str(i * 7919) for i in range(200))}
    G._mesh_disk_put("mesh3d", "first", payload)
    one = next(env["dir"].glob("*.json.gz")).stat().st_size
    monkeypatch.setattr(G, "_MESH_DISK_MAX_BYTES", 3 * one + one // 2, raising=False)
    for i in range(12):
        time.sleep(0.02)                                                    # the trim goes by file time
        G._mesh_disk_put("mesh3d", f"hash{i:02d}", payload)
    files = list(env["dir"].glob("*.json.gz"))
    assert len(files) == 3
    assert G._mesh_disk_get("mesh3d", "hash11") == payload
    assert G._mesh_disk_get("mesh3d", "hash00") is None
