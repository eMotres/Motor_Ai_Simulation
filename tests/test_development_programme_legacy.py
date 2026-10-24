from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, users
from motor_ai_sim.development import convert_programme, legacy_adapter, programme_routes, programme_store, store
from motor_ai_sim.development.admin_routes import router as admin_router
from motor_ai_sim.development.routes import router

FIXTURE = Path(__file__).parent / "fixtures" / "development_programme" / "boat_programme.json"


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    return tmp_path / "projects"


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


PROJECT = {
    "id": "alpha", "title": "Alpha", "customer_org": "Example Org", "status": "design",
    "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-02-01T00:00:00+00:00",
    "owner_engineer": "eng@example.test",
    "members": [{"email": "eng@example.test", "role": "owner"}, {"email": "customer@example.test", "role": "customer"}],
    "linked_motors": [{"die": "synthetic-die-t", "config": "tm-a", "status": "current"},
                      {"die": "synthetic-die-t", "config": "tm-b", "status": "trial"}],
    "notes": "internal", "pending_invites": [{"email": "later@example.test", "role": "customer"}],
    "future_field": {"nested": [1, 2, {"kept": True}]},
    "grant_removal_candidates": [],
}


@pytest.fixture
def legacy(project_root):
    base = project_root / "alpha"
    write(base / "project.json", PROJECT)
    write(base / "draft.json", {"draft": {"schema_version": 2, "fields": {"rated_power_kw": 12}, "mission_profile": [],
                                          "unknown_section": {"x": 1}},
                                "schema_version": 2, "revision": 3, "version": 2})
    write(base / "spec" / "index.json", {"versions": [{"version": 1, "created_at": "2026-01-10T00:00:00+00:00"}]})
    write(base / "spec" / "v1.json", {"version": 1, "created_at": "2026-01-10T00:00:00+00:00", "draft": {"fields": {}}})
    # v2 is on disk but missing from the index; the route helper repairs the index, the adapter must not.
    write(base / "spec" / "v2.json", {"version": 2, "created_at": "2026-01-20T00:00:00+00:00", "draft": {"fields": {"a": 1}}})
    write(base / "snapshots" / "snapshot-0001.json", {"id": "snapshot-0001", "version": 1, "kind": "history",
                                                      "die": "synthetic-die-h", "config": "old",
                                                      "created_at": "2026-01-05T00:00:00+00:00"})
    events = [{"time": "2026-01-02T00:00:00+00:00", "actor_email": "eng@example.test", "action": "motor.link",
               "entity": "motor", "entity_id": "synthetic-die-h/old", "details": {}},
              {"time": "2026-01-03T00:00:00+00:00", "actor_email": "eng@example.test", "action": "motor.unlink",
               "entity": "motor", "entity_id": "synthetic-die-h/old", "details": {}}]
    (base / "audit.jsonl").write_text("".join(json.dumps(e) + "\r\n" for e in events), encoding="utf-8")
    write(project_root / "memberships.json", {"eng@example.test": ["alpha"], "customer@example.test": ["alpha"]})
    return base


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_adapter_reads_a_legacy_project_unchanged(legacy):
    view = legacy_adapter.read_project("alpha")
    assert view["id"] == "alpha" and view["title"] == "Alpha"
    assert [m["email"] for m in view["members"]] == ["eng@example.test", "customer@example.test"]
    assert view["draft"]["revision"] == 3 and view["draft"]["draft"]["unknown_section"] == {"x": 1}
    assert [v["version"] for v in view["spec_versions"]] == [1, 2]
    assert [m["config"] for m in view["linked_motors"]["current"]] == ["tm-a"]
    assert [m["config"] for m in view["linked_motors"]["trial"]] == ["tm-b"]
    assert [(h["config"], h["unlinked_at"]) for h in view["motor_history"]] == [("old", "2026-01-03T00:00:00+00:00")]
    assert [s["id"] for s in view["snapshots"]] == ["snapshot-0001"]
    assert set(view["unknown_fields"]) == {"future_field", "grant_removal_candidates"}


def test_unknown_fields_and_key_order_round_trip(legacy):
    original = (legacy / "project.json").read_text(encoding="utf-8")
    rebuilt = legacy_adapter.project_document(legacy_adapter.read_project("alpha"))
    assert json.dumps(rebuilt, indent=2) == original


def test_adapter_and_dry_run_never_write(legacy, project_root):
    before = tree(project_root)
    legacy_adapter.read_project("alpha")
    legacy_adapter.read_project("missing")
    legacy_adapter.read_project("../alpha")
    manifest = convert_programme.dry_run(catalog_lookup=lambda die, config: None)
    assert tree(project_root) == before
    assert manifest["store_fingerprint_before"] == manifest["store_fingerprint_after"]
    assert json.loads((legacy / "spec" / "index.json").read_text(encoding="utf-8"))["versions"][0]["version"] == 1


