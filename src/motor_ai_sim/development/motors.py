"""Project motor links using the existing per-user motor grant system."""
from __future__ import annotations

import json
import copy
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from motor_ai_sim import users
from motor_ai_sim.development import access, store

router = APIRouter(prefix="/api/projects/{project_id}/motors", tags=["development-motors"])
_METRIC_KEYS = {"torque_nm", "t_em_avg_nm", "t_em_nm", "t_avg_nm", "torque_avg_nm", "power_w", "p_out_w", "p_shaft_out_w", "p_loss_total_w", "p_cu_w", "p_fe_w", "p_mag_w", "p_shaft_w", "p_mech_total_w", "p_loss_total_incl_mech_w", "loss_w", "losses_w", "efficiency_pct", "efficiency_percent", "eta_pct", "eta_shaft", "eta_em", "eta_shaft_est", "efficiency_shaft", "ripple_pct", "t_max", "t_min", "t_coil_c", "coil_temp_c", "t_magnet_c", "magnet_temp_c", "magnet_temp_max_c", "bearing_temp_c", "t_stator_c", "t_rotor_c", "temperature_c", "t_max_c", "t_min_c", "max_temperature_c", "stator_temperature_c", "rotor_temperature_c", "magnet_temperature_c", "winding_temperature_c", "rpm", "current_arms", "i_phase_rms"}

class MotorRef(BaseModel):
    die: str
    config: str


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
    die_dir = Path(str(family._die_dir(die))).resolve(strict=True)
    cfg_path = Path(str(family._cfg_file(die, config))).resolve(strict=True)
    if die_dir not in cfg_path.parents or not cfg_path.is_file():
        raise HTTPException(404, detail="Motor configuration not found")
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
        raise HTTPException(404, detail="Motor configuration not found")
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
    die = resolved.get("canonical_die", die)
    grant_key = f"{die}/{config}"
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
        if not any(_key(m.get("die", ""), m.get("config", "")) == key for m in previous):
            project.setdefault("linked_motors", []).append({"die": die, "config": config})
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
    return {"die": die, "config": config}


def unlink_motor(project_id: str, die: str, config: str) -> None:
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


def _metric_projection(value: Any) -> dict:
    output = {}
    if not isinstance(value, dict): return output
    for key, item in value.items():
        norm = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        if norm in _METRIC_KEYS and isinstance(item, (int, float, bool)):
            output[norm] = item
    for child in value.values():
        if isinstance(child, dict):
            for key, item in _metric_projection(child).items(): output.setdefault(key, item)
    return output


def _results(die: str, config: str, cfg: dict) -> list[dict]:
    from motor_ai_sim import duty_results
    try: doc = duty_results.get(die, config)
    except Exception: doc = {}
    by_duty = doc if isinstance(doc, dict) else {}
    out = []
    for duty in cfg.get("duties") or []:
        if not isinstance(duty, dict): continue
        name = str(duty.get("name") or "")
        out.append({"duty": name, "operating_point": {k: duty[k] for k in ("rpm", "current_arms", "mode") if k in duty},
                    "electromagnetic": _metric_projection(duty.get("result") or duty.get("summary") or {}),
                    "stored_results": {str(kind): _metric_projection(row) for kind, row in (by_duty.get(name) or {}).items()
                                       if isinstance(row, dict) and _metric_projection(row)}})
    return out


@router.get("")
def list_project_motors(project_id: str, request: Request):
    user, project, role = access.require_project(request, project_id)
    from motor_ai_sim.routes import family
    results = []
    is_customer = role == "customer" and not access.is_engineer(user)
    for link in project.get("linked_motors", []):
        if not isinstance(link, dict): continue
        die, config = str(link.get("die") or ""), str(link.get("config") or "")
        row = (_index().get("motors") or {}).get(_key(die, config))
        if not isinstance(row, dict) or row.get("project_id") != project_id: continue
        open_die = die
        from motor_ai_sim.workspace import split_published_label
        raw_die, owner_label = split_published_label(die)
        ident = _identity(request)
        if owner_label:
            # Publisher's normal resolver uses the bare name; reject any workspace shadow.
            candidate = next((e for e in family._iter_die_entries() if e.get("layer") == "published" and e.get("die") == raw_die and str(e.get("owner") or "").lower() == str(ident.get("id") or ident.get("email") or "").lower()), None)
            if candidate and Path(str(family._die_dir(raw_die))).resolve() == Path(str(candidate.get("dir"))).resolve():
                open_die = raw_die
        try:
            family._require_die_access(open_die, request.headers.get("authorization"), config)
            cfg_path = Path(str(family._cfg_file(open_die, config))).resolve(strict=True)
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            available = True
        except Exception:
            cfg, available = {}, False
        item = {"die": open_die if available else die, "canonical_die": die, "config": config, "available": available, "duties": [str(x.get("name")) for x in cfg.get("duties", []) if isinstance(x, dict)],
                "default_duty": next((str(x.get("name")) for x in cfg.get("duties", []) if isinstance(x, dict)), None),
                "results": _results(open_die, config, cfg) if available else []}
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
    unlink_motor(project_id, die, config)
    store.append_audit(project_id, str(user.get("email") or ""), "motor.unlink", "motor", f"{die}/{config}")
    return {"ok": True}
