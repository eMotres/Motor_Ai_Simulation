from __future__ import annotations

import json
import math
import re
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


_BALANCE_GRADE = re.compile(r"(?<![A-Za-z])G\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_RUNOUT_NUMBER = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(mm)?(?![\w.,])", re.IGNORECASE)
_FILLER = re.compile(r"run-?out|tir|balance|grade|iso\s*21940|max(?:imum)?|up to|mm|[\s;,:/()\-≤<>=≈~]+", re.IGNORECASE)


def legacy_balance_runout(text: str) -> dict:
    """The structured 4_8 value (``balance_grade``, ``runout_mm``, ``note``) of a legacy single-string answer.

    Before 2026-10-27 field 4_8 held one string such as ``"G2.5; runout <= 0.05 mm"`` or ``"G6.3, 0.02 mm"``.  A grade
    ``G<number>`` becomes ``balance_grade`` (written ``G2.5``); the first number AFTER the grade (with no grade: the first
    number followed by ``mm`` or named as runout / TIR) becomes ``runout_mm``.  When anything else is left over, or nothing could be read, the whole raw
    text is kept in ``note`` so nothing is lost.  Pure function; a stored value is never rewritten by it.
    """
    raw = text.strip()
    result: dict = {}
    rest = raw
    grade = _BALANCE_GRADE.search(raw)
    if grade:
        result["balance_grade"] = "G" + grade.group(1).replace(",", ".")
        rest = raw[:grade.start()] + " " + raw[grade.end():]
        tail = raw[grade.end():]
    else:
        tail = raw
    runout = _RUNOUT_NUMBER.search(tail)
    # a bare number is a runout only after a grade, with "mm", or when the text says runout / TIR
    if runout and (grade or runout.group(2) or re.search(r"run-?out|\btir\b|跳动", raw, re.IGNORECASE)):
        number = float(runout.group(1).replace(",", "."))
        if math.isfinite(number):
            result["runout_mm"] = number
            rest = rest.replace(runout.group(0), " ", 1)
    if not result or _FILLER.sub("", rest).strip():
        result["note"] = raw
    return result


def upgrade_legacy_values(draft: dict) -> dict:
    """A copy of a version-2 draft where a legacy STRING in 4_8 (balance grade + runout in one text) is the structured value.

    Read-time only: stored drafts and the immutable submitted versions are never rewritten.  Idempotent.
    """
    import copy
    out = copy.deepcopy(draft)
    if int(out.get("schema_version", 1)) < 2:
        return out
    fields = out.get("fields")
    answer = fields.get("4_8") if isinstance(fields, dict) else None
    if isinstance(answer, str):
        answer = {"status": "filled", "value": answer}
    if isinstance(answer, dict) and isinstance(answer.get("value"), str):
        text = answer["value"]
        fields["4_8"] = ({**answer, "value": legacy_balance_runout(text)} if text.strip()
                         else {**answer, "value": None, "status": "missing" if answer.get("status") == "filled" else answer.get("status", "missing")})
    return out


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
        normalized_answer = (_normalize_key_points(answer) if field_id == "key_points" and version == 2
                             else _normalize_answer(answer, known[field_id][1], field_id))
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
        _apply_key_point_derivation(out["fields"])
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
    if version == 2:
        out = with_defaults(out)
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
    if field_id == "4_8" and field.get("properties") and isinstance(value, str):
        value = legacy_balance_runout(value)  # a pre-2026-10-27 single text -> grade / runout / note
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
            if field_id == "2_1" and key == "supply_type" and item not in ("battery", "dc_source"):
                raise ValueError("2_1.supply_type: battery or dc_source")
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


