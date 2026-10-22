from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.json")


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _field_map() -> dict[str, tuple[dict, dict]]:
    result = {}
    for section in schema()["sections"]:
        for field in section["fields"]:
            result[field["id"]] = (section, field)
    return result


def normalize_draft(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("draft must be an object")
    extra = set(value) - {"fields", "mission_profile", "mission_profile_status", "production", "provenance"}
    if extra:
        raise ValueError("unknown draft keys: " + ", ".join(sorted(extra)))
    out = {"fields": {}, "mission_profile": [], "mission_profile_status": value.get("mission_profile_status", "missing"), "production": {}}
    if out["mission_profile_status"] not in ("missing", "filled", "not_known_yet"):
        raise ValueError("mission_profile_status must be missing, filled, or not_known_yet")
    fields = value.get("fields", {})
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    known = _field_map()
    prod_ids = {f["id"] for f in schema()["sections"][-1]["fields"]}
    for field_id, answer in fields.items():
        if field_id not in known or field_id == "mission_profile":
            raise ValueError(f"unknown field: {field_id}")
        if known[field_id][0]["id"] == "production":
            raise ValueError(f"production field must be placed under production: {field_id}")
        out["fields"][field_id] = _normalize_answer(answer, known[field_id][1], field_id)
    mission = value.get("mission_profile", [])
    if not isinstance(mission, list):
        raise ValueError("mission_profile must be an array")
    row_keys = {"phase", "torque_Nm", "speed_rpm", "duration_s", "battery_voltage_V"}
    for i, row in enumerate(mission):
        if not isinstance(row, dict):
            raise ValueError(f"mission_profile[{i}] must be an object")
        extra_row = set(row) - row_keys
        if extra_row:
            raise ValueError(f"mission_profile[{i}] has unknown fields: {', '.join(sorted(extra_row))}")
        normalized = {}
        for key, typ in (("phase", str), ("torque_Nm", (int, float)), ("speed_rpm", (int, float)),
                         ("duration_s", (int, float)), ("battery_voltage_V", (int, float))):
            item = row.get(key)
            if item == "":
                item = None
            if item is not None and (not isinstance(item, typ) or isinstance(item, bool) or
                                     (isinstance(item, float) and not __import__("math").isfinite(item))):
                raise ValueError(f"mission_profile[{i}].{key} has invalid type or value")
            if key == "duration_s" and item is not None and item < 0:
                raise ValueError(f"mission_profile[{i}].{key} must be non-negative")
            if key == "battery_voltage_V" and item is not None and item < 0:
                raise ValueError(f"mission_profile[{i}].{key} must be non-negative")
            normalized[key] = item
        out["mission_profile"].append(normalized)
    if out["mission_profile"] and out["mission_profile_status"] == "missing":
        out["mission_profile_status"] = "filled"
    production = value.get("production", {})
    if not isinstance(production, dict):
        raise ValueError("production must be an object")
    prod_fields = {f["id"]: f for f in schema()["sections"][-1]["fields"]}
    for field_id, answer in production.items():
        if field_id not in prod_fields:
            raise ValueError(f"unknown production field: {field_id}")
        out["production"][field_id] = _normalize_answer(answer, prod_fields[field_id], field_id)
    provenance = value.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError("provenance must be an object")
    out["provenance"] = {str(k): v for k, v in provenance.items()}
    return out


def _normalize_answer(answer: Any, field: dict, field_id: str) -> dict:
    if not isinstance(answer, dict) or "status" not in answer:
        answer = {"value": answer, "status": "filled"}
    if set(answer) - {"value", "status"}:
        raise ValueError(f"{field_id}: unsupported answer keys")
    status = answer.get("status")
    if status not in ("filled", "not_known_yet", "missing"):
        raise ValueError(f"{field_id}: invalid status")
    value = answer.get("value")
    if status != "filled" and value is None:
        return {"value": None, "status": status}
    if value is None:
        if status == "filled":
            raise ValueError(f"{field_id}: value required")
        return {"value": None, "status": status}
    if isinstance(value, str) and not value.strip():
        return {"value": None, "status": "missing" if status == "filled" else status}
    if field.get("properties"):
        if not isinstance(value, dict):
            raise ValueError(f"{field_id}: structured object required")
        ids = {p["id"]: p for p in field["properties"]}
        unknown = set(value) - set(ids)
        if unknown:
            raise ValueError(f"{field_id}: unknown properties: {', '.join(sorted(unknown))}")
        normalized = {}
        for key, item in value.items():
            prop = ids[key]
            if prop.get("type") == "number" and isinstance(item, dict) and prop.get("allow_range"):
                item = _normalize_range(item, field_id + "." + key, prop)
                normalized[key] = item
                continue
            if item is None or (isinstance(item, str) and not item.strip()):
                normalized[key] = None
                continue
            _validate_typed_item(item, prop, f"{field_id}.{key}")
            if prop["type"] == "number" and (isinstance(item, bool) or not isinstance(item, (int, float)) or
                                                (isinstance(item, float) and not __import__("math").isfinite(item))):
                raise ValueError(f"{field_id}.{key}: finite number required")
            if prop["type"] == "number" and (("min" in prop and item < prop["min"]) or ("max" in prop and item > prop["max"])):
                raise ValueError(f"{field_id}.{key}: value outside allowed range")
            if prop["type"] == "string" and (not isinstance(item, str) or not item.strip()):
                raise ValueError(f"{field_id}.{key}: non-empty text required")
            if prop["type"] == "boolean" and not isinstance(item, bool):
                raise ValueError(f"{field_id}.{key}: boolean required")
            if prop["type"] == "array":
                if not isinstance(item, list):
                    raise ValueError(f"{field_id}.{key}: array required")
                if prop.get("items"):
                    item_schema = prop["items"]
                    for index, element in enumerate(item):
                        _validate_typed_item(element, item_schema, f"{field_id}.{key}[{index}]")
            normalized[key] = item
        value = normalized
    else:
        if field.get("type") == "number" and isinstance(value, dict):
            if not field.get("allow_range"):
                raise ValueError(f"{field_id}: numeric range is not allowed")
            value = _normalize_range(value, field_id, field)
        typ = field.get("type", "string")
        if typ == "number" and not isinstance(value, dict) and (isinstance(value, bool) or not isinstance(value, (int, float)) or
                                 (isinstance(value, float) and not __import__("math").isfinite(value))):
            raise ValueError(f"{field_id}: finite number required")
        if typ == "number" and not isinstance(value, dict) and (("min" in field and value < field["min"]) or ("max" in field and value > field["max"])):
            raise ValueError(f"{field_id}: value outside allowed range")
        if typ == "string" and not isinstance(value, str):
            raise ValueError(f"{field_id}: text required")
        if typ == "array":
            if not isinstance(value, list):
                raise ValueError(f"{field_id}: array required")
            if field.get("items"):
                for index, element in enumerate(value):
                    _validate_typed_item(element, field["items"], f"{field_id}[{index}]")
        if typ == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{field_id}: boolean required")
    return {"value": value, "status": status}


def _normalize_range(value: dict, path: str, spec: dict) -> dict:
    if set(value) - {"minimum", "maximum"}:
        raise ValueError(f"{path}: range accepts only minimum and maximum")
    result = {}
    for bound in ("minimum", "maximum"):
        item = value.get(bound)
        if item == "": item = None
        if item is not None and (isinstance(item, bool) or not isinstance(item, (int, float)) or
                                 (isinstance(item, float) and not __import__("math").isfinite(item))):
            raise ValueError(f"{path}.{bound}: finite number required")
        if item is not None and (("min" in spec and item < spec["min"]) or ("max" in spec and item > spec["max"])):
            raise ValueError(f"{path}.{bound}: value outside allowed range")
        result[bound] = item
    if result["minimum"] is not None and result["maximum"] is not None and result["minimum"] > result["maximum"]:
        raise ValueError(f"{path}: minimum exceeds maximum")
    return result


def _validate_typed_item(value: Any, spec: dict, path: str, *, require_complete: bool = False) -> None:
    typ = spec.get("type", "string")
    if typ == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path}: object required")
        props = {p["id"]: p for p in spec.get("properties", [])}
        extra = set(value) - set(props)
        if extra:
            raise ValueError(f"{path}: unknown properties: {', '.join(sorted(extra))}")
        for key, prop in props.items():
            item = value.get(key)
            if require_complete and prop.get("required") and not _value_present(item, prop):
                raise ValueError(f"{path}.{key}: required value missing")
            if item is None or item == "":
                continue
            _validate_typed_item(item, prop, f"{path}.{key}", require_complete=require_complete)
    elif typ == "string":
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{path}: non-empty text required")
    elif typ == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not __import__("math").isfinite(value)):
            raise ValueError(f"{path}: finite number required")
        if ("min" in spec and value < spec["min"]) or ("max" in spec and value > spec["max"]):
            raise ValueError(f"{path}: value outside allowed range")
    elif typ == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path}: boolean required")
    elif typ == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path}: array required")
        item_spec = spec.get("items")
        if isinstance(item_spec, dict):
            for index, item in enumerate(value):
                _validate_typed_item(item, item_spec, f"{path}[{index}]", require_complete=require_complete)


