"""Project motor links using the existing per-user motor grant system."""
from __future__ import annotations

import json
import copy
import logging
import math
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from motor_ai_sim import users
from motor_ai_sim.development import access, store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}/motors", tags=["development-motors"])
_METRIC_KEYS = {
    "torque_nm", "t_em_avg_nm", "t_em_nm", "t_avg_nm", "torque_avg_nm",
    "power_w", "p_mech_w", "p_mech_avg_w", "p_out_w", "p_shaft_out_w", "p_shaft_w",
    "p_loss_total_w", "p_loss_total_incl_mech_w", "loss_w", "loss_mech_w",
    "p_cu_w", "p_fe_w", "p_core_w", "p_stranded_w", "p_solid_w", "p_mag_w",
    "p_rotor_w", "p_mech_total_w", "p_mech_extra_w", "p_mech_loss_w", "p_bearings_w", "p_windage_w",
    "efficiency_pct", "efficiency_percent", "efficiency", "eta_pct", "eta_shaft", "eta_em",
    "eta_shaft_est", "efficiency_shaft", "ripple_pct",
    "t_max", "t_min", "t_coil_c", "coil_temp_c", "t_magnet_c", "magnet_temp_c",
    "magnet_temp_max_c", "bearing_temp_c", "t_stator_c", "stator_temp_c", "t_rotor_c", "rotor_temp_c",
    "temperature_c", "t_max_c", "t_min_c", "max_temperature_c",
    "stator_temperature_c", "rotor_temperature_c", "magnet_temperature_c",
    "winding_temperature_c", "rpm", "current_arms", "i_phase_rms", "gamma_deg",
    "mass_kg", "mass_full_kg", "mass_total_kg", "mass_partial", "motor_mass_kg", "total_mass_kg",
    "torque_to_mass_nm_kg", "torque_per_mass_full_nm_kg", "t_per_mass_nm_kg",
    "v_line_peak_v", "v_line_peak_solved_v", "v_ll_peak_v", "v_phase_peak_v", "line_voltage_peak_v",
    "v_line_peak_physical_v",
    "v_dc_v", "v_dc_required_v", "v_dc_required", "dc_bus_v", "v_bus_v",
    "current_density_a_mm2", "j_rms_a_mm2", "j_max_a_mm2", "j_coil_a_mm2", "j_coil_a_per_mm2",
    "i_phase_rms_a", "i1_phase_rms_a",
    "t_ripple_pct",
    "demag", "demag_flag", "demag_occurred", "demagnetized",
    "demagnetisation", "demagnetization",
}

class MotorRef(BaseModel):
    die: str
    config: str


class MotorStatusUpdate(BaseModel):
    die: str
    config: str
    status: Literal["current", "trial"]


@router.get("/picker")
def motor_picker(project_id: str, request: Request):
    user, project, _ = access.require_project(request, project_id, edit=True)
    if not access.is_engineer(user):
        raise HTTPException(403, detail="Engineer access required")
    from motor_ai_sim.routes import family
    authorization = request.headers.get("authorization")
    catalog_access = __import__("motor_ai_sim.motor_access", fromlist=["catalog_access"]).catalog_access(authorization)
    records = _picker_records(family, catalog_access)
    by_canonical = {(record["canonical"].casefold(), record["config"].casefold()): record
                    for record in records}
    by_alias = {(record["alias"].casefold(), record["config"].casefold()): record
                for record in records}
    def canonical_key(die, config):
        record = (by_canonical.get((str(die).casefold(), str(config).casefold()))
                  or by_alias.get((str(die).casefold(), str(config).casefold())))
        return _key(record["canonical"] if record else die, config)
    current = {canonical_key(x.get("die", ""), x.get("config", ""))
               for x in project.get("linked_motors", []) if isinstance(x, dict)}
    history = store.motor_history(project_id, project)
    this_project = []
    latest = {}
    for row in history:
        die, config = str(row.get("die") or ""), str(row.get("config") or "")
        key = canonical_key(die, config)
        if not die or not config or key in current:
            continue
        latest[key] = row
    idx = (_index().get("motors") or {})
    for key, row in latest.items():
        die, config = str(row.get("die") or ""), str(row.get("config") or "")
        record = (by_canonical.get((die.casefold(), config.casefold()))
                  or by_alias.get((die.casefold(), config.casefold())))
        conflict = _indexed_motor(idx, record, die, config)
        available = bool(record and _record_share_allowed(request, record))
        dimensions = _motor_dimensions_docs(record["die_doc"], record["config_doc"]) if record else {
            "diameter_mm": None, "slots": None, "poles": None, "stack_mm": None}
        this_project.append({"die": record["alias"] if record else die,
                             "canonical_die": die, "config": config,
                             "label": _motor_label_docs(die, config, record["die_doc"], record["config_doc"]) if record else f"{die} / {config}",
                             **dimensions,
                             "linked_at": row.get("linked_at"), "unlinked_at": row.get("unlinked_at"),
                             "unlinked_by": row.get("unlinked_by"),
                             "disabled": not available or bool(isinstance(conflict, dict) and conflict.get("project_id") != project_id),
                             "reason_code": "linked_elsewhere" if isinstance(conflict, dict) and conflict.get("project_id") != project_id else ("unavailable" if not available else None),
                             "reason": "Already linked to another project" if isinstance(conflict, dict) and conflict.get("project_id") != project_id else (None if available else "Motor is no longer available")})
    catalog = []
    index = (_index().get("motors") or {})
    ident = _identity(request)
    for record in records:
        entry, config = record["entry"], record["config"]
        die, canonical = record["alias"], record["canonical"]
        key = _key(canonical, config)
        if key in current:
            continue
        if entry.get("layer") not in {"shared", "published"}:
            continue
        if not _record_share_allowed(request, record, ident=ident):
            continue
        linked = _indexed_motor(index, record, canonical, config)
        elsewhere = isinstance(linked, dict) and linked.get("project_id") != project_id
        catalog.append({"die": die, "canonical_die": canonical, "config": config,
                        "label": _motor_label_docs(canonical, config, record["die_doc"], record["config_doc"]),
                        **_motor_dimensions_docs(record["die_doc"], record["config_doc"]),
                        "disabled": bool(elsewhere),
                        "reason_code": "linked_elsewhere" if elsewhere else None,
                        "reason": "Already linked to another project" if elsewhere else None})
    return {"this_project": this_project, "catalog": catalog}


def _indexed_motor(index: dict, record: dict | None, die: str, config: str):
    """Read canonical indexes while accepting pre-Stage-3 alias keys."""
    candidates = [die]
    if record:
        candidates = [record["canonical"], record["alias"]]
    for candidate in candidates:
        row = index.get(_key(candidate, config))
        if isinstance(row, dict):
            return row
    return None


def _canonical_entry_name(entry: dict) -> str:
    alias = str(entry.get("name") or "")
    if entry.get("layer") != "published":
        return alias
    from motor_ai_sim.workspace import published_label
    return published_label(str(entry.get("die") or alias), str(entry.get("owner") or ""))