def _inverted_pairs(field: dict, value: Any) -> list[tuple[str, str]]:
    """The `ordered_pairs` (lower bound, upper bound) of a structured field whose two stored numbers are inverted.  A pair with a
    missing / non-numeric side is never reported (partial answers stay allowed); nothing is rewritten."""
    if not isinstance(value, dict):
        return []
    def number(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    return [(low, high) for low, high in field.get("ordered_pairs", [])
            if number(value.get(low)) and number(value.get(high)) and value[low] > value[high]]


def ordered_pair_errors(draft: dict) -> list[dict]:
    """A field with `ordered_pairs` (the generator output window 2_13) whose lower bound exceeds its upper bound: a validation error
    on the lower bound's path, whether or not the field is required.  Unknown answers are skipped."""
    errors = []
    for section in schema(int(draft.get("schema_version", 1)))["sections"]:
        area = draft.get("production", {}) if section["id"] == "production" else draft.get("fields", {})
        for field in section["fields"]:
            answer = area.get(field["id"])
            if not field.get("ordered_pairs") or not isinstance(answer, dict) or answer.get("status") == "not_known_yet":
                continue
            for low, high in _inverted_pairs(field, answer.get("value")):
                errors.append({"field": f"{field['id']}.{low}", "section": section["id"],
                               "message": f"{low} exceeds {high}: enter one value per quantity, the lowest not above the highest"})
    return errors


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
        required_props = [p for p in field["properties"] if _property_required(p, value)]
        if not all(_property_complete(value.get(prop["id"]), prop) for prop in required_props):
            return False
        if _missing_any_groups(field, value) or _inverted_pairs(field, value):
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


# Load x cooling (owner 2026-10-25): the specification refuses what cannot occur.  The same table as the web's
# specChoices.ts (LOAD_COOLING / AIRFLOW_BY_LOAD): an air propeller never runs under water, and propeller wash only
# exists behind an air propeller.  Finer stored load types sit under their group.
_LOAD_GROUP = {"water_jet": "marine_propeller", "pump": "shaft", "engine_driven_generator": "shaft", "robot_joint": "shaft"}
_LOAD_COOLING = {
    "air_propeller": ("air", "liquid"),
    "marine_propeller": ("submerged", "liquid", "air"),
    "wheel": ("air", "liquid"),  # a wheel drive runs on the ground, never under water (owner 2026-10-26)
    "turbine": ("air", "submerged", "liquid"),
    "shaft": ("air", "liquid", "submerged"),
}
_AIRFLOW_BY_LOAD = {
    "air_propeller": ("propeller wash", "ram air", "still air", "fan"),
    "wheel": ("still air", "ram air", "fan"),
    "turbine": ("ram air", "still air", "fan"),
    "marine_propeller": ("still air", "fan"),
    "shaft": ("still air", "fan", "ram air"),
}
# The mode a load brings when none is stored (the web pre-selects it; the user can change it).  Ram air stays allowed
# behind an air propeller (forward flight) but is never its default.  The backend does not write it - the mirror exists
# so one case table (ambient_cases.json) pins the web's DEFAULT_AIRFLOW to it.
_DEFAULT_AIRFLOW = {"air_propeller": "propeller wash", "turbine": "ram air", "wheel": "still air", "shaft": "still air",
                    "marine_propeller": "still air"}
_KNOWN_AIRFLOW = {m for modes in _AIRFLOW_BY_LOAD.values() for m in modes}


_COOLING_MEDIA = ("air", "liquid", "submerged")
_SUBMERGED_PROPERTIES = ("water_temperature_C", "water_speed_mps", "depth_m")  # depth_m: the legacy 3_1 depth still tells the medium


def _present(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_present(v) for v in value.values())
    return not (value is None or (isinstance(value, str) and not value.strip()))


def cooling_medium(value: Any) -> tuple[str | None, bool]:
    """(medium, inferred) of a 3_1 cooling answer: the stored `cooling_medium`, else the medium its stored values
    imply, else (None, False).  The same rule as the web's `coolingMedium` (specChoices.ts) - keep them in step."""
    v = value if isinstance(value, dict) else {}
    if v.get("cooling_medium") in _COOLING_MEDIA:
        return v["cooling_medium"], False
    ctype = v["cooling_type"].lower() if isinstance(v.get("cooling_type"), str) else ""
    coolant = v["coolant"].lower() if isinstance(v.get("coolant"), str) else ""
    text = re.sub(r"\b(no|without|not an?)\s+(internal\s+)?(coolant\s+|liquid\s+|cooling\s+)?(loop|circuit|jacket)s?\b", " ",
                  f"{ctype} {coolant}", flags=re.ASCII)
    circuit = bool(re.search(r"glycol|jacket|ppg|loop|乙二醇|冷却液|回路", text))
    if re.search(r"submerg|immers|underwater|sea ?water|水下|浸没|海水", text) and not circuit:
        return "submerged", True
    if any(_present(v.get(k)) for k in ("coolant", "flow_rate", "pressure_drop_Pa")) or circuit:
        return "liquid", True
    if _present(v.get("airflow")) or re.search(r"\bair|风冷|空气", ctype, flags=re.ASCII):
        return "air", True
    if _present(v.get("inlet_temperature_C")):
        return "liquid", True
    if any(_present(v.get(k)) for k in _SUBMERGED_PROPERTIES) or re.search(r"water|submerg|immers|水下|浸没|水冷", ctype):
        return ("liquid" if re.search(r"glycol|coolant|jacket|乙二醇|冷却液", ctype) else "submerged"), True
    if re.search(r"liquid|coolant|glycol|oil|液冷|冷却液|油冷", ctype):
        return "liquid", True
    return None, False


def _active_cooling(draft: dict):
    """The cooling answer's value as the rules read it: a "not known yet" cooling is no choice (web: activeCooling)."""
    answer = (draft.get("fields") or {}).get("3_1")
    if isinstance(answer, dict) and "status" in answer:
        return None if answer.get("status") == "not_known_yet" else answer.get("value")
    return answer


def field_applies(field: dict, draft: dict) -> bool:
    """A field with `applies_when_cooling_medium` (the maximum ambient temperature, 3_2) is asked, required and counted
    only for those cooling media - air - or while no medium is chosen (air is the default assumption).  Liquid cooling
    uses the coolant inlet temperature and underwater cooling the water temperature of 3_1.  The stored value is kept.
    The same rule as the web's `fieldApplies` (wizardState.ts)."""
    media, only = field.get("applies_when_cooling_medium"), field.get("only_when_cooling_medium")
    if not media and not only:
        return True
    medium, _ = cooling_medium(_active_cooling(draft))
    if only:  # `only_when_cooling_medium` (the immersion depth, 3_6): meaningless unless that medium is chosen
        return medium is not None and medium in only
    return medium is None or medium in media


def lift_legacy_depth(draft: dict) -> dict:
    """Read-time move of the underwater depth: a stored 3_1 `depth_m` shows in its new place, 3_6 (max immersion depth,
    owner 2026-10-26).  Nothing is lost - the 3_1 value stays stored; the lifted copy is added only while 3_6 holds no
    answer at all (a cleared 3_6 stays cleared).  Returns a copy; the stored draft is not changed."""
    import copy
    out = copy.deepcopy(draft)
    fields = out.get("fields")
    if not isinstance(fields, dict) or "3_6" in fields:
        return out
    cooling = fields.get("3_1")
    value = cooling.get("value") if isinstance(cooling, dict) else None
    depth = value.get("depth_m") if isinstance(value, dict) else None
    if isinstance(depth, (int, float)) and not isinstance(depth, bool) and math.isfinite(depth) and depth >= 0:
        fields["3_6"] = {"status": "filled", "value": depth}
    return out


#: Pre-filled, editable defaults of a version-2 specification: ``{field: {property: value}}``.  They are COUNTED AS FILLED
#: (the customer can change them any time).  Prototypes: 1 (owner 2026-10-26).
SPEC_DEFAULTS = {"6_1": {"prototypes": 1}}


def with_defaults(draft: dict) -> dict:
    """A copy of a version-2 draft where every default in :data:`SPEC_DEFAULTS` is filled in if it is empty.

    Empty = the answer is absent, or has no value for that property.  A field marked "not known yet" is left alone, and so is
    a value the customer typed.  Idempotent; the stored draft is not changed.  The same rule as the web's ``withSpecDefaults``
    (wizardState.ts).
    """
    import copy
    out = upgrade_legacy_values(draft)  # includes the deep copy; 4_8 text -> structured, read-time only
    if int(out.get("schema_version", 1)) < 2:
        return out
    fields = out.setdefault("fields", {})
    if not isinstance(fields, dict):
        return out
    for field_id, props in SPEC_DEFAULTS.items():
        answer = fields.get(field_id)
        if isinstance(answer, dict) and answer.get("status") == "not_known_yet":
            continue
        value = answer.get("value") if isinstance(answer, dict) else None
        value = dict(value) if isinstance(value, dict) else {}
        changed = False
        for prop, default in props.items():
            current = value.get(prop)
            if not _value_present(current) or (isinstance(current, dict) and all(x in (None, "") for x in current.values())):
                value[prop] = default
                changed = True
        if changed or not isinstance(answer, dict):
            fields[field_id] = {"status": "filled", "value": value}
    return out


def proof_speed_rpm(draft: dict) -> float | None:
    """The speed the mechanics are checked at (owner 2026-10-26): exactly the specification's maximum speed (1_6) - the single
    highest speed the motor ever sees, no safety factor on top.

    An older project may hold an explicitly stored overspeed value (1_6 ``overspeed_rpm``, it may come from an engine or
    generator limit): when that is LARGER it wins, and the web shows it read-only in the Maximum speed tooltip.  A range is
    read as its worst (maximum) value.  ``None`` while no maximum speed is stated or the field is marked not known yet.
    The torque and voltage requirements are NOT read from here - they come from the key operating points.
    """
    answer = (draft.get("fields") or {}).get("1_6") if isinstance(draft, dict) else None
    if not isinstance(answer, dict) or answer.get("status") == "not_known_yet":
        return None
    value = answer.get("value")
    if not isinstance(value, dict):
        return None
    top = _worst_number(value.get("maximum_rpm"))
    stored = _worst_number(value.get("overspeed_rpm"))
    candidates = [v for v in (top, stored) if v is not None and v > 0]
    return float(max(candidates)) if candidates else None


def _property_required(prop: dict, value: Any) -> bool:
    """A structured answer's property is required outright, or - the cooling temperatures - when the answer's own
    cooling medium is the one named by `required_when_cooling_medium` (liquid: the coolant inlet temperature,
    underwater: the water temperature).  The same rule as the web's `propertyRequired` (wizardState.ts)."""
    if _choice_holds(prop.get("required_if_value"), value):
        return True
    if prop.get("required"):
        return not _choice_holds(prop.get("required_unless_value"), value)
    medium = prop.get("required_when_cooling_medium")
    return bool(medium) and cooling_medium(value)[0] == medium


def _choice_holds(rule: Any, value: Any) -> bool:
    """Requirements that follow another property of the same answer (the power supply, 2_1): `required_if_value` makes a
    property required while the named property holds the given choice, `required_unless_value` drops a required property
    while it does.  The same rule as the web's `propertyRequired` (wizardState.ts)."""
    return isinstance(rule, dict) and isinstance(value, dict) and value.get(rule.get("property")) == rule.get("equals")


def _field_required(field: dict, draft: dict) -> bool:
    return field_applies(field, draft) and bool(field.get("required") or _conditional_required(field, draft))


def thermal_basis(draft: dict) -> dict:
    """The temperature every thermal calculation starts from: the maximum ambient temperature (3_2) for air cooling,
    the maximum coolant inlet temperature (3_1) for liquid cooling, the maximum water temperature (3_1) underwater;
    not chosen yet reads as air.  Any reader of the thermal basis goes through this, never straight to 3_2
    `temperature_max_C`.  `temperature_C` is None while the value is not stated."""
    cooling = _active_cooling(draft)
    medium = cooling_medium(cooling)[0] or "air"
    source = {"air": "temperature_max_C", "liquid": "inlet_temperature_C", "submerged": "water_temperature_C"}[medium]
    holder = _active_value(draft.get("fields") or {}, "3_2") if medium == "air" else cooling
    raw = holder.get(source) if isinstance(holder, dict) else None
    ok = isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw)
    return {"medium": medium, "source": f"{'3_2' if medium == 'air' else '3_1'}.{source}", "temperature_C": float(raw) if ok else None}


