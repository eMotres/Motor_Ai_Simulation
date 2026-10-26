from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from motor_ai_sim import users
from motor_ai_sim.development import access, models, store
from motor_ai_sim.development.motors import _metric_projection

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["development-projects"])


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _author(email: str) -> dict:
    email = str(email or "").strip().lower()
    rec = users.get_user(email) if email else None
    name = str((rec or {}).get("name") or "")
    if "@" in name or email and email in name.lower():
        name = ""
    return {"author_id": hashlib.sha256(email.encode("utf-8")).hexdigest()[:20], "author_display_name": name}


def _customer_legacy_s1(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    answer = {}
    if value.get("status") in ("filled", "not_known_yet", "missing"):
        answer["status"] = value["status"]
    old_value = value.get("value")
    if isinstance(old_value, dict):
        allowed = {"torque_Nm", "speed_rpm", "input_power_W", "output_power_W"}
        safe_value = {}
        for key, item in old_value.items():
            if key not in allowed:
                continue
            if isinstance(item, dict):
                bounds = {name: bound for name, bound in item.items() if name in ("minimum", "maximum")
                          and (bound is None or isinstance(bound, (int, float)) and not isinstance(bound, bool)
                               and math.isfinite(float(bound)))}
                if bounds:
                    safe_value[key] = bounds
            elif item is None or isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item)):
                safe_value[key] = item
        answer["value"] = safe_value
    return answer or None


def _customer_migration_issues(value: Any) -> list[dict]:
    allowed = {
        "s1_range_collapsed_to_maxima": {"1_4"},
        "legacy_power_semantics_preserved": {"1_4.input_power_W", "1_4.output_power_W"},
        "s1_maximum_missing": {"1_4"},
    }
    result = []
    if not isinstance(value, list):
        return result
    for issue in value:
        if not isinstance(issue, dict) or issue.get("code") not in allowed:
            continue
        field = issue.get("field")
        if field not in allowed[issue["code"]]:
            continue
        result.append({"code": issue["code"], "field": field})
    return result


def _customer_spec(value: dict) -> dict:
    out = copy.deepcopy(value)
    if "author_email" in out:
        out.update(_author(out.pop("author_email")))
    draft = out.get("draft")
    if isinstance(draft, dict):
        provenance = draft.get("provenance") if isinstance(draft.get("provenance"), dict) else {}
        def safe_row_map(item: dict) -> list[dict]:
            mappings = item.get("mission_row_index_map", [])
            if not isinstance(mappings, list):
                return []
            return [{"legacy_index": mapping["legacy_index"], "row_id": mapping["row_id"]}
                    for mapping in mappings
                    if isinstance(mapping, dict)
                    and isinstance(mapping.get("legacy_index"), int)
                    and not isinstance(mapping.get("legacy_index"), bool)
                    and mapping["legacy_index"] >= 0
                    and isinstance(mapping.get("row_id"), str)
                    and 0 < len(mapping["row_id"]) <= 128]
        safe_migrations = [
            {"from_schema_version": 1, "to_schema_version": 2,
             "source_revision": item.get("source_revision"),
             "legacy_1_4": _customer_legacy_s1(item.get("legacy_1_4")),
             "mission_row_index_map": safe_row_map(item),
             "acknowledged": bool(item.get("acknowledged")),
             "issues": _customer_migration_issues(item.get("issues", []))}
            for item in provenance.get("schema_migrations", [])
            if isinstance(item, dict) and item.get("from_schema_version") == 1
        ]
        draft["provenance"] = {"schema_migrations": safe_migrations} if safe_migrations else {}
    return out