def _picker_records(family, catalog_access: dict) -> list[dict]:
    """Parse each ordinary visible catalog document once.

    The alias is the actionable ordinary family name; canonical is the stable
    project/index identity. Exact path equality prevents a workspace shadow
    from becoming a back door to its hidden published namesake.
    """
    from motor_ai_sim import motor_access
    records: list[dict] = []
    for entry in family._iter_die_entries():
        alias = str(entry.get("name") or "")
        layer = str(entry.get("layer") or "")
        if layer not in {"shared", "published"}:
            continue
        directory = Path(str(entry.get("dir") or ""))
        try:
            resolved = directory.resolve(strict=True)
            if Path(str(family._die_dir(alias))).resolve(strict=True) != resolved:
                continue
            die_doc = yaml.safe_load((resolved / "die.yaml").read_text(encoding="utf-8")) or {}
            for cfg_path in sorted(resolved.glob("*.yaml")):
                if cfg_path.name == "die.yaml":
                    continue
                if not motor_access.may_see_config_file(
                        catalog_access, alias, cfg_path.stem, cfg_path, layer=layer):
                    continue
                records.append({"entry": entry, "alias": alias,
                                "canonical": _canonical_entry_name(entry),
                                "config": cfg_path.stem, "die_doc": die_doc,
                                "config_doc": yaml.safe_load(
                                    cfg_path.read_text(encoding="utf-8")) or {}})
        except (OSError, ValueError, yaml.YAMLError, HTTPException):
            continue
    return records


def _record_share_allowed(request: Request, record: dict, *, ident: dict | None = None) -> bool:
    ident = ident or _identity(request)
    if ident.get("is_admin"):
        return True
    entry = record["entry"]
    caller = str(ident.get("id") or ident.get("email") or "").lower()
    owner = str(entry.get("owner") or "").lower()
    return bool(entry.get("layer") == "published" and owner and owner == caller)


def _ordinary_alias_for_canonical(family, canonical_die: str) -> str | None:
    """Return an ordinary alias only when it resolves to that exact entry."""
    for entry in family._iter_die_entries():
        if _canonical_entry_name(entry).casefold() != canonical_die.casefold():
            continue
        alias = str(entry.get("name") or "")
        try:
            if (Path(str(family._die_dir(alias))).resolve(strict=True)
                    == Path(str(entry.get("dir"))).resolve(strict=True)):
                return alias
        except (OSError, ValueError, HTTPException):
            continue
    return None


def _motor_label(die: str, config: str) -> str:
    try:
        d, p, _, _ = _motor_for_picker(die, config)
        geo = yaml.safe_load((d / "die.yaml").read_text(encoding="utf-8")) or {}
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return _motor_label_docs(die, config, geo, cfg)
    except Exception:
        return f"{die} / {config}"


def _motor_label_docs(die: str, config: str, die_doc: dict, cfg: dict) -> str:
    dims = _motor_dimensions_docs(die_doc, cfg)
    return f"{dims['diameter_mm']} mm Â· {dims['slots']} slots / {dims['poles']} poles Â· {dims['stack_mm']} mm stack"


def _motor_dimensions(die: str, config: str) -> dict:
    try:
        d, p, _, _ = _motor_for_picker(die, config)
        die_doc = yaml.safe_load((d / "die.yaml").read_text(encoding="utf-8")) or {}
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return _motor_dimensions_docs(die_doc, cfg)
    except Exception:
        return {"diameter_mm": None, "slots": None, "poles": None, "stack_mm": None}


def _motor_dimensions_docs(die_doc: dict, cfg: dict) -> dict:
    geometry = die_doc.get("geometry") or die_doc
    overrides = cfg.get("geometry_overrides") or {}
    merged = {**geometry, **overrides}
    return {"diameter_mm": merged.get("stator_diameter"), "slots": merged.get("num_slots"),
            "poles": merged.get("num_poles"), "stack_mm": merged.get("motor_length")}


def _motor_for_picker(die: str, config: str):
    from motor_ai_sim.routes import family
    d = Path(str(family._die_dir(die))).resolve(strict=True)
    p = Path(str(family._cfg_file(die, config))).resolve(strict=True)
    return d, p, None, None


def _picker_authorized(request: Request, die: str, config: str, entry: dict) -> None:
    from motor_ai_sim.routes import family
    from motor_ai_sim import motor_access
    authorization = request.headers.get("authorization")
    family._require_die_access(die, authorization, config)
    path = Path(str(family._cfg_file(die, config))).resolve(strict=True)
    layer = entry.get("layer")
    doc = motor_access.catalog_access(authorization)
    if layer not in {"shared", "published"} or not motor_access.config_visible(doc, die, config, path, layer=layer):
        raise HTTPException(404, detail="Motor configuration not found")
    ident = _identity(request)
    owner = str(entry.get("owner") or "").lower()
    caller = str(ident.get("id") or ident.get("email") or "").lower()
    if not ident.get("is_admin") and not (layer == "published" and owner and owner == caller):
        raise HTTPException(403, detail="Only an administrator or published motor owner may share this motor")


def _key(die: str, config: str) -> str:
    return f"{die.casefold()}\0{config.casefold()}"


def _tx_path() -> Path:
    return store.root() / ".project-transaction.lock"

@contextmanager
def transaction():
    """Common project/membership/grant lock. Never create a default shared root on read."""
    with store.project_transaction():
        yield


def _index() -> dict:
    return store.load_index()


def _identity(request: Request) -> dict:
    from motor_ai_sim.auth import caller_identity
    return caller_identity(request.headers.get("authorization"))


def _motor(request: Request, die: str, config: str) -> tuple[Path, Path, dict, dict]:
    """Resolve only through the ordinary caller-visible motor namespace."""
    from motor_ai_sim.routes import family
    from motor_ai_sim import motor_access
    authorization = request.headers.get("authorization")
    family._require_die_access(die, authorization, config)
    # A missing configuration answers exactly like a hidden one (the 404
    # _require_die_access gives), never a 500: otherwise the link is an oracle.
    missing = HTTPException(404, detail=family._config_missing_detail(die, config))
    try:
        die_dir = Path(str(family._die_dir(die))).resolve(strict=True)
        cfg_path = Path(str(family._cfg_file(die, config))).resolve(strict=True)
    except (OSError, HTTPException):
        raise missing from None
    if die_dir not in cfg_path.parents or not cfg_path.is_file():
        raise missing
    ident = _identity(request)
    # The ordinary resolver is authoritative. Match the catalog entry by both
    # its request alias and the exact resolved directory; raw die names can
    # collide with another owner's decorated published entry.
    entries = family._iter_die_entries()
    resolved_entry = next((e for e in entries
                           if e.get("name") == die
                           and e.get("dir") is not None
                           and Path(str(e["dir"])).resolve() == die_dir), None)
    layer = resolved_entry.get("layer") if resolved_entry else None
    if layer not in {"shared", "published"}:
        raise HTTPException(403, detail="Publish or copy the motor to the shared catalog before linking it")
    access_doc = motor_access.catalog_access(authorization)
    if not motor_access.config_visible(access_doc, die, config, cfg_path, layer=layer):
        raise missing
    # Share authority comes from the resolved namespace, never the request body.
    canonical_die = die
    published = resolved_entry if layer == "published" else None
    if not ident.get("is_admin"):
        caller_email = str(ident.get("id") or ident.get("email") or "").lower()
        owner_email = str((published or {}).get("owner") or "").lower()
        allowed_owner = bool(published and owner_email and owner_email == caller_email)
        if not allowed_owner:
            raise HTTPException(403, detail="Only an administrator or published motor owner may share this motor")
    if published:
        from motor_ai_sim.workspace import published_label
        canonical_die = published_label(str(published.get("die") or die), str(published.get("owner") or ""))
    return die_dir, cfg_path, ident, {"layer": layer, "canonical_die": canonical_die}