def _active_value(fields: dict, field_id: str):
    """The value of a FILLED answer; a value kept under missing / not_known_yet is retained, not active."""
    answer = fields.get(field_id)
    if isinstance(answer, dict) and "status" in answer:
        return answer.get("value") if answer.get("status") == "filled" else None
    return answer


def cooling_load_errors(draft: dict) -> list[dict]:
    """The cooling answer (3_1) against the chosen load: a medium the load cannot run in, or a standard airflow mode
    the load cannot make.  A typed own airflow description is not judged; nothing is checked before a load is chosen."""
    fields = draft.get("fields") or {}
    load = _active_value(fields, "load_type")
    group = load if load in _LOAD_COOLING else _LOAD_GROUP.get(load)
    cooling = _active_value(fields, "3_1")
    if not group or not isinstance(cooling, dict):
        return []
    errors = []
    medium = cooling_medium(cooling)[0]  # the medium the form shows: stored, else the one the stored values imply
    if medium in _COOLING_MEDIA and medium not in _LOAD_COOLING[group]:
        errors.append({"field": "3_1.cooling_medium", "section": "3",
                       "message": f"{medium} cooling does not fit the load {load}"})
    airflow = cooling.get("airflow") if isinstance(cooling.get("airflow"), dict) else {}
    mode = str(airflow.get("mode") or "").strip().lower()
    # the airflow is judged only where the form asks it: air cooling, or liquid outside a water propeller's pod
    # (a hidden, retained airflow from an earlier load never blocks the current answer)
    airflow_active = medium == "air" or (medium == "liquid" and group != "marine_propeller")
    if airflow_active and mode in _KNOWN_AIRFLOW and mode not in _AIRFLOW_BY_LOAD[group]:
        errors.append({"field": "3_1.airflow.mode", "section": "3",
                       "message": f"airflow '{airflow.get('mode')}' cannot occur with the load {load}"})
    return errors


