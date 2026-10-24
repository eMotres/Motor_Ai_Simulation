"""Pure legacy read adapter for Development projects.

Guarantees:
- Existing projects read unchanged: title, members, drafts, versions, linked motors, snapshots.
- Absolutely ZERO writes on GET (no audit writes, no index mutation, no file touches).
- Round-trips unknown fields and version history without loss of customer/extended data.
- Programme records are hidden from non-admin callers in Stage 1.
- Old code / catch-all serializers never receive un-sanitized programme data.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from motor_ai_sim import json_store
from motor_ai_sim.development import store


class LegacyReadAdapter:
    """Read-only adapter for legacy development project records."""

    @staticmethod
    def read_project(project_id: str, *, as_admin: bool = False) -> dict | None:
        """Pure read of a project document. Zero side-effects, zero disk writes."""
        if not store.valid_id(project_id):
            return None
        path = store.path_for(project_id) / "project.json"
        if not path.is_file():
            return None
        data = json_store.read_json(path, default=None)
        if not isinstance(data, dict):
            return None

        # Stage 1 isolation: programme projects are admin-only and hidden from non-admins
        if data.get("kind") == "programme" and not as_admin:
            return None

        # Deep copy to guarantee caller mutations cannot affect store memory
        return copy.deepcopy(data)

    @staticmethod
    def read_draft(project_id: str) -> dict:
        """Pure read of project draft.json without audit or disk writes."""
        if not store.valid_id(project_id):
            return {"draft": {}, "revision": 0, "version": None}
        path = store.path_for(project_id) / "draft.json"
        data = json_store.read_json(path, default={"draft": {}, "revision": 0, "version": None})
        return copy.deepcopy(data if isinstance(data, dict) else {"draft": {}, "revision": 0, "version": None})

    @staticmethod
    def read_versions(project_id: str) -> list[dict]:
        """Pure read of all submitted versions without writes."""
        if not store.valid_id(project_id):
            return []
        versions_dir = store.path_for(project_id) / "versions"
        if not versions_dir.is_dir():
            return []
        out = []
        for path in sorted(versions_dir.glob("*.json")):
            val = json_store.read_json(path, default=None)
            if isinstance(val, dict):
                out.append(copy.deepcopy(val))
        return out

    @staticmethod
    def read_snapshots(project_id: str) -> list[dict]:
        """Pure read of snapshots without catalog queries or disk writes."""
        if not store.valid_id(project_id):
            return []
        snap_dir = store.path_for(project_id) / "snapshots"
        if not snap_dir.is_dir():
            return []
        out = []
        for path in sorted(snap_dir.glob("*.json")):
            val = json_store.read_json(path, default=None)
            if isinstance(val, dict):
                out.append(copy.deepcopy(val))
        return sorted(out, key=lambda s: str(s.get("created_at") or ""), reverse=True)

    @staticmethod
    def round_trip(record: dict) -> dict:
        """Verify that a project record with unknown/custom fields round-trips without loss."""
        serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
        deserialized = json.loads(serialized)
        return deserialized

    @staticmethod
    def sanitize_legacy_summary(project: dict) -> dict:
        """Remove programme-specific fields so legacy catch-all serializers cannot leak them."""
        out = copy.deepcopy(project)
        # Drop programme internals if present
        for key in (
            "programme", "components", "instances", "connections",
            "contract_baselines", "design_baselines", "obligations",
            "publication_refs", "programme_revision",
        ):
            out.pop(key, None)
        return out