def _summary(project: dict, user: dict) -> dict:
    role = access.membership(_email(user), project)
    engineer = access.is_engineer(user) or role in ("owner", "engineer")
    draft = store.load_json(project["id"], "draft.json", {"draft": {"fields": {}, "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}, "revision": 0})
    projection = {k: copy.deepcopy(v) for k, v in project.items() if k not in ("notes", "pending_invites")}
    if not engineer:
        owner = projection.get("owner_engineer")
        if owner:
            projection["owner_engineer"] = _author(owner)["author_display_name"]
        projection.pop("project_motor_grants", None)
        projection.pop("grant_removal_candidates", None)
        projection.pop("motor_history", None)
    projection["can_edit"] = bool(engineer)
    projection["can_edit_spec"] = access.can_edit_spec(user, project)
    projection["completeness"] = models.completeness(draft.get("draft", {}))
    if engineer:
        projection["notes"] = project.get("notes", "")
    # Only expose the current caller's member record to customers.
    if not access.is_engineer(user):
        projection["members"] = [m for m in project.get("members", []) if m.get("email", "").lower() == _email(user)]
    return projection


def _find_project_id(project_id: str):
    if not store.valid_id(project_id):
        raise HTTPException(status_code=404, detail="Project not found")


def _safe_comparison(value: Any) -> dict:
    result = _metric_projection(value)
    if not isinstance(value, dict):
        return result
    temperatures = value.get("temperatures_c")
    if isinstance(temperatures, dict):
        import math
        temperature_names = {"coil", "coils", "stator", "rotor", "magnet", "magnets",
                             "bearing", "bearings", "winding", "windings", "ambient",
                             "housing", "stator_core", "rotor_core", "sleeve", "liner",
                             "insulation", "enamel", "slot_fill", "gap_air", "pocket_air",
                             "shaft", "coolant"}
        numeric = {str(name): number for name, number in temperatures.items()
                   if str(name).casefold() in temperature_names
                   and isinstance(number, (int, float)) and not isinstance(number, bool)
                   and math.isfinite(float(number))}
        if numeric:
            result["temperatures_c"] = numeric
    for key in ("line_peak_physical", "demag", "loss_total_partial", "mass_partial"):
        if isinstance(value.get(key), bool):
            result[key] = value[key]
    for key in ("line_peak_provenance", "line_peak_source", "line_peak_reason"):
        item = value.get(key)
        if isinstance(item, str) and len(item) <= 48 and item.replace(".", "").replace("_", "").isalnum():
            result[key] = item
    return result


def _fingerprint(value: Any) -> str | None:
    if value is None:
        return None
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, default=str).encode("utf-8")
        if len(encoded) > 16384:
            return None
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _safe_provenance(value: Any, *, default_source: str | None = None) -> dict:
    """Keep a small provenance witness; never persist geometry or solver payloads."""
    value = value if isinstance(value, dict) else {}
    source = value.get("source") or value.get("result_provenance") or default_source
    source = str(source)[:64] if isinstance(source, str) else ""
    explicit_kind = value.get("kind")
    if explicit_kind not in {"estimate", "solve", "unknown"}:
        explicit_kind = "estimate" if source == "configure_interpolation" else (
            "solve" if source in {"fem", "solver", "simulation", "solve"} else "unknown")
    out = {"kind": explicit_kind}
    if source:
        out["source"] = source
    for key in ("run_id", "revision", "solver_version"):
        item = value.get(key)
        if isinstance(item, (str, int)) and not isinstance(item, bool):
            out[key] = str(item)[:64]
    for key in ("input_fingerprint", "geometry_fingerprint", "materials_fingerprint"):
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(r"[0-9a-fA-F]{64}", item):
            out[key] = item.lower()
        elif item is not None:
            digest = _fingerprint(item)
            if digest:
                out[key] = digest
    for key, source_key in (("geometry_fingerprint", "geometry"),
                            ("materials_fingerprint", "materials")):
        if key not in out and isinstance(value.get(source_key), (dict, list)):
            digest = _fingerprint(value[source_key])
            if digest:
                out[key] = digest
    return out


def _resolved_catalog_scope(die: str, config: str) -> dict:
    """Canonicalize one catalog identity while preserving unavailable origins."""
    if not die or not config:
        return {"kind": "catalog", "die": die[:160], "config": config[:120],
                "grant_key": f"{die[:160]}/{config[:120]}"}
    try:
        from motor_ai_sim.routes import family
        from motor_ai_sim.development.motors import _canonical_entry_name
        entries = family._iter_die_entries()
        match = next((entry for entry in entries
                      if str(entry.get("layer") or "") in {"shared", "published"}
                      and (_canonical_entry_name(entry).casefold() == die.casefold()
                           or str(entry.get("name") or "").casefold() == die.casefold())), None)
        if match is not None:
            canonical = _canonical_entry_name(match)
            alias = str(match.get("name") or "")
            directory = Path(str(match.get("dir") or "")).resolve()
            if Path(str(family._die_dir(alias))).resolve() != directory:
                match = None
            path = directory / f"{config}.yaml"
        if match is not None:
            if path.is_file():
                return {"kind": "catalog", "die": canonical, "config": config,
                        "grant_key": f"{canonical}/{config}"}
            # Keep the catalog origin through a deleted/unavailable source.
            return {"kind": "catalog", "die": canonical, "config": config,
                    "grant_key": f"{canonical}/{config}"}
    except Exception:
        pass
    return {"kind": "catalog", "die": die[:160], "config": config[:120],
            "grant_key": f"{die[:160]}/{config[:120]}"}


def _catalog_origin(machine: dict, supplied: Any = None, *,
                    allow_unresolved_claim: bool = False) -> dict:
    """Resolve origin from the machine; a client scope may only corroborate it."""
    supplied = supplied if isinstance(supplied, dict) else {}
    machine_die = str(machine.get("die_name") or "").strip()
    machine_config = str(machine.get("config_name") or "").strip()
    supplied_catalog = supplied.get("kind") == "catalog"
    supplied_die = str(supplied.get("die") or "").strip() if supplied_catalog else ""
    supplied_config = str(supplied.get("config") or "").strip() if supplied_catalog else ""

    if machine_die or machine_config:
        origin = _resolved_catalog_scope(machine_die, machine_config)
        if supplied_catalog:
            claimed = _resolved_catalog_scope(supplied_die, supplied_config)
            equivalent = (str(origin.get("die") or "").casefold()
                          == str(claimed.get("die") or "").casefold()
                          and str(origin.get("config") or "") == str(claimed.get("config") or ""))
            if not equivalent:
                # Only admins may use a persisted scope to resolve an older
                # alias/canonical ambiguity after the source disappears.
                # Engineers need a frozen machine identity that is already
                # canonical, so a forged legacy machine A + scope B cannot
                # borrow engineer B's grant.
                if (allow_unresolved_claim and not _catalog_source_exists(origin)
                        and supplied_config == machine_config
                        and supplied_die and supplied.get("grant_key") == f"{supplied_die}/{supplied_config}"):
                    return {"kind": "catalog", "die": supplied_die,
                            "config": supplied_config,
                            "grant_key": f"{supplied_die}/{supplied_config}"}
                # Legacy forged/malformed rows fail closed on every read.
                return {"kind": "catalog", "die": "", "config": "", "grant_key": ""}
        return origin
    if supplied_catalog:
        # A restored detached workspace has no catalog names of its own. Keep
        # its previously persisted stable origin so resaving cannot weaken it.
        return _resolved_catalog_scope(supplied_die, supplied_config)
    return {"kind": "project"}


def _catalog_source_exists(origin: dict) -> bool:
    """Whether the frozen origin still has an exact catalog config file."""
    try:
        from motor_ai_sim.routes import family
        from motor_ai_sim.development.motors import _canonical_entry_name
        die, config = str(origin.get("die") or ""), str(origin.get("config") or "")
        if not die or not config:
            return False
        for entry in family._iter_die_entries():
            if (str(entry.get("layer") or "") not in {"shared", "published"}
                    or _canonical_entry_name(entry).casefold() != die.casefold()):
                continue
            directory = Path(str(entry.get("dir") or "")).resolve()
            # Existence is a property of the canonical catalog entry itself.
            # An ordinary workspace shadow may make its alias non-actionable,
            # but it does not turn the published source into a deleted archive.
            # Keep the source-present path active so `_catalog_visible` applies
            # the ordinary shadow and grant checks and fails closed.
            if (directory / f"{config}.yaml").is_file():
                return True
        return False
    except Exception:
        # An inspection failure is not evidence that the source was removed;
        # keep the ordinary visibility path authoritative.
        return True


def _catalog_visible(request: Request, origin: dict) -> bool:
    if origin.get("kind") != "catalog":
        return True
    try:
        from motor_ai_sim import motor_access
        from motor_ai_sim.routes import family
        from motor_ai_sim.development.motors import _canonical_entry_name, _ordinary_alias_for_canonical
        die, config = str(origin.get("die") or ""), str(origin.get("config") or "")
        alias = _ordinary_alias_for_canonical(family, die)
        if not alias:
            return False
        entry = next((item for item in family._iter_die_entries()
                      if str(item.get("name") or "") == alias
                      and _canonical_entry_name(item).casefold() == die.casefold()), None)
        if not entry:
            return False
        path = Path(str(entry.get("dir"))) / f"{config}.yaml"
        if not path.is_file():
            return False
        authorization = request.headers.get("authorization")
        family._require_die_access(alias, authorization, config)
        return bool(path.is_file() and motor_access.config_visible(
            motor_access.catalog_access(authorization),
            alias, config, path, layer=entry.get("layer")))
    except Exception:
        return False


def _snapshot_catalog_visible(request: Request, user: dict, origin: dict) -> bool:
    """Apply ordinary source checks, with narrow recovery for a missing archive source."""
    if origin.get("kind") != "catalog":
        return True
    # If the source is still present, ordinary identity, visibility, and grant
    # checks remain authoritative, including hidden published-name shadows.
    if _catalog_source_exists(origin):
        return _catalog_visible(request, origin)
    die, config = str(origin.get("die") or ""), str(origin.get("config") or "")
    if (not die or not config
            or origin.get("grant_key") != f"{die}/{config}"):
        return False
    role = str(user.get("role") or "").strip().lower()
    if role == "admin":
        return True
    if role != "engineer":
        return False
    try:
        from motor_ai_sim import motor_access
        authorization = request.headers.get("authorization")
        grants = motor_access.catalog_access(authorization).get("configs") or frozenset()
        return motor_access.config_grant_key(die, config) in grants
    except Exception:
        return False


def _safe_results(results: Any) -> list[dict]:
    if not isinstance(results, list):
        return []
    allowed_kinds = {"thermal", "rotor_stress", "modes", "critical_speeds",
                     "coupled", "em", "duty_cycle", "duty"}
    safe = []
    for item in results:
        if not isinstance(item, dict):
            continue
        row = {}
        if isinstance(item.get("duty"), str):
            row["duty"] = item["duty"][:120]
        if isinstance(item.get("has_result"), bool):
            row["has_result"] = item["has_result"]
        point = item.get("operating_point") if isinstance(item.get("operating_point"), dict) else {}
        row["operating_point"] = {k: point[k] for k in ("rpm", "current_arms", "gamma_deg")
                                   if isinstance(point.get(k), (int, float)) and not isinstance(point.get(k), bool)
                                   and __import__("math").isfinite(float(point[k]))}
        if point.get("mode") in ("motor", "generator"):
            row["operating_point"]["mode"] = point["mode"]
        row["electromagnetic"] = _metric_projection(item.get("electromagnetic"))
        stored = item.get("stored_results") if isinstance(item.get("stored_results"), dict) else {}
        row["stored_results"] = {str(name): _metric_projection(value) for name, value in stored.items()
                                  if str(name).casefold() in allowed_kinds and isinstance(value, dict)}
        raw_comparison = item.get("comparison") if isinstance(item.get("comparison"), dict) else {}
        row["comparison"] = _safe_comparison(raw_comparison)
        default_source = item.get("result_provenance")
        if not isinstance(default_source, str):
            default_source = raw_comparison.get("result_provenance")
        row["provenance"] = _safe_provenance(item.get("provenance"),
                                              default_source=default_source)
        safe.append(row)
    return safe


@router.get("/{project_id}/snapshots")
def list_snapshots(request: Request, project_id: str):
    user, project, role = access.require_project(request, project_id)
    rows = []
    for item in store.list_snapshots(project_id):
        machine = item.get("machine") if isinstance(item.get("machine"), dict) else {}
        origin = _catalog_origin(machine, item.get("access_scope"),
                                 allow_unresolved_claim=str(user.get("role") or "").lower() == "admin")
        if not _snapshot_catalog_visible(request, user, origin):
            continue
        public = {k: item.get(k) for k in ("id", "created_at", "author_id", "author_display_name", "note", "source")}
        if not public.get("author_display_name"):
            public["author_display_name"] = ""
        from motor_ai_sim.development.motors import _motor_dimensions_docs
        dimensions = _motor_dimensions_docs(machine.get("die") or {}, machine.get("configuration") or {})
        public.update({"die": machine.get("die_name"), "config": machine.get("config_name"),
                       "label": machine.get("label") or "Configuration history", **dimensions})
        results = _safe_results(item.get("results"))
        public.update({"kind": "history", "results": results})
        rows.append(public)
    return {"snapshots": rows}



def _readable_project(request: Request, project_id: str):
    _find_project_id(project_id)
    return access.require_project(request, project_id)


@router.get("/schema")
def get_schema(request: Request):
    access.require_signed_in(request)
    raw_version = request.query_params.get("version")
    if raw_version is None:
        return models.schema()
    if raw_version not in ("1", "2"):
        raise HTTPException(status_code=422, detail="version must be 1 or 2")
    return models.schema(int(raw_version))


@router.get("")
def list_projects(request: Request):
    user = access.require_signed_in(request)
    visible = [p for p in store.list_projects() if access.can_view(user, p)]
    return {"projects": [_summary(p, user) for p in visible]}


@router.get("/membership")
def project_membership(request: Request):
    """Cheap navigation gate: consult one index, never enumerate project docs."""
    user = access.require_signed_in(request)
    return {"has_projects": bool(store.membership_projects(_email(user)))}


@router.post("")
async def create_project(request: Request):
    user = access.require_signed_in(request)
    if not access.is_engineer(user):
        raise HTTPException(status_code=403, detail="Engineer access required")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    project_id = str(body.get("id") or "").strip().lower()
    if not store.valid_id(project_id):
        raise HTTPException(status_code=422, detail="id must be a lowercase slug")
    try:
        project = models.project_record(project_id, body, _email(user))
        store.create_project(project)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Project already exists")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    store.save_json(project_id, "draft.json", {"draft": {"schema_version": 2, "fields": {}, "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}, "schema_version": 2, "revision": 0, "version": None, "updated_at": project["created_at"]})
    store.append_audit(project_id, _email(user), "create", "project", project_id, {})
    return _summary(project, user)


