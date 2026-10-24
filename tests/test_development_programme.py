from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from motor_ai_sim import auth, json_store
from motor_ai_sim.development import programme, programme_routes, programme_store, store
from motor_ai_sim.development.admin_routes import router as admin_router

FIXTURE = Path(__file__).parent / "fixtures" / "development_programme" / "boat_programme.json"
PID = "demo-foil-boat"
ADMIN = {"email": "admin@example.test", "role": "admin"}


@pytest.fixture
def project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    return tmp_path / "projects"


def boat() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def codes(data: dict, programme_id: str = PID) -> list[str]:
    _, errors = programme.normalize(data, programme_id)
    return [e["code"] for e in errors]


def by_id(rows: list[dict], record_id: str) -> dict:
    return next(row for row in rows if row["id"] == record_id)


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# --- domain -------------------------------------------------------------------------------------------------------


def test_boat_fixture_is_valid_and_reconciles():
    doc, errors = programme.normalize(boat(), PID)
    assert errors == []
    machines = [c for c in doc["components"] if c["kind"] == "machine"]
    converters = [c for c in doc["components"] if c["kind"] == "converter"]
    active = lambda cid: [p for p in doc["positions"] if p["component"] == cid and not p["archived"]]
    assert {c["id"]: len(active(c["id"])) for c in machines} == {
        "traction-motor": 4, "main-generator": 1, "backup-generator": 1}
    assert sorted(len(active(c["id"])) for c in converters) == [3, 4]
    report = programme.reconciliation(doc)
    assert all(not row["gaps"] for row in report["components"].values())
    assert report["components"]["traction-motor"]["spare_lots"] == ["tm-spare-1"]
    assert report["components"]["traction-motor"]["spare_units"] == 1
    assert report["components"]["traction-motor"]["archived_positions"] == 1
    assert report["components"]["traction-motor"]["archived_lots"] == ["tm-proto-1"]
    assert sum(r["units"] for cid, r in report["components"].items() if cid.startswith("inverter")) == 7
    assert report["active_connections"] == 7 and report["archived_connections"] == 1


def test_main_generator_is_one_unit_with_two_ports_each_fed_by_its_own_converter():
    doc, _ = programme.normalize(boat(), PID)
    generator = by_id(doc["components"], "main-generator")
    assert [p["id"] for p in generator["ports"]] == ["w1", "w2"]
    lots = [i for i in doc["instances"] if i["component"] == "main-generator"]
    assert len(lots) == 1 and lots[0]["quantity"] == 1 and lots[0]["allocations"] == ["gen-main"]
    feeds = {}
    for connection in doc["connections"]:
        ends = {e["position"]: e["port"] for e in connection["ends"]}
        if "gen-main" in ends:
            feeds[ends["gen-main"]] = next(p for p in ends if p != "gen-main")
    assert feeds == {"w1": "inv-b-1", "w2": "inv-b-2"}


def test_allocation_gap_is_an_error():
    data = boat()
    by_id(data["instances"], "tm-lot-1")["allocations"].remove("tm-stbd-aft")
    assert codes(data) == ["allocation_gap"]


def test_lot_allocated_beyond_its_quantity_is_an_error():
    data = boat()
    lot = by_id(data["instances"], "tm-lot-1")
    lot["quantity"] = 3
    assert codes(data) == ["allocation_excess"]


def test_position_filled_twice_is_an_error():
    data = boat()
    by_id(data["instances"], "tm-spare-1")["allocations"] = ["tm-port-fwd"]
    assert codes(data) == ["allocation_excess"]


def test_allocation_must_match_the_position_component():
    data = boat()
    by_id(data["instances"], "tm-spare-1")["allocations"] = ["gen-backup"]
    assert "allocation_component_mismatch" in codes(data)


