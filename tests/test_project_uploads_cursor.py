"""The audit replay of the project stores reads only what was appended since its last read, per store, streamed, and trusts its
cursor only while the file continues what was read (Astra follow-ups of #819 / #823)."""
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


def ids(wanted, store_name="s.json"):
    return PU._written_event_ids("proj1", store_name, set(wanted))


def test_only_appended_bytes_are_read_and_only_the_wanted_ids_are_answered(audit):
    audit.write_bytes(_line("e1") + _line("e2"))
    assert ids({"e1", "e9"}) == {"e1"}
    first = PU._CURSORS[(str(audit), "s.json")][1]
    assert first == audit.stat().st_size
    with audit.open("ab") as fh:
        fh.write(_line("e3", "name with separator"))
    assert ids({"e3"}) == {"e3"} and PU._CURSORS[(str(audit), "s.json")][1] == audit.stat().st_size
    assert ids({"e1"}) == set()                                               # before the cursor: never re-read (by design)


def test_each_store_has_its_own_cursor(audit):
    audit.write_bytes(_line("m1"))
    assert ids({"other"}, "contract.json") == set()                           # the contract store read past m1 ...
    assert ids({"m1"}, "models.json") == {"m1"}                               # ... the model store still finds its own event


def test_a_cut_last_line_is_ended_and_never_counts(audit):
    audit.write_bytes(_line("e1") + b'{"action": "x", "details": {"event_id": "e2", "na')
    assert ids({"e1", "e2"}) == {"e1"}
    assert audit.read_bytes().endswith(b'"na\r\n')
    with audit.open("ab") as fh:
        fh.write(_line("e2"))
    assert ids({"e2"}) == {"e2"}


def test_a_truncated_and_regrown_file_with_the_same_inode_is_read_again(audit):
    audit.write_bytes(_line("e1"))
    assert ids({"e1"}) == {"e1"}
    ino = audit.stat().st_ino
    with audit.open("r+b") as fh:                                             # same inode, same length, other content
        fh.truncate(0)
        fh.write(_line("e2"))
    assert audit.stat().st_ino == ino
    assert ids({"e1", "e2"}) == {"e2"}                                        # the fingerprint changed: rescan from the start
    with audit.open("r+b") as fh:                                             # regrown beyond the old offset
        fh.truncate(0)
        fh.write(_line("e5") + _line("e6") + _line("e7"))
    assert ids({"e5", "e7"}) == {"e5", "e7"}


def test_a_replaced_or_shrunk_file_is_read_again_from_the_start(audit, tmp_path):
    audit.write_bytes(_line("e1") + _line("e2"))
    ids({"e1"})
    new = tmp_path / "new.jsonl"
    new.write_bytes(_line("e9"))
    new.replace(audit)                                                        # another inode
    assert ids({"e9"}) == {"e9"}
    audit.write_bytes(b"")
    assert ids({"e9"}) == set()


def test_records_split_across_read_chunks_are_found(audit, monkeypatch):
    monkeypatch.setattr(PU, "_CHUNK", 7)
    audit.write_bytes(b"".join(_line(f"e{i}", "x" * i) for i in range(30)))
    assert ids({f"e{i}" for i in range(30)}) == {f"e{i}" for i in range(30)}


def test_the_cursor_keeps_no_ids(audit):
    audit.write_bytes(b"".join(_line(f"e{i}") for i in range(1000)))
    ids({"e5"})
    dev_ino, offset, fp = PU._CURSORS[(str(audit), "s.json")]
    assert isinstance(fp, bytes) and len(fp) <= PU._FP and offset == audit.stat().st_size


def test_a_missing_file_has_no_ids(audit):
    assert ids({"e1"}) == set()


def _pending_store(audit, eid="ev1"):
    doc = {"pending_audit": [{"event_id": eid, "time": "t", "actor_email": "a@example.test", "action": "x.y", "entity": "e",
                              "entity_id": "1", "details": {}}]}
    store.save_json("proj1", "s.json", doc)


def test_a_failed_store_save_never_appends_the_same_event_twice(audit, monkeypatch):
    """Astra on #823 r2: 12 failed saves after the append must leave ONE record, not one per second retry."""
    _pending_store(audit)
    real = store.mutate_json
    fails = {"n": 12}

    def failing(project_id, name, fn, default=None):
        if name == "s.json" and fails["n"] > 0:
            fails["n"] -= 1
            doc = json.loads(json.dumps(store.load_json(project_id, name, {})))
            fn(doc)                                                           # the appends happen ...
            raise OSError("save failed")                                      # ... the pending removal does not commit
        return real(project_id, name, fn, default=default)

    monkeypatch.setattr(store, "mutate_json", failing)
    for _ in range(12):
        with pytest.raises(OSError):
            PU.flush_events("proj1", "s.json")
    PU.flush_events("proj1", "s.json")
    lines = [json.loads(x) for x in audit.read_bytes().decode().split("\n") if x.strip()]
    assert [r["details"]["event_id"] for r in lines] == ["ev1"]
    assert "pending_audit" not in store.load_json("proj1", "s.json", {})


def test_an_empty_pending_set_reads_nothing(audit, monkeypatch):
    audit.write_bytes(b"".join(_line(f"e{i}") for i in range(100)))
    monkeypatch.setattr(PU, "_scan", lambda *a: (_ for _ in ()).throw(AssertionError("scanned with nothing pending")))
    assert PU._written_event_ids("proj1", "s.json", set()) == set()
    store.save_json("proj1", "s.json", {"pending_audit": []})
    PU.flush_events("proj1", "s.json")                                        # nothing pending: no scan, no cursor
    assert (str(audit), "s.json") not in PU._CURSORS