def _grant(email: str, key: str) -> bool:
    """Add one stored config grant while preserving unrelated motor grants."""
    with users._LOCK:
        current = users.get_stored_motor_grants(email)
        configs = set(current.get("configs") or [])
        had = key in configs
        configs.add(key)
        if configs != set(current.get("configs") or []):
            users.set_motor_grants(email, all_motors=bool(current.get("all")),
                                   dies=list(current.get("dies") or []),
                                   default=current.get("default"), configs=sorted(configs))
        return had


def _customers(project: dict) -> set[str]:
    result = set()
    for member in project.get("members", []):
        if not isinstance(member, dict) or member.get("role") != "customer":
            continue
        email = str(member.get("email") or "").strip().lower()
        account = users.get_user(email) if email else None
        if account and not account.get("disabled"):
            result.add(email)
    return result


def _sync_grants(project: dict, motor_key: str, grant_key: str,
                 emails: set[str] | None = None) -> None:
    """Add ordinary motor grants and retain only historical project additions.

    Project cleanup is advisory: this module never removes a user grant because
    an administrator may have reissued the same grant since project creation.
    """
    ledger = project.setdefault("project_motor_grants", {})
    entry = ledger.get(motor_key) or {"grant_key": grant_key, "emails": []}
    historical = set(entry.get("emails", [])) if isinstance(entry, dict) else set(entry)
    ledger[motor_key] = {"grant_key": grant_key, "emails": sorted(historical)}
    for email in sorted(_customers(project) if emails is None else emails):
        existed = grant_key in set(users.get_stored_motor_grants(email).get("configs") or [])
        try:
            _grant(email, grant_key)
        except Exception:
            # A writer can persist and then raise (for example after an fsync
            # fault). Detect that add-only side effect and retain provenance.
            after = grant_key in set(users.get_stored_motor_grants(email).get("configs") or [])
            if not existed and after:
                historical.add(email)
                ledger[motor_key] = {"grant_key": grant_key, "emails": sorted(historical)}
            raise
        if not existed:
            historical.add(email)
            ledger[motor_key] = {"grant_key": grant_key, "emails": sorted(historical)}
    ledger[motor_key] = {"grant_key": grant_key, "emails": sorted(historical)}


def _mark_removal_candidates(project: dict, motor_key: str, *, reason: str,
                             emails: set[str] | None = None) -> None:
    entry = (project.get("project_motor_grants") or {}).get(motor_key)
    if not isinstance(entry, dict):
        return
    selected = set(entry.get("emails", [])) if emails is None else set(entry.get("emails", [])) & emails
    grant_key = str(entry.get("grant_key") or "")
    if not grant_key or not selected:
        return
    die, _, config = grant_key.partition("/")
    rows = project.setdefault("grant_removal_candidates", [])
    existing = {(str(x.get("email", "")).lower(), str(x.get("die", "")).casefold(),
                 str(x.get("config", "")).casefold()) for x in rows if isinstance(x, dict)}
    for email in sorted(selected):
        key = (email.lower(), die.casefold(), config.casefold())
        if key not in existing:
            rows.append({"email": email, "die": die, "config": config, "reason": reason})
            existing.add(key)


def link_motor(project_id: str, die: str, config: str, request: Request) -> dict:
    die = str(die or "").strip(); config = str(config or "").strip()
    if not die or not config or any(x in die + config for x in ("/", "\\", "..")):
        raise HTTPException(422, detail="Invalid motor identity")
    # Perform ordinary read + owner checks before changing project or account state.
    _dir, _path, _ident, resolved = _motor(request, die, config)
    actionable_die = die
    die = resolved.get("canonical_die", die)
    grant_key = f"{die}/{config}"
    created_link = False
    with transaction(), users._LOCK:
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(404, detail="Project not found")
        old_project = copy.deepcopy(project)
        key = _key(die, config)
        index = store.load_index()
        current = (index.get("motors") or {}).get(key)
        if current and current.get("project_id") != project_id:
            raise HTTPException(409, detail="Motor is already linked to another project")
        previous = [m for m in project.get("linked_motors", []) if isinstance(m, dict)]
        was_linked = any(_key(m.get("die", ""), m.get("config", "")) == key for m in previous)
        if not was_linked:
            created_link = True
            history = project.setdefault("motor_history", [])
            prior = next((m for m in reversed(history) if isinstance(m, dict) and _key(m.get("die", ""), m.get("config", "")) == key and m.get("unlinked_at")), None)
            now = __import__("motor_ai_sim.development.models", fromlist=["now"]).now()
            project.setdefault("linked_motors", []).append({"die": die, "config": config, "status": "current"})
            if prior:
                prior["restored_at"] = now
                prior["restored_by"] = str(_ident.get("email") or _ident.get("id") or "")
            history.append({"die": die, "config": config, "linked_at": now,
                            "linked_by": str(_ident.get("email") or _ident.get("id") or "")})
        project["updated_at"] = __import__("motor_ai_sim.development.models", fromlist=["now"]).now()
        try:
            _sync_grants(project, key, grant_key)
            store.mutate_project(project_id, lambda doc: doc.update(project))
            def update(doc):
                doc.setdefault("motors", {})[key] = {"project_id": project_id, "die": die, "config": config}
            from motor_ai_sim import json_store
            json_store.mutate_json(store.index_path(), update, default={"motors": {}})
        except Exception:
            # Grants are add-only. Keep provenance for any additions even when
            # the project link/index write failed, so cleanup remains auditable.
            for motor_key, entry in (project.get("project_motor_grants") or {}).items():
                prior = (old_project.get("project_motor_grants") or {}).get(motor_key, {})
                old_emails = set(prior.get("emails", [])) if isinstance(prior, dict) else set(prior)
                grant = entry.get("grant_key", grant_key) if isinstance(entry, dict) else grant_key
                emails = set(entry.get("emails", [])) if isinstance(entry, dict) else set(entry)
                inserted = emails - old_emails
                if inserted:
                    old_project.setdefault("project_motor_grants", {})[motor_key] = {
                        "grant_key": grant, "emails": sorted(old_emails | emails)}
                    candidate = {"project_motor_grants": {motor_key: {"grant_key": grant, "emails": sorted(inserted)}}}
                    _mark_removal_candidates(candidate, motor_key, reason="link_failed")
                    old_project.setdefault("grant_removal_candidates", []).extend(candidate.get("grant_removal_candidates", []))
            store.save_json(project_id, "project.json", old_project)
            store.save_index(index)
            raise
    if created_link:
        try:
            _capture_link_snapshot(project_id, actionable_die, config, request, "linked motor")
        except Exception as exc:
            log.warning("project %s linked %s/%s but could not capture snapshot: %s", project_id, die, config, exc)
    return {"die": die, "config": config}