# --- Key operating points (owner 2026-10-26) ----------------------------------------------------------------
# A motor has several key points, not just continuous + peak: takeoff, cruise, landing, peak ... (free names).  Each
# row: name, torque, speed, duration ("continuous" or seconds > 0) and an optional DC voltage.  The stored list is the
# source of truth; the S1 point (1_4) and the peak point (1_5) that the rest of the system reads are DERIVED from it on
# every save (continuous = the longest-running point, peak = the highest-torque point), so nothing downstream breaks.
# A project that holds only 1_4 / 1_5 stays valid untouched and reads as two points until a key point is edited.
KEY_POINTS_FIELD = "key_points"
CONTINUOUS = "continuous"
MAX_KEY_POINTS = 50
_KEY_POINT_KEYS = ("row_id", "name", "torque_Nm", "speed_rpm", "duration_s", "voltage_V")


def _key_point_number(value: Any, path: str, minimum: float | None = None) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{path}: finite number required")
    if minimum is not None and value < minimum:
        raise ValueError(f"{path}: value outside allowed range")
    return value


def _normalize_key_points(answer: Any) -> dict:
    if not isinstance(answer, dict) or "status" not in answer:
        answer = {"value": answer, "status": "filled"}
    if set(answer) - {"value", "status"}:
        raise ValueError("key_points: unsupported answer keys")
    status = answer.get("status")
    if status not in ("filled", "not_known_yet", "missing"):
        raise ValueError("key_points: invalid status")
    rows = answer.get("value")
    if rows is None:
        if status == "filled":
            raise ValueError("key_points: value required")
        return {"value": None, "status": status}
    if not isinstance(rows, list):
        raise ValueError("key_points: array required")
    if len(rows) > MAX_KEY_POINTS:
        raise ValueError(f"key_points: at most {MAX_KEY_POINTS} points")
    out, seen = [], set()
    for i, row in enumerate(rows):
        path = f"key_points[{i}]"
        if not isinstance(row, dict):
            raise ValueError(f"{path}: object required")
        extra = set(row) - set(_KEY_POINT_KEYS)
        if extra:
            raise ValueError(f"{path}: unknown properties: {', '.join(sorted(extra))}")
        name = row.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError(f"{path}.name: text required")
        name = (name or "").strip() or None
        if name is not None and len(name) > 80:
            raise ValueError(f"{path}.name: at most 80 characters")
        duration = row.get("duration_s")
        if isinstance(duration, str):
            if duration.strip().lower() == CONTINUOUS:
                duration = CONTINUOUS
            elif not duration.strip():
                duration = None
            else:
                raise ValueError(f"{path}.duration_s: continuous or a number of seconds")
        else:
            duration = _key_point_number(duration, f"{path}.duration_s", 0)
        item = {"name": name, "torque_Nm": _key_point_number(row.get("torque_Nm"), f"{path}.torque_Nm"),
                "speed_rpm": _key_point_number(row.get("speed_rpm"), f"{path}.speed_rpm", 0),
                "duration_s": duration, "voltage_V": _key_point_number(row.get("voltage_V"), f"{path}.voltage_V", 0)}
        row_id = row.get("row_id")
        if row_id is not None and (not isinstance(row_id, str) or not row_id.strip() or len(row_id) > 128):
            raise ValueError(f"{path}.row_id must contain 1 to 128 characters")
        if not row_id:
            import uuid
            row_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"dev-key-point:{i}:{json.dumps(item, sort_keys=True, separators=(',', ':'))}"))
        if row_id in seen:
            raise ValueError(f"{path}.row_id must be unique")
        seen.add(row_id)
        out.append({"row_id": row_id, **item})
    if not out:
        return {"value": [], "status": "missing" if status == "filled" else status}
    return {"value": out, "status": status}