def test_archived_position_keeps_history_but_takes_no_active_unit_or_connection():
    data = boat()
    by_id(data["instances"], "tm-spare-1")["allocations"] = ["tm-centre"]
    assert codes(data) == ["archived_reference"]
    data = boat()
    by_id(data["connections"], "c-tm-centre")["archived"] = False
    assert codes(data) == ["archived_reference"]
    data = boat()
    by_id(data["components"], "backup-generator")["archived"] = True
    assert codes(data) == ["archived_reference", "archived_reference"]
    data = boat()
    by_id(data["positions"], "tm-port-fwd")["archived"] = True
    by_id(data["instances"], "tm-lot-1")["allocations"].remove("tm-port-fwd")
    by_id(data["connections"], "c-tm-port-fwd")["archived"] = True
    doc, errors = programme.normalize(data, PID)
    assert errors == []
    row = programme.reconciliation(doc)["components"]["traction-motor"]
    assert row["positions"] == 3 and row["archived_positions"] == 2 and row["spare_units"] == 2


def test_spare_lot_and_partial_lot_spares_are_reported_not_errors():
    data = boat()
    by_id(data["instances"], "inv-b-lot-1")["quantity"] = 5
    data["instances"].append({"id": "inv-b-spares", "component": "inverter-b", "variant": "inv-b-r1", "quantity": 2})
    doc, errors = programme.normalize(data, PID)
    assert errors == []
    row = programme.reconciliation(doc)["components"]["inverter-b"]
    assert row["units"] == 7 and row["spare_units"] == 4 and row["spare_lots"] == ["inv-b-spares"]


def test_duplicate_connections_are_rejected_in_either_direction():
    data = boat()
    data["connections"].append({"id": "c-dup", "ends": [{"position": "gen-main", "port": "w1"},
                                                        {"position": "inv-b-1", "port": "ac"}]})
    assert codes(data) == ["duplicate_connection"]


def test_a_single_port_cannot_be_connected_twice_but_a_multi_port_can():
    data = boat()
    data["connections"].append({"id": "c-second-feed", "ends": [{"position": "inv-b-3", "port": "ac"},
                                                               {"position": "gen-main", "port": "w1"}]})
    assert codes(data) == ["duplicate_connection", "duplicate_connection"]
    data = boat()
    data["connections"].append({"id": "c-dc-1", "ends": [{"position": "inv-a-1", "port": "dc"},
                                                        {"position": "inv-b-1", "port": "dc"}]})
    data["connections"].append({"id": "c-dc-2", "ends": [{"position": "inv-a-2", "port": "dc"},
                                                        {"position": "inv-b-1", "port": "dc"}]})
    assert codes(data) == []


def test_connection_endpoints_must_exist_and_differ():
    data = boat()
    data["connections"].append({"id": "c-bad-port", "ends": [{"position": "inv-a-1", "port": "dc"},
                                                            {"position": "tm-port-fwd", "port": "w2"}]})
    assert codes(data) == ["unknown_reference"]
    data = boat()
    data["connections"].append({"id": "c-self", "ends": [{"position": "gen-main", "port": "w1"},
                                                        {"position": "gen-main", "port": "w2"}]})
    assert codes(data) == ["self_connection"]


def test_cross_programme_references_are_rejected():
    data = boat()
    by_id(data["instances"], "tm-spare-1")["allocations"] = ["other-boat/positions/tm-port-fwd"]
    assert codes(data) == ["cross_programme_reference"]
    data = boat()
    by_id(data["positions"], "gen-main")["programme_id"] = "other-boat"
    assert codes(data) == ["cross_programme_reference"]
    data = boat()
    by_id(data["components"], "traction-motor")["variants"][0]["ref"] = {"source": "project", "project_id": "alpha"}
    assert codes(data) == ["cross_programme_reference"]
    assert codes(boat(), "other-boat") == ["cross_programme_reference"]


def test_source_scoped_references_to_the_same_programme_resolve():
    data = boat()
    by_id(data["instances"], "tm-lot-1")["allocations"] = [
        programme.scoped_id(PID, "positions", p) for p in by_id(data["instances"], "tm-lot-1")["allocations"]]
    data["connections"][0]["ends"][0]["position"] = f"{PID}/positions/inv-a-1"
    doc, errors = programme.normalize(data, PID)
    assert errors == []
    assert by_id(doc["instances"], "tm-lot-1")["allocations"][0] == "tm-port-fwd"
    data["connections"][1]["ends"][0]["position"] = f"{PID}/components/inv-a-2"
    assert codes(data) == ["invalid_reference"]


