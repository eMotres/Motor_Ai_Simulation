"""Development Programme domain models, invariants, and validation.

Implements Stage 1 of Development Programme:
- Programme -> Components -> Variants -> Instances -> Connections
- Quantity reconciliation (4 M1 + 1 G1 + 1 G2 machines; 7 converters: 4 Max + 3 75 kW)
- Duplicate connection detection (prevent duplicate machine/converter port bindings)
- Multi-port machine support (e.g. G1 is one machine with two winding ports: W1, W2)
- Strict cross-project reference prevention
- Archive semantics (archived items retain position and history)
- Source-hash stale reference tracking (never silently repinned)
- Immutable spec/design/contract/publication reference types
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Domain enums and constants
COMPONENT_KINDS = ("machine", "converter", "other")
VARIANT_STATUSES = ("current", "trial", "history")
ALLOCATION_STATES = ("allocated", "unassigned", "archived", "spare")
CONNECTION_STATUSES = ("active", "proposed", "reviewed", "archived")
CONTRACT_STATUSES = ("proposed", "effective", "superseded", "proposed_fixture", "effective_fixture")
DESIGN_BASELINE_STATUSES = ("approved", "candidate", "superseded")
PUBLICATION_STATUSES = ("draft", "published", "withdrawn")

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9]+(?:[-_][a-zA-Z0-9]+)*$")


class ProgrammeError(Exception):
    """Base error for programme operations."""


class ProgrammeInvariantError(ProgrammeError):
    """Raised when a programme domain invariant is violated."""


class QuantityInvariantError(ProgrammeInvariantError):
    """Raised when component/variant quantities fail to reconcile."""


class DuplicateConnectionError(ProgrammeInvariantError):
    """Raised when duplicate or conflicting connections are detected."""


class CrossProjectReferenceError(ProgrammeInvariantError):
    """Raised when a reference escapes project boundary."""


class InvalidIdentifierError(ProgrammeError):
    """Raised when an identifier fails validation."""


class StaleReferenceError(ProgrammeError):
    """Raised when a referenced source hash does not match."""


class ConcurrencyConflictError(ProgrammeError):
    """Raised on optimistic concurrency revision mismatch."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_identifier(name: str, label: str = "id") -> str:
    """Validate that an identifier is safe, non-empty, and free of path traversal."""
    if not isinstance(name, str) or not name.strip():
        raise InvalidIdentifierError(f"{label} must be non-empty text")
    name = name.strip()
    if "/" in name or "\\" in name or ".." in name or "\0" in name:
        raise InvalidIdentifierError(f"{label} contains invalid path characters: {name}")
    if not _IDENTIFIER_RE.fullmatch(name):
        raise InvalidIdentifierError(f"{label} has invalid format: {name!r}")
    if len(name) > 128:
        raise InvalidIdentifierError(f"{label} exceeds 128 characters: {name}")
    return name


