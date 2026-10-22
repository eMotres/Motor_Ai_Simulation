from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from fastapi import HTTPException

from motor_ai_sim import workspace
from motor_ai_sim.development import store


def _project(project_id: str = "store-check") -> dict:
    return {"id": project_id, "title": "Store check"}


def test_root_and_reads_do_not_create_a_missing_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = tmp_path / "not" / "yet" / "created"
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(configured))

    assert store.root() == configured.resolve()
    assert store.list_projects() == []
    assert store.load_index() == {"motors": {}}
    assert store.get_project("store-check") is None
    assert not configured.exists()


def test_explicit_nested_root_is_created_on_first_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = tmp_path / "nested" / "project-data" / "projects"
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(configured))

    assert store.ensure_writable() == configured.resolve()
    store.create_project(_project())
    store.mutate_project("store-check", lambda doc: doc.update({"status": "spec"}))
    store.save_json("store-check", "draft.json", {"revision": 0})
    store.mutate_json("store-check", "draft.json", lambda doc: doc.update({"revision": 1}))
    store.append_jsonl("store-check", "thread.jsonl", {"text": "hello"})
    store.append_audit("store-check", "engineer@example.test", "project.create", "project", "store-check")
    store.save_index({"motors": {}})

    assert store.get_project("store-check")["status"] == "spec"
    assert store.load_json("store-check", "draft.json", {})["revision"] == 1
    assert (configured / "store-check" / "thread.jsonl").is_file()
    assert (configured / "store-check" / "audit.jsonl").is_file()
    assert store.load_index() == {"motors": {}}


@pytest.mark.parametrize("operation", [
    lambda: store.create_project(_project()),
    lambda: store.mutate_project("store-check", lambda doc: doc.update({"changed": True})),
    lambda: store.save_json("store-check", "draft.json", {"revision": 1}),
    lambda: store.mutate_json("store-check", "draft.json", lambda doc: doc.update({"revision": 1})),
    lambda: store.append_jsonl("store-check", "thread.jsonl", {"text": "no write"}),
    lambda: store.append_audit("store-check", "engineer@example.test", "project.create", "project", "store-check"),
    lambda: store.save_index({"motors": {}}),
])
def test_readonly_default_shared_root_rejects_writes_without_creating_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation,
) -> None:
    shared = tmp_path / "readonly-shared"
    shared.mkdir()
    original_mode = stat.S_IMODE(shared.stat().st_mode)
    monkeypatch.delenv("MOTRES_PROJECTS_ROOT", raising=False)
    monkeypatch.setattr(workspace, "shared_root", lambda: shared)
    try:
        shared.chmod(original_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        assert store.root() == (shared / "projects").resolve()
        with pytest.raises(HTTPException) as exc:
            operation()
        assert exc.value.status_code == 503
        assert not (shared / "projects").exists()
        assert list(shared.iterdir()) == []
    finally:
        shared.chmod(original_mode)


def test_os_access_denial_is_respected_even_if_permission_bits_look_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "denied"
    configured.mkdir()
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(configured))
    monkeypatch.setattr(os, "access", lambda _path, _mode: False)

    with pytest.raises(HTTPException) as exc:
        store.save_index({"motors": {}})
    assert exc.value.status_code == 503
    assert list(configured.iterdir()) == []
