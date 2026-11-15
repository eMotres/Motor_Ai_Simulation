"""The read-only report of open proposals (scripts/production_proposals_report.py): lists, changes nothing."""
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("ppr", Path(__file__).resolve().parents[1] / "scripts" / "production_proposals_report.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def make(root, die, cfg, proposals):
    d = Path(root) / die / cfg
    d.mkdir(parents=True)
    (d / "production.json").write_text(json.dumps({"die": die, "config": cfg}))
    (d / "proposals.json").write_text(json.dumps({"proposals": proposals}))
    return d


def prop(i, status, text="Make it longer", part="p1"):
    return {"id": i, "status": status, "part_id": part, "author_email": "a@x.com", "created_at": "2026-11-01T10:00:00", "text": {"en": text, "zh": "更长"}}


def test_lists_open_and_accepted_proposals_by_default_and_everything_with_all_and_changes_nothing():
    root = Path(tempfile.mkdtemp())
    d1 = make(root, "DIE A", "L1", [prop("1", "open"), prop("2", "implemented"), prop("3", "accepted"), prop("4", "rejected")])
    make(root, "DIE B", "L2", [])
    d3 = make(root, "DIE C", "L3", [prop("5", "open", part=None)])
    (root / "DIE D" / "L4").mkdir(parents=True)                       # no production.json: skipped
    before = {p: p.read_text() for p in root.rglob("*.json")}
    rows = M.collect(root)
    assert [(r["die"], r["id"], r["status"]) for r in rows] == [("DIE A", "1", "open"), ("DIE A", "3", "accepted"), ("DIE C", "5", "open")]
    assert rows[2]["part_id"] is None and rows[0]["text"] == "Make it longer"
    assert len(M.collect(root, include_all=True)) == 5
    assert {p: p.read_text() for p in root.rglob("*.json")} == before  # read-only
    (d3 / "proposals.json").write_text("not json")
    assert [r["id"] for r in M.collect(root)] == ["1", "3"]            # a broken file is skipped, not fatal
    assert d1.exists()