def normalize_variant(data: Any, parent_component_id: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError("variant must be an object")
    var_id = validate_identifier(str(data.get("id") or ""), f"component[{parent_component_id}].variant.id")
    status = str(data.get("status") or "current")
    if status not in VARIANT_STATUSES:
        raise ValueError(f"variant status must be one of {VARIANT_STATUSES}, got {status}")
    label = str(data.get("label") or var_id).strip()
    die = str(data.get("die") or "").strip() or None
    config = str(data.get("config") or "").strip() or None
    source_hash = data.get("source_hash")
    if source_hash is not None:
        source_hash = str(source_hash).strip()
        if not re.fullmatch(r"^[0-9a-fA-F]{64}$", source_hash):
            raise ValueError(f"variant {var_id} source_hash must be a 64-char hex string")
        source_hash = source_hash.lower()

    planned_qty = data.get("planned_quantity")
    if planned_qty is not None:
        if isinstance(planned_qty, bool) or not isinstance(planned_qty, int) or planned_qty < 0:
            raise ValueError(f"variant {var_id} planned_quantity must be a non-negative integer")

    return {
        "id": var_id,
        "component_id": parent_component_id,
        "label": label,
        "status": status,
        "die": die,
        "config": config,
        "source_hash": source_hash,
        "stale": bool(data.get("stale", False)),
        "planned_quantity": planned_qty,
        "overrides": copy.deepcopy(data.get("overrides", {})) if isinstance(data.get("overrides"), dict) else {},
        "archived": bool(data.get("archived", False)),
    }


def normalize_component(data: Any, project_id: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError("component must be an object")
    comp_id = validate_identifier(str(data.get("id") or ""), "component.id")
    kind = str(data.get("kind") or "machine")
    if kind not in COMPONENT_KINDS:
        raise ValueError(f"component kind must be one of {COMPONENT_KINDS}, got {kind}")

    planned_qty = data.get("planned_quantity", data.get("quantity", 1))
    if isinstance(planned_qty, bool) or not isinstance(planned_qty, int) or planned_qty < 0:
        raise ValueError(f"component {comp_id} planned_quantity must be a non-negative integer")

    # Placement groups
    pg = data.get("placement_groups") or []
    if not isinstance(pg, list):
        raise ValueError(f"component {comp_id} placement_groups must be a list")
    placement_groups = [str(g).strip() for g in pg if str(g).strip()]

    # Ports (e.g. machine winding ports like W1, W2)
    declared_ports = data.get("ports")
    if declared_ports is None:
        ports = ["W1"] if kind == "machine" else (["AC"] if kind == "converter" else ["default"])
    else:
        if not isinstance(declared_ports, list) or not declared_ports:
            raise ValueError(f"component {comp_id} ports must be a non-empty list")
        ports = []
        for port in declared_ports:
            p_id = validate_identifier(str(port or ""), f"component[{comp_id}].port")
            if p_id in ports:
                raise ValueError(f"duplicate port {p_id} in component {comp_id}")
            ports.append(p_id)

    # Variants
    variants_raw = data.get("variants") or []
    if not isinstance(variants_raw, list):
        raise ValueError(f"component {comp_id} variants must be a list")
    variants = []
    seen_var_ids = set()
    for item in variants_raw:
        var = normalize_variant(item, comp_id)
        if var["id"] in seen_var_ids:
            raise ValueError(f"duplicate variant id {var['id']} in component {comp_id}")
        seen_var_ids.add(var["id"])
        variants.append(var)

    return {
        "id": comp_id,
        "name": str(data.get("name") or data.get("label") or comp_id).strip(),
        "kind": kind,
        "planned_quantity": planned_qty,
        "placement_groups": placement_groups,
        "ports": ports,
        "variants": variants,
        "archived": bool(data.get("archived", False)),
        "notes": str(data.get("notes") or "").strip(),
    }


def normalize_instance(data: Any, project_id: str, valid_components: dict[str, dict]) -> dict:
    if not isinstance(data, dict):
        raise ValueError("instance must be an object")
    inst_id = validate_identifier(str(data.get("id") or ""), "instance.id")
    comp_id = str(data.get("component") or data.get("component_id") or "").strip()
    if not comp_id or comp_id not in valid_components:
        raise CrossProjectReferenceError(f"instance {inst_id} references unknown or foreign component: {comp_id}")

    # Check for foreign project tag if present
    ref_project = data.get("project_id")
    if ref_project is not None and str(ref_project) != project_id:
        raise CrossProjectReferenceError(f"instance {inst_id} has foreign project_id: {ref_project}")

    component = valid_components[comp_id]
    variant_id = data.get("variant") or data.get("variant_id")
    if variant_id:
        variant_id = str(variant_id).strip()
        comp_var_ids = {v["id"] for v in component.get("variants", [])}
        if comp_var_ids and variant_id not in comp_var_ids:
            raise ValueError(f"instance {inst_id} references unknown variant {variant_id} for component {comp_id}")
    else:
        variant_id = None

    state = str(data.get("allocation_state") or data.get("state") or "allocated")
    if state not in ALLOCATION_STATES:
        raise ValueError(f"instance allocation_state must be one of {ALLOCATION_STATES}, got {state}")

    serial = data.get("serial")
    if serial is not None:
        serial = str(serial).strip() or None

    return {
        "id": inst_id,
        "component_id": comp_id,
        "position": str(data.get("position") or inst_id).strip(),
        "variant_id": variant_id,
        "allocation_state": state,
        "serial": serial,
        "production_ref": str(data.get("production_ref") or "").strip() or None,
        "assigned_design_baseline": str(data.get("assigned_design_baseline") or "").strip() or None,
        "archived": bool(data.get("archived", False) or state == "archived"),
    }


def normalize_connection(data: Any, project_id: str, valid_instances: dict[str, dict], valid_components: dict[str, dict]) -> dict:
    if not isinstance(data, dict):
        raise ValueError("connection must be an object")

    # Cross-project reference checks
    ref_project = data.get("project_id")
    if ref_project is not None and str(ref_project) != project_id:
        raise CrossProjectReferenceError(f"connection has foreign project_id: {ref_project}")

    converter_id = str(data.get("converter") or data.get("converter_instance_id") or "").strip()
    if not converter_id or converter_id not in valid_instances:
        raise CrossProjectReferenceError(f"connection references unknown converter instance: {converter_id}")

    conv_inst = valid_instances[converter_id]
    conv_comp = valid_components.get(conv_inst["component_id"], {})
    if conv_comp.get("kind") != "converter":
        raise ValueError(f"converter instance {converter_id} belongs to kind {conv_comp.get('kind')}, expected converter")

    conv_port = str(data.get("converter_port") or "AC").strip()

    machine_id = str(data.get("machine") or data.get("machine_instance_id") or "").strip()
    if not machine_id or machine_id not in valid_instances:
        raise CrossProjectReferenceError(f"connection references unknown machine instance: {machine_id}")

    mach_inst = valid_instances[machine_id]
    mach_comp = valid_components.get(mach_inst["component_id"], {})
    if mach_comp.get("kind") != "machine":
        raise ValueError(f"machine instance {machine_id} belongs to kind {mach_comp.get('kind')}, expected machine")

    mach_port = str(data.get("port") or data.get("machine_port") or "W1").strip()
    allowed_ports = mach_comp.get("ports", ["W1"])
    if mach_port not in allowed_ports:
        raise ValueError(f"port {mach_port} not in declared machine ports {allowed_ports} for component {mach_comp.get('id')}")

    default_id = f"{converter_id}:{conv_port}->{machine_id}:{mach_port}"
    conn_id = str(data.get("id") or default_id).strip()

    status = str(data.get("status") or "active")
    if status not in CONNECTION_STATUSES:
        raise ValueError(f"connection status must be one of {CONNECTION_STATUSES}, got {status}")

    return {
        "id": conn_id,
        "converter_instance_id": converter_id,
        "converter_port": conv_port,
        "machine_instance_id": machine_id,
        "machine_port": mach_port,
        "status": status,
        "archived": bool(data.get("archived", False) or status == "archived"),
        "notes": str(data.get("notes") or "").strip(),
    }


def normalize_contract_baseline(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValueError("contract baseline must be an object")
    rev = str(data.get("revision") or "").strip()
    if not rev:
        raise ValueError("contract baseline revision required")
    status = str(data.get("status") or "proposed")
    mapping = data.get("mapping") or {}
    if not isinstance(mapping, dict):
        raise ValueError("contract baseline mapping must be an object")
    return {
        "revision": rev,
        "status": status,
        "mapping": {str(k): str(v) for k, v in mapping.items()},
        "approvals": copy.deepcopy(data.get("approvals", [])),
        "created_at": str(data.get("created_at") or now()),
    }


def normalize_programme(data: Any, project_id: str) -> dict:
    """Normalize and validate all entities in a programme document."""
    if not isinstance(data, dict):
        raise ValueError("programme must be an object")

    p_id = validate_identifier(str(data.get("project_id") or data.get("id") or project_id), "programme.project_id")
    if p_id != project_id:
        raise CrossProjectReferenceError(f"programme project_id mismatch: expected {project_id}, got {p_id}")

    title = str(data.get("programme") or data.get("title") or "Programme").strip()
    revision = int(data.get("revision", 1))
    if revision < 0:
        raise ValueError("revision must be non-negative")

    # Components
    components_raw = data.get("components") or []
    if not isinstance(components_raw, list):
        raise ValueError("components must be a list")
    components = []
    comp_map = {}
    for item in components_raw:
        c = normalize_component(item, project_id)
        if c["id"] in comp_map:
            raise ValueError(f"duplicate component id: {c['id']}")
        components.append(c)
        comp_map[c["id"]] = c

    # Instances (from 'units' or 'instances')
    instances_raw = data.get("units") or data.get("instances") or []
    if not isinstance(instances_raw, list):
        raise ValueError("instances must be a list")
    instances = []
    inst_map = {}
    for item in instances_raw:
        inst = normalize_instance(item, project_id, comp_map)
        if inst["id"] in inst_map:
            raise ValueError(f"duplicate instance id: {inst['id']}")
        instances.append(inst)
        inst_map[inst["id"]] = inst

    # Connections
    conns_raw = data.get("connections") or []
    if not isinstance(conns_raw, list):
        raise ValueError("connections must be a list")
    connections = []
    conn_map = {}
    for item in conns_raw:
        conn = normalize_connection(item, project_id, inst_map, comp_map)
        if conn["id"] in conn_map:
            raise DuplicateConnectionError(f"duplicate connection id: {conn['id']}")
        connections.append(conn)
        conn_map[conn["id"]] = conn

    # Contract baselines
    cb_raw = data.get("contract_baselines") or []
    contract_baselines = [normalize_contract_baseline(b) for b in cb_raw] if isinstance(cb_raw, list) else []

    # Extra immutable reference fields
    design_baselines = copy.deepcopy(data.get("design_baselines", []))
    obligations = copy.deepcopy(data.get("obligations", []))
    publications = copy.deepcopy(data.get("publication_refs", []))
    admins = [str(a).strip().lower() for a in data.get("admins", []) if str(a).strip()]

    return {
        "synthetic": bool(data.get("synthetic", False)),
        "project_id": project_id,
        "title": title,
        "kind": "programme",
        "schema_version": int(data.get("schema_version", 2)),
        "programme_schema_version": int(data.get("programme_schema_version", 1)),
        "revision": revision,
        "admins": admins,
        "components": components,
        "instances": instances,
        "connections": connections,
        "contract_baselines": contract_baselines,
        "design_baselines": design_baselines,
        "obligations": obligations,
        "publication_refs": publications,
        "archived": bool(data.get("archived", False)),
        "created_at": str(data.get("created_at") or now()),
        "updated_at": str(data.get("updated_at") or now()),
    }


def validate_programme_invariants(
    programme: dict,
    *,
    catalog_lookup: Callable[[str, str], str | None] | None = None,
    strict_quantities: bool = True,
) -> dict:
    """Validate all domain invariants for a programme:
    1. Quantity reconciliation: allocated instances match component planned_quantity.
    2. Variant reconciliation: allocated variant instances match converter totals.
    3. Duplicate connections: no machine port or converter port is bound to multiple active connections.
    4. Multi-port machines: one machine instance can have multiple distinct winding ports (e.g. G1.W1 and G1.W2).
    5. Archived semantics: archived items do not count towards active allocations or active connections.
    6. Stale reference detection: catalog source hash mismatch marks reference stale; NEVER silently repins.
    7. Cross-project references: strictly rejected.
    """
    comp_map = {c["id"]: c for c in programme.get("components", [])}
    inst_map = {i["id"]: i for i in programme.get("instances", [])}
    conns = programme.get("connections", [])

    # 1. Quantity reconciliation per component
    component_counts: dict[str, dict] = {}
    for comp_id, comp in comp_map.items():
        if comp.get("archived"):
            continue
        planned = comp.get("planned_quantity", 0)
        # Active allocated instances
        allocated = [
            i for i in programme.get("instances", [])
            if i["component_id"] == comp_id and i["allocation_state"] == "allocated" and not i.get("archived")
        ]
        spares = [
            i for i in programme.get("instances", [])
            if i["component_id"] == comp_id and i["allocation_state"] == "spare" and not i.get("archived")
        ]
        archived = [
            i for i in programme.get("instances", [])
            if i["component_id"] == comp_id and (i["allocation_state"] == "archived" or i.get("archived"))
        ]
        unassigned = [
            i for i in programme.get("instances", [])
            if i["component_id"] == comp_id and i["allocation_state"] == "unassigned" and not i.get("archived")
        ]

        actual_allocated = len(allocated)
        diff = actual_allocated - planned
        component_counts[comp_id] = {
            "planned": planned,
            "allocated": actual_allocated,
            "spares": len(spares),
            "archived": len(archived),
            "unassigned": len(unassigned),
            "gap": max(0, -diff),
            "excess": max(0, diff),
            "reconciled": diff == 0,
        }

        if strict_quantities and diff != 0:
            if diff < 0:
                raise QuantityInvariantError(
                    f"Component '{comp_id}' allocation gap: planned {planned}, allocated {actual_allocated} (missing {abs(diff)})"
                )
            else:
                raise QuantityInvariantError(
                    f"Component '{comp_id}' allocation excess: planned {planned}, allocated {actual_allocated} (excess {diff})"
                )

    # 2. Variant reconciliation for converter components
    variant_counts: dict[str, dict] = {}
    for comp_id, comp in comp_map.items():
        if comp.get("kind") != "converter" or comp.get("archived"):
            continue
        variants = comp.get("variants", [])
        if not variants:
            continue
        total_var_planned = 0
        comp_instances = [
            i for i in programme.get("instances", [])
            if i["component_id"] == comp_id and i["allocation_state"] == "allocated" and not i.get("archived")
        ]
        for var in variants:
            v_id = var["id"]
            v_planned = var.get("planned_quantity")
            v_allocated = [i for i in comp_instances if i.get("variant_id") == v_id]
            actual = len(v_allocated)
            variant_counts[f"{comp_id}/{v_id}"] = {
                "planned": v_planned,
                "allocated": actual,
                "reconciled": v_planned is None or actual == v_planned,
            }
            if v_planned is not None:
                total_var_planned += v_planned
                if strict_quantities and actual != v_planned:
                    raise QuantityInvariantError(
                        f"Converter variant '{comp_id}/{v_id}' mismatch: planned {v_planned}, allocated {actual}"
                    )
        if strict_quantities and total_var_planned > 0 and total_var_planned != comp.get("planned_quantity", 0):
            raise QuantityInvariantError(
                f"Converter variants total planned {total_var_planned} does not match component planned {comp.get('planned_quantity')}"
            )

    # 3. Connection integrity & duplicate prevention
    seen_machine_endpoints: dict[tuple[str, str], str] = {}
    seen_converter_endpoints: dict[tuple[str, str], str] = {}
    stale_references: list[dict] = []

    for conn in conns:
        if conn.get("archived") or conn.get("status") == "archived":
            continue

        c_inst_id = conn["converter_instance_id"]
        c_port = conn["converter_port"]
        m_inst_id = conn["machine_instance_id"]
        m_port = conn["machine_port"]

        # Ensure instances are active
        c_inst = inst_map.get(c_inst_id)
        if not c_inst or c_inst.get("archived") or c_inst.get("allocation_state") == "archived":
            raise ProgrammeInvariantError(f"Connection {conn['id']} references archived or missing converter {c_inst_id}")

        m_inst = inst_map.get(m_inst_id)
        if not m_inst or m_inst.get("archived") or m_inst.get("allocation_state") == "archived":
            raise ProgrammeInvariantError(f"Connection {conn['id']} references archived or missing machine {m_inst_id}")

        # Machine endpoint uniqueness: (m_inst_id, m_port)
        m_key = (m_inst_id, m_port)
        if m_key in seen_machine_endpoints:
            prev_conn = seen_machine_endpoints[m_key]
            raise DuplicateConnectionError(
                f"Machine port {m_inst_id}.{m_port} already connected by {prev_conn}; cannot also connect via {conn['id']}"
            )
        seen_machine_endpoints[m_key] = conn["id"]

        # Converter endpoint uniqueness: (c_inst_id, c_port)
        c_key = (c_inst_id, c_port)
        if c_key in seen_converter_endpoints:
            prev_conn = seen_converter_endpoints[c_key]
            raise DuplicateConnectionError(
                f"Converter port {c_inst_id}.{c_port} already connected by {prev_conn}; cannot also connect via {conn['id']}"
            )
        seen_converter_endpoints[c_key] = conn["id"]

    # 4. Check for stale references (source hash mismatch)
    if catalog_lookup:
        for comp in comp_map.values():
            for var in comp.get("variants", []):
                die = var.get("die")
                config = var.get("config")
                recorded_hash = var.get("source_hash")
                if die and config and recorded_hash:
                    actual_hash = catalog_lookup(die, config)
                    if actual_hash and actual_hash.lower() != recorded_hash.lower():
                        # Mark stale! Invariant: NEVER silently update recorded_hash
                        var["stale"] = True
                        stale_references.append({
                            "type": "variant",
                            "component_id": comp["id"],
                            "variant_id": var["id"],
                            "die": die,
                            "config": config,
                            "recorded_hash": recorded_hash,
                            "actual_hash": actual_hash,
                        })

    return {
        "reconciled": True,
        "components": component_counts,
        "variants": variant_counts,
        "active_connections": len(seen_machine_endpoints),
        "stale_references": stale_references,
    }


def load_synthetic_fixture() -> dict:
    """Load the approved Stage 1 synthetic fixture (4 M1 + 1 G1 + 1 G2; 7 converters; one G1 instance / two ports)."""
    fixture_path = Path(__file__).resolve().parents[3] / "_scratch" / "prog" / "programme_fixture.json"
    if fixture_path.is_file():
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    else:
        # Canonical fallback if fixture file path is not resolved
        raw = {
            "synthetic": True,
            "programme": "Example hydrofoil boat",
            "project_id": "example-hydrofoil",
            "components": [
                {"id": "M1", "quantity": 4, "kind": "machine", "ports": ["W1"]},
                {"id": "G1", "quantity": 1, "kind": "machine", "ports": ["W1", "W2"]},
                {"id": "G2", "quantity": 1, "kind": "machine", "ports": ["W1"]},
                {"id": "CTRL", "quantity": 7, "kind": "converter", "ports": ["AC"],
                 "variants": [
                     {"id": "Max", "planned_quantity": 4, "status": "current"},
                     {"id": "75kW", "planned_quantity": 3, "status": "current"}
                 ]},
            ],
            "units": [
                {"id": "M1-P-A1", "component": "M1"},
                {"id": "C01", "component": "CTRL", "variant": "Max"},
                {"id": "M1-P-A2", "component": "M1"},
                {"id": "C02", "component": "CTRL", "variant": "Max"},
                {"id": "M1-P-B1", "component": "M1"},
                {"id": "C03", "component": "CTRL", "variant": "Max"},
                {"id": "M1-P-B2", "component": "M1"},
                {"id": "C04", "component": "CTRL", "variant": "Max"},
                {"id": "G1-01", "component": "G1"},
                {"id": "G2-01", "component": "G2"},
                {"id": "C05", "component": "CTRL", "variant": "75kW"},
                {"id": "C06", "component": "CTRL", "variant": "75kW"},
                {"id": "C07", "component": "CTRL", "variant": "75kW"},
            ],
            "connections": [
                {"converter": "C01", "machine": "M1-P-A1", "port": "W1"},
                {"converter": "C02", "machine": "M1-P-A2", "port": "W1"},
                {"converter": "C03", "machine": "M1-P-B1", "port": "W1"},
                {"converter": "C04", "machine": "M1-P-B2", "port": "W1"},
                {"converter": "C05", "machine": "G1-01", "port": "W1"},
                {"converter": "C06", "machine": "G1-01", "port": "W2"},
                {"converter": "C07", "machine": "G2-01", "port": "W1"},
            ],
            "contract_baselines": [
                {"revision": "2.0", "status": "effective_fixture", "mapping": {
                    "M1": "M1-S4", "G1": "G1-S2", "G2": "G2-S5", "CTRL": "Platform-S3",
                    "CTRL/Max": "Max-S2", "CTRL/75kW": "75-S2"
                }},
                {"revision": "3.0", "status": "proposed_fixture", "mapping": {
                    "M1": "M1-S5", "G1": "G1-S3", "G2": "G2-S5", "CTRL": "Platform-S4",
                    "CTRL/Max": "Max-S3", "CTRL/75kW": "75-S2"
                }}
            ],
        }

    # Ensure component kind and ports on raw fixture if missing
    for c in raw.get("components", []):
        if c.get("id") in ("M1", "G1", "G2"):
            c.setdefault("kind", "machine")
            if c.get("id") == "G1":
                c.setdefault("ports", ["W1", "W2"])
            else:
                c.setdefault("ports", ["W1"])
        elif c.get("id") == "CTRL":
            c.setdefault("kind", "converter")
            c.setdefault("ports", ["AC"])
            c.setdefault("variants", [
                {"id": "Max", "planned_quantity": 4, "status": "current"},
                {"id": "75kW", "planned_quantity": 3, "status": "current"},
            ])

    return normalize_programme(raw, str(raw.get("project_id") or "example-hydrofoil"))