def key_point_row_errors(row: dict, index: int) -> list[dict]:
    """Loud validation of one key point: a name, torque and speed, and a duration that is continuous or seconds > 0."""
    base = f"key_points[{index}]"
    errors = []

    def missing(prop: str, message: str = "Required value is missing"):
        errors.append({"field": f"{base}.{prop}", "section": "1", "message": message})
    if not isinstance(row.get("name"), str) or not row["name"].strip():
        missing("name")
    for prop in ("torque_Nm", "speed_rpm"):
        value = row.get(prop)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            missing(prop)
    duration = row.get("duration_s")
    if duration is None or duration == "":
        missing("duration_s")
    elif duration != CONTINUOUS and (isinstance(duration, bool) or not isinstance(duration, (int, float))
                                     or not math.isfinite(duration) or duration <= 0):
        missing("duration_s", "Duration must be continuous or more than 0 s")
    return errors


def key_points_stored(draft: dict) -> bool:
    """A key points list (or an explicit "not known yet") is stored: it is then the source of truth, even when the list
    is empty.  A bare placeholder without a list (nothing typed yet) leaves the legacy S1 / peak in charge."""
    answer = (draft.get("fields") or {}).get(KEY_POINTS_FIELD)
    return isinstance(answer, dict) and (answer.get("status") == "not_known_yet" or isinstance(answer.get("value"), list))


