from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.json")


def schema(version: int | None = None) -> dict:
    path = SCHEMA_PATH.with_name("schema_v1.json") if version == 1 else SCHEMA_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _field_map(version: int | None = None) -> dict[str, tuple[dict, dict]]:
    result = {}
    for section in schema(version)["sections"]:
        for field in section["fields"]:
            result[field["id"]] = (section, field)
    return result


def normalize_draft(value: Any, *, schema_version: int | None = None) -> dict:
    if not isinstance(value, dict):
        raise ValueError("draft must be an object")
    version = value.get("schema_version", 1) if schema_version is None else schema_version
    if version not in (1, 2):
        raise ValueError("unsupported schema_version")
    active_schema = schema(version)
    extra = set(value) - {"schema_version", "fields", "mission_profile", "mission_profile_status", "production", "provenance"}
    if extra:
        raise ValueError("unknown draft keys: " + ", ".join(sorted(extra)))
    out = {"schema_version": version, "fields": {}, "mission_profile": [], "mission_profile_status": value.get("mission_profile_status", "missing"), "production": {}}
    if out["mission_profile_status"] not in ("missing", "filled", "not_known_yet"):
        raise ValueError("mission_profile_status must be missing, filled, or not_known_yet")
    fields = value.get("fields", {})
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    known = _field_map(version)
    prod_section = next((s for s in active_schema["sections"] if s["id"] == "production"), None)
    for field_id, answer in fields.items():
        if field_id not in known or field_id == "mission_profile":
            raise ValueError(f"unknown field: {field_id}")
        if known[field_id][0]["id"] == "production":
            raise ValueError(f"production field must be placed under production: {field_id}")
        normalized_answer = _normalize_answer(answer, known[field_id][1], field_id)
        if version == 2 and field_id == "1_4" and normalized_answer.get("status") == "filled":
            point = normalized_answer.get("value") or {}
            torque, speed = point.get("torque_Nm"), point.get("speed_rpm")
            if isinstance(torque, (int, float)) and isinstance(speed, (int, float)):
                derived = torque * 2 * __import__("math").pi * speed / 60
                if not __import__("math").isfinite(derived):
                    raise ValueError("1_4 shaft power is outside the finite numeric range")
                supplied = point.get("shaft_power_W")
                if supplied is not None and abs(float(supplied) - derived) > max(1e-6, abs(derived) * 1e-6):
                    raise ValueError("1_4.shaft_power_W does not match torque and speed")
                point["shaft_power_W"] = derived
        if version == 2 and field_id in ("load_type", "load_source") and normalized_answer.get("status") == "filled":
            choice = normalized_answer.get("value")
            allowed = {item.get("value", item) if isinstance(item, dict) else item for item in known[field_id][1].get("options", [])}
            if choice not in allowed:
                raise ValueError(f"{field_id}: unsupported choice")
        out["fields"][field_id] = normalized_answer
    if version == 2:
        load_answer = out["fields"].get("load_characteristic", {})
        if load_answer.get("status") == "filled":
            _validate_load_characteristic(load_answer.get("value") or {}, out["fields"])
    mission = value.get("mission_profile", [])
    if not isinstance(mission, list):
        raise ValueError("mission_profile must be an array")
    row_keys = {"phase", "torque_Nm", "speed_rpm", "duration_s", "battery_voltage_V", "row_id", "rpm", "voltage_V", "source", "source_kind", "source_ref", "sample_id", "source_snapshot", "conditions", "stale", "override", "override_fields", "shaft_side", "direct_drive_confirmed"}
    seen_row_ids = set()
    for i, row in enumerate(mission):
        if not isinstance(row, dict):
            raise ValueError(f"mission_profile[{i}] must be an object")
        extra_row = set(row) - row_keys
        if extra_row:
            raise ValueError(f"mission_profile[{i}] has unknown fields: {', '.join(sorted(extra_row))}")
        normalized = {}
        aliases = {"speed_rpm": "rpm", "battery_voltage_V": "voltage_V"}
        for key, typ in (("phase", str), ("torque_Nm", (int, float)), ("speed_rpm", (int, float)),
                         ("duration_s", (int, float)), ("battery_voltage_V", (int, float))):
            alias = aliases.get(key)
            if key in row and alias in row and row[key] != row[alias]:
                raise ValueError(f"mission_profile[{i}].{key} conflicts with {alias}")
            item = row.get(key, row.get(alias) if alias else None)
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
        for key in ("row_id", "source", "source_kind", "source_ref", "sample_id", "source_snapshot", "conditions", "stale", "override", "override_fields", "shaft_side", "direct_drive_confirmed"):
            if key not in row:
                continue
            item = row[key]
            if key in ("row_id", "source", "source_kind", "source_ref", "sample_id") and not isinstance(item, str):
                raise ValueError(f"mission_profile[{i}].{key} must be text")
            if key == "row_id" and (not item.strip() or len(item) > 128):
                raise ValueError(f"mission_profile[{i}].row_id must contain 1 to 128 characters")
            if key in ("stale", "direct_drive_confirmed") and not isinstance(item, bool):
                raise ValueError(f"mission_profile[{i}].{key} must be boolean")
            if key == "shaft_side" and item not in ("load", "motor"):
                raise ValueError(f"mission_profile[{i}].shaft_side must be load or motor")
            if key in ("source_snapshot", "conditions", "override", "override_fields"):
                if not isinstance(item, dict):
                    raise ValueError(f"mission_profile[{i}].{key} must be an object")
                nested_allow = {
                    "source_snapshot": {"catalog_id", "catalog_type", "calculation_model", "vendor", "model", "data_quality", "power_data", "temperature_C", "altitude_m", "pressure_Pa", "extrapolated", "rpm_range_tested", "pitch_in", "digest", "sha256", "content_digest", "direct_drive_confirmed", "shaft_side"},
                    "conditions": {"temperature_C", "altitude_m", "pressure_Pa"},
                    "override": {"rpm", "torque_Nm"}, "override_fields": {"rpm", "torque_Nm"},
                }[key]
                if set(item) - nested_allow:
                    raise ValueError(f"mission_profile[{i}].{key} has unknown fields")
                if key in ("override", "override_fields") and any(not isinstance(v, bool) for v in item.values()):
                    raise ValueError(f"mission_profile[{i}].{key} values must be boolean")
                if key == "conditions" and any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                                                or (name == "pressure_Pa" and v <= 0) for name, v in item.items()):
                    raise ValueError(f"mission_profile[{i}].conditions values must be finite and pressure positive")
                if key == "source_snapshot":
                    text_keys = {"catalog_id", "catalog_type", "calculation_model", "vendor", "model", "data_quality", "power_data", "digest", "sha256", "content_digest"}
                    numeric_keys = {"temperature_C", "altitude_m", "pressure_Pa", "pitch_in"}
                    for name, nested in item.items():
                        valid = ((name in text_keys and isinstance(nested, str))
                                 or (name in numeric_keys and isinstance(nested, (int, float)) and not isinstance(nested, bool) and __import__("math").isfinite(nested))
                                 or (name in {"extrapolated", "direct_drive_confirmed"} and isinstance(nested, bool))
                                 or (name == "shaft_side" and nested in ("load", "motor")))
                        if name == "rpm_range_tested":
                            valid = (isinstance(nested, (list, tuple)) and len(nested) == 2
                                     and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                             and math.isfinite(v) and v >= 0 for v in nested)
                                     and nested[0] <= nested[1])
                        if name == "pressure_Pa":
                            valid = isinstance(nested, (int, float)) and not isinstance(nested, bool) and math.isfinite(nested) and nested > 0
                        if name == "pitch_in":
                            valid = isinstance(nested, (int, float)) and not isinstance(nested, bool) and math.isfinite(nested) and nested > 0
                        if not valid:
                            raise ValueError(f"mission_profile[{i}].source_snapshot.{name} has invalid type or value")
            normalized[key] = item
        if version == 2:
            import uuid
            row_id = normalized.get("row_id") or str(uuid.uuid5(uuid.NAMESPACE_URL, f"dev-spec-row:{i}:{json.dumps(normalized, sort_keys=True, separators=(',', ':'))}"))
            if row_id in seen_row_ids:
                raise ValueError(f"mission_profile[{i}].row_id must be unique")
            seen_row_ids.add(row_id)
            normalized["row_id"] = row_id
            source = normalized.get("source", normalized.get("source_kind", "manual"))
            if source not in ("manual", "catalog"):
                raise ValueError(f"mission_profile[{i}].source must be manual or catalog")
            normalized["source"] = source
        out["mission_profile"].append(normalized)
    if out["mission_profile"] and out["mission_profile_status"] == "missing":
        out["mission_profile_status"] = "filled"
    production = value.get("production", {})
    if not isinstance(production, dict):
        raise ValueError("production must be an object")
    prod_fields = {f["id"]: f for f in (prod_section or {}).get("fields", [])}
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
    if field.get("options") and status == "filled":
        allowed = {option.get("value", option) if isinstance(option, dict) else option
                   for option in field["options"]}
        if not any(value == option for option in allowed):
            raise ValueError(f"{field_id}: unsupported choice")
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
    if spec.get("options") and not any(value == (option.get("value") if isinstance(option, dict) else option)
                                       for option in spec["options"]):
        raise ValueError(f"{path}: unsupported choice")


