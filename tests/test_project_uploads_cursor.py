"""The audit replay of the project stores reads only what was appended since its last read (Astra follow-up of #819)."""
from __future__ import annotations

import json

import pytest

from motor_ai_sim.development import project_uploads as PU
from motor_ai_sim.development import store


@pytest.fixture
def audit(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTRES_PROJECTS_ROOT", str(tmp_path / "projects"))
    PU._CURSORS.clear()
    store.create_project({"id": "proj1", "title": "P", "customer_org": "Org", "status": "spec", "created_at": "t", "updated_at": "t",
                          "owner_engineer": "o@example.test", "linked_motors": [], "notes": "", "pending_invites": [], "members": []})
    return tmp_path / "projects" / "proj1" / "audit.jsonl"


def _line(eid: str, name: str = "a") -> bytes:
    return (json.dumps({"action": "x", "details": {"event_id": eid, "name": name}}, ensure_ascii=False) + "\r\n").encode("utf-8")


def test_only_appended_bytes_are_read_and_ids_accumulate(audit):
    audit.write_bytes(_line("e1") + _line("e2"))
    assert PU._written_event_ids("proj1") == {"e1", "e2"}
    first = PU._CURSORS[str(audit)][1]
    assert first == audit.stat().st_size
    with audit.open("ab") as fh:
        fh.write(_line("e3", "name with separator"))
    assert PU._written_event_ids("proj1") == {"e1", "e2", "e3"}
    assert PU._CURSORS[str(audit)][1] == audit.stat().st_size > first


def test_a_cut_last_line_is_ended_and_never_counts(audit):
    audit.write_bytes(_line("e1") + b'{"action": "x", "details": {"event_id": "e2", "na')
    assert PU._written_event_ids("proj1") == {"e1"}
    assert audit.read_bytes().endswith(b'"na\r\n')
    with audit.open("ab") as fh:
        fh.write(_line("e2"))
    assert PU._written_event_ids("proj1") == {"e1", "e2"}


def test_a_replaced_or_shrunk_file_is_read_again_from_the_start(audit, tmp_path):
    audit.write_bytes(_line("e1") + _line("e2"))
    PU._written_event_ids("proj1")
    new = tmp_path / "new.jsonl"
    new.write_bytes(_line("e9"))
    new.replace(audit)                                                        # another inode
    assert PU._written_event_ids("proj1") == {"e9"}
    audit.write_bytes(b"")                                                    # shrunk in place
    assert PU._written_event_ids("proj1") == set()


def test_a_missing_file_has_no_ids(audit):
    assert PU._written_event_ids("proj1") == set()