def _worst_number(value: Any) -> float | None:
    """A plain number, or the maximum of a range stored before the one-value rule."""
    if isinstance(value, dict):
        value = value.get("maximum")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def legacy_key_points(fields: dict) -> list[dict]:
    """A project that holds only the S1 point (1_4) and the peak point (1_5) reads as those two key points."""
    rows = []
    for fid, name, row_id in (("1_4", "Continuous", "legacy-s1"), ("1_5", "Peak", "legacy-peak")):
        answer = fields.get(fid)
        value = answer.get("value") if isinstance(answer, dict) else None
        if not isinstance(value, dict) or (isinstance(answer, dict) and answer.get("status") == "not_known_yet"):
            continue
        torque, speed = _worst_number(value.get("torque_Nm")), _worst_number(value.get("speed_rpm"))
        if torque is None and speed is None:
            continue
        duration = CONTINUOUS if fid == "1_4" else _worst_number(value.get("duration_s"))
        rows.append({"row_id": row_id, "name": name, "torque_Nm": torque, "speed_rpm": speed,
                     "duration_s": duration, "voltage_V": None})
    return rows


def key_points_effective(draft: dict) -> list[dict]:
    """The key points of a draft: the stored list, or the two legacy points while nothing is stored."""
    fields = draft.get("fields") or {}
    answer = fields.get(KEY_POINTS_FIELD)
    if isinstance(answer, dict):
        rows = answer.get("value")
        return [dict(row) for row in rows] if isinstance(rows, list) else []
    return legacy_key_points(fields)