@pytest.mark.parametrize("bad", ["Tm-Lot", "tm_lot", "../tm", "tm/../x", "", " ", "-tm", "x" * 65, 7, None])
def test_invalid_ids_are_rejected(bad):
    data = boat()
    by_id(data["instances"], "tm-spare-1")["id"] = bad
    assert codes(data) == ["invalid_id"]


@pytest.mark.parametrize("bad", ["capabilities", "../escape", "Demo", "a" * 65, "_programmes"])
def test_invalid_programme_ids_never_reach_the_filesystem(project_root, bad):
    with pytest.raises((ValueError, programme.ProgrammeInvalid)):
        programme_store.programme_dir(bad)
    with pytest.raises((ValueError, programme.ProgrammeInvalid)):
        programme_store.commit(bad, boat(), expected_revision=0, actor="a@example.test")
    assert not project_root.exists()


def test_duplicate_ids_unknown_fields_and_two_current_variants_are_rejected():
    data = boat()
    data["positions"].append(copy.deepcopy(data["positions"][0]))
    assert codes(data) == ["duplicate_id"]
    data = boat()
    by_id(data["instances"], "tm-lot-1")["quantiy"] = 4
    assert codes(data) == ["unknown_field"]
    data = boat()
    by_id(data["components"], "traction-motor")["variants"][1]["status"] = "current"
    assert codes(data) == ["multiple_current_variants"]


# --- store --------------------------------------------------------------------------------------------------------


def lookup_from(hashes: dict):
    return lambda die, config: hashes.get((die, config))


def test_commit_publishes_manifest_last_and_reads_verify_hashes(project_root):
    result = programme_store.commit(PID, boat(), expected_revision=0, actor="Admin@Example.test",
                                    idempotency_key="create-0001")
    assert result["revision"] == 1 and result["replayed"] is False
    envelope = programme_store.read(PID)
    assert envelope["revision"] == 1 and envelope["committed_by"] == "admin@example.test"
    assert envelope["programme"] == result["programme"]
    directory = project_root / "_programmes" / PID
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert [e["revision"] for e in manifest["revisions"]] == [1]
    assert (directory / "revisions" / manifest["revisions"][0]["file"]).is_file()
    assert store.list_projects() == []
    assert programme_store.list_programmes()[0]["id"] == PID


def test_optimistic_revision_check(project_root):
    programme_store.commit(PID, boat(), expected_revision=0, actor="a@example.test")
    with pytest.raises(programme_store.RevisionConflict) as exc:
        programme_store.commit(PID, boat(), expected_revision=0, actor="a@example.test")
    assert exc.value.current == 1
    with pytest.raises(programme.ProgrammeInvalid):
        programme_store.commit(PID, boat(), expected_revision=None, actor="a@example.test")
    assert programme_store.commit(PID, boat(), expected_revision=1, actor="a@example.test")["revision"] == 2


def test_archive_never_delete_and_identities_are_immutable(project_root):
    programme_store.commit(PID, boat(), expected_revision=0, actor="a@example.test")
    data = boat()
    data["instances"] = [i for i in data["instances"] if i["id"] != "tm-spare-1"]
    with pytest.raises(programme.ProgrammeInvalid) as exc:
        programme_store.commit(PID, data, expected_revision=1, actor="a@example.test")
    assert [e["code"] for e in exc.value.errors] == ["record_deleted"]
    data = boat()
    by_id(data["components"], "traction-motor")["variants"].pop(1)
    with pytest.raises(programme.ProgrammeInvalid) as exc:
        programme_store.commit(PID, data, expected_revision=1, actor="a@example.test")
    assert [e["code"] for e in exc.value.errors] == ["record_deleted"]
    data = boat()
    by_id(data["connections"], "c-gen-main-w1")["ends"][0]["position"] = "inv-b-3"
    by_id(data["connections"], "c-gen-backup-w1")["ends"][0]["position"] = "inv-b-1"
    with pytest.raises(programme.ProgrammeInvalid) as exc:
        programme_store.commit(PID, data, expected_revision=1, actor="a@example.test")
    assert [e["code"] for e in exc.value.errors] == ["immutable_field", "immutable_field"]
    data = boat()
    by_id(data["instances"], "tm-spare-1")["archived"] = True
    assert programme_store.commit(PID, data, expected_revision=1, actor="a@example.test")["revision"] == 2
    assert by_id(programme_store.read(PID, 1)["programme"]["instances"], "tm-spare-1")["archived"] is False