def _value_present(value: Any, spec: dict | None = None) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    if isinstance(value, dict) and (spec or {}).get("allow_range"):
        return value.get("minimum") is not None and value.get("maximum") is not None
    return True


def _answer_complete(answer: dict, field: dict) -> bool:
    if not isinstance(answer, dict):
        return False
    status = answer.get("status", "missing")
    if status == "not_known_yet":
        return True
    if status != "filled":
        return False
    value = answer.get("value")
    if field.get("properties"):
        if not isinstance(value, dict):
            return False
        required_props = [p for p in field["properties"] if p.get("required")]
        for prop in required_props:
            item = value.get(prop["id"])
            if prop.get("allow_range") and isinstance(item, dict):
                if item.get("minimum") is None or item.get("maximum") is None:
                    return False
            elif not _value_present(item, prop):
                return False
            try:
                _validate_typed_item(item, prop, prop["id"], require_complete=True)
            except ValueError:
                return False
        minimum = int(field.get("min_filled_properties", 0))
        if sum(1 for key, v in value.items() if _value_present(v, next((p for p in field["properties"] if p["id"] == key), {}))) < minimum:
            return False
        return True
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    return True


def completeness(draft: dict) -> dict:
    required_by_section = {}
    section_fields = {}
    for section in schema()["sections"]:
        section_fields[section["id"]] = {field["id"]: field for field in section["fields"]}
        required_by_section[section["id"]] = [field["id"] for field in section["fields"] if field.get("required")]
    section_stats, total, filled = {}, 0, 0
    for section_id, ids in required_by_section.items():
        section_filled = 0
        for field_id in ids:
            if section_id == "mission_profile":
                status = draft.get("mission_profile_status", "missing")
                valid = status == "not_known_yet" or (status == "filled" and bool(draft.get("mission_profile")) and not validation_errors_for_mission(draft))
            else:
                area = draft.get("production", {}) if section_id == "production" else draft.get("fields", {})
                answer = area.get(field_id, {})
                valid = _answer_complete(answer, section_fields[section_id][field_id])
            if valid:
                section_filled += 1
        total += len(ids)
        filled += section_filled
        section_stats[section_id] = {"filled": section_filled, "required": len(ids), "total_required": len(ids),
                                     "complete": section_filled == len(ids)}
    return {"total_required": total, "filled_required": filled, "sections": section_stats,
            "complete": total == filled}