def _value_present(value: Any, spec: dict | None = None) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    if isinstance(value, (list, dict)) and not value:
        return False
    if isinstance(value, dict) and (spec or {}).get("allow_range"):
        return value.get("minimum") is not None and value.get("maximum") is not None
    return True


def _property_complete(item: Any, prop: dict) -> bool:
    """A structured answer's property holds a usable value (a range needs both ordered bounds)."""
    if prop.get("allow_range") and isinstance(item, dict):
        low, high = item.get("minimum"), item.get("maximum")
        numeric = all(isinstance(b, (int, float)) and not isinstance(b, bool) for b in (low, high))
        return numeric and low <= high
    if not _value_present(item, prop):
        return False
    try:
        _validate_typed_item(item, prop, prop["id"], require_complete=True)
    except ValueError:
        return False
    return True


def _missing_any_groups(field: dict, value: dict) -> list[list[str]]:
    """`required_any` groups of which no property is filled (e.g. torque OR power)."""
    props = {p["id"]: p for p in field.get("properties", [])}
    return [group for group in field.get("required_any", [])
            if not any(_property_complete(value.get(pid), props[pid]) for pid in group if pid in props)]


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
        if not all(_property_complete(value.get(prop["id"]), prop) for prop in required_props):
            return False
        if _missing_any_groups(field, value):
            return False
        minimum = int(field.get("min_filled_properties", 0))
        if sum(1 for key, v in value.items() if _value_present(v, next((p for p in field["properties"] if p["id"] == key), {}))) < minimum:
            return False
        return True
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    return True