def _usable_point(row: dict) -> bool:
    return not key_point_row_errors(row, 0)


def derive_continuous_peak(rows: list[dict]) -> tuple[dict | None, dict | None]:
    """Deterministic S1 / peak choice (the design wizard reads these two).
    Continuous = a point with duration "continuous" (the highest torque among several), else the longest-duration
    point.  Peak = the highest-torque point other than the continuous one, only if it exceeds the continuous torque.
    Ties: higher torque, then higher speed, then the earlier row."""
    usable = [(i, row) for i, row in enumerate(rows) if _usable_point(row)]
    if not usable:
        return None, None
    continuous = [(i, row) for i, row in usable if row["duration_s"] == CONTINUOUS]
    if continuous:
        pick = max(continuous, key=lambda ir: (abs(ir[1]["torque_Nm"]), ir[1]["speed_rpm"], -ir[0]))
    else:
        pick = max(usable, key=lambda ir: (ir[1]["duration_s"], abs(ir[1]["torque_Nm"]), ir[1]["speed_rpm"], -ir[0]))
    others = [(i, row) for i, row in usable if i != pick[0]]
    peak = max(others, key=lambda ir: (abs(ir[1]["torque_Nm"]), ir[1]["speed_rpm"], -ir[0]), default=None)
    if peak is not None and abs(peak[1]["torque_Nm"]) <= abs(pick[1]["torque_Nm"]):
        peak = None
    return pick[1], (peak[1] if peak else None)


def _shaft_power(torque: float, rpm: float) -> float:
    return torque * 2 * math.pi * rpm / 60


def _apply_key_point_derivation(fields: dict) -> None:
    """Keep 1_4 (S1) and 1_5 (peak) in step with the stored key points.  Nothing is cleared while no usable point
    exists (a half-typed list never wipes the old values); frequency / repeat period of the peak are kept."""
    answer = fields.get(KEY_POINTS_FIELD)
    if not isinstance(answer, dict):
        return
    if answer.get("status") == "not_known_yet":
        old = fields.get("1_4") if isinstance(fields.get("1_4"), dict) else {}
        fields["1_4"] = {"value": old.get("value"), "status": "not_known_yet"}
        return
    continuous, peak = derive_continuous_peak(answer.get("value") or [])
    if continuous is None:
        return
    fields["1_4"] = {"value": {"torque_Nm": continuous["torque_Nm"], "speed_rpm": continuous["speed_rpm"],
                               "shaft_power_W": _shaft_power(continuous["torque_Nm"], continuous["speed_rpm"])},
                     "status": "filled"}
    old_peak = fields.get("1_5").get("value") if isinstance(fields.get("1_5"), dict) else None
    kept = {k: v for k, v in (old_peak or {}).items() if k in ("frequency", "period_s") and v not in (None, "")}
    if peak is None:
        fields["1_5"] = {"value": kept or None, "status": "missing"}
        return
    point = {"torque_Nm": peak["torque_Nm"], "speed_rpm": peak["speed_rpm"],
             "output_power_W": abs(_shaft_power(peak["torque_Nm"], peak["speed_rpm"]))}
    if peak["duration_s"] != CONTINUOUS:
        point["duration_s"] = peak["duration_s"]
    fields["1_5"] = {"value": {**point, **kept}, "status": "filled"}