def validation_errors(draft: dict) -> list[dict]:
    errors = []
    for section in schema()["sections"]:
        sid = section["id"]
        for field in section["fields"]:
            if not field.get("required"):
                continue
            fid = field["id"]
            if sid == "mission_profile":
                status = draft.get("mission_profile_status", "missing")
                if status == "filled":
                    errors.extend(validation_errors_for_mission(draft))
                elif status != "not_known_yet":
                    errors.append({"field": "mission_profile", "section": sid, "message": "Add a mission phase or mark not known yet"})
            else:
                area = draft.get("production", {}) if sid == "production" else draft.get("fields", {})
                answer = area.get(fid, {})
                if answer.get("status") not in ("filled", "not_known_yet"):
                    errors.append({"field": f"production.{fid}" if sid == "production" else fid, "section": sid,
                                   "message": "Required field must be filled or marked not known yet"})
                if answer.get("status") == "filled" and field.get("properties"):
                    val = answer.get("value", {})
                    for prop in field["properties"]:
                        if prop.get("required"):
                            item = val.get(prop["id"])
                            missing = not _value_present(item, prop)
                            if prop.get("allow_range") and isinstance(item, dict):
                                for bound in ("minimum", "maximum"):
                                    if item.get(bound) is None:
                                        errors.append({"field": f"{fid}.{prop['id']}.{bound}", "section": sid, "message": "Required range bound is missing"})
                                continue
                            if missing:
                                errors.append({"field": f"{fid}.{prop['id']}", "section": sid, "message": "Required value is missing"})
                            elif prop.get("type") in ("object", "array"):
                                try:
                                    _validate_typed_item(item, prop, f"{fid}.{prop['id']}", require_complete=True)
                                except ValueError as exc:
                                    errors.append({"field": str(exc).split(":", 1)[0], "section": sid,
                                                   "message": str(exc).split(":", 1)[-1].strip()})
                    minimum = int(field.get("min_filled_properties", 0))
                    if minimum and sum(1 for v in val.values() if v is not None and (not isinstance(v, str) or v.strip())) < minimum:
                        errors.append({"field": fid, "section": sid, "message": "Enter at least one relevant value"})
    return errors