def unlink_motor(project_id: str, die: str, config: str, *, actor: str = "") -> None:
    key = _key(die, config)
    with transaction(), users._LOCK:
        project = store.get_project(project_id)
        if not project:
            raise HTTPException(404, detail="Project not found")
        old_index = store.load_index()
        updated = copy.deepcopy(project)
        canonical = next((m for m in updated.get("linked_motors", [])
                          if isinstance(m, dict) and _key(m.get("die", ""), m.get("config", "")) == key),
                         {"die": die, "config": config})
        grant_entry = (updated.get("project_motor_grants") or {}).get(key)
        grant_key = (grant_entry.get("grant_key") if isinstance(grant_entry, dict) else None) or f"{canonical.get('die', die)}/{canonical.get('config', config)}"
        updated["linked_motors"] = [m for m in updated.get("linked_motors", [])
                                     if not (isinstance(m, dict) and _key(m.get("die", ""), m.get("config", "")) == key)]
        from motor_ai_sim.development.models import now
        history = updated.setdefault("motor_history", [])
        stamp = now()
        latest_link = next((event for event in reversed(history)
                            if isinstance(event, dict)
                            and _key(event.get("die", ""), event.get("config", "")) == key
                            and event.get("linked_at") and not event.get("unlinked_at")), {})
        history.append({"die": canonical.get("die", die), "config": canonical.get("config", config),
                        "linked_at": latest_link.get("linked_at"),
                        "linked_by": latest_link.get("linked_by"), "unlinked_at": stamp,
                        "unlinked_by": str(actor or "")})
        updated["updated_at"] = __import__("motor_ai_sim.development.models", fromlist=["now"]).now()
        try:
            _mark_removal_candidates(updated, key, reason="motor_unlinked")
            store.mutate_project(project_id, lambda doc: doc.update(updated))
            from motor_ai_sim import json_store
            def remove(doc):
                row = (doc.get("motors") or {}).get(key)
                if isinstance(row, dict) and row.get("project_id") == project_id:
                    doc["motors"].pop(key, None)
            json_store.mutate_json(store.index_path(), remove, default={"motors": {}})
        except Exception:
            store.save_json(project_id, "project.json", project)
            store.save_index(old_index)
            raise
    try:
        _capture_project_motor_snapshot(project_id, canonical.get("die", die), canonical.get("config", config), actor, "unlinked motor")
    except Exception as exc:
        log.warning("project %s unlinked %s/%s but could not capture snapshot: %s", project_id, die, config, exc)


def _capture_link_snapshot(project_id: str, die: str, config: str, request: Request, source: str) -> None:
    """Best-effort immutable project copy when a catalog motor is linked."""
    try:
        die_dir, cfg_path, ident, resolved = _motor(request, die, config)
        canonical_die = str(resolved.get("canonical_die") or die)
        die_doc = yaml.safe_load((die_dir / "die.yaml").read_text(encoding="utf-8")) or {}
        config_doc = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        definition = {"die": die_doc, "configuration": config_doc,
                      "die_name": canonical_die, "config_name": config,
                      "label": _motor_label_docs(canonical_die, config, die_doc, config_doc)}
        from motor_ai_sim import duty_results
        results = duty_results.get(die, config)
        cfg = definition["configuration"]
        projection = _results(die, config, cfg, results)
        snapshot = {"machine": definition, "results": projection}
        _create_snapshot(project_id, snapshot, str(ident.get("email") or ident.get("id") or ""),
                         f"{source}: {canonical_die}/{config}", source,
                         access_scope={"kind": "catalog", "die": canonical_die, "config": config,
                                       "grant_key": f"{canonical_die}/{config}"})
    except Exception:
        raise


def _capture_project_motor_snapshot(project_id: str, die: str, config: str, actor: str, source: str) -> None:
    from motor_ai_sim.routes import family
    from motor_ai_sim import duty_results
    alias = _ordinary_alias_for_canonical(family, die)
    if not alias:
        raise FileNotFoundError(f"ordinary catalog alias unavailable for {die}")
    die_dir = Path(str(family._die_dir(alias))).resolve(strict=True)
    cfg_path = Path(str(family._cfg_file(alias, config))).resolve(strict=True)
    die_doc = yaml.safe_load((die_dir / "die.yaml").read_text(encoding="utf-8")) or {}
    config_doc = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    definition = {"die": die_doc, "configuration": config_doc,
                  "die_name": die, "config_name": config,
                  "label": _motor_label_docs(die, config, die_doc, config_doc)}
    try:
        stored = duty_results.get(alias, config)
    except Exception:
        stored = {}
    results = _results(die, config, definition["configuration"], stored)
    _create_snapshot(project_id, {"machine": definition, "results": results}, actor,
                     f"{source}: {die}/{config}", source,
                     access_scope={"kind": "catalog", "die": die, "config": config,
                                   "grant_key": f"{die}/{config}"})


def _create_snapshot(project_id: str, payload: dict, author: str, note: str, source: str,
                     *, access_scope: dict | None = None) -> dict:
    import hashlib
    from datetime import datetime, timezone
    from motor_ai_sim.development import store
    stamp = datetime.now(timezone.utc).isoformat()
    snapshot_id = "snapshot-" + hashlib.sha256(f"{project_id}:{stamp}:{note}".encode()).hexdigest()[:20]
    author_name = str((users.get_user(str(author).lower()) or {}).get("name", "")) if "@" in str(author) else ""
    if "@" in author_name or "@" in str(author) and str(author).lower() in author_name.lower():
        author_name = ""
    row = {"id": snapshot_id, "version": 1, "created_at": stamp,
           "author_id": hashlib.sha256(str(author).lower().encode()).hexdigest()[:20],
           "author_display_name": author_name,
        "note": note, "source": source, "machine": payload.get("machine") or {},
           "results": payload.get("results") or [],
           "access_scope": access_scope or {"kind": "project"}}
    store.save_snapshot(project_id, row)
    return row


def _metric_projection(value: Any) -> dict:
    """Copy allowlisted numeric fields from this object only; never mix nested runs."""
    output = {}
    if not isinstance(value, dict): return output
    for key, item in value.items():
        norm = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        if (norm in _METRIC_KEYS and isinstance(item, (int, float))
                and not isinstance(item, bool) and math.isfinite(float(item))):
            output[norm] = item
    return output


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(float(value)) else None


def _first(sources: list[dict], *keys: str) -> float | int | None:
    for source in sources:
        for key in keys:
            value = _number(source.get(key))
            if value is not None:
                return value
    return None


def _direct_bool(sources: list[dict], *keys: str) -> bool | None:
    for source in sources:
        for key in keys:
            if isinstance(source.get(key), bool):
                return source[key]
    return None


def _demag_flag(*sources: Any) -> bool | None:
    """Read only an explicitly stored demagnetisation verdict."""
    for source in sources:
        if not isinstance(source, dict):
            continue
        direct = _direct_bool([source], "demag", "demag_flag", "demag_occurred",
                              "demagnetization", "demagnetisation",
                              "demagnetized", "demagnetised")
        if direct is not None:
            return direct
        report = source.get("demag_report")
        if isinstance(report, list):
            flags = [row.get("demagnetised", row.get("demagnetized"))
                     for row in report if isinstance(row, dict)]
            flags = [flag for flag in flags if isinstance(flag, bool)]
            if flags:
                return any(flags)
        block = source.get("demag")
        if isinstance(block, dict) and block:
            losses = [_number(block.get(key)) for key in
                      ("loss_pct", "bh_loss_pct", "area_derated_pct")]
            kept = [_number(block.get(key)) for key in
                    ("br_kept_vol_pct", "bh_kept_vol_pct")]
            known = [value for value in losses + kept if value is not None]
            if known:
                return (any(value is not None and value > 0 for value in losses)
                        or any(value is not None and value < 100 for value in kept))
    return None


def _component_max(thermal: dict, *names: str) -> float | int | None:
    components = thermal.get("components")
    if not isinstance(components, dict):
        return None
    for name in names:
        component = components.get(name)
        if not isinstance(component, dict):
            continue
        value = _number(component.get("max"))
        if value is not None:
            return value
    return None