def key_points_slot_status(draft: dict) -> str:
    fields = draft.get("fields") or {}
    answer = fields.get(KEY_POINTS_FIELD if key_points_stored(draft) else "1_4")
    return answer.get("status", "missing") if isinstance(answer, dict) else "missing"


def key_points_slot_complete(draft: dict) -> bool:
    """The required "key operating points" slot: valid stored points, or "not known yet", or (while nothing is stored)
    a complete legacy S1 point."""
    fields = draft.get("fields") or {}
    if not key_points_stored(draft):
        field = _field_map(2)["1_4"][1]
        return _answer_complete(fields.get("1_4", {}), field)
    answer = fields[KEY_POINTS_FIELD]
    if answer.get("status") == "not_known_yet":
        return True
    rows = answer.get("value")
    if answer.get("status") != "filled" or not isinstance(rows, list) or not rows:
        return False
    return not any(key_point_row_errors(row, i) for i, row in enumerate(rows))


def key_point_errors(draft: dict) -> list[dict]:
    """Submit errors of the key points slot (rows judged loudly; nothing stored = the legacy S1 rules apply)."""
    if not key_points_stored(draft):
        return []
    answer = draft["fields"][KEY_POINTS_FIELD]
    if answer.get("status") == "not_known_yet":
        return []
    rows = answer.get("value")
    if not isinstance(rows, list) or not rows:
        return [{"field": KEY_POINTS_FIELD, "section": "1", "message": "Add at least one key operating point or mark not known yet"}]
    errors = []
    for i, row in enumerate(rows):
        errors.extend(key_point_row_errors(row, i))
    return errors


def completeness(draft: dict) -> dict:
    draft = with_defaults(draft)
    schema_version = int(draft.get("schema_version", 1))
    active_schema = schema(schema_version)
    required_by_section = {}
    section_fields = {}
    for section in active_schema["sections"]:
        section_fields[section["id"]] = {field["id"]: field for field in section["fields"]}
        required_by_section[section["id"]] = [field["id"] for field in section["fields"] if _field_required(field, draft)]
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
                if field_id == "3_1" and valid and cooling_load_errors(draft):
                    valid = False
                if field_id == "key_points" and schema_version == 2:
                    valid = key_points_slot_complete(draft)
            status = (draft.get("mission_profile_status", "missing") if section_id == "mission_profile"
                      else key_points_slot_status(draft) if field_id == "key_points"
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
    draft = with_defaults(draft)
    errors = []
    for section in schema(int(draft.get("schema_version", 1)))["sections"]:
        sid = section["id"]
        for field in section["fields"]:
            fid = field["id"]
            legacy_s1 = fid == "1_4" and int(draft.get("schema_version", 1)) == 2 and not key_points_stored(draft)
            if not (_field_required(field, draft) or legacy_s1):
                continue
            if fid == "key_points":
                errors.extend(key_point_errors(draft))
                continue
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
                        if _property_required(prop, val):
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
    errors.extend(cooling_load_errors(draft))
    errors.extend(ordered_pair_errors(draft))
    if int(draft.get("schema_version", 1)) >= 2:
        from motor_ai_sim.inverter import controller_catalog
        battery = controller_catalog.battery_vs_controller(draft)  # a pack above the linked controller's supply maximum
        if battery:
            errors.append(battery)
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