def project_record(project_id: str, body: dict, email: str) -> dict:
    from motor_ai_sim.development import PROJECT_STATUSES
    title = str(body.get("title") or "").strip()
    if not title:
        raise ValueError("title required")
    status = str(body.get("status") or "spec")
    if status not in PROJECT_STATUSES:
        raise ValueError("invalid status")
    now_iso = now()
    members = body.get("members") or [{"email": email.lower(), "role": "owner"}]
    if not isinstance(members, list):
        raise ValueError("members must be an array")
    normalized = []
    seen = set()
    for member in members:
        if not isinstance(member, dict):
            raise ValueError("each member must be an object")
        e = str(member.get("email") or "").strip().lower()
        role = member.get("role")
        if not e or role not in ("customer", "engineer", "owner"):
            raise ValueError("each member needs an email and valid role")
        if e in seen:
            raise ValueError("duplicate project member email")
        seen.add(e)
        normalized.append({"email": e, "role": role})
    if not normalized:
        normalized = [{"email": email.lower(), "role": "owner"}]
    return {"id": project_id, "title": title, "customer_org": str(body.get("customer_org") or "").strip(),
            "status": status, "created_at": now_iso, "updated_at": now_iso,
            "owner_engineer": str(body.get("owner_engineer") or email).strip().lower(), "members": normalized,
            "linked_motors": [], "notes": "", "pending_invites": []}


def version_record(n: int, email: str, draft: dict, source_revision: int | None = None) -> dict:
    record = {"version": n, "author_email": email.lower(), "created_at": now(), "draft": draft}
    if source_revision is not None:
        record["source_revision"] = source_revision
    return record


def validation_errors_for_mission(draft: dict) -> list[dict]:
    errors = []
    rows = draft.get("mission_profile", [])
    if not rows:
        return [{"field": "mission_profile", "section": "mission_profile", "message": "Add at least one complete phase or mark not known yet"}]
    for i, row in enumerate(rows):
        missing = [k for k in ("phase", "torque_Nm", "speed_rpm", "duration_s", "battery_voltage_V")
                   if row.get(k) is None or (isinstance(row.get(k), str) and not row[k].strip())]
        for key in missing:
            errors.append({"field": f"mission_profile[{i}].{key}", "section": "mission_profile", "message": "Required mission value is missing"})
    return errors