def _validate_load_characteristic(value: dict, fields: dict) -> None:
    load_type_answer = fields.get("load_type") if isinstance(fields.get("load_type"), dict) else {}
    source_answer = fields.get("load_source") if isinstance(fields.get("load_source"), dict) else {}
    load_type = load_type_answer.get("value") if load_type_answer.get("status") == "filled" else None
    source = source_answer.get("value") if source_answer.get("status") == "filled" else None
    if source not in ("catalog", "manual"):
        raise ValueError("load_characteristic.source: choose catalog or manual before adding a curve")
    if source == "catalog" and load_type != "air_propeller":
        raise ValueError("load_characteristic: no reviewed compatible catalog model for this load type")
    samples = value.get("samples", [])
    if not isinstance(samples, list) or len(samples) < (1 if source == "catalog" else 2):
        raise ValueError("load_characteristic.samples: add a usable curve sample")
    if not isinstance(value.get("units"), str) or not value["units"].strip():
        raise ValueError("load_characteristic.units: units are required")
    if value["units"] not in ("rpm,N·m,W", "rpm, N·m, W"):
        raise ValueError("load_characteristic.units: expected canonical rpm, N·m, and W units")
    if not isinstance(value.get("shaft_side"), str) or not value["shaft_side"].strip():
        raise ValueError("load_characteristic.shaft_side: shaft side is required")
    if samples:
        rpms = [sample.get("rpm") for sample in samples]
        if any(isinstance(rpm, bool) or not isinstance(rpm, (int, float)) or not math.isfinite(float(rpm)) or rpm < 0 for rpm in rpms):
            raise ValueError("load_characteristic.samples.rpm: finite number required")
        if any(b <= a for a, b in zip(rpms, rpms[1:])):
            raise ValueError("load_characteristic.samples: RPM values must be strictly increasing")
        for index, sample in enumerate(samples):
            torque, power, rpm = sample.get("torque_Nm"), sample.get("shaft_power_W"), sample.get("rpm")
            if isinstance(torque, bool) or not isinstance(torque, (int, float)) or not __import__("math").isfinite(torque):
                raise ValueError(f"load_characteristic.samples[{index}].torque_Nm: finite number required")
            expected = torque * 2 * __import__("math").pi * rpm / 60
            if (not math.isfinite(expected) or isinstance(power, bool) or not isinstance(power, (int, float))
                    or not math.isfinite(power) or abs(power - expected) > max(1e-6, abs(expected) * 1e-5)):
                raise ValueError(f"load_characteristic.samples[{index}].shaft_power_W: inconsistent with torque and RPM")
            if not isinstance(sample.get("quality"), str) or not sample["quality"].strip():
                raise ValueError(f"load_characteristic.samples[{index}].quality: quality is required")
            if not isinstance(sample.get("extrapolated"), bool):
                raise ValueError(f"load_characteristic.samples[{index}].extrapolated: boolean required")
    domain = value.get("valid_domain")
    if not isinstance(domain, dict) or domain.get("min_rpm") is None or domain.get("max_rpm") is None:
        raise ValueError("load_characteristic.valid_domain: RPM bounds are required")
    low, high = domain["min_rpm"], domain["max_rpm"]
    if any(isinstance(bound, bool) or not isinstance(bound, (int, float)) or not math.isfinite(float(bound)) for bound in (low, high)):
        raise ValueError("load_characteristic.valid_domain: RPM bounds must be finite numbers")
    if low < 0 or high < 0:
        raise ValueError("load_characteristic.valid_domain: RPM bounds must be non-negative")
    if low > high:
        raise ValueError("load_characteristic.valid_domain: minimum RPM exceeds maximum")
    if source == "catalog" and value.get("catalog_type") != "air_propeller":
        raise ValueError("load_characteristic.catalog_type: incompatible catalog record")
    if source == "catalog" and value.get("medium") != "air":
        raise ValueError("load_characteristic.medium: catalog model must use air")
    if source == "catalog" and (not value.get("catalog_id") or not value.get("content_digest") or not value.get("calculation_model")):
        raise ValueError("load_characteristic: catalog ID, content digest, and calculation model are required")
    if value.get("units") != "rpm,N·m,W":
        raise ValueError("load_characteristic.units: expected rpm,N·m,W")
    if value.get("shaft_side") not in ("motor", "load"):
        raise ValueError("load_characteristic.shaft_side: expected motor or load")