def test_content_hash_is_pinned_once_and_a_mismatch_is_stale_never_repinned(project_root):
    data = boat()
    variant = by_id(data["components"], "traction-motor")["variants"][1]
    variant["ref"]["content_hash"] = None
    with pytest.raises(programme.ProgrammeInvalid) as exc:
        programme_store.commit(PID, data, expected_revision=0, actor="a@example.test", catalog_lookup=lookup_from({}))
    assert [e["code"] for e in exc.value.errors] == ["unresolved_reference"]
    catalog = {("synthetic-die-t", "tm-a"): "a" * 64, ("synthetic-die-t", "tm-b"): "e" * 64}
    result = programme_store.commit(PID, data, expected_revision=0, actor="a@example.test",
                                    catalog_lookup=lookup_from(catalog))
    pinned = by_id(result["programme"]["components"], "traction-motor")["variants"][1]["ref"]["content_hash"]
    assert pinned == "e" * 64

    catalog[("synthetic-die-t", "tm-b")] = "f" * 64          # the catalog configuration was edited
    states = {(r["variant"]): r["state"] for r in programme.reference_status(programme_store.read(PID)["programme"],
                                                                            lookup_from(catalog))}
    assert states == {"tm-a": "current", "tm-b": "stale", "gen-a": "missing", "bgen-a": "missing"}
    assert by_id(programme_store.read(PID)["programme"]["components"], "traction-motor")["variants"][1]["ref"][
        "content_hash"] == "e" * 64
    resubmit = copy.deepcopy(programme_store.read(PID)["programme"])
    by_id(resubmit["components"], "traction-motor")["variants"][1]["ref"]["content_hash"] = "f" * 64
    with pytest.raises(programme.ProgrammeInvalid) as exc:
        programme_store.commit(PID, resubmit, expected_revision=1, actor="a@example.test", catalog_lookup=lookup_from(catalog))
    assert [e["code"] for e in exc.value.errors] == ["reference_repinned"]
    by_id(resubmit["components"], "traction-motor")["variants"][1]["ref"]["content_hash"] = None
    with pytest.raises(programme.ProgrammeInvalid) as exc:
        programme_store.commit(PID, resubmit, expected_revision=1, actor="a@example.test", catalog_lookup=lookup_from(catalog))
    assert [e["code"] for e in exc.value.errors] == ["reference_repinned"]


def test_idempotency_key_replays_and_rejects_reuse(project_root):
    first = programme_store.commit(PID, boat(), expected_revision=0, actor="a@example.test", idempotency_key="key-00000001")
    again = programme_store.commit(PID, boat(), expected_revision=0, actor="a@example.test", idempotency_key="key-00000001")
    assert again["replayed"] is True and again["revision"] == first["revision"] == 1
    assert len(programme_store.history(PID)) == 1
    changed = boat()
    changed["title"] = "Renamed"
    with pytest.raises(programme_store.IdempotencyConflict):
        programme_store.commit(PID, changed, expected_revision=0, actor="a@example.test", idempotency_key="key-00000001")
    with pytest.raises(programme.ProgrammeInvalid):
        programme_store.commit(PID, changed, expected_revision=1, actor="a@example.test", idempotency_key="short")


def test_concurrent_writers_one_wins_the_revision(project_root):
    programme_store.commit(PID, boat(), expected_revision=0, actor="a@example.test")
    barrier = threading.Barrier(4)
    outcomes = []

    def writer(n):
        data = boat()
        data["title"] = f"Writer {n}"
        barrier.wait()
        try:
            outcomes.append(programme_store.commit(PID, data, expected_revision=1, actor="a@example.test",
                                                   idempotency_key=f"writer-key-{n}")["revision"])
        except programme_store.RevisionConflict as exc:
            outcomes.append(("conflict", exc.current))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes, key=str) == sorted([2] + [("conflict", 2)] * 3, key=str)
    assert [e["revision"] for e in programme_store.history(PID)] == [1, 2]


