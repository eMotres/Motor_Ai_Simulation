"""Pure read adapter over existing Development projects.

``read_project`` assembles everything a programme migration needs from one legacy
project (record, members, draft, specification versions, linked current/trial
motors, motor history, snapshots) through read-only store helpers. It never writes:
no audit line, no index repair, no draft recovery (unlike the route helpers that
serve the same files). ``project_document`` rebuilds the stored ``project.json``
from the view, unknown fields and key order included.
"""
from __future__ import annotations

import copy
import re

from motor_ai_sim.development import store

# Fields models.project_record and the Development routes write today; anything
# else in project.json is carried through ``unknown_fields`` untouched.
KNOWN_FIELDS = ("id", "title", "customer_org", "status", "created_at", "updated_at", "owner_engineer",
                "members", "linked_motors", "notes", "pending_invites")

_VERSION_FILE = re.compile(r"^v(\d+)\.json$")


def _spec_versions(project_id: str) -> list[dict]:
    """Submitted specification versions, by number, from the index and the version files."""
    index = store.load_json(project_id, "spec/index.json", {"versions": []})
    entries = index.get("versions", []) if isinstance(index, dict) else []
    numbers = {int(e["version"]) for e in entries
               if isinstance(e, dict) and isinstance(e.get("version"), int) and e["version"] > 0}
    directory = store.path_for(project_id) / "spec"
    if directory.is_dir():
        numbers |= {int(m.group(1)) for p in directory.iterdir() if (m := _VERSION_FILE.fullmatch(p.name))}
    out = []
    for number in sorted(numbers):
        version = store.load_json(project_id, f"spec/v{number}.json", None)
        if isinstance(version, dict) and version.get("version") == number:
            out.append(version)
    return out


def read_project(project_id: str) -> dict | None:
    if not store.valid_id(project_id):
        return None
    project = store.get_project(project_id)
    if project is None:
        return None
    project = copy.deepcopy(project)
    linked = [dict(row) for row in project.get("linked_motors") or [] if isinstance(row, dict)]
    current = [row for row in linked if str(row.get("status") or "current") != "trial"]
    trial = [row for row in linked if str(row.get("status") or "current") == "trial"]
    draft = store.load_json(project_id, "draft.json", None)
    return {
        "id": project.get("id"),
        "title": project.get("title"),
        "members": [dict(m) for m in project.get("members") or [] if isinstance(m, dict)],
        "fields": {key: project[key] for key in KNOWN_FIELDS if key in project},
        "unknown_fields": {key: value for key, value in project.items() if key not in KNOWN_FIELDS},
        "field_order": list(project),
        "draft": copy.deepcopy(draft) if isinstance(draft, dict) else None,
        "spec_versions": _spec_versions(project_id),
        "linked_motors": {"current": current, "trial": trial},
        "motor_history": store.motor_history(project_id, project),
        "snapshots": store.list_snapshots(project_id),
    }


def project_document(view: dict) -> dict:
    """The ``project.json`` document the view was read from."""
    merged = {**view["fields"], **view["unknown_fields"]}
    ordered = {key: copy.deepcopy(merged[key]) for key in view["field_order"] if key in merged}
    ordered.update({key: copy.deepcopy(value) for key, value in merged.items() if key not in ordered})
    return ordered