def _comparison_projection(duty: dict, stored: dict) -> dict:
    """Project one duty from its primary catalog row and compact stored results.

    Source precedence is field-specific.  Only top-level primary values, the
    compact coupled ``em`` block and thermal component maxima are
    read.  Historical runs, reference-sine, controller and 20 C blocks are never
    traversed.
    """
    result_raw = duty.get("result") if isinstance(duty.get("result"), dict) else {}
    summary_raw = duty.get("summary") if isinstance(duty.get("summary"), dict) else {}
    coupled_raw = stored.get("coupled") if isinstance(stored.get("coupled"), dict) else {}
    coupled_em_raw = coupled_raw.get("em") if isinstance(coupled_raw.get("em"), dict) else {}
    coupled_inverter_raw = coupled_raw.get("inverter") if isinstance(coupled_raw.get("inverter"), dict) else {}
    thermal_raw = stored.get("thermal") if isinstance(stored.get("thermal"), dict) else {}

    result = _metric_projection(result_raw)
    summary = _metric_projection(summary_raw)
    coupled = _metric_projection(coupled_raw)
    coupled_em = _metric_projection(coupled_em_raw)
    thermal = _metric_projection(thermal_raw)
    primary = [result, summary]
    comparison: dict[str, Any] = {}

    # DutySpec.torque_nm / power_kw are requested rated targets.  Comparison
    # result columns are populated only from solved result/summary records.
    torque = _first(primary + [coupled_em], "t_em_avg_nm", "t_em_nm",
                    "t_avg_nm", "torque_avg_nm", "torque_nm")
    if torque is not None:
        comparison["torque_nm"] = torque

    power = _first(primary + [coupled_em], "p_mech_w", "p_mech_avg_w", "p_out_w",
        "p_shaft_out_w", "p_shaft_w", "power_w")
    if power is not None:
        comparison["power_w"] = power

    efficiency_pct = _first(primary, "efficiency_pct", "efficiency_percent", "eta_pct")
    if efficiency_pct is None:
        efficiency_ratio = _first([summary, coupled], "efficiency_shaft", "eta_shaft",
                                  "eta_shaft_est")
        if efficiency_ratio is None:
            efficiency_ratio = _first([summary, coupled_em], "efficiency", "eta_em")
        if efficiency_ratio is not None:
            # These named fields are fractions by contract.  Never infer a unit
            # from the numeric magnitude of an ambiguous field.
            efficiency_pct = efficiency_ratio * 100
    if efficiency_pct is not None:
        comparison["efficiency_pct"] = efficiency_pct

    em_loss = _first([result], "loss_w", "p_loss_total_w")
    mech_loss = _first([result], "loss_mech_w", "p_mech_total_w")
    total_loss = None
    loss_partial = False
    if em_loss is not None:
        total_loss = em_loss + mech_loss if mech_loss is not None else em_loss
        loss_partial = mech_loss is None
    else:
        total_loss = _first([summary], "p_loss_total_incl_mech_w")
        if total_loss is None:
            em_loss = _first([summary], "p_loss_total_w", "loss_w")
            mech_loss = _first([summary], "p_mech_extra_w", "loss_mech_w",
                               "p_mech_total_w")
            if em_loss is not None:
                total_loss = em_loss + mech_loss if mech_loss is not None else em_loss
                loss_partial = mech_loss is None
        if total_loss is None:
            total_loss = _first([coupled], "p_loss_total_incl_mech_w")
        if total_loss is None:
            em_loss = _first([coupled_em, thermal], "p_loss_total_w", "loss_w")
            mech_loss = _first([coupled], "p_mech_extra_w", "p_mech_total_w", "loss_mech_w")
            if em_loss is not None:
                total_loss = em_loss + mech_loss if mech_loss is not None else em_loss
                loss_partial = mech_loss is None
    if total_loss is not None:
        comparison["p_loss_total_w"] = total_loss
        if loss_partial:
            comparison["loss_total_partial"] = True

    split_sources = primary + [coupled_em, thermal]
    for target, aliases in {
        "p_cu_w": ("p_stranded_w", "p_cu_w"),
        "p_fe_w": ("p_core_w", "p_fe_w"),
        "p_solid_w": ("p_solid_w",),
        "p_mag_w": ("p_mag_w",),
    }.items():
        value = _first(split_sources, *aliases)
        if value is not None:
            comparison[target] = value
    mech_split = _first([result, summary, coupled], "loss_mech_w", "p_mech_total_w",
                        "p_mech_extra_w", "p_mech_loss_w")
    if mech_split is None:
        bearings = _first([summary, coupled], "p_bearings_w")
        windage = _first([summary, coupled], "p_windage_w")
        if bearings is not None and windage is not None:
            mech_split = bearings + windage
    if mech_split is not None:
        comparison["p_mech_total_w"] = mech_split

    # The completeness stamp is an explicit boolean and is intentionally not
    # part of the general numeric projection. Read it from these two bounded
    # top-level source objects while retaining the same field precedence.
    full_source = next((source for source in (summary_raw, result_raw)
                        if _first([source], "mass_full_kg") is not None
                        and _direct_bool([source], "mass_partial") is False), None)
    full_mass = _first([full_source], "mass_full_kg") if full_source else None
    full_stamp = full_source is not None
    mass = full_mass if full_stamp else _first(
        [result, summary], "mass_kg", "mass_total_kg", "motor_mass_kg", "total_mass_kg")
    if mass is not None:
        comparison["mass_kg"] = mass
        comparison["mass_partial"] = not full_stamp
        # The ratio must use the exact torque and mass selected for this row.
        # Stored ratios with an unstated basis can otherwise mix active and full mass.
        torque_mass = torque / mass if torque is not None and mass > 0 else None
        if torque_mass is not None:
            comparison["torque_to_mass_nm_kg"] = torque_mass

    # The delta equivalent is physical for the sinusoidal voltage drive, but
    # PWM uses a scaled model bus and its branch peak is not the bridge peak.
    def _peak_block(source: dict, source_name: str) -> tuple[str | None, str | None]:
        equivalent = source.get("delta_equivalent_star")
        pwm = source.get("pwm")
        provenance = source.get("line_peak_provenance")
        if isinstance(equivalent, dict) and equivalent.get("applied") is not False:
            drive = str(equivalent.get("drive") or "").strip().lower()
            model_bus = _number(equivalent.get("v_bus_model_V"))
            if drive in ("pwm_voltage", "inverter") or (model_bus is not None and model_bus > 0):
                return "model_branch_peak", source_name
            if drive != "voltage" or model_bus != 0:
                return "unqualified_equivalent_peak", source_name
        if isinstance(pwm, dict) and pwm.get("equivalent_star") is True:
            return "model_branch_peak", source_name
        if coupled_inverter_raw.get("equivalent_star") is True and source_name == "coupled.em":
            return "model_branch_peak", source_name
        if source.get("line_peak_physical") is False or source.get("v_line_peak_physical") is False:
            return "nonphysical_line_peak", source_name
        if provenance is not None and provenance != "physical_terminal_peak":
            return str(provenance), source_name
        return None, None

    primary_block = next((block for block in (
        _peak_block(summary_raw, "summary"), _peak_block(result_raw, "result"))
        if block[0] is not None), (None, None))
    coupled_block = _peak_block(coupled_em_raw, "coupled.em")
    if coupled_inverter_raw.get("equivalent_star") is True and coupled_block[0] is None:
        coupled_block = ("model_branch_peak", "coupled.em")

    def _qualified_peak(sources: list[tuple[str, dict]], blocked_source: str) -> tuple[float | int, str] | None:
        for source_name, source in sources:
            if source_name != blocked_source:
                continue
            # A contradictory explicit nonphysical qualifier wins. An
            # equivalent-star marker alone can coexist with this distinct,
            # physically named field in the same stored source record.
            if (source.get("line_peak_physical") is False
                    or source.get("v_line_peak_physical") is False):
                continue
            source_provenance = source.get("line_peak_provenance")
            if source_provenance is not None and source_provenance != "physical_terminal_peak":
                continue
            peak = _first([_metric_projection(source)], "v_line_peak_physical_v")
            if peak is not None:
                return peak, source_name
        return None

    explicit_sources = [("result", result_raw), ("summary", summary_raw),
                        ("coupled.em", coupled_em_raw)]
    primary_physical = next(((name, _first([source], "v_line_peak_physical_v"))
                             for name, source in (("summary", summary), ("result", result))
                             if _first([source], "v_line_peak_physical_v") is not None), None)
    primary_solved = next(((name, _first([source], "v_line_peak_solved_v"))
                           for name, source in (("summary", summary), ("result", result))
                           if _first([source], "v_line_peak_solved_v") is not None), None)
    primary_generic = next(((name, _first([source], "v_ll_peak_v", "v_line_peak_v",
                                          "line_voltage_peak_v"))
                            for name, source in (("summary", summary), ("result", result))
                            if _first([source], "v_ll_peak_v", "v_line_peak_v",
                                      "line_voltage_peak_v") is not None), None)
    primary_selected = primary_physical or primary_solved or primary_generic
    primary_legacy_peak = primary_selected[1] if primary_selected else None
    if primary_block[0] is not None or (coupled_block[0] is not None and primary_legacy_peak is None):
        blocked_source = primary_block[1] if primary_block[0] is not None else coupled_block[1]
        qualified = _qualified_peak(explicit_sources, blocked_source)
        if qualified is not None:
            comparison["v_line_peak_v"], comparison["line_peak_source"] = qualified
            comparison["line_peak_physical"] = True
            comparison["line_peak_provenance"] = "physical_terminal_peak"
        else:
            reason, source_name = primary_block if primary_block[0] is not None else coupled_block
            comparison["line_peak_physical"] = False
            comparison["line_peak_provenance"] = (
                "delta_equivalent_star" if reason == "model_branch_peak" else reason)
            comparison["line_peak_reason"] = reason
            comparison["line_peak_source"] = source_name
    else:
        line_peak = primary_legacy_peak
        peak_source = primary_selected[0] if primary_selected else "summary"
        if line_peak is None and coupled_block[0] is None:
            line_peak = _first([coupled_em], "v_line_peak_solved_v", "v_ll_peak_v",
                               "v_line_peak_v", "line_voltage_peak_v")
            if line_peak is not None:
                peak_source = "coupled.em"
        if line_peak is not None:
            comparison["v_line_peak_v"] = line_peak
            comparison["line_peak_physical"] = True
            comparison["line_peak_provenance"] = "physical_terminal_peak"
            comparison["line_peak_source"] = peak_source
        elif coupled_block[0] is not None:
            comparison["line_peak_physical"] = False
            comparison["line_peak_provenance"] = (
                "delta_equivalent_star" if coupled_block[0] == "model_branch_peak"
                else coupled_block[0])
            comparison["line_peak_reason"] = coupled_block[0]
            comparison["line_peak_source"] = coupled_block[1]

    for target, aliases, sources in (
        ("ripple_pct", ("ripple_pct", "t_ripple_pct"), primary + [coupled_em]),
        ("v_dc_required_v", ("v_dc_required_v", "v_dc_required"),
         primary + [coupled, coupled_em]),
        ("current_density_a_mm2", ("j_coil_a_per_mm2", "j_coil_a_mm2",
                                    "current_density_a_mm2", "j_rms_a_mm2", "j_max_a_mm2"),
         primary),
    ):
        value = _first(sources, *aliases)
        if value is not None:
            comparison[target] = value

    # Thermal component maxima are the per-part comparison values.  Coupled
    # scalar temperatures are fallbacks for older compact maps.
    temperatures = {
        "coil_temp_c": (_component_max(thermal_raw, "winding", "coil"),
                        _first([coupled, thermal], "coil_temp_c", "t_coil_c",
                               "winding_temperature_c")),
        "magnet_temp_c": (_component_max(thermal_raw, "magnet"),
                          _first([coupled, thermal], "magnet_temp_max_c",
                                 "magnet_temp_c", "t_magnet_c", "magnet_temperature_c")),
        "stator_temp_c": (_component_max(thermal_raw, "stator"),
                          _first([thermal], "stator_temp_c", "t_stator_c",
                                 "stator_temperature_c")),
        "rotor_temp_c": (_component_max(thermal_raw, "rotor"),
                         _first([thermal], "rotor_temp_c", "t_rotor_c",
                                "rotor_temperature_c")),
        "bearing_temp_c": (_component_max(thermal_raw, "bearing"),
                           _first([coupled, thermal], "bearing_temp_c")),
    }
    for target, candidates in temperatures.items():
        value = next((candidate for candidate in candidates if candidate is not None), None)
        if value is not None:
            comparison[target] = value

    components = thermal_raw.get("components")
    if isinstance(components, dict):
        all_temperatures = {
            str(part): value
            for part, component in components.items()
            if isinstance(component, dict)
            and (value := _number(component.get("max"))) is not None
        }
        if all_temperatures:
            comparison["temperatures_c"] = all_temperatures

    demag = _demag_flag(result_raw, summary_raw, coupled_em_raw)
    if demag is not None:
        comparison["demag"] = demag
    return comparison


