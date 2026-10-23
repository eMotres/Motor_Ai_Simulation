from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim.development import store


def test_snapshot_list_survives_missing_source_with_admin_or_exact_engineer_grant(tmp_path, monkeypatch):
    from motor_ai_sim.development import routes
    from motor_ai_sim import motor_access
    from motor_ai_sim.routes import family

    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    (tmp_path / "projects").mkdir()
    project = {"id": "alpha", "members": [], "linked_motors": []}
    store.create_project(project)
    machine = {"die_name": "CANON", "config_name": "A",
               "die": {"geometry": {"stator_diameter": 30}},
               "configuration": {"materials": {}, "winding": {"turns": 2}}}
    store.save_snapshot("alpha", {"id": "snapshot-archive", "version": 1,
        "created_at": "2026-10-23T00:00:00Z", "machine": machine,
        "access_scope": {"kind": "catalog", "die": "CANON", "config": "A",
                         "grant_key": "CANON/A"},
        "results": [{"duty": "rated", "has_result": True,
                     "operating_point": {"rpm": 1000, "current_arms": 8},
                     "comparison": {"torque_nm": 2.5},
                     "provenance": {"kind": "unknown", "source": "stored_run"}}]})
    monkeypatch.setattr(family, "_iter_die_entries", lambda: [])  # archived source is absent
    grants = {"configs": frozenset()}
    monkeypatch.setattr(motor_access, "catalog_access", lambda _auth: grants)
    live = {"user": {"email": "engineer@example.test", "role": "engineer"}}
    monkeypatch.setattr(routes.access, "require_project", lambda *_a, **_kw:
        (live["user"], project, "owner"))
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    assert client.get("/api/projects/alpha/snapshots").json()["snapshots"] == []
    grants["configs"] = frozenset({"CANON/A"})
    visible = client.get("/api/projects/alpha/snapshots").json()["snapshots"]
    assert len(visible) == 1
    row = visible[0]
    assert row["id"] == "snapshot-archive"
    assert row["results"][0]["comparison"] == {"torque_nm": 2.5}
    assert row["results"][0]["provenance"] == {"kind": "unknown", "source": "stored_run"}
    assert "machine" not in row and "geometry" not in str(row)
    live["user"] = {"email": "customer@example.test", "role": "customer"}
    assert client.get("/api/projects/alpha/snapshots").json()["snapshots"] == []
    live["user"] = {"email": "admin@example.test", "role": "admin"}
    assert len(client.get("/api/projects/alpha/snapshots").json()["snapshots"]) == 1
