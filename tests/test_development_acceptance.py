from __future__ import annotations

from motor_ai_sim.development import store


def test_project_read_index_does_not_create_store(tmp_path, monkeypatch):
    root = tmp_path / "missing-project-store"
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(root))
    assert store.load_index() == {"motors": {}}
    assert store.list_projects() == []
    assert not root.exists()