def _results(die: str, config: str, cfg: dict, stored_by_duty: dict | None = None,
             configure_duties: set[str] | None = None) -> list[dict]:
    from motor_ai_sim import duty_results
    if stored_by_duty is None:
        try: doc = duty_results.get(die, config)
        except Exception: doc = {}
    else:
        doc = stored_by_duty
    by_duty = doc if isinstance(doc, dict) else {}
    out = []
    for duty in cfg.get("duties") or []:
        if not isinstance(duty, dict): continue
        name = str(duty.get("name") or "")
        try:
            from motor_ai_sim.routes import family
            primary_result = family._with_mech_loss(duty)
        except Exception:
            primary_result = duty.get("result")
        em = _metric_projection(primary_result)
        for key, value in _metric_projection(duty.get("summary") or {}).items():
            em.setdefault(key, value)
        stored_duty = by_duty.get(name) or {}
        has_result = bool(duty.get("result") or duty.get("summary") or stored_duty)
        try:
            from motor_ai_sim.routes import family as family_module
            can_open_simulation = _simulation_available(duty, die, family_module)
        except Exception:
            can_open_simulation = False
        can_open_configure = name in (configure_duties or set())
        out.append({"duty": name, "has_result": has_result,
                    "can_open_simulation": can_open_simulation,
                    "can_open_configure": can_open_configure,
                    "operating_point": {k: duty[k] for k in ("rpm", "current_arms", "mode", "gamma_deg") if k in duty},
                    "electromagnetic": em,
                    "stored_results": {str(kind): _metric_projection(row) for kind, row in stored_duty.items()
                                       if isinstance(row, dict) and _metric_projection(row)},
                    "comparison": _comparison_projection({**duty, "result": primary_result}, stored_duty)})
    return out


_SOLVED_METRICS = ("T_em_avg_Nm", "T_avg_Nm", "torque_nm", "P_mech_W",
                   "efficiency_pct", "V_line_peak_V", "P_loss_total_W")


