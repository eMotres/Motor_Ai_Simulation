"""Development programme records: components, variants, positions, instances, connections.

A programme is one product (a boat, a vehicle) built from several machines and
converters. Its document is pure data; this module only normalizes it and checks
its invariants. Storage and revisions live in ``programme_store``.

    programme
      components[]   machine | converter | other, with named ports
        variants[]   design alternatives, current | trial | history, optional catalog link
      positions[]    installed slots; each active position holds exactly one unit
      instances[]    physical lots of one component/variant: quantity + allocations to positions
      connections[]  two (position, port) ends, e.g. converter AC port <-> machine winding

A generator with two windings is ONE machine component with two winding ports;
each port is fed by its own converter position through its own connection.

Ids are local lowercase slugs, unique per collection inside one programme. A
reference may also be written source-scoped (``<programme>/<collection>/<id>``);
a scoped reference naming another programme is rejected, never resolved.
Catalog links pin the content hash of the configuration they were made against;
a hash that no longer matches is reported stale and is never re-pinned here.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable

PROGRAMME_SCHEMA_VERSION = 1
COMPONENT_KINDS = ("machine", "converter", "other")
VARIANT_STATUSES = ("current", "trial", "history")
COLLECTIONS = ("components", "positions", "instances", "connections")
DEFAULT_PORT_KIND = {"machine": "winding", "converter": "ac", "other": "port"}
REFERENCE_SOURCES = ("catalog",)
# Route segments under /programmes that must never be read as a programme id.
RESERVED_IDS = frozenset({"capabilities", "grant-removal-candidates"})
MAX_ID_LENGTH = 64

_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HASH = re.compile(r"^[0-9a-f]{64}$")

_TOP_FIELDS = {"id", "title", "programme_schema_version", "notes", *COLLECTIONS}
_COMPONENT_FIELDS = {"id", "programme_id", "kind", "name", "ports", "variants", "archived", "notes"}
_PORT_FIELDS = {"id", "kind", "multi", "label"}
_VARIANT_FIELDS = {"id", "programme_id", "status", "label", "ref", "archived", "notes"}
_REF_FIELDS = {"source", "die", "config", "content_hash"}
_POSITION_FIELDS = {"id", "programme_id", "name", "component", "archived", "notes"}
_INSTANCE_FIELDS = {"id", "programme_id", "component", "variant", "quantity", "allocations", "archived", "notes"}
_CONNECTION_FIELDS = {"id", "programme_id", "ends", "archived", "notes"}
_END_FIELDS = {"position", "port"}


class ProgrammeError(Exception):
    """Base error; ``errors`` is a list of ``{code, path, message}`` issues."""

    def __init__(self, message: str, errors: list[dict] | None = None):
        super().__init__(message)
        self.errors = list(errors or [])


class ProgrammeInvalid(ProgrammeError):
    """The document, or the change from the previous revision, breaks an invariant."""


def issue(code: str, path: str, message: str) -> dict:
    return {"code": code, "path": path, "message": message}


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and len(value) <= MAX_ID_LENGTH and bool(_ID.fullmatch(value))


def valid_programme_id(value: Any) -> bool:
    return valid_id(value) and value not in RESERVED_IDS


def scoped_id(programme_id: str, collection: str, local_id: str) -> str:
    return f"{programme_id}/{collection}/{local_id}"


def _local_ref(value: Any, programme_id: str, collection: str, path: str, errors: list[dict]) -> str | None:
    """Resolve a local or source-scoped reference to a local id, recording why it cannot be."""
    if not isinstance(value, str) or not value:
        errors.append(issue("invalid_reference", path, f"expected a {collection} id"))
        return None
    if "/" in value:
        parts = value.split("/")
        if len(parts) != 3:
            errors.append(issue("invalid_reference", path, f"scoped reference must be <programme>/{collection}/<id>"))
            return None
        owner, scope, local = parts
        if owner != programme_id:
            errors.append(issue("cross_programme_reference", path,
                                f"reference {value!r} belongs to another programme"))
            return None
        if scope != collection:
            errors.append(issue("invalid_reference", path, f"reference {value!r} is not a {collection} id"))
            return None
        value = local
    if not valid_id(value):
        errors.append(issue("invalid_id", path, f"{value!r} is not a lowercase slug of at most {MAX_ID_LENGTH} characters"))
        return None
    return value


def _check_fields(item: dict, allowed: set, path: str, errors: list[dict]) -> None:
    for key in sorted(set(item) - allowed):
        errors.append(issue("unknown_field", f"{path}.{key}", f"unknown field {key!r}"))


def _check_owner(item: dict, programme_id: str, path: str, errors: list[dict]) -> None:
    owner = item.get("programme_id")
    if owner is not None and owner != programme_id:
        errors.append(issue("cross_programme_reference", f"{path}.programme_id",
                            f"record belongs to programme {owner!r}, not {programme_id!r}"))


def _text(value: Any, path: str, errors: list[dict], *, limit: int = 200, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        errors.append(issue("invalid_value", path, "expected text"))
        return ""
    value = value.strip()
    if required and not value:
        errors.append(issue("required", path, "required"))
    if len(value) > limit:
        errors.append(issue("invalid_value", path, f"longer than {limit} characters"))
    return value


def _flag(value: Any, path: str, errors: list[dict]) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        errors.append(issue("invalid_value", path, "expected true or false"))
        return False
    return value


def _list(value: Any, path: str, errors: list[dict]) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(issue("invalid_value", path, "expected a list"))
        return []
    return value


def _record_id(item: Any, path: str, seen: set, errors: list[dict]) -> str | None:
    if not isinstance(item, dict):
        errors.append(issue("invalid_value", path, "expected an object"))
        return None
    value = item.get("id")
    if not valid_id(value):
        errors.append(issue("invalid_id", f"{path}.id", f"{value!r} is not a lowercase slug of at most {MAX_ID_LENGTH} characters"))
        return None
    if value in seen:
        errors.append(issue("duplicate_id", f"{path}.id", f"duplicate id {value!r}"))
        return None
    seen.add(value)
    return value


def _catalog_segment(value: Any, path: str, errors: list[dict]) -> str:
    text = _text(value, path, errors, limit=128, required=True)
    if text and (text in (".", "..") or any(ch in text for ch in "/\\\0") or not text.isprintable()):
        errors.append(issue("invalid_value", path, "catalog names cannot contain path separators or control characters"))
    return text


def _normalize_ref(value: Any, programme_id: str, path: str, errors: list[dict]) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append(issue("invalid_value", path, "expected an object or null"))
        return None
    source = value.get("source")
    if source not in REFERENCE_SOURCES:
        code = "cross_programme_reference" if source in ("project", "programme") else "unsupported_reference_source"
        errors.append(issue(code, f"{path}.source",
                            "only catalog configurations can be linked; project data stays in its own project"))
        return None
    _check_fields(value, _REF_FIELDS, path, errors)
    content_hash = value.get("content_hash")
    if content_hash is not None and not (isinstance(content_hash, str) and _HASH.fullmatch(content_hash)):
        errors.append(issue("invalid_value", f"{path}.content_hash", "expected a lowercase sha256 hex digest"))
        content_hash = None
    return {"source": source, "die": _catalog_segment(value.get("die"), f"{path}.die", errors),
            "config": _catalog_segment(value.get("config"), f"{path}.config", errors),
            "content_hash": content_hash}


def _normalize_component(item: dict, programme_id: str, path: str, errors: list[dict]) -> dict:
    _check_fields(item, _COMPONENT_FIELDS, path, errors)
    _check_owner(item, programme_id, path, errors)
    kind = item.get("kind")
    if kind not in COMPONENT_KINDS:
        errors.append(issue("invalid_value", f"{path}.kind", f"kind must be one of {', '.join(COMPONENT_KINDS)}"))
        kind = "other"
    ports, port_ids = [], set()
    raw_ports = _list(item.get("ports"), f"{path}.ports", errors)
    if not raw_ports:
        errors.append(issue("required", f"{path}.ports", "a component needs at least one port"))
    for p_index, port in enumerate(raw_ports):
        p_path = f"{path}.ports[{p_index}]"
        port_id = _record_id(port, p_path, port_ids, errors)
        if port_id is None:
            continue
        _check_fields(port, _PORT_FIELDS, p_path, errors)
        port_kind = port.get("kind", DEFAULT_PORT_KIND[kind])
        if not valid_id(port_kind):
            errors.append(issue("invalid_value", f"{p_path}.kind", "port kind must be a lowercase slug"))
            port_kind = DEFAULT_PORT_KIND[kind]
        ports.append({"id": port_id, "kind": port_kind, "multi": _flag(port.get("multi"), f"{p_path}.multi", errors),
                      "label": _text(port.get("label"), f"{p_path}.label", errors)})
    variants, variant_ids = [], set()
    for v_index, variant in enumerate(_list(item.get("variants"), f"{path}.variants", errors)):
        v_path = f"{path}.variants[{v_index}]"
        variant_id = _record_id(variant, v_path, variant_ids, errors)
        if variant_id is None:
            continue
        _check_fields(variant, _VARIANT_FIELDS, v_path, errors)
        _check_owner(variant, programme_id, v_path, errors)
        status = variant.get("status", "trial")
        if status not in VARIANT_STATUSES:
            errors.append(issue("invalid_value", f"{v_path}.status", f"status must be one of {', '.join(VARIANT_STATUSES)}"))
            status = "trial"
        variants.append({"id": variant_id, "status": status, "label": _text(variant.get("label"), f"{v_path}.label", errors),
                         "ref": _normalize_ref(variant.get("ref"), programme_id, f"{v_path}.ref", errors),
                         "archived": _flag(variant.get("archived"), f"{v_path}.archived", errors),
                         "notes": _text(variant.get("notes"), f"{v_path}.notes", errors, limit=2000)})
    current = [v["id"] for v in variants if v["status"] == "current" and not v["archived"]]
    if len(current) > 1:
        errors.append(issue("multiple_current_variants", f"{path}.variants",
                            f"only one active variant can be current, found {', '.join(current)}"))
    return {"id": item["id"], "kind": kind, "name": _text(item.get("name"), f"{path}.name", errors),
            "ports": ports, "variants": variants, "archived": _flag(item.get("archived"), f"{path}.archived", errors),
            "notes": _text(item.get("notes"), f"{path}.notes", errors, limit=2000)}


def normalize(data: Any, programme_id: str) -> tuple[dict, list[dict]]:
    """Return ``(document, issues)``; the document is usable only when ``issues`` is empty."""
    errors: list[dict] = []
    if not valid_programme_id(programme_id):
        raise ProgrammeInvalid("invalid programme id", [issue("invalid_id", "id", f"{programme_id!r} is not a valid programme id")])
    if not isinstance(data, dict):
        raise ProgrammeInvalid("programme must be an object", [issue("invalid_value", "", "expected an object")])
    _check_fields(data, _TOP_FIELDS, "programme", errors)
    if data.get("id", programme_id) != programme_id:
        errors.append(issue("cross_programme_reference", "id", f"document is for programme {data.get('id')!r}"))
    if data.get("programme_schema_version", PROGRAMME_SCHEMA_VERSION) != PROGRAMME_SCHEMA_VERSION:
        errors.append(issue("unsupported_schema", "programme_schema_version",
                            f"only programme schema {PROGRAMME_SCHEMA_VERSION} is supported"))

    components, ids = [], set()
    for index, item in enumerate(_list(data.get("components"), "components", errors)):
        path = f"components[{index}]"
        if _record_id(item, path, ids, errors) is not None:
            components.append(_normalize_component(item, programme_id, path, errors))
    by_component = {c["id"]: c for c in components}

    positions, ids = [], set()
    for index, item in enumerate(_list(data.get("positions"), "positions", errors)):
        path = f"positions[{index}]"
        if _record_id(item, path, ids, errors) is None:
            continue
        _check_fields(item, _POSITION_FIELDS, path, errors)
        _check_owner(item, programme_id, path, errors)
        component = _local_ref(item.get("component"), programme_id, "components", f"{path}.component", errors)
        if component is not None and component not in by_component:
            errors.append(issue("unknown_reference", f"{path}.component", f"no component {component!r}"))
        positions.append({"id": item["id"], "name": _text(item.get("name"), f"{path}.name", errors),
                          "component": component, "archived": _flag(item.get("archived"), f"{path}.archived", errors),
                          "notes": _text(item.get("notes"), f"{path}.notes", errors, limit=2000)})
    by_position = {p["id"]: p for p in positions}

    instances, ids = [], set()
    for index, item in enumerate(_list(data.get("instances"), "instances", errors)):
        path = f"instances[{index}]"
        if _record_id(item, path, ids, errors) is None:
            continue
        _check_fields(item, _INSTANCE_FIELDS, path, errors)
        _check_owner(item, programme_id, path, errors)
        component = _local_ref(item.get("component"), programme_id, "components", f"{path}.component", errors)
        if component is not None and component not in by_component:
            errors.append(issue("unknown_reference", f"{path}.component", f"no component {component!r}"))
        variant = item.get("variant")
        if variant is not None:
            if not valid_id(variant):
                errors.append(issue("invalid_id", f"{path}.variant", f"{variant!r} is not a variant id"))
                variant = None
            elif component in by_component and variant not in {v["id"] for v in by_component[component]["variants"]}:
                errors.append(issue("unknown_reference", f"{path}.variant", f"component {component!r} has no variant {variant!r}"))
        quantity = item.get("quantity", 1)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            errors.append(issue("invalid_value", f"{path}.quantity", "quantity must be a positive integer"))
            quantity = 1
        allocations = []
        for a_index, raw in enumerate(_list(item.get("allocations"), f"{path}.allocations", errors)):
            position = _local_ref(raw, programme_id, "positions", f"{path}.allocations[{a_index}]", errors)
            if position is None:
                continue
            if position in allocations:
                errors.append(issue("duplicate_allocation", f"{path}.allocations[{a_index}]",
                                    f"position {position!r} is listed twice"))
                continue
            allocations.append(position)
        instances.append({"id": item["id"], "component": component, "variant": variant, "quantity": quantity,
                          "allocations": allocations, "archived": _flag(item.get("archived"), f"{path}.archived", errors),
                          "notes": _text(item.get("notes"), f"{path}.notes", errors, limit=2000)})

    connections, ids = [], set()
    for index, item in enumerate(_list(data.get("connections"), "connections", errors)):
        path = f"connections[{index}]"
        if _record_id(item, path, ids, errors) is None:
            continue
        _check_fields(item, _CONNECTION_FIELDS, path, errors)
        _check_owner(item, programme_id, path, errors)
        raw_ends = _list(item.get("ends"), f"{path}.ends", errors)
        ends = []
        if len(raw_ends) != 2:
            errors.append(issue("invalid_value", f"{path}.ends", "a connection has exactly two ends"))
        for e_index, end in enumerate(raw_ends[:2]):
            e_path = f"{path}.ends[{e_index}]"
            if not isinstance(end, dict):
                errors.append(issue("invalid_value", e_path, "expected {position, port}"))
                continue
            _check_fields(end, _END_FIELDS, e_path, errors)
            position = _local_ref(end.get("position"), programme_id, "positions", f"{e_path}.position", errors)
            port = end.get("port")
            if not valid_id(port):
                errors.append(issue("invalid_id", f"{e_path}.port", f"{port!r} is not a port id"))
                port = None
            ends.append({"position": position, "port": port})
        connections.append({"id": item["id"], "ends": ends,
                            "archived": _flag(item.get("archived"), f"{path}.archived", errors),
                            "notes": _text(item.get("notes"), f"{path}.notes", errors, limit=2000)})

    document = {"id": programme_id, "title": _text(data.get("title"), "title", errors, required=True),
                "programme_schema_version": PROGRAMME_SCHEMA_VERSION,
                "notes": _text(data.get("notes"), "notes", errors, limit=2000),
                "components": components, "positions": positions, "instances": instances, "connections": connections}
    if not errors:
        errors.extend(_topology_issues(document, by_component, by_position))
    return document, errors


def _topology_issues(doc: dict, by_component: dict, by_position: dict) -> list[dict]:
    """Cross-record invariants, checked once every record is individually valid."""
    errors: list[dict] = []
    for index, position in enumerate(doc["positions"]):
        if not position["archived"] and by_component[position["component"]]["archived"]:
            errors.append(issue("archived_reference", f"positions[{index}].component",
                                f"active position {position['id']!r} uses archived component {position['component']!r}"))

    holders: dict[str, list[str]] = {}
    for index, instance in enumerate(doc["instances"]):
        path = f"instances[{index}]"
        if instance["archived"]:
            continue  # history: an archived lot no longer fills any position
        if by_component[instance["component"]]["archived"]:
            errors.append(issue("archived_reference", f"{path}.component",
                                f"active lot {instance['id']!r} uses archived component {instance['component']!r}"))
        if len(instance["allocations"]) > instance["quantity"]:
            errors.append(issue("allocation_excess", f"{path}.allocations",
                                f"lot {instance['id']!r} has {instance['quantity']} unit(s) but is allocated to "
                                f"{len(instance['allocations'])} positions"))
        for a_index, position_id in enumerate(instance["allocations"]):
            a_path = f"{path}.allocations[{a_index}]"
            position = by_position.get(position_id)
            if position is None:
                errors.append(issue("unknown_reference", a_path, f"no position {position_id!r}"))
                continue
            if position["archived"]:
                errors.append(issue("archived_reference", a_path, f"position {position_id!r} is archived"))
                continue
            if position["component"] != instance["component"]:
                errors.append(issue("allocation_component_mismatch", a_path,
                                    f"position {position_id!r} takes {position['component']!r}, lot {instance['id']!r} "
                                    f"is {instance['component']!r}"))
                continue
            holders.setdefault(position_id, []).append(instance["id"])
    for index, position in enumerate(doc["positions"]):
        if position["archived"]:
            continue
        held = holders.get(position["id"], [])
        if not held:
            errors.append(issue("allocation_gap", f"positions[{index}]", f"position {position['id']!r} has no unit allocated"))
        elif len(held) > 1:
            errors.append(issue("allocation_excess", f"positions[{index}]",
                                f"position {position['id']!r} is allocated to {', '.join(held)}"))

    pairs: dict[tuple, str] = {}
    port_use: dict[tuple, list[str]] = {}
    for index, connection in enumerate(doc["connections"]):
        path = f"connections[{index}]"
        keys = []
        for e_index, end in enumerate(connection["ends"]):
            e_path = f"{path}.ends[{e_index}]"
            position = by_position.get(end["position"])
            if position is None:
                errors.append(issue("unknown_reference", f"{e_path}.position", f"no position {end['position']!r}"))
                continue
            component = by_component[position["component"]]
            port = next((p for p in component["ports"] if p["id"] == end["port"]), None)
            if port is None:
                errors.append(issue("unknown_reference", f"{e_path}.port",
                                    f"component {component['id']!r} has no port {end['port']!r}"))
                continue
            if position["archived"] and not connection["archived"]:
                errors.append(issue("archived_reference", f"{e_path}.position",
                                    f"active connection {connection['id']!r} ends at archived position {position['id']!r}"))
            keys.append(((position["id"], port["id"]), port))
        if len(keys) != 2 or connection["archived"]:
            continue
        if keys[0][0][0] == keys[1][0][0]:
            errors.append(issue("self_connection", path, "a connection must join two different positions"))
            continue
        pair = tuple(sorted(k for k, _ in keys))
        if pair in pairs:
            errors.append(issue("duplicate_connection", path,
                                f"connection {connection['id']!r} duplicates {pairs[pair]!r}"))
            continue
        pairs[pair] = connection["id"]
        for key, port in keys:
            port_use.setdefault(key, []).append(connection["id"])
            if not port["multi"] and len(port_use[key]) > 1:
                errors.append(issue("duplicate_connection", path,
                                    f"port {key[0]}.{key[1]} is already connected by {port_use[key][0]!r}"))
    return errors


def transition_issues(previous: dict | None, current: dict) -> list[dict]:
    """Archive, never delete; identities and pinned references never change in place."""
    if previous is None:
        return []
    errors: list[dict] = []
    for collection in COLLECTIONS:
        new = {item["id"]: item for item in current[collection]}
        for old in previous.get(collection, []):
            item = new.get(old["id"])
            if item is None:
                errors.append(issue("record_deleted", f"{collection}.{old['id']}",
                                    f"{collection[:-1]} {old['id']!r} was removed; archive it instead"))
                continue
            for field in {"components": ("kind",), "positions": ("component",),
                          "instances": ("component",), "connections": ("ends",)}[collection]:
                if item[field] != old[field]:
                    errors.append(issue("immutable_field", f"{collection}.{old['id']}.{field}",
                                        f"{field} cannot change; archive {old['id']!r} and add a new record"))
            if collection != "components":
                continue
            ports = {p["id"] for p in item["ports"]}
            for port in old["ports"]:
                if port["id"] not in ports:
                    errors.append(issue("record_deleted", f"components.{old['id']}.ports.{port['id']}",
                                        f"port {port['id']!r} was removed"))
            variants = {v["id"]: v for v in item["variants"]}
            for variant in old["variants"]:
                now_variant = variants.get(variant["id"])
                if now_variant is None:
                    errors.append(issue("record_deleted", f"components.{old['id']}.variants.{variant['id']}",
                                        f"variant {variant['id']!r} was removed; set its status to history instead"))
                elif variant["ref"] is not None and now_variant["ref"] != variant["ref"]:
                    errors.append(issue("reference_repinned", f"components.{old['id']}.variants.{variant['id']}.ref",
                                        "a pinned catalog link cannot change; add a new variant for the new configuration"))
    return errors


def pin_new_references(previous: dict | None, current: dict,
                       lookup: Callable[[str, str], str | None] | None) -> list[dict]:
    """Pin the content hash of catalog links made in this revision. Mutates ``current``."""
    errors: list[dict] = []
    pinned_before = {(c["id"], v["id"]) for c in (previous or {}).get("components", [])
                     for v in c["variants"] if v["ref"] is not None}
    for component in current["components"]:
        for variant in component["variants"]:
            ref = variant["ref"]
            if ref is None or ref["content_hash"] is not None or (component["id"], variant["id"]) in pinned_before:
                continue
            content_hash = lookup(ref["die"], ref["config"]) if lookup else None
            if content_hash is None:
                errors.append(issue("unresolved_reference", f"components.{component['id']}.variants.{variant['id']}.ref",
                                    f"catalog configuration {ref['die']}/{ref['config']} could not be resolved to pin it"))
            else:
                ref["content_hash"] = content_hash
    return errors


def validated(data: Any, programme_id: str, previous: dict | None = None,
              lookup: Callable[[str, str], str | None] | None = None) -> dict:
    """Normalize ``data`` as the next revision after ``previous``; raise ``ProgrammeInvalid`` on any issue."""
    document, errors = normalize(copy.deepcopy(data), programme_id)
    if not errors:
        errors = transition_issues(previous, document) + pin_new_references(previous, document, lookup)
    if errors:
        raise ProgrammeInvalid(f"programme has {len(errors)} issue(s)", errors)
    return document


def reconciliation(doc: dict) -> dict:
    """Per-component counts for review; read-only, never repairs anything."""
    rows = {}
    for component in doc["components"]:
        cid = component["id"]
        active = [p["id"] for p in doc["positions"] if p["component"] == cid and not p["archived"]]
        archived = [p["id"] for p in doc["positions"] if p["component"] == cid and p["archived"]]
        lots = [i for i in doc["instances"] if i["component"] == cid and not i["archived"]]
        filled = {p for lot in lots for p in lot["allocations"] if p in active}
        units = sum(lot["quantity"] for lot in lots)
        allocated = sum(len(lot["allocations"]) for lot in lots)
        rows[cid] = {"kind": component["kind"], "archived": component["archived"],
                     "positions": len(active), "archived_positions": len(archived),
                     "filled_positions": len(filled), "gaps": sorted(set(active) - filled),
                     "units": units, "allocated_units": allocated, "spare_units": max(0, units - allocated),
                     "spare_lots": sorted(lot["id"] for lot in lots if not lot["allocations"]),
                     "archived_lots": sorted(i["id"] for i in doc["instances"] if i["component"] == cid and i["archived"])}
    return {"components": rows,
            "active_connections": sum(1 for c in doc["connections"] if not c["archived"]),
            "archived_connections": sum(1 for c in doc["connections"] if c["archived"])}


def reference_status(doc: dict, lookup: Callable[[str, str], str | None] | None) -> list[dict]:
    """Compare every pinned catalog link with the catalog as it is now; never re-pins."""
    rows = []
    for component in doc["components"]:
        for variant in component["variants"]:
            ref = variant["ref"]
            if ref is None:
                continue
            current = None
            if lookup is not None:
                try:
                    current = lookup(ref["die"], ref["config"])
                except Exception:  # noqa: BLE001 - an unreadable catalog reads as missing, not as a 500
                    current = None
            state = "missing" if current is None else ("current" if current == ref["content_hash"] else "stale")
            rows.append({"component": component["id"], "variant": variant["id"], "die": ref["die"],
                         "config": ref["config"], "pinned_hash": ref["content_hash"], "catalog_hash": current,
                         "state": state})
    return rows