@router.get("/{project_id}")
def get_project(request: Request, project_id: str):
    _, project, _ = _readable_project(request, project_id)
    user = access.require_signed_in(request)
    store.append_audit(project_id, _email(user), "read", "project", project_id, {})
    return _summary(project, user)


@router.patch("/{project_id}")
async def update_project(request: Request, project_id: str):
    user, project, role = access.require_project(request, project_id, edit=True)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    allowed = {"title", "customer_org", "owner_engineer", "status", "notes"}
    if set(body) - allowed:
        raise HTTPException(status_code=422, detail="Unsupported project fields")
    from motor_ai_sim.development import PROJECT_STATUSES
    if "status" in body and (not (access.is_engineer(user) or role in ("owner", "engineer")) or body["status"] not in PROJECT_STATUSES):
        raise HTTPException(status_code=403, detail="Engineer access required for status changes")
    if "notes" in body and not (access.is_engineer(user) or role in ("owner", "engineer")):
        raise HTTPException(status_code=403, detail="Engineer access required for notes")
    if "owner_engineer" in body and not access.is_engineer(user):
        raise HTTPException(status_code=403, detail="Admin or engineer access required")
    for key in ("title", "customer_org", "owner_engineer", "notes"):
        if key in body and not isinstance(body[key], str):
            raise HTTPException(status_code=422, detail=f"{key} must be text")
    if "title" in body and not body["title"].strip():
        raise HTTPException(status_code=422, detail="title cannot be empty")

    def mutate(d):
        d.update(body)
        d["updated_at"] = models.now()
    from motor_ai_sim.development.motors import transaction
    with transaction():
        store.mutate_project(project_id, mutate)
    store.append_audit(project_id, _email(user), "update", "project", project_id, {"fields": sorted(body)})
    return _summary(store.get_project(project_id), user)