def test_concurrent_retries_of_one_request_commit_once(project_root):
    barrier = threading.Barrier(3)
    results = []

    def retry():
        barrier.wait()
        results.append(programme_store.commit(PID, boat(), expected_revision=0, actor="a@example.test",
                                              idempotency_key="same-request-1"))

    threads = [threading.Thread(target=retry) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(r["replayed"] for r in results) == [False, True, True]
    assert {r["revision"] for r in results} == {1}


def test_interrupted_publication_is_invisible_and_a_retry_recovers(project_root, monkeypatch):
    programme_store.commit(PID, boat(), expected_revision=0, actor="a@example.test", idempotency_key="create-0001")
    data = boat()
    data["title"] = "Second revision"
    real_write = json_store.atomic_write_json

    def crash_on_manifest(path, value, **kwargs):
        if Path(path).name == "manifest.json":
            raise OSError("power lost before the manifest was published")
        return real_write(path, value, **kwargs)

    monkeypatch.setattr(json_store, "atomic_write_json", crash_on_manifest)
    with pytest.raises(OSError):
        programme_store.commit(PID, data, expected_revision=1, actor="a@example.test", idempotency_key="update-0002")
    monkeypatch.setattr(json_store, "atomic_write_json", real_write)

    assert programme_store.read(PID)["revision"] == 1
    assert programme_store.read(PID)["programme"]["title"] == "Synthetic hydrofoil boat"
    report = programme_store.recover(PID)
    assert report["head"] == 1 and report["corrupt"] == [] and len(report["orphans"]) == 1
    orphan = report["orphans"][0]

    result = programme_store.commit(PID, data, expected_revision=1, actor="a@example.test", idempotency_key="update-0002")
    assert result["revision"] == 2 and result["replayed"] is False and result["quarantined"] == [orphan]
    assert programme_store.read(PID)["programme"]["title"] == "Second revision"
    kept = list((project_root / "_programmes" / PID / "orphans").iterdir())
    assert [p.name.endswith(orphan) for p in kept] == [True]
    assert programme_store.recover(PID)["orphans"] == []


def test_lost_response_after_publication_replays_instead_of_committing_twice(project_root):
    programme_store.commit(PID, boat(), expected_revision=0, actor="a@example.test", idempotency_key="create-0001")
    data = boat()
    data["title"] = "Second revision"
    programme_store.commit(PID, data, expected_revision=1, actor="a@example.test", idempotency_key="update-0002")
    retry = programme_store.commit(PID, data, expected_revision=1, actor="a@example.test", idempotency_key="update-0002")
    assert retry["replayed"] is True and retry["revision"] == 2
    assert len(programme_store.history(PID)) == 2


def test_recovery_quarantines_stray_files_and_reports_corruption_without_deleting(project_root):
    programme_store.commit(PID, boat(), expected_revision=0, actor="a@example.test")
    revisions = project_root / "_programmes" / PID / "revisions"
    (revisions / "r000002-0000000000000000.json.tmp").write_text("{", encoding="utf-8")
    report = programme_store.recover(PID, apply=True)
    assert report["quarantined"] == ["r000002-0000000000000000.json.tmp"]
    published = next(revisions.iterdir())
    published.write_text(published.read_text(encoding="utf-8").replace("Synthetic", "Tampered"), encoding="utf-8")
    with pytest.raises(programme_store.StoreCorrupt):
        programme_store.read(PID)
    report = programme_store.recover(PID, apply=True)
    assert [row["revision"] for row in report["corrupt"]] == [1] and published.is_file()
    with pytest.raises(programme_store.StoreCorrupt):
        programme_store.commit(PID, boat(), expected_revision=1, actor="a@example.test")


# --- routes -------------------------------------------------------------------------------------------------------


@pytest.fixture
def admin_client(project_root, monkeypatch):
    caller = {"user": dict(ADMIN)}
    monkeypatch.setattr(auth, "_is_admin_caller",
                        lambda token: (caller["user"].get("role") == "admin", dict(caller["user"])))
    monkeypatch.setenv(programme_routes.ENV_FLAG, "1")
    monkeypatch.setattr(programme_routes, "catalog_lookup", lookup_from({("synthetic-die-t", "tm-a"): "a" * 64}))
    app = FastAPI(); app.include_router(admin_router)
    client = TestClient(app)
    client.caller = caller
    return client


def put(client, data, revision, key):
    return client.put(f"/api/admin/projects/programmes/{PID}",
                      json={"programme": data, "expected_revision": revision, "idempotency_key": key})


def test_routes_are_hidden_unless_enabled(admin_client, project_root, monkeypatch):
    monkeypatch.delenv(programme_routes.ENV_FLAG)
    assert admin_client.get("/api/admin/projects/programmes/capabilities").json()["programmes"]["enabled"] is False
    assert admin_client.get("/api/admin/projects/programmes").status_code == 404
    assert put(admin_client, boat(), 0, "create-0001").status_code == 404
    assert not (project_root / "_programmes").exists()


def test_routes_refuse_non_admins_before_reading_anything(admin_client, monkeypatch):
    assert put(admin_client, boat(), 0, "create-0001").status_code == 200
    monkeypatch.setattr(programme_store, "read", lambda *a, **k: pytest.fail("non-admin request read a programme"))
    for role in ("engineer", "user"):
        admin_client.caller["user"] = {"email": f"{role}@example.test", "role": role}
        for method, path in (("get", "/api/admin/projects/programmes/capabilities"),
                             ("get", "/api/admin/projects/programmes"),
                             ("get", f"/api/admin/projects/programmes/{PID}"),
                             ("get", f"/api/admin/projects/programmes/{PID}/revisions/1"),
                             ("get", f"/api/admin/projects/programmes/{PID}/recovery"),
                             ("post", f"/api/admin/projects/programmes/{PID}/recovery")):
            response = getattr(admin_client, method)(path)
            assert response.status_code == 403 and PID not in response.text


def test_admin_route_flow(admin_client, project_root):
    created = put(admin_client, boat(), 0, "create-0001")
    assert created.status_code == 200 and created.json()["revision"] == 1
    assert put(admin_client, boat(), 0, "create-0001").json()["replayed"] is True
    conflict = put(admin_client, boat(), 0, "create-0002")
    assert conflict.status_code == 409 and conflict.json()["detail"]["revision"] == 1
    reused = put(admin_client, {**boat(), "title": "Other"}, 0, "create-0001")
    assert reused.status_code == 409
    bad = boat()
    by_id(bad["instances"], "tm-lot-1")["allocations"].pop()
    invalid = put(admin_client, bad, 1, "update-0002")
    assert invalid.status_code == 422 and invalid.json()["detail"]["errors"][0]["code"] == "allocation_gap"
    assert admin_client.put(f"/api/admin/projects/programmes/{PID}", json={"programme": boat(), "expected_revision": 1}
                            ).status_code == 422

    before = tree(project_root)
    view = admin_client.get(f"/api/admin/projects/programmes/{PID}").json()
    assert view["revision"] == 1 and [h["revision"] for h in view["history"]] == [1]
    assert view["reconciliation"]["components"]["traction-motor"]["spare_lots"] == ["tm-spare-1"]
    assert {r["variant"]: r["state"] for r in view["references"]}["tm-b"] == "missing"
    assert {r["variant"]: r["state"] for r in view["references"]}["tm-a"] == "current"
    assert admin_client.get(f"/api/admin/projects/programmes/{PID}/revisions/1").json()["revision"] == 1
    assert admin_client.get(f"/api/admin/projects/programmes/{PID}/revisions/9").status_code == 404
    assert admin_client.get("/api/admin/projects/programmes/missing-boat").status_code == 404
    assert admin_client.get("/api/admin/projects/programmes/Bad_Id").status_code == 404
    assert admin_client.get("/api/admin/projects/programmes").json()["programmes"][0]["id"] == PID
    assert admin_client.get(f"/api/admin/projects/programmes/{PID}/recovery").json()["orphans"] == []
    assert tree(project_root) == before, "programme GET routes must not write"