def test_dry_run_manifest_maps_counts_hashes_links_and_access(legacy):
    catalog = {("synthetic-die-t", "tm-a"): "a" * 64}
    manifest = convert_programme.dry_run(catalog_lookup=lambda die, config: catalog.get((die, config)))
    assert manifest["mutating_migration"] == "disabled"
    assert manifest["totals"] == {"projects": 1, "missing": 0, "unresolved_links": 2, "access_warnings": 3,
                                  "projects_with_issues": 0}
    plan = manifest["projects"][0]
    assert plan["programme_id"] == "legacy-alpha" and plan["validation_issues"] == []
    assert plan["before"]["linked_current"] == 1 and plan["before"]["linked_trial"] == 1
    assert plan["before"]["spec_versions"] == 2 and plan["before"]["snapshots"] == 1
    assert plan["before"]["unknown_fields"] == ["future_field", "grant_removal_candidates"]
    assert plan["after"]["variants"] == 3 and plan["after"]["instances"] == 1
    variants = plan["intended_programme"]["components"][0]["variants"]
    assert [(v["ref"]["config"], v["status"], v["ref"]["content_hash"]) for v in variants] == [
        ("tm-a", "current", "a" * 64), ("tm-b", "trial", None), ("old", "history", None)]
    assert plan["intended_programme"]["instances"][0]["variant"] == variants[0]["id"]
    assert {(u["config"], u["origin"]) for u in plan["unresolved_links"]} == {
        ("tm-b", "linked_motors (trial)"), ("old", "motor_history")}
    assert [w["code"] for w in plan["access_warnings"]] == ["member_loses_access", "member_loses_access", "pending_invites"]
    import hashlib
    for name in ("project.json", "draft.json", "spec/v2.json", "audit.jsonl", "snapshots/snapshot-0001.json"):
        assert plan["source_hashes"][name] == hashlib.sha256((legacy / name).read_bytes()).hexdigest()
    # Variant ids are deterministic from the catalog identity, so two runs map the same way.
    again = convert_programme.dry_run(catalog_lookup=lambda die, config: catalog.get((die, config)))
    assert again["projects"][0]["intended_programme"] == plan["intended_programme"]


def test_dry_run_flags_projects_that_need_an_engineer_decision(legacy, project_root):
    project = json.loads((legacy / "project.json").read_text(encoding="utf-8"))
    project["linked_motors"][1]["status"] = "current"
    write(legacy / "project.json", project)
    plan = convert_programme.dry_run(["alpha", "nope"], catalog_lookup=None)
    assert plan["missing_projects"] == ["nope"]
    row = plan["projects"][0]
    assert [i["code"] for i in row["validation_issues"]] == ["multiple_current_variants"]
    assert row["intended_programme"]["instances"][0]["variant"] is None
    assert any("several current" in a for a in row["assumptions"])


def test_command_writes_only_its_manifest_and_apply_is_disabled(legacy, project_root, tmp_path, capsys):
    before = tree(project_root)
    out = tmp_path / "out" / "dry-run.json"
    assert convert_programme.main(["--output", str(out), "--apply"]) == 2
    assert not out.exists()
    assert convert_programme.main(["--output", str(project_root / "alpha" / "manifest.json"), "--no-catalog"]) == 2
    assert "outside the project store" in capsys.readouterr().err
    assert convert_programme.main(["--output", str(out), "--no-catalog", "--project", "alpha"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["projects"][0]["project_id"] == "alpha"
    assert tree(project_root) == before


def test_programme_records_are_absent_from_every_non_admin_payload(legacy, project_root, monkeypatch):
    caller = {"user": {"email": "customer@example.test", "role": "user"}}
    monkeypatch.setattr(auth, "resolve_user", lambda token: dict(caller["user"]) if token else None)
    monkeypatch.setattr(auth, "_is_admin_caller",
                        lambda token: (caller["user"].get("role") == "admin", dict(caller["user"])))
    monkeypatch.setattr(users, "get_user", lambda email: {"email": email, "email_verified": True})
    monkeypatch.setenv(programme_routes.ENV_FLAG, "1")
    app = FastAPI(); app.include_router(router); app.include_router(admin_router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer dev"}
    callers = [{"email": "customer@example.test", "role": "user"}, {"email": "eng@example.test", "role": "engineer"},
               {"email": "admin@example.test", "role": "admin"}]
    paths = ["/api/projects", "/api/projects/membership", "/api/projects/alpha"]

    def snapshot():
        out = {}
        for user in callers:
            caller["user"] = user
            for path in paths:
                response = client.get(path, headers=headers)
                assert response.status_code == 200, (user, path, response.text)
                out[(user["role"], path)] = response.json()
            if user["role"] == "admin":
                listing = client.get("/api/admin/projects", headers=headers).json()
                for row in listing["projects"]:
                    row.pop("last_activity")  # legacy GETs append audit lines, so this moves on every read
                out[("admin", "/api/admin/projects")] = listing
        return out

    before = snapshot()
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    programme_store.commit("demo-foil-boat", data, expected_revision=0, actor="admin@example.test")
    after = snapshot()
    assert after == before
    assert all("demo-foil-boat" not in json.dumps(body) and "programme" not in json.dumps(body) for body in after.values())
    assert [p["id"] for p in store.list_projects()] == ["alpha"]
    assert store.membership_projects("customer@example.test") == ["alpha"]
    for user in callers[:2]:
        caller["user"] = user
        assert client.get("/api/projects/demo-foil-boat", headers=headers).status_code == 404
        assert client.get("/api/admin/projects/programmes/demo-foil-boat", headers=headers).status_code == 403
    caller["user"] = callers[2]
    assert client.get("/api/admin/projects/programmes/demo-foil-boat", headers=headers).status_code == 200