def _conditional_required(field: dict, draft: dict) -> bool:
    condition = field.get("required_when")
    if not isinstance(condition, dict):
        return False
    answer = draft.get("fields", {}).get(condition.get("field"), {})
    return answer.get("status") == "filled" and answer.get("value") in condition.get("in", [])


def _load_characteristic_complete(answer: dict, fields: dict) -> bool:
    source_answer = fields.get("load_source", {})
    source = source_answer.get("value") if isinstance(source_answer, dict) and source_answer.get("status") == "filled" else None
    if source not in ("catalog", "manual"):
        return True
    if not isinstance(answer, dict) or answer.get("status") != "filled":
        return _answer_complete(answer, {"type": "object"})
    value = answer.get("value")
    if not isinstance(value, dict):
        return False
    samples = value.get("samples")
    if not isinstance(samples, list) or len(samples) < (2 if source == "manual" else 1):
        return False
    domain = value.get("valid_domain")
    domain_ok = isinstance(domain, dict) and domain.get("min_rpm") is not None and domain.get("max_rpm") is not None
    samples_ok = all(isinstance(item, dict) and bool(item.get("quality")) and isinstance(item.get("extrapolated"), bool)
                    and isinstance(item.get("shaft_power_W"), (int, float)) for item in samples)
    if source == "catalog":
        return bool(value.get("catalog_id")) and bool(value.get("content_digest")) and bool(value.get("calculation_model")) and bool(value.get("units")) and bool(value.get("shaft_side")) and value.get("medium") == "air" and value.get("catalog_type") == "air_propeller" and domain_ok and samples_ok
    return bool(value.get("units")) and bool(value.get("shaft_side")) and domain_ok and samples_ok