def _draft_wrapper(project_id: str) -> dict:
    return store.load_json(project_id, "draft.json", {"draft": {"fields": {}, "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}, "revision": 0, "version": None})


def _versions(project_id: str) -> list[dict]:
    return [{k: v for k, v in item.items() if k != "draft"}
            for item in _versions_full(project_id)]


@router.get("/{project_id}/spec")
def get_spec(request: Request, project_id: str):
    user, _, _ = _readable_project(request, project_id)
    versions = _versions(project_id)
    wrapper = _draft_wrapper(project_id)
    store.append_audit(project_id, _email(user), "read", "spec_draft", "draft", {})
    stored_draft = wrapper.get("draft", {"fields": {}, "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}})
    schema_version = wrapper.get("schema_version", stored_draft.get("schema_version", 1))
    if int(schema_version) >= 2:
        stored_draft = models.lift_legacy_depth(stored_draft)  # a 3_1 depth shows as the 3_6 immersion depth; nothing is lost
        stored_draft = models.with_defaults(stored_draft)  # prototypes = 1 until the customer changes it (counted as filled)
        try:  # a free-text controller that IS a catalogue entry is linked to it; no other value is touched
            from motor_ai_sim.inverter import controller_catalog
            stored_draft = controller_catalog.link_catalog_controller(stored_draft)
        except Exception:  # noqa: BLE001 - an unreadable catalogue must never hide the specification
            pass
    return {"draft": _customer_spec({"draft": stored_draft})["draft"] if not access.is_engineer(user) else stored_draft,
            "revision": wrapper.get("revision", 0), "version": wrapper.get("version"), "schema_version": schema_version,
            "schema": models.schema(int(schema_version)), "completeness": models.completeness(wrapper.get("draft", {})), "versions": [_customer_spec(v) for v in versions]}


@router.post("/{project_id}/spec/migration/preview")
async def preview_spec_migration(request: Request, project_id: str):
    user, _, _ = access.require_spec_editor(request, project_id)
    wrapper = _draft_wrapper(project_id)
    draft = wrapper.get("draft", {})
    version = int(wrapper.get("schema_version", draft.get("schema_version", 1)))
    if version >= 2:
        raise HTTPException(status_code=409, detail="Specification already uses schema version 2")
    from motor_ai_sim.development import migrations
    preview = migrations.preview_v1_to_v2(draft, source_revision=int(wrapper.get("revision", 0)))
    if not access.is_engineer(user):
        preview = copy.deepcopy(preview)
        preview["draft"] = _customer_spec({"draft": preview["draft"]})["draft"]
    return {"preview": preview, "revision": wrapper.get("revision", 0)}


@router.post("/{project_id}/spec/migration/apply")
async def apply_spec_migration(request: Request, project_id: str):
    user, _, _ = access.require_spec_editor(request, project_id)
    body = await request.json()
    if not isinstance(body, dict) or isinstance(body.get("revision"), bool) or not isinstance(body.get("revision"), int):
        raise HTTPException(status_code=422, detail="revision must be an integer")
    expected = body["revision"]
    acknowledge = body.get("acknowledge_point", False)
    if not isinstance(acknowledge, bool):
        raise HTTPException(status_code=422, detail="acknowledge_point must be boolean")
    from motor_ai_sim import json_store
    from motor_ai_sim.development import migrations
    path = store._file(project_id, "draft.json")
    ensure = getattr(store, "ensure_writable", None)
    if ensure:
        try:
            ensure()
        except OSError as exc:
            raise HTTPException(status_code=503, detail="Project store is read-only") from exc
    result = {}
    no_op = False
    with json_store.lock_for(path):
        current = json_store.read_json(path, {"draft": {}, "revision": 0, "schema_version": 1})
        draft = current.get("draft", {})
        current_version = int(current.get("schema_version", draft.get("schema_version", 1)))
        if current_version >= 2:
            records = draft.get("provenance", {}).get("schema_migrations", [])
            already_applied = any(isinstance(item, dict) and item.get("from_schema_version") == 1
                                  and item.get("source_revision") == expected
                                  and item.get("source_sha256") == body.get("source_sha256")
                                  for item in records) and int(current.get("revision", 0)) == expected + 1
            if already_applied:
                result.update(current)
                no_op = True
            else:
                raise HTTPException(status_code=409, detail="Specification already uses schema version 2")
        if result:
            pass
        elif int(current.get("revision", 0)) != expected:
            raise HTTPException(status_code=409, detail={"message": "Draft changed; request a new migration preview", "revision": current.get("revision", 0)})
        if result:
            pass
        else:
            preview = migrations.preview_v1_to_v2(draft, source_revision=expected)
            if body.get("source_sha256") != preview["source_sha256"]:
                raise HTTPException(status_code=409, detail="Draft differs from migration preview")
            try:
                migrated = migrations.apply_acknowledgement(preview, acknowledge)
                normalized = models.normalize_draft(migrated)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            current.update({"draft": normalized, "schema_version": 2, "revision": expected + 1, "updated_at": models.now()})
            json_store.atomic_write_json(path, current)
            result.update(current)
    if not no_op:
        store.append_audit(project_id, _email(user), "migrate", "spec_draft", "draft", {"from_schema_version": 1, "to_schema_version": 2, "revision": result["revision"]})
    response_draft = result["draft"] if access.is_engineer(user) else _customer_spec({"draft": result["draft"]})["draft"]
    return {"draft": response_draft, "revision": result["revision"], "schema_version": 2, "completeness": models.completeness(result["draft"])}


@router.put("/{project_id}/spec")
async def put_spec(request: Request, project_id: str):
    user, _, _ = access.require_spec_editor(request, project_id)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")
    try:
        raw_revision = body.get("revision")
        if isinstance(raw_revision, bool) or not isinstance(raw_revision, int):
            raise ValueError("revision must be an integer")
        expected = raw_revision
        if expected < 0:
            raise ValueError("revision must be non-negative")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    result = {"saved": None}
    def mutate(current):
        current_revision = int(current.get("revision", 0))
        if current_revision != expected:
            result["conflict"] = current_revision
            return
        persisted_version = int(current.get("schema_version", current.get("draft", {}).get("schema_version", 1)))
        try:
            requested_version = int(body.get("schema_version", persisted_version))
        except (ValueError, TypeError):
            result["validation_error"] = "schema_version must be 1 or 2"
            return
        if requested_version != persisted_version:
            result["version_conflict"] = persisted_version
            return
        try:
            draft = models.normalize_draft(body.get("draft"), schema_version=persisted_version)
        except (ValueError, TypeError) as exc:
            result["validation_error"] = str(exc)
            return
        if not access.is_engineer(user):
            draft["provenance"] = copy.deepcopy(current.get("draft", {}).get("provenance", {}))
        draft["schema_version"] = persisted_version
        current.update({"draft": draft, "schema_version": persisted_version, "revision": current_revision + 1, "updated_at": models.now()})
        result["saved"] = copy.deepcopy(current)
    store.mutate_json(project_id, "draft.json", mutate, default={"draft": {"fields": {}, "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}, "revision": 0, "version": None})
    if result.get("conflict") is not None:
        raise HTTPException(status_code=409, detail={"message": "Draft changed; reload before saving", "revision": result["conflict"]})
    if result.get("version_conflict") is not None:
        raise HTTPException(status_code=409, detail={"message": "Draft schema changed; reload and migrate", "schema_version": result["version_conflict"]})
    if result.get("validation_error") is not None:
        raise HTTPException(status_code=422, detail=result["validation_error"])
    saved = result["saved"]
    store.append_audit(project_id, _email(user), "autosave", "spec_draft", "draft", {"revision": saved["revision"]})
    response_draft = saved["draft"] if access.is_engineer(user) else _customer_spec({"draft": saved["draft"]})["draft"]
    return {"draft": response_draft, "revision": saved["revision"], "version": saved.get("version"), "schema_version": saved.get("schema_version", 1),
            "completeness": models.completeness(saved["draft"])}


@router.post("/{project_id}/spec/submit")
async def submit_spec(request: Request, project_id: str):
    user, _, _ = access.require_spec_editor(request, project_id)
    body = await request.json()
    raw_revision = body.get("revision") if isinstance(body, dict) else None
    if isinstance(raw_revision, bool) or not isinstance(raw_revision, int) or raw_revision < 0:
        raise HTTPException(status_code=422, detail="revision must be a non-negative integer")
    expected = raw_revision
    path = store._file(project_id, "draft.json")
    from motor_ai_sim import json_store
    submitted = {}
    ensure = getattr(store, "ensure_writable", None)
    if ensure:
        try: ensure()
        except OSError as exc: raise HTTPException(status_code=503, detail="Project store is read-only") from exc
    store.append_audit(project_id, _email(user), "submit_attempt", "spec_draft", "draft", {"revision": expected})
    with json_store.lock_for(path):
        current = json_store.read_json(path, {"draft": {"fields": {}, "mission_profile": [], "mission_profile_status": "missing", "production": {}, "provenance": {}}, "revision": 0, "version": None})
        if int(current.get("revision", 0)) != expected:
            raise HTTPException(status_code=409, detail={"message": "Draft changed; reload before submitting", "revision": current.get("revision", 0)})
        try:
            draft = models.normalize_draft(current.get("draft", {}), schema_version=int(current.get("schema_version", current.get("draft", {}).get("schema_version", 1))))
        except ValueError as exc:
            store.append_audit(project_id, _email(user), "submit_rejected", "spec_draft", "draft", {"revision": expected, "reason": str(exc)})
            raise HTTPException(status_code=422, detail={"message": "Stored specification is invalid", "errors": [{"field": "draft", "message": str(exc)}]})
        errors = models.validation_errors(draft)
        if errors:
            store.append_audit(project_id, _email(user), "submit_rejected", "spec_draft", "draft", {"revision": expected, "errors": errors})
            raise HTTPException(status_code=422, detail={"message": "Specification is incomplete", "errors": errors,
                                                         "completeness": models.completeness(draft)})
        index = store.load_json(project_id, "spec/index.json", {"versions": []})
        versions = index.get("versions", [])
        version_dir = store.path_for(project_id) / "spec"
        existing_numbers = [int(m.group(1)) for file in version_dir.glob("v*.json") if (m := re.fullmatch(r"v(\d+)\.json", file.name))]
        number = max([int(v.get("version", 0)) for v in versions] + existing_numbers, default=0) + 1
        version = models.version_record(number, _email(user), copy.deepcopy(draft), source_revision=expected)
        version_path = store._file(project_id, f"spec/v{number}.json")
        if version_path.exists():
            raise HTTPException(status_code=409, detail="Specification version already exists; retry")
        json_store.atomic_write_json(version_path, version)
        versions.append({k: v for k, v in version.items() if k != "draft"})
        json_store.atomic_write_json(store._file(project_id, "spec/index.json"), {"versions": versions})
        current["version"] = number
        current["revision"] = int(current.get("revision", 0)) + 1
        current["updated_at"] = models.now()
        json_store.atomic_write_json(path, current)
        store.append_audit(project_id, _email(user), "submit", "spec_version", str(number),
                           {"revision": expected, "completeness": models.completeness(draft)})
        submitted.update({"version": version, "current": current})
    return {"version": submitted["version"]["version"], "spec": _customer_spec(submitted["version"]),
            "revision": submitted["current"]["revision"],
            "completeness": models.completeness(submitted["version"]["draft"])}


@router.get("/{project_id}/spec/versions")
def list_spec_versions(request: Request, project_id: str):
    user, _, _ = _readable_project(request, project_id)
    versions = _versions_full(project_id)
    store.append_audit(project_id, _email(user), "read", "spec_versions", "", {})
    return {"versions": [{k: v for k, v in item.items() if k != "draft"} for item in versions] if access.is_engineer(user) else [_customer_spec({k: v for k, v in item.items() if k != "draft"}) for item in versions]}


@router.get("/{project_id}/spec/versions/{number}")
def get_spec_version(request: Request, project_id: str, number: int):
    user, _, _ = _readable_project(request, project_id)
    versions = _versions_full(project_id)
    for item in versions:
        if int(item.get("version", -1)) == number:
            store.append_audit(project_id, _email(user), "read", "spec_version", str(number), {})
            projected = item if access.is_engineer(user) else _customer_spec(item)
            projected["schema_version"] = int(item.get("schema_version", item.get("draft", {}).get("schema_version", 1)))
            projected["schema"] = models.schema(projected["schema_version"])
            if isinstance(projected.get("draft"), dict):
                projected["draft"] = models.upgrade_legacy_values(projected["draft"])  # display only; the stored version is untouched
            return projected
    raise HTTPException(status_code=404, detail="Specification version not found")


def _versions_full(project_id: str) -> list[dict]:
    from motor_ai_sim import json_store
    with json_store.lock_for(store._file(project_id, "draft.json")):
        return _versions_full_locked(project_id)


def _versions_full_locked(project_id: str) -> list[dict]:
    index = store.load_json(project_id, "spec/index.json", {"versions": []})
    indexed = {int(x.get("version", 0)): x for x in index.get("versions", [])}
    version_dir = store.path_for(project_id) / "spec"
    for file in version_dir.glob("v*.json"):
        match = re.fullmatch(r"v(\d+)\.json", file.name)
        if not match:
            continue
        number = int(match.group(1))
        if number not in indexed:
            version = store.load_json(project_id, f"spec/{file.name}", None)
            if isinstance(version, dict) and int(version.get("version", -1)) == number:
                indexed[number] = {k: v for k, v in version.items() if k != "draft"}
    normalized_index = [indexed[n] for n in sorted(indexed) if n > 0]
    if normalized_index != index.get("versions", []):
        store.save_json(project_id, "spec/index.json", {"versions": normalized_index})
    out = []
    for entry in normalized_index:
        number = int(entry.get("version", 0))
        version = store.load_json(project_id, f"spec/v{number}.json", None)
        if isinstance(version, dict):
            out.append(version)
    if out:
        latest = out[-1]
        source_revision = latest.get("source_revision")
        if isinstance(source_revision, int):
            def recover_draft(current: dict) -> None:
                if (int(current.get("revision", 0)) == source_revision and
                        current.get("draft") == latest.get("draft")):
                    current["version"] = int(latest["version"])
                    current["revision"] = source_revision + 1
                    current["updated_at"] = latest.get("created_at", models.now())
            store.mutate_json(project_id, "draft.json", recover_draft, default={})
    return out


def _read_jsonl(project_id: str, name: str) -> list[dict]:
    path = store._file(project_id, name)
    if not path.is_file(): return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = __import__("json").loads(line)
            if isinstance(item, dict): out.append(item)
        except ValueError:
            continue
    return out


@router.get("/{project_id}/thread")
def get_thread(request: Request, project_id: str):
    user, _, _ = _readable_project(request, project_id)
    messages = _read_jsonl(project_id, "thread.jsonl")
    store.append_audit(project_id, _email(user), "read", "thread", "", {})
    resolutions = {}
    for event in messages:
        if event.get("kind") == "resolution" and event.get("resolves_id"):
            resolutions[str(event["resolves_id"])] = event
    projected = []
    for message in messages:
        if message.get("kind") == "resolution":
            continue
        item = dict(message)
        resolution = resolutions.get(str(item.get("id")))
        item["resolved"] = bool(resolution.get("resolved", True)) if resolution is not None else False
        if resolution:
            item["resolved_at"] = resolution.get("created_at")
            item["resolved_by"] = resolution.get("resolved_by")
        projected.append(_customer_spec(item))
    return {"messages": projected}


@router.post("/{project_id}/thread")
async def post_thread(request: Request, project_id: str):
    user, _, _ = _readable_project(request, project_id)
    body = await request.json()
    text = str(body.get("text") or "").strip() if isinstance(body, dict) else ""
    if not text or len(text) > 10000:
        raise HTTPException(status_code=422, detail="text must contain 1 to 10000 characters")
    field_ref = body.get("field_ref")
    if field_ref is not None and not isinstance(field_ref, str):
        raise HTTPException(status_code=422, detail="field_ref must be text")
    row_id = body.get("row_id")
    version = body.get("version")
    revision = body.get("revision")
    if row_id is not None and (not isinstance(row_id, str) or len(row_id) > 128):
        raise HTTPException(status_code=422, detail="row_id must be text")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
        raise HTTPException(status_code=422, detail="version must be a positive integer")
    if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int) or revision < 0):
        raise HTTPException(status_code=422, detail="revision must be a non-negative integer")
    message = {"id": __import__("secrets").token_urlsafe(12), "author_email": _email(user),
               "created_at": datetime.now(timezone.utc).isoformat(), "text": text, "field_ref": field_ref,
               "row_id": row_id, "version": version, "revision": revision}
    store.append_jsonl(project_id, "thread.jsonl", message)
    store.append_audit(project_id, _email(user), "post", "thread_message", message["id"], {"field_ref": field_ref})
    notify_project_members(project_id, message)
    return _customer_spec(message)


@router.post("/{project_id}/thread/{message_id}/resolve")
async def resolve_thread_message(request: Request, project_id: str, message_id: str):
    user, project, _ = access.require_project(request, project_id)
    if not access.can_edit_spec(user, project):
        raise HTTPException(status_code=403, detail="Verified project member access required to resolve discussion")
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("resolved"), bool):
        raise HTTPException(status_code=422, detail="resolved boolean required")
    messages = _read_jsonl(project_id, "thread.jsonl")
    target = next((m for m in messages if m.get("id") == message_id and m.get("kind") != "resolution"), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Thread message not found")
    existing = next((m for m in reversed(messages) if m.get("kind") == "resolution" and m.get("resolves_id") == message_id), None)
    if existing and bool(existing.get("resolved")) == body["resolved"]:
        return {"id": message_id, "resolved": body["resolved"], "resolved_at": existing.get("created_at")}
    if not body["resolved"] and not existing:
        return {"id": message_id, "resolved": False}
    event = {"id": __import__("secrets").token_urlsafe(12), "kind": "resolution", "resolves_id": message_id,
             "resolved": body["resolved"], "resolved_by": _author(_email(user))["author_display_name"],
             "author_email": _email(user), "created_at": datetime.now(timezone.utc).isoformat()}
    store.append_jsonl(project_id, "thread.jsonl", event)
    store.append_audit(project_id, _email(user), "project_thread_resolved" if body["resolved"] else "project_thread_reopened", "thread_message", message_id, {"resolved": body["resolved"]})
    return {"id": message_id, "resolved": body["resolved"], "resolved_at": event["created_at"]}


def _release_member_grants(project: dict, project_id: str, email: str, reason: str, actor: str) -> None:
    """Take back the motor grants the project made for ``email`` and save the project's ledger.  The membership is already
    committed; a fault here only leaves ledger rows that the next ``grant_cleanup.drain_all`` settles."""
    try:
        from motor_ai_sim.development import grant_cleanup
        from motor_ai_sim.development.motors import _key
        for motor in project.get("linked_motors", []):
            if isinstance(motor, dict):
                grant_cleanup.release_motor_grants(project, _key(motor.get("die", ""), motor.get("config", "")),
                                                   reason=reason, emails={email}, actor=actor)
        store.mutate_project(project_id, lambda doc: doc.update(project))
    except Exception:                                       # noqa: BLE001
        log.warning("project %s: motor grants of %s not released", project_id, email, exc_info=True)


def _apply_member(project_id: str, email: str, role: str) -> tuple[dict, bool]:
    """Add or re-role `email` on a project: a member when the account exists, else a pending invite.
    Returns (entry, account_found). Shared by POST /members and by the sign-in invite claim."""
    entry = {"email": email, "role": role}
    from motor_ai_sim.development.motors import transaction, _sync_grants, _mark_removal_candidates, _key, MOTOR_GRANT_ROLES
    with transaction(), users._LOCK:
        account = users.get_user(email)
        if account and account.get("disabled"):
            account = None
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        project_before = copy.deepcopy(project)
        membership_before = store.load_membership_index()
        old_role = next((m.get("role") for m in project.get("members", []) if m.get("email", "").lower() == email), None)
        updated = copy.deepcopy(project)
        updated["members"] = [m for m in updated.get("members", []) if m.get("email", "").lower() != email]
        if account:
            updated["pending_invites"] = [m for m in updated.get("pending_invites", []) if m.get("email", "").lower() != email]
            updated["members"].append(entry)
        else:
            updated.setdefault("pending_invites", [])
            updated["pending_invites"] = [m for m in updated["pending_invites"] if m.get("email") != email]
            updated["pending_invites"].append({**entry, "created_at": models.now()})
        updated["updated_at"] = models.now()
        try:
            if account and role in MOTOR_GRANT_ROLES:
                for motor in updated.get("linked_motors", []):
                    if isinstance(motor, dict):
                        _sync_grants(updated, _key(motor.get("die", ""), motor.get("config", "")),
                                     f"{motor.get('die', '')}/{motor.get('config', '')}", {email})
            store.mutate_project(project_id, lambda doc: doc.update(updated))
            store.sync_membership_index(project_id, project.get("members", []), updated.get("members", []))
        except Exception:
            # Membership metadata rolls back, while grants already added remain
            # and are reported for manual cleanup.
            restored = project_before
            for motor_key, entry in (updated.get("project_motor_grants") or {}).items():
                prior = (restored.get("project_motor_grants") or {}).get(motor_key, {})
                old_emails = set(prior.get("emails", [])) if isinstance(prior, dict) else set(prior)
                emails = set(entry.get("emails", [])) if isinstance(entry, dict) else set(entry)
                inserted = emails - old_emails
                if inserted:
                    grant = entry.get("grant_key", "") if isinstance(entry, dict) else ""
                    restored.setdefault("project_motor_grants", {})[motor_key] = {
                        "grant_key": grant, "emails": sorted(old_emails | emails)}
                    candidate = {"project_motor_grants": {motor_key: {"grant_key": grant, "emails": sorted(inserted)}}}
                    _mark_removal_candidates(candidate, motor_key, reason="membership_update_failed")
                    restored.setdefault("grant_removal_candidates", []).extend(candidate.get("grant_removal_candidates", []))
            store.save_json(project_id, "project.json", restored)
            store.save_membership_index(membership_before)
            raise
        if not (account and role in MOTOR_GRANT_ROLES) and old_role in MOTOR_GRANT_ROLES:
            # the member no longer gets the project's motors: the grants the project made for them go too (owner
            # 2026-10-27) - after the membership is committed, so a failed change never costs anyone access
            _release_member_grants(updated, project_id, email, "customer_membership_changed", "member change")
    return entry, bool(account)


def claim_pending_invites(email: str) -> list[str]:
    """Turn every pending invite for `email` into a membership (called at sign-in with a VERIFIED address).

    Without this an invite to an address that had no account stayed pending forever (CR-HF-75, 2026-10-25).
    Only a verified address may claim: someone registering a stranger's address must not inherit the projects."""
    email = str(email or "").strip().lower()
    claimed: list[str] = []
    for project in store.list_projects():
        invite = next((m for m in project.get("pending_invites", []) if str(m.get("email", "")).lower() == email), None)
        if not invite:
            continue
        role = str(invite.get("role") or "customer")
        _entry, found = _apply_member(project["id"], email, role)
        if found:
            store.append_audit(project["id"], email, "invite_claimed", "member", email, {"role": role})
            claimed.append(project["id"])
    return claimed


@router.post("/{project_id}/members")
async def add_member(request: Request, project_id: str):
    user, _, _ = access.require_project(request, project_id, edit=True)
    body = await request.json()
    email = str(body.get("email") or "").strip().lower() if isinstance(body, dict) else ""
    role = str(body.get("role") or "customer") if isinstance(body, dict) else "customer"
    if not email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or role not in ("customer", "engineer", "owner"):
        raise HTTPException(status_code=422, detail="Valid email and role required")
    entry, found = _apply_member(project_id, email, role)
    account = found
    store.append_audit(project_id, _email(user), "invite", "member", email, {"role": role, "pending": not bool(account)})
    return {"member": entry if account else None, "pending": not bool(account)}


@router.delete("/{project_id}/members")
async def remove_member(request: Request, project_id: str, email: str):
    user, _, _ = access.require_project(request, project_id, edit=True)
    email = str(email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="Valid email required")
    from motor_ai_sim.development.motors import transaction, _mark_removal_candidates, _key
    with transaction(), users._LOCK:
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        project_before = copy.deepcopy(project)
        membership_before = store.load_membership_index()
        updated = copy.deepcopy(project)
        previous_members = updated.get("members", [])
        updated["members"] = [m for m in previous_members if str(m.get("email") or "").lower() != email]
        updated["pending_invites"] = [m for m in updated.get("pending_invites", []) if str(m.get("email") or "").lower() != email]
        if len(updated["members"]) == len(previous_members) and len(updated["pending_invites"]) == len(project.get("pending_invites", [])):
            raise HTTPException(status_code=404, detail="Project member not found")
        updated["updated_at"] = models.now()
        try:
            store.mutate_project(project_id, lambda doc: doc.update(updated))
            store.sync_membership_index(project_id, previous_members, updated["members"])
        except Exception:
            store.save_json(project_id, "project.json", project_before)
            store.save_membership_index(membership_before)
            raise
        # the grants the project made for this member leave with them (owner 2026-10-27), after the commit
        _release_member_grants(updated, project_id, email, "customer_membership_removed", _email(user))
    store.append_audit(project_id, _email(user), "remove", "member", email, {})
    return {"ok": True}



def notify_project_members(project_id: str, message: dict) -> None:
    """Stage 1 notification hook. Stage 2 may deliver notifications here."""
    return None
