"""Explicit, loss-preserving adapters for mutable development specification drafts."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from typing import Any


def _sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def preview_v1_to_v2(draft: dict, *, source_revision: int) -> dict:
    """Build a deterministic preview; never mutate the source draft."""
    original = copy.deepcopy(draft)
    if int(original.get("schema_version", 1)) == 2:
        return {"draft": copy.deepcopy(original), "source_sha256": _sha(original),
                "source_revision": source_revision, "requires_acknowledgement": False,
                "issues": [], "already_migrated": True}
    out = copy.deepcopy(draft)
    fields = out.setdefault("fields", {})
    old = fields.get("1_4")
    issues = []
    ack_required = False
    if isinstance(old, dict):
        answer = copy.deepcopy(old)
        value = answer.get("value")
        if isinstance(value, dict):
            converted = {}
            for key in ("torque_Nm", "speed_rpm"):
                item = value.get(key)
                if isinstance(item, dict):
                    maximum = item.get("maximum")
                    if isinstance(maximum, (int, float)) and not isinstance(maximum, bool) and math.isfinite(maximum):
                        converted[key] = maximum
                    else:
                        converted[key] = None
                elif isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item):
                    converted[key] = item
                else:
                    converted[key] = None
            answer["value"] = converted
            if answer.get("status") == "filled" and any(converted.get(k) is None for k in ("torque_Nm", "speed_rpm")):
                answer["status"] = "missing"
                issues.append({"code": "s1_maximum_missing", "field": "1_4",
                               "message": "A maximum continuous point is incomplete; provide it or mark it not known yet."})
            if any(isinstance(value.get(k), dict) for k in ("torque_Nm", "speed_rpm")):
                ack_required = True
                issues.append({"code": "s1_range_collapsed_to_maxima", "field": "1_4",
                               "message": "Confirm that the retained torque and speed apply together."})
            torque, speed = converted.get("torque_Nm"), converted.get("speed_rpm")
            if answer.get("status") == "filled" and isinstance(torque, (int, float)) and isinstance(speed, (int, float)):
                derived_power = torque * 2 * math.pi * speed / 60
                if math.isfinite(derived_power):
                    answer["value"]["shaft_power_W"] = derived_power
                else:
                    # Preserve both scalar inputs and the complete legacy snapshot, but
                    # do not create an invalid JSON infinity or a falsely filled S1 point.
                    answer["status"] = "missing"
                    ack_required = True
                    issues.append({"code": "derived_power_out_of_range", "field": "1_4.shaft_power_W",
                                   "message": "The retained torque and speed exceed the finite range for derived shaft power; review and correct the point."})
            for legacy_power in ("input_power_W", "output_power_W"):
                if legacy_power in value and value[legacy_power] is not None:
                    ack_required = True
                    issues.append({"code": "legacy_power_semantics_preserved", "field": f"1_4.{legacy_power}",
                                   "message": "Legacy power is preserved for explicit semantic review."})
            fields["1_4"] = answer
    rows = out.setdefault("mission_profile", [])
    row_map = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        if not row.get("row_id"):
            row["row_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dev-spec:{source_revision}:{index}:{_sha(row)}"))
        row_map.append({"legacy_index": index, "row_id": row["row_id"]})
    provenance = copy.deepcopy(out.get("provenance", {}))
    migration = {
        "from_schema_version": 1,
        "to_schema_version": 2,
        "source_revision": source_revision,
        "source_sha256": _sha(original),
        "legacy_draft_snapshot": original,
        "legacy_1_4": copy.deepcopy(original.get("fields", {}).get("1_4")),
        "mission_row_index_map": row_map,
        "issues": copy.deepcopy(issues),
        "acknowledged": False,
    }
    migrations = provenance.setdefault("schema_migrations", [])
    if not any(item.get("from_schema_version") == 1 and item.get("source_revision") == source_revision
               and item.get("source_sha256") == migration["source_sha256"] for item in migrations):
        migrations.append(migration)
    out["provenance"] = provenance
    out["schema_version"] = 2
    return {"draft": out, "source_sha256": _sha(original), "source_revision": source_revision,
            "requires_acknowledgement": ack_required, "issues": issues}


def apply_acknowledgement(preview: dict, acknowledged: bool) -> dict:
    if preview.get("requires_acknowledgement") and not acknowledged:
        raise ValueError("S1 point confirmation acknowledgement is required")
    result = copy.deepcopy(preview["draft"])
    records = result.get("provenance", {}).get("schema_migrations", [])
    if records:
        records[-1]["acknowledged"] = bool(acknowledged)
    return result