def completeness(draft: dict) -> dict:
    schema_version = int(draft.get("schema_version", 1))
    active_schema = schema(schema_version)
    required_by_section = {}
    section_fields = {}
    for section in active_schema["sections"]:
        section_fields[section["id"]] = {field["id"]: field for field in section["fields"]}
        required_by_section[section["id"]] = [field["id"] for field in section["fields"] if field.get("required") or _conditional_required(field, draft)]
    section_stats, total, filled, answered, unknown, missing, invalid = {}, 0, 0, 0, 0, 0, 0
    for section_id, ids in required_by_section.items():
        section_filled = 0
        section_answered = 0
        section_unknown = 0
        for field_id in ids:
            if section_id == "mission_profile":
                status = draft.get("mission_profile_status", "missing")
                valid = status == "not_known_yet" or (status == "filled" and bool(draft.get("mission_profile")) and not validation_errors_for_mission(draft))
            else:
                area = draft.get("production", {}) if section_id == "production" else draft.get("fields", {})
                answer = area.get(field_id, {})
                if field_id == "load_characteristic" and schema_version == 2:
                    valid = _load_characteristic_complete(answer, draft.get("fields", {}))
                else:
                    valid = _answer_complete(answer, section_fields[section_id][field_id])
            status = (draft.get("mission_profile_status", "missing") if section_id == "mission_profile"
                      else (draft.get("production", {}) if section_id == "production" else draft.get("fields", {})).get(field_id, {}).get("status", "missing"))
            if valid and status != "not_known_yet":
                section_filled += 1
            if status == "not_known_yet":
                unknown += 1
                section_unknown += 1
            elif valid:
                answered += 1
                section_answered += 1
            elif status == "filled":
                invalid += 1
            else:
                missing += 1
        total += len(ids)
        filled += section_filled
        section_stats[section_id] = {"filled": section_answered + section_unknown, "filled_known": section_filled,
                                     "answered": section_answered, "required": len(ids), "total_required": len(ids),
                                     "unknown": section_unknown, "complete": section_answered + section_unknown == len(ids)}
    return {"total_required": total, "filled_required": answered + unknown, "filled_known": filled, "answered": answered, "unknown": unknown,
            "missing": missing, "invalid": invalid, "sections": section_stats, "complete": total == answered + unknown}


def validation_errors(draft: dict) -> list[dict]:
    errors = []
    for section in schema(int(draft.get("schema_version", 1)))["sections"]:
        sid = section["id"]
        for field in section["fields"]:
            if not (field.get("required") or _conditional_required(field, draft)):
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
                if fid == "load_characteristic" and answer.get("status") == "filled":
                    try:
                        _validate_load_characteristic(answer.get("value") or {}, draft.get("fields", {}))
                    except ValueError as exc:
                        errors.append({"field": str(exc).split(":", 1)[0], "section": sid, "message": str(exc).split(":", 1)[-1].strip()})
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
                    for group in _missing_any_groups(field, val):
                        errors.append({"field": f"{fid}.{group[0]}", "section": sid,
                                       "message": "Enter at least one of: " + ", ".join(group)})
                    minimum = int(field.get("min_filled_properties", 0))
                    props = {p["id"]: p for p in field["properties"]}
                    if minimum and sum(1 for k, v in val.items() if _value_present(v, props.get(k, {}))) < minimum:
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
    record = {"version": n, "schema_version": int(draft.get("schema_version", 1)), "author_email": email.lower(), "created_at": now(), "draft": draft}
    if source_revision is not None:
        record["source_revision"] = source_revision
    return record


def validation_errors_for_mission(draft: dict) -> list[dict]:
    errors = []
    rows = draft.get("mission_profile", [])
    if not rows:
        return [{"field": "mission_profile", "section": "mission_profile", "message": "Add at least one complete phase or mark not known yet"}]
    for i, row in enumerate(rows):
        missing = [k for k in ("phase", "torque_Nm", "rpm", "duration_s", "voltage_V")
                   if row.get(k, row.get({"rpm": "speed_rpm", "voltage_V": "battery_voltage_V"}.get(k, ""))) is None or
                   (isinstance(row.get(k, row.get({"rpm": "speed_rpm", "voltage_V": "battery_voltage_V"}.get(k, ""))), str) and not row.get(k, row.get({"rpm": "speed_rpm", "voltage_V": "battery_voltage_V"}.get(k, ""))).strip())]
        for key in missing:
            errors.append({"field": f"mission_profile[{i}].{key}", "section": "mission_profile", "message": "Required mission value is missing"})
        if row.get("source") == "catalog" and row.get("shaft_side") == "load" and row.get("direct_drive_confirmed") is not True:
            errors.append({"field": f"mission_profile[{i}].direct_drive_confirmed", "section": "mission_profile",
                           "message": "Confirm direct drive before using a load-shaft catalog point as a motor point"})
    return errors