def _solved_block(value: Any, *, legacy_result: bool = False) -> bool:
    """Whether a stored legacy block contains a real Simulation answer."""
    if not isinstance(value, dict) or not value:
        return False
    status = str(value.get("status") or value.get("solve_status") or "").casefold()
    if status and any(word in status for word in ("fail", "error", "estimate", "pending", "invalid")):
        return False
    for key in ("estimated", "is_estimate", "estimate"):
        if value.get(key) is True:
            return False
    for key in ("error", "failure"):
        if value.get(key):
            return False
    provenance = value.get("provenance")
    provenance_kind = (str(provenance.get("kind") or "").casefold()
                       if isinstance(provenance, dict) else str(provenance or "").casefold())
    result_provenance = str(value.get("result_provenance") or "").casefold()
    comparison = value.get("comparison") if isinstance(value.get("comparison"), dict) else {}
    comparison_provenance = str(comparison.get("result_provenance") or "").casefold()
    if (provenance_kind == "estimate" or result_provenance == "configure_interpolation"
            or comparison_provenance == "configure_interpolation"):
        return False
    keys = ("efficiency_pct",) if legacy_result else _SOLVED_METRICS
    for key in keys:
        number = _finite_number(value.get(key))
        if number is not None:
            return True
    return False


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _simulation_available(duty: dict, die: str, family_module) -> bool:
    """Whether the ordinary loader will restore a genuinely solved display."""
    summary = duty.get("summary")
    result = duty.get("result")
    run_rows = duty.get("runs") if isinstance(duty.get("runs"), dict) else {}
    primary = family_module._primary_drive(duty)
    run = run_rows.get(primary)
    rel = run.get("payload_file") if isinstance(run, dict) else None
    if rel:
        try:
            payload = family_module._read_run_payload(die, str(rel))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            # TransientCharts renders these exact torque-series fields.  A duty
            # summary replaces the run summary, but cannot erase real charts.
            if _solved_payload_series(payload):
                return True
            if payload.get("time_s") is not None and payload["time_s"] != []:
                return False
            effective_summary = (summary if isinstance(summary, dict)
                                 else run.get("summary") if isinstance(run, dict)
                                 else None)
            if effective_summary is None:
                effective_summary = payload.get("summary")
            if _solved_block(effective_summary):
                return True
            # A readable primary payload makes dutyLocalApply return here. It
            # never falls through to the YAML legacy result below.
            return False
    # Without a displayable primary payload, dutyLocalApply dispatches an
    # object summary as-is. The legacy result row is used only when no summary
    # object exists, matching its efficiency/torque fallback.
    if isinstance(summary, dict):
        return _solved_block(summary)
    if _solved_block(result, legacy_result=True):
        return True
    return (_finite_number(duty.get("torque_nm")) is not None
            and _solved_block(result))


def _solved_payload_series(payload: Any) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    status = str(payload.get("status") or payload.get("solve_status") or "").casefold()
    provenance = payload.get("provenance")
    provenance_kind = (str(provenance.get("kind") or "").casefold()
                       if isinstance(provenance, dict) else str(provenance or "").casefold())
    if (any(word in status for word in ("fail", "error", "estimate", "pending", "invalid"))
            or payload.get("estimated") is True or payload.get("is_estimate") is True
            or provenance_kind == "estimate"
            or str(payload.get("result_provenance") or "").casefold() == "configure_interpolation"):
        return False
    time_values = payload.get("time_s")
    torque_values = (payload.get("T_em_raw_Nm")
                     if payload.get("T_em_raw_Nm") is not None else payload.get("T_em_Nm"))
    if not isinstance(time_values, list) or not time_values or not isinstance(torque_values, list):
        return False
    count = len(time_values)
    required = ("P_cu_W", "P_fe_W", "P_mag_eddy_W", "P_loss_total_W",
                "I_A", "I_B", "I_C", "V_A", "V_B", "V_C")
    if len(torque_values) < count or any(
            not isinstance(payload.get(key), list) or len(payload[key]) < count for key in required):
        return False
    real = lambda value: (isinstance(value, (int, float)) and not isinstance(value, bool)
                          and math.isfinite(float(value)))
    return (all(real(value) for value in time_values)
            and any(real(value) for value in torque_values[:count])
            and all(real(payload.get(key)) for key in ("f_elec_Hz", "T_avg_Nm", "V_peak")))


def _configure_reference_duties(die: str, config: str, cfg: dict,
                                cfg_path: Path, references: list[dict], catalog_access: dict,
                                authorization: str | None, resolver_cache: dict,
                                context_cache: dict) -> set[str]:
    """Duties whose exact config can be opened through a caller-visible passport.

    ``/catalog/references`` is read once for the request. A reference is useful
    only when its bound family is this die (or the exact die/config fallback
    used by Configure) and this configuration is present in its context.
    The loaded family YAML has already passed the ordinary family access gate.
    """
    from motor_ai_sim import motor_access
    from motor_ai_sim.routes import catalog as catalog_routes
    chosen = next((ref for ref in references if isinstance(ref, dict)
                   and isinstance(ref.get("card"), dict)
                   and ref["card"].get("die") == die
                   and ref["card"].get("config") == config), None)
    if chosen is None:
        chosen = next((ref for ref in references if isinstance(ref, dict)
                       and isinstance(ref.get("card"), dict)
                       and ref["card"].get("die") == die), None)
    passport = chosen.get("passport") if isinstance(chosen, dict) else None
    if (chosen is None or not isinstance(passport, dict)
            or not all(isinstance(passport.get(key), dict)
                       for key in ("passport", "geo", "fit"))):
        return set()
    ref_id = str(chosen.get("id") or "")
    if not ref_id:
        return set()
    # Configure's explicit-id resolver requires one exact id+die binding.
    if sum(1 for ref in references if isinstance(ref, dict)
           and ref.get("id") == ref_id and isinstance(ref.get("card"), dict)
           and ref["card"].get("die") == die) != 1:
        return set()
    if ref_id not in resolver_cache:
        try:
            resolver_cache[ref_id] = catalog_routes._configure_motor(ref_id, authorization)[0]
        except Exception:
            resolver_cache[ref_id] = None
    actual_motor = resolver_cache[ref_id]
    if not isinstance(actual_motor, dict):
        return set()
    family_key = ("family", ref_id)
    if family_key not in resolver_cache:
        geometry = actual_motor.get("geometry")
        if not geometry:
            l0 = ((actual_motor.get("passport") or {}).get("passport") or {}).get("L0_mm")
            geometry = {"motor_length": l0} if l0 is not None else None
        resolver_cache[family_key] = catalog_routes._family_doc_of_motor(actual_motor, geometry)
    family_doc = resolver_cache[family_key]
    family_die = str((family_doc or {}).get("die") or "")
    if family_die != die:
        return set()
    context_key = family_die
    if context_key not in context_cache:
        access_info: dict = {}
        docs, _geometry = catalog_routes._die_docs_of(family_doc, access_info)
        context_cache[context_key] = (docs, access_info)
    docs, access_info = context_cache[context_key]
    target = next((doc for doc in docs if isinstance(doc, dict)
                   and str(doc.get("name") or "") == config
                   and str(doc.get("die") or "") == die), None)
    row = access_info.get(config)
    if target is None or not isinstance(row, (list, tuple)) or len(row) != 4:
        return set()
    try:
        if not motor_access.may_see_config_file(catalog_access, str(row[0]), str(row[1]),
                                                Path(str(row[2])), layer=row[3]):
            return set()
        if Path(str(row[2])).resolve(strict=True) != cfg_path.resolve(strict=True):
            return set()
    except Exception:
        return set()
    return {str(d.get("name")) for d in cfg.get("duties", [])
            if isinstance(d, dict) and d.get("name")}


def _stored_results_index(die: str, cache: dict[str, dict]) -> dict:
    """Return a per-request die index, reading the catalog-backed store once per die."""
    if die not in cache:
        from motor_ai_sim import duty_results
        try:
            cache[die] = duty_results.index(die)
        except Exception:
            cache[die] = {}
    return cache[die]


def _linked_motor_for_status(project: dict, die: str, config: str) -> dict | None:
    """Find a linked entry by its stable identity or an exact ordinary alias."""
    links = [row for row in project.get("linked_motors", []) if isinstance(row, dict)]
    direct = next((row for row in links
                   if _key(str(row.get("die") or ""), str(row.get("config") or "")) == _key(die, config)), None)
    if direct:
        return direct
    from motor_ai_sim.routes import family
    for entry in family._iter_die_entries():
        alias = str(entry.get("name") or "")
        canonical = _canonical_entry_name(entry)
        if die.casefold() not in {alias.casefold(), canonical.casefold()}:
            continue
        try:
            resolved = Path(str(entry.get("dir"))).resolve(strict=True)
            if Path(str(family._die_dir(alias))).resolve(strict=True) != resolved:
                continue
            cfg_path = Path(str(family._cfg_file(alias, config))).resolve(strict=True)
            if resolved not in cfg_path.parents or not cfg_path.is_file():
                continue
        except (OSError, ValueError, HTTPException):
            continue
        return next((row for row in links
                     if str(row.get("config") or "").casefold() == config.casefold()
                     and str(row.get("die") or "").casefold() in {canonical.casefold(), alias.casefold()}), None)
    return None


@router.patch("/status")
def update_project_motor_status(project_id: str, req: MotorStatusUpdate, request: Request):
    user, _project, _role = access.require_project(request, project_id, edit=True)
    if not access.is_engineer(user):
        raise HTTPException(403, detail="Engineer access required")
    if not req.die.strip() or not req.config.strip():
        raise HTTPException(422, detail="Invalid motor identity")
    actor = str(user.get("email") or "")
    old_status = "current"
    changed = False
    with transaction():
        project = store.get_project(project_id)
        if project is None:
            raise HTTPException(404, detail="Project not found")
        linked = _linked_motor_for_status(project, req.die.strip(), req.config.strip())
        if linked is None:
            raise HTTPException(404, detail="Linked motor not found")
        old_status = str(linked.get("status") or "current")
        if old_status not in {"current", "trial"}:
            old_status = "current"
        changed = old_status != req.status
        if changed:
            key_die, key_config = str(linked.get("die") or ""), str(linked.get("config") or "")
            def mutate(doc):
                row = next((item for item in doc.get("linked_motors", [])
                            if isinstance(item, dict)
                            and _key(str(item.get("die") or ""), str(item.get("config") or "")) == _key(key_die, key_config)), None)
                if row is None:
                    raise HTTPException(404, detail="Linked motor not found")
                row["status"] = req.status
                from motor_ai_sim.development.models import now
                doc["updated_at"] = now()
            store.mutate_project(project_id, mutate)
            store.append_audit(project_id, actor, "motor.status", "motor",
                               f"{linked.get('die', req.die)}/{linked.get('config', req.config)}",
                               {"old": old_status, "new": req.status})
    return {"die": str(linked.get("die") or req.die), "config": str(linked.get("config") or req.config),
            "status": req.status if changed else old_status}


@router.get("")
def list_project_motors(project_id: str, request: Request, show_all: bool = False):
    user, project, role = access.require_project(request, project_id)
    from motor_ai_sim.routes import family
    results = []
    project_motor_index = (_index().get("motors") or {})
    # duty_results.index() reads each die's catalog-backed result store once;
    # resolving one configuration per linked motor would re-read that whole store.
    result_index = {}
    is_customer = role == "customer" and not access.is_engineer(user)
    try:
        from motor_ai_sim.routes import catalog as catalog_routes
        authorization = request.headers.get("authorization")
        configure_references = catalog_routes.get_references(authorization).get("motors", [])
        from motor_ai_sim import motor_access
        configure_catalog_access = motor_access.catalog_access(authorization)
    except Exception:
        configure_references = []
        configure_catalog_access = {}
    configure_context_cache = {}
    configure_resolver_cache = {}
    ident = _identity(request)
    die_entries = None
    for link in project.get("linked_motors", []):
        if not isinstance(link, dict): continue
        die, config = str(link.get("die") or ""), str(link.get("config") or "")
        status = str(link.get("status") or "current")
        if status not in {"current", "trial"}:
            status = "current"
        if status == "trial" and not show_all:
            continue
        row = project_motor_index.get(_key(die, config))
        if not isinstance(row, dict) or row.get("project_id") != project_id: continue
        open_die = die
        from motor_ai_sim.workspace import split_published_label
        raw_die, owner_label = split_published_label(die)
        if owner_label:
            if die_entries is None:
                die_entries = list(family._iter_die_entries())
            # Publisher's normal resolver uses the bare name; reject any workspace shadow.
            candidate = next((e for e in die_entries if e.get("layer") == "published" and e.get("die") == raw_die and str(e.get("owner") or "").lower() == str(ident.get("id") or ident.get("email") or "").lower()), None)
            if candidate and Path(str(family._die_dir(raw_die))).resolve() == Path(str(candidate.get("dir"))).resolve():
                open_die = raw_die
        try:
            family._require_die_access(open_die, request.headers.get("authorization"), config)
            cfg_path = Path(str(family._cfg_file(open_die, config))).resolve(strict=True)
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            available = True
        except Exception:
            cfg, available = {}, False
        if available:
            try:
                stored = (_stored_results_index(open_die, result_index) or {}).get(config, {})
            except Exception:
                stored = {}
        else:
            stored = {}
        configure_duties: set[str] = set()
        if available:
            try:
                configure_duties = _configure_reference_duties(
                    open_die, config, cfg, cfg_path, configure_references,
                    configure_catalog_access, authorization, configure_resolver_cache,
                    configure_context_cache)
            except Exception:
                # A malformed reference must not break the read-only project list.
                configure_duties = set()
        result_rows = _results(open_die, config, cfg, stored, configure_duties) if available else []
        duty_names = [str(x.get("name")) for x in cfg.get("duties", [])
                      if isinstance(x, dict) and x.get("name")]
        default_duty = duty_names[0] if duty_names else None
        configure_duty = default_duty if default_duty in configure_duties else None
        simulation_duty = next((str(row.get("duty")) for row in result_rows
                               if row.get("can_open_simulation")), None)
        item = {"die": open_die if available else die, "canonical_die": die, "config": config, "status": status, "available": available, "duties": duty_names,
                "default_duty": default_duty,
                "can_open_configure": configure_duty is not None,
                "configure_duty": configure_duty,
                "can_open_simulation": any(row.get("can_open_simulation") for row in result_rows),
                "simulation_duty": simulation_duty,
                "results": result_rows}
        results.append(item)
        if is_customer:
            store.append_audit(project_id, str(user.get("email") or ""), "motor.results.read", "motor", f"{die}/{config}")
    return {"motors": results}


@router.post("")
def add_project_motor(project_id: str, req: MotorRef, request: Request):
    user, _, _ = access.require_project(request, project_id, edit=True)
    linked = link_motor(project_id, req.die, req.config, request)
    store.append_audit(project_id, str(user.get("email") or ""), "motor.link", "motor", f"{linked['die']}/{linked['config']}")
    return {"motor": linked}


@router.delete("")
def remove_project_motor(project_id: str, die: str, config: str, request: Request):
    user, _, _ = access.require_project(request, project_id, edit=True)
    unlink_motor(project_id, die, config, actor=str(user.get("email") or "").strip().lower())
    store.append_audit(project_id, str(user.get("email") or ""), "motor.unlink", "motor", f"{die}/{config}")
    return {"ok": True}
