"""Caller routes for the production module: ``/api/production/*``.

Contract: docs/PRODUCTION_MODULE_DESIGN.md section 5 and
web/src/modules/production/api.ts.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from motor_ai_sim import auth, model3d, users
from motor_ai_sim.production import store, models, access
from motor_ai_sim.production.access import (
    require_member, require_member_with_full_view,
    require_editor, require_editor_with_full_view,
    require_proposer_or_editor,
    require_procurement_editor, require_schedule_editor_or_oem,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/production", tags=["production"])


def _email(user: dict) -> str:
    return str(user.get("email") or "").strip().lower()


def _is_admin(user: dict) -> bool:
    return bool(user.get("role") == "admin")


def _now() -> str:
    return models._now()


def _production_list_for(email: str, is_admin: bool) -> list[dict]:
    root = store.production_root()
    out = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for c in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
            p = c / "production.json"
            if not p.is_file():
                continue
            rec = store._read_json(p, default={})
            if not isinstance(rec, dict) or not rec.get("die"):
                continue
            if is_admin or models.is_member(email, rec) is not None:
                out.append(rec)
    return out


def _touch(die: str, config: str) -> None:
    def mut(d):
        if not isinstance(d, dict):
            d.update(models.normalize_production({}, die, config))
        d["updated_at"] = _now()
    store.mutate_file(die, config, "production.json", mut)


def _audit(who: str, die: str, config: str, action: str, entity: str, entity_id: str, snapshot: Optional[dict] = None):
    store.append_audit(die, config, models.audit_entry(who, action, entity, entity_id, snapshot))


@router.get("")
def list_productions(request: Request):
    user = auth.resolve_user(request.headers.get("authorization"))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    return {"productions": _production_list_for(_email(user), _is_admin(user))}


# ── procurement & project schedule helpers ───────────────────────────────────

def _get_procurement_data(die: str, config: str, production: dict, today: Optional[str] = None) -> tuple[list[dict], dict]:
    bom = store.load_file(die, config, "bom.json")
    bom_rows = bom.get("rows", [])
    parts_data = store.load_file(die, config, "parts.json")
    parts = parts_data.get("parts", [])
    parts_map = {p["id"]: p for p in parts if isinstance(p, dict) and p.get("id")}

    proc_data = store.load_file(die, config, "procurement.json")
    proc_rows = proc_data.get("rows", [])
    proc_map = {r["part_id"]: r for r in proc_rows if isinstance(r, dict) and r.get("part_id")}

    sched_data = store.load_file(die, config, "schedule.json")
    phases = sched_data.get("phases", [])
    assembly_phase = next((p for p in phases if p.get("key") == "assembly" or p.get("id") == "assembly"), None)
    assembly_planned_start = assembly_phase.get("planned_start") if assembly_phase else None

    order_qty = max(0, int(production.get("order_quantity") or 0))

    # We want a row for every part in BOM (or parts list if BOM is empty)
    target_part_ids = []
    if bom_rows:
        for br in bom_rows:
            pid = str(br.get("part_id") or "")
            if pid and pid not in target_part_ids:
                target_part_ids.append(pid)
    else:
        for p in parts:
            pid = str(p.get("id") or "")
            if pid and pid not in target_part_ids:
                target_part_ids.append(pid)

    enriched_rows = []
    for pid in target_part_ids:
        bom_row = next((br for br in bom_rows if br.get("part_id") == pid), None)
        part = parts_map.get(pid)
        raw_row = proc_map.get(pid, {"part_id": pid})
        enriched = models.enrich_procurement_row(
            raw_row,
            bom_row=bom_row,
            part=part,
            order_quantity=order_qty,
            default_need_by=assembly_planned_start,
            today=today,
        )
        enriched_rows.append(enriched)

    summary = models.compute_readiness_summary(enriched_rows, assembly_planned_start=assembly_planned_start)
    return enriched_rows, summary


def _get_schedule_data(die: str, config: str, production: dict, today: Optional[str] = None) -> dict:
    sched_data = store.load_file(die, config, "schedule.json")
    phases = list(sched_data.get("phases") or [])
    if not phases:
        phases = [dict(p) for p in models.DEFAULT_SCHEDULE_PHASES]
    else:
        phases = [models.normalize_schedule_phase(p) for p in phases]

    milestones = list(sched_data.get("milestones") or [])
    if not milestones:
        milestones = [dict(m) for m in models.DEFAULT_MILESTONES]
    else:
        milestones = [models.normalize_milestone(m) for m in milestones]

    baseline = sched_data.get("baseline") or {}

    proc_rows, proc_summary = _get_procurement_data(die, config, production, today=today)
    ready_for_assembly = proc_summary.get("ready_for_assembly", False)

    # Ordering phase actual dates derived when not set
    phases = models.derive_ordering_phase(phases, proc_rows, ready_for_assembly)
    milestones = models.update_auto_milestones(milestones, phases, proc_rows, ready_for_assembly)

    enriched_phases = models.compute_critical_path_and_slack(phases)
    return {
        "phases": enriched_phases,
        "tasks": enriched_phases,
        "milestones": milestones,
        "baseline": baseline,
        "procurement_summary": proc_summary,
    }


@router.get("/{die}/timeline")
def get_die_timeline(request: Request, die: str, today: Optional[str] = None):
    user = auth.resolve_user(request.headers.get("authorization"))
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in required.")
    caller_email = _email(user)
    is_admin = _is_admin(user)

    root = store.production_root()
    die_dir = root / store._safe_slug(die)
    if not die_dir.is_dir():
        return {"die": die, "productions": []}

    productions_out = []
    for c in sorted(p for p in die_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
        prod_file = c / "production.json"
        if not prod_file.is_file():
            continue
        prod = store._read_json(prod_file, default={})
        if not isinstance(prod, dict) or not prod.get("die"):
            continue

        role = models.is_member(caller_email, prod)
        if not is_admin and role is None:
            continue
        if role == "oem" and not is_admin:
            identity = auth.caller_identity(request.headers.get("authorization"))
            rec = access._user_rec(identity.get("id") or "")
            if not model3d.can_download(identity, rec):
                continue

        config_name = str(prod.get("config") or c.name)
        rows, summary = _get_procurement_data(die, config_name, prod, today=today)
        sched = _get_schedule_data(die, config_name, prod, today=today)
        productions_out.append({
            "config": config_name,
            "title": prod.get("title"),
            "order_quantity": prod.get("order_quantity", 0),
            "status": prod.get("status", "active"),
            "procurement": {
                "rows": rows,
                "summary": summary,
            },
            "schedule": sched,
        })

    return {"die": die, "productions": productions_out}


@router.get("/{die}/{config}")
def get_production(request: Request, die: str, config: str):
    user, production = require_member_with_full_view(request, die, config)
    return production


# ── parts ────────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/parts")
def list_parts(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "parts.json")


@router.post("/{die}/{config}/parts")
async def create_part(request: Request, die: str, config: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)
    part = models.normalize_part(body)
    # Do not machine-translate part names: canonical English is kept or pending
    part["description"] = models.ensure_translated(part["description"])
    part["material_i18n"] = models.ensure_i18n_translated(part["material_i18n"])
    part["spec_i18n"] = models.ensure_i18n_translated(part["spec_i18n"])
    part["material"] = part["material_i18n"]["zh"]
    part["spec"] = part["spec_i18n"]["zh"]
    store.mutate_file(die, config, "parts.json", lambda d: d.setdefault("parts", []).append(part))
    _touch(die, config)
    _audit(_email(user), die, config, "create", "part", part["id"], {"part": part})
    return part


@router.patch("/{die}/{config}/parts/{part_id}")
async def update_part(request: Request, die: str, config: str, part_id: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)

    def mut(d):
        parts = d.setdefault("parts", [])
        for i, p in enumerate(parts):
            if isinstance(p, dict) and p.get("id") == part_id:
                # merge allowed scalar fields, replace bilingual objects
                updated = dict(p)
                for k in ("kind", "qty_per_motor", "optional_step", "revision", "parent_id"):
                    if k in body:
                        if k == "parent_id":
                            val = str(body[k]).strip() if body[k] is not None and str(body[k]).strip() else None
                            if val != part_id:
                                updated["parent_id"] = val
                        else:
                            updated[k] = body[k]
                if "material" in body or "material_i18n" in body:
                    mat_raw = body.get("material_i18n", body.get("material"))
                    mat_i18n = models.normalize_i18n_dict(mat_raw, default_zh=str(body.get("material") or ""))
                    updated["material_i18n"] = models.ensure_i18n_translated(mat_i18n)
                    updated["material"] = updated["material_i18n"]["zh"]
                if "spec" in body or "spec_i18n" in body:
                    spec_raw = body.get("spec_i18n", body.get("spec"))
                    spec_i18n = models.normalize_i18n_dict(spec_raw, default_zh=str(body.get("spec") or ""))
                    updated["spec_i18n"] = models.ensure_i18n_translated(spec_i18n)
                    updated["spec"] = updated["spec_i18n"]["zh"]
                if "name" in body:
                    updated["name"] = models.normalize_bilingual(body["name"])
                if "description" in body:
                    updated["description"] = models.ensure_translated(models.normalize_bilingual(body["description"]))
                if "drawing_files" in body:
                    updated["drawing_files"] = models.normalize_part({"drawing_files": body["drawing_files"]}).get("drawing_files", [])
                parts[i] = updated
                return
        raise HTTPException(status_code=404, detail="Part not found")

    store.mutate_file(die, config, "parts.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, "update", "part", part_id, {"patch": body})
    parts = store.load_file(die, config, "parts.json").get("parts", [])
    for p in parts:
        if p.get("id") == part_id:
            return p
    raise HTTPException(status_code=404, detail="Part not found")


@router.delete("/{die}/{config}/parts/{part_id}")
def delete_part(request: Request, die: str, config: str, part_id: str):
    user, _production = require_editor_with_full_view(request, die, config)

    def mut(d):
        before = list(d.get("parts", []))
        d["parts"] = [p for p in before if not (isinstance(p, dict) and p.get("id") == part_id)]
        if len(d["parts"]) == len(before):
            raise HTTPException(status_code=404, detail="Part not found")

    store.mutate_file(die, config, "parts.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, "delete", "part", part_id, {})
    return {}


@router.get("/{die}/{config}/assembly")
def get_assembly(request: Request, die: str, config: str):
    user, production = require_member_with_full_view(request, die, config)
    parts_data = store.load_file(die, config, "parts.json")
    parts = parts_data.get("parts", [])
    tree = models.build_assembly_tree(parts)
    from motor_ai_sim.production import access
    costs = store.load_file(die, config, "costs.json") if access.can_view_costs(user, production) else {}
    cost_rollup = models.compute_assembly_costs(parts, costs) if costs else {}
    return {
        "tree": tree,
        "parts": models.ensure_default_assembly(parts, die, config),
        "costs": cost_rollup,
    }


@router.post("/{die}/{config}/assembly/default")
def apply_default_assembly(request: Request, die: str, config: str):
    user, _production = require_editor_with_full_view(request, die, config)
    def mut(d):
        parts = d.get("parts", [])
        d["parts"] = models.ensure_default_assembly(parts, die, config)
    store.mutate_file(die, config, "parts.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, "update", "assembly", "default_tree", {})
    parts = store.load_file(die, config, "parts.json").get("parts", [])
    return {"parts": parts, "tree": models.build_assembly_tree(parts)}


# ── BOM ──────────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/bom")
def get_bom(request: Request, die: str, config: str):
    user, production = require_member_with_full_view(request, die, config)
    bom_data = store.load_file(die, config, "bom.json")
    rows = list(bom_data.get("rows", []))
    from motor_ai_sim.production import access
    if not access.can_view_costs(user, production):
        # OEM members do not receive cost fields at all (strip them server-side)
        cleaned = [
            {
                "part_id": str(r.get("part_id") or ""),
                "qty_per_motor": max(0, int(r.get("qty_per_motor") or 1)),
                "order_total": max(0, int(r.get("order_total") or 0)),
            }
            for r in rows if isinstance(r, dict)
        ]
        return {"rows": cleaned}

    costs = store.load_file(die, config, "costs.json")
    cost_map = {r["part_id"]: r for r in costs.get("rows", []) if isinstance(r, dict) and r.get("part_id")}
    order_qty = max(0, int(production.get("order_quantity") or 0))

    enriched_rows = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        pid = str(r.get("part_id") or "")
        row_dict = {
            "part_id": pid,
            "qty_per_motor": max(0, int(r.get("qty_per_motor") or 1)),
            "order_total": max(0, int(r.get("order_total") or 0)),
        }
        crow = cost_map.get(pid)
        if crow and crow.get("unit_price") is not None:
            try:
                up = float(crow["unit_price"])
                row_dict["unit_price"] = up
                row_dict["line_total"] = round(up * order_qty, 2)
            except (ValueError, TypeError):
                row_dict["unit_price"] = None
                row_dict["line_total"] = None
        else:
            row_dict["unit_price"] = None
            row_dict["line_total"] = None
        row_dict["currency"] = str(crow.get("currency") or "CNY") if crow else "CNY"
        row_dict["note"] = crow.get("note") if crow else None
        enriched_rows.append(row_dict)

    summary = models.compute_cost_summary(
        enriched_rows,
        costs.get("rows", []),
        costs.get("extras", []),
        costs.get("margin_pct", 0.0),
        order_qty,
    )
    return {
        "rows": enriched_rows,
        "costs": costs,
        "summary": summary,
    }


@router.put("/{die}/{config}/bom")
async def save_bom(request: Request, die: str, config: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)
    rows = body.get("rows") if isinstance(body, dict) else []
    parts = store.load_file(die, config, "parts.json").get("parts", [])
    part_ids = {p.get("id") for p in parts if isinstance(p, dict)}
    cleaned = models.normalize_bom_rows(rows, part_ids)
    store.save_file(die, config, "bom.json", {"rows": cleaned})
    _touch(die, config)
    _audit(_email(user), die, config, "update", "bom", "bom", {"rows": cleaned})
    return {}


# ── costs ────────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/costs")
def get_costs(request: Request, die: str, config: str):
    user, production = require_member_with_full_view(request, die, config)
    from motor_ai_sim.production import access
    if not access.can_view_costs(user, production):
        raise HTTPException(status_code=403, detail="Cost access restricted")

    costs = store.load_file(die, config, "costs.json")
    bom = store.load_file(die, config, "bom.json")
    bom_rows = bom.get("rows", [])
    order_qty = max(0, int(production.get("order_quantity") or 0))

    cost_map = {r["part_id"]: r for r in costs.get("rows", []) if isinstance(r, dict) and r.get("part_id")}
    rows_out = []
    for br in bom_rows:
        if not isinstance(br, dict):
            continue
        pid = str(br.get("part_id") or "")
        crow = cost_map.get(pid)
        if crow:
            nr = models.normalize_cost_row(crow)
            nr["part_id"] = pid
            rows_out.append(nr)
        else:
            rows_out.append({"part_id": pid, "unit_price": None, "currency": "CNY", "note": None})

    summary = models.compute_cost_summary(
        bom_rows,
        rows_out,
        costs.get("extras", []),
        costs.get("margin_pct", 0.0),
        order_qty,
    )
    return {
        "rows": rows_out,
        "extras": costs.get("extras", []),
        "margin_pct": float(costs.get("margin_pct", 0.0)),
        "summary": summary,
    }


@router.put("/{die}/{config}/costs")
async def save_costs(request: Request, die: str, config: str):
    user, production = require_editor_with_full_view(request, die, config)
    from motor_ai_sim.production import access
    if not access.can_edit_costs(user, production):
        raise HTTPException(status_code=403, detail="Editor access required")

    body = await _json_or_form(request)
    costs = models.normalize_costs(body)
    for extra in costs["extras"]:
        extra["name"] = models.ensure_translated(extra["name"])

    store.save_file(die, config, "costs.json", costs)
    _touch(die, config)
    _audit(_email(user), die, config, "update", "costs", "costs", {"costs": costs})

    bom = store.load_file(die, config, "bom.json")
    bom_rows = bom.get("rows", [])
    order_qty = max(0, int(production.get("order_quantity") or 0))
    summary = models.compute_cost_summary(
        bom_rows,
        costs["rows"],
        costs["extras"],
        costs["margin_pct"],
        order_qty,
    )
    return {
        **costs,
        "summary": summary,
    }


# ── procurement & project timeline ───────────────────────────────────────────

@router.get("/{die}/{config}/procurement")
def get_procurement(request: Request, die: str, config: str, today: Optional[str] = None):
    user, production = require_member_with_full_view(request, die, config)
    rows, summary = _get_procurement_data(die, config, production, today=today)
    return {"rows": rows, "summary": summary}


@router.put("/{die}/{config}/procurement/{part_id}")
async def save_procurement_line(request: Request, die: str, config: str, part_id: str):
    user, production = require_procurement_editor(request, die, config)
    body = await _json_or_form(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object.")
    body["part_id"] = part_id

    normalized = models.normalize_procurement_line(body)
    normalized["part_id"] = part_id
    normalized["updated_by"] = _email(user)
    normalized["updated_at"] = _now()

    old_row = None
    def mut(data: dict):
        nonlocal old_row
        rows = data.setdefault("rows", [])
        found = False
        for i, r in enumerate(rows):
            if isinstance(r, dict) and r.get("part_id") == part_id:
                old_row = dict(r)
                rows[i] = normalized
                found = True
                break
        if not found:
            rows.append(normalized)

    store.mutate_file(die, config, "procurement.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, "update", "procurement", part_id, {"old": old_row, "new": normalized})
    return {"row": normalized}


@router.get("/{die}/{config}/schedule")
def get_schedule(request: Request, die: str, config: str, today: Optional[str] = None):
    user, production = require_member_with_full_view(request, die, config)
    return _get_schedule_data(die, config, production, today=today)


@router.put("/{die}/{config}/schedule")
async def save_schedule(request: Request, die: str, config: str):
    user, production, is_full_editor = access.require_schedule_editor_or_oem(request, die, config)
    body = await _json_or_form(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object.")

    existing_sched = store.load_file(die, config, "schedule.json")
    existing_phases = list(existing_sched.get("phases") or [])
    if not existing_phases:
        existing_phases = [dict(p) for p in models.DEFAULT_SCHEDULE_PHASES]
    else:
        existing_phases = [models.normalize_schedule_phase(p) for p in existing_phases]

    existing_milestones = list(existing_sched.get("milestones") or [])
    if not existing_milestones:
        existing_milestones = [dict(m) for m in models.DEFAULT_MILESTONES]
    else:
        existing_milestones = [models.normalize_milestone(m) for m in existing_milestones]

    existing_baseline = existing_sched.get("baseline") or {}

    if not is_full_editor:
        # OEM member: may only set actual_start, actual_end, and status of ordering, assembly, testing
        ALLOWED_OEM_PHASES = {"ordering", "assembly", "testing"}

        # OEM cannot edit baseline
        if "baseline" in body and body["baseline"] != existing_baseline:
            raise HTTPException(status_code=403, detail="OEM members cannot edit schedule baseline.")
        # OEM cannot edit milestones
        if "milestones" in body and body["milestones"] != existing_milestones:
            raise HTTPException(status_code=403, detail="OEM members cannot edit milestones.")

        incoming_phases = body.get("phases") or body.get("tasks") or []
        if not isinstance(incoming_phases, list):
            raise HTTPException(status_code=422, detail="Phases must be a list.")

        phase_lookup = {p["id"]: p for p in existing_phases}
        phase_key_lookup = {p.get("key", p["id"]): p for p in existing_phases}

        for inc in incoming_phases:
            if not isinstance(inc, dict):
                continue
            inc_id = str(inc.get("id") or inc.get("key") or "")
            target = phase_lookup.get(inc_id) or phase_key_lookup.get(inc_id)
            if not target:
                continue
            target_key = target.get("key") or target.get("id")
            if target_key not in ALLOWED_OEM_PHASES:
                # Disallow modifying non-allowed phases
                for forbidden_field in ("actual_start", "actual_end", "status", "planned_start", "planned_end", "depends_on", "title", "percent_complete", "owner_role", "note"):
                    if forbidden_field in inc and inc[forbidden_field] != target.get(forbidden_field):
                        raise HTTPException(
                            status_code=403,
                            detail=f"OEM members may not modify phase '{target_key}'. Only Ordering, Assembly, and Testing actuals may be edited.",
                        )
            else:
                # Disallow modifying planned_start, planned_end, depends_on, title, owner_role
                for forbidden_field in ("planned_start", "planned_end", "depends_on", "title", "owner_role"):
                    if forbidden_field in inc and inc[forbidden_field] != target.get(forbidden_field):
                        raise HTTPException(
                            status_code=403,
                            detail=f"OEM members cannot modify {forbidden_field} on phase '{target_key}'.",
                        )
                # Apply allowed updates: actual_start, actual_end, status
                if "actual_start" in inc:
                    target["actual_start"] = models.validate_iso_date(inc["actual_start"], "actual_start") or ""
                if "actual_end" in inc:
                    target["actual_end"] = models.validate_iso_date(inc["actual_end"], "actual_end") or ""
                if "status" in inc:
                    st = str(inc["status"] or "").strip()
                    if st in ("not_started", "in_progress", "done", "blocked"):
                        target["status"] = st
                if "percent_complete" in inc:
                    try:
                        target["percent_complete"] = max(0, min(100, int(inc["percent_complete"] or 0)))
                    except (ValueError, TypeError):
                        pass

        saved_phases = existing_phases
        saved_milestones = existing_milestones
        saved_baseline = existing_baseline
        action = "schedule_oem_update"
    else:
        # Full editor
        raw_phases = body.get("phases") or body.get("tasks")
        if raw_phases is not None:
            saved_phases = [models.normalize_schedule_phase(p) for p in raw_phases if isinstance(p, dict)]
        else:
            saved_phases = existing_phases

        raw_milestones = body.get("milestones")
        if raw_milestones is not None:
            saved_milestones = [models.normalize_milestone(m) for m in raw_milestones if isinstance(m, dict)]
        else:
            saved_milestones = existing_milestones

        saved_baseline = body.get("baseline") if "baseline" in body else existing_baseline
        action = "schedule_update"

    models.detect_cycle(saved_phases)
    store.save_file(die, config, "schedule.json", {
        "phases": saved_phases,
        "milestones": saved_milestones,
        "baseline": saved_baseline,
    })
    _touch(die, config)
    _audit(_email(user), die, config, action, "schedule", "schedule", {"phases": saved_phases, "milestones": saved_milestones})
    return _get_schedule_data(die, config, production)


@router.post("/{die}/{config}/schedule/baseline")
def freeze_schedule_baseline(request: Request, die: str, config: str):
    user, _production = require_editor_with_full_view(request, die, config)
    sched_data = store.load_file(die, config, "schedule.json")
    phases = sched_data.get("phases") or list(models.DEFAULT_SCHEDULE_PHASES)
    milestones = sched_data.get("milestones") or list(models.DEFAULT_MILESTONES)

    baseline = {
        "phases": [
            {
                "id": p["id"],
                "key": p.get("key"),
                "planned_start": p.get("planned_start", ""),
                "planned_end": p.get("planned_end", ""),
            }
            for p in phases if isinstance(p, dict)
        ],
        "milestones": [
            {
                "id": m["id"],
                "date": m.get("date", ""),
            }
            for m in milestones if isinstance(m, dict)
        ],
        "frozen_at": _now(),
        "frozen_by": _email(user),
    }

    sched_data["baseline"] = baseline
    store.save_file(die, config, "schedule.json", sched_data)
    _touch(die, config)
    _audit(_email(user), die, config, "freeze_baseline", "schedule", "baseline", {"baseline": baseline})
    return {"baseline": baseline}


# ── process ──────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/process")
def get_process(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "process.json")


@router.put("/{die}/{config}/process")
async def save_process(request: Request, die: str, config: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)
    sections = body.get("sections") if isinstance(body, dict) else []
    if not isinstance(sections, list):
        sections = []
    cleaned = [models.normalize_process_section(s) for s in sections]
    for sec in cleaned:
        sec["title"] = models.ensure_translated(sec["title"])
        sec["body"] = models.ensure_translated(sec["body"])
    store.save_file(die, config, "process.json", {"sections": cleaned})
    _touch(die, config)
    _audit(_email(user), die, config, "update", "process", "process", {"sections": cleaned})
    return {}


# ── documents / files ────────────────────────────────────────────────────────

@router.get("/{die}/{config}/documents")
def list_documents(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "documents.json")


@router.post("/{die}/{config}/documents")
async def upload_document(request: Request, die: str, config: str):
    # every member may upload (owner 2026-10-17: the factory sends photos, test reports, anything it needs;
    # we sort the documents out ourselves).  Design data (parts / BOM / process) stays editor-only.
    user, _production = require_member_with_full_view(request, die, config)
    form = await request.form()
    uploaded = form.get("file")
    if uploaded is None:
        raise HTTPException(status_code=400, detail="No file uploaded")
    data = await uploaded.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    file_id = secrets.token_urlsafe(16)
    store.save_uploaded_file(die, config, file_id, data)
    part_id = form.get("part_id")
    if part_id and not isinstance(part_id, str):
        part_id = str(part_id)
    doc = models.normalize_document({}, file_id, _email(user), str(uploaded.filename or "unnamed"), part_id if part_id else None)
    store.mutate_file(die, config, "documents.json", lambda d: d.setdefault("documents", []).append(doc))
    _touch(die, config)
    _audit(_email(user), die, config, "create", "document", doc["id"], {"doc": doc})
    return doc


@router.get("/{die}/{config}/files/{file_id}")
def download_file(request: Request, die: str, config: str, file_id: str):
    """Download one production file.  Logged in the SAME traced download log as the full 3-D / STEP files
    (model3d.log_download: who, company, NDA basis, sha256, IP) plus the production audit."""
    import datetime
    from motor_ai_sim import model3d as M
    if not store.valid_file_id(file_id):
        raise HTTPException(status_code=404, detail="File not found")
    user, _production = require_member_with_full_view(request, die, config)
    path = store.read_uploaded_file(die, config, file_id)
    if path is None:
        raise HTTPException(status_code=404, detail="File not found")
    data = path.read_bytes()
    name = _original_name(die, config, file_id) or file_id
    rec = users.get_user(_email(user)) or {}
    basis = M.nda_basis(user, rec)
    who_for = M.recipient_of(user, rec, basis)
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        from motor_ai_sim.client_ip import from_request
        ip = from_request(request)
    except Exception:                                       # noqa: BLE001
        ip = "?"
    try:
        M.log_download(dict(download_id=M.new_download_id(now), user=user.get("id"), email=who_for["email"],
                            name=M.customer_name(user, rec), role=user.get("role"), company=who_for["company"],
                            nda=basis["type"] != "internal", nda_basis=basis, die=die, config=config,
                            motor_id="%s/%s" % (die, config), kind="production_file", file=name, source_file=file_id,
                            sha256=M.sha256_bytes(data), bytes=len(data), ip=ip,
                            user_agent=request.headers.get("user-agent", "")[:300]))
    except OSError as ex:
        log.error("production: download log not writable (%s) - download refused", ex)
        raise HTTPException(status_code=503, detail="Download log unavailable; try again later.")
    _audit(_email(user), die, config, "download", "document", file_id, {"file": name})
    safe = M.ascii_text(name, 120).replace('"', "") or file_id
    from urllib.parse import quote
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (safe, quote(name)),
                             "Cache-Control": "no-store"})


def _original_name(die: str, config: str, file_id: str) -> Optional[str]:
    """The uploader's file name of a document or a message attachment (None when unknown)."""
    for d in (store.load_file(die, config, "documents.json") or {}).get("documents", []):
        if d.get("file_id") == file_id or d.get("id") == file_id:
            return d.get("name") or d.get("filename")
    for t in (store.load_file(die, config, "threads.json") or {}).get("threads", []):
        for m in t.get("messages", []):
            for a in m.get("attachments", []) or []:
                if a.get("file_id") == file_id:
                    return a.get("name")
    return None


# ── threads / messages ───────────────────────────────────────────────────────

@router.get("/{die}/{config}/threads")
def list_threads(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "threads.json")


@router.post("/{die}/{config}/threads")
async def create_thread(request: Request, die: str, config: str):
    user, _production = require_member_with_full_view(request, die, config)
    body = await _json_or_form(request)
    part_id = body.get("part_id") if isinstance(body, dict) else None
    thread = models.normalize_thread({"part_id": part_id})
    store.mutate_file(die, config, "threads.json", lambda d: d.setdefault("threads", []).append(thread))
    _touch(die, config)
    _audit(_email(user), die, config, "create", "thread", thread["id"], {"thread": thread})
    return thread


@router.post("/{die}/{config}/threads/{thread_id}/messages")
async def post_message(request: Request, die: str, config: str, thread_id: str):
    user, _production = require_member_with_full_view(request, die, config)
    text: Optional[dict] = None
    attachments: list[dict] = []
    src_lang = "en"

    content_type = request.headers.get("content-type") or ""
    if "multipart/form-data" in content_type:
        form = await request.form()
        text_raw = form.get("text")
        if isinstance(text_raw, str):
            try:
                import json as _json
                text = _json.loads(text_raw)
            except Exception:                               # noqa: BLE001
                text = None
        src_lang = str(form.get("src_lang") or "en")
        for key in form:
            val = form[key]
            if key == "files" and hasattr(val, "read"):
                data = await val.read()
                if data:
                    file_id = secrets.token_urlsafe(16)
                    store.save_uploaded_file(die, config, file_id, data)
                    attachments.append({"file_id": file_id, "name": str(val.filename or "attachment")})
    else:
        body = await _json_or_form(request)
        if isinstance(body, dict):
            text = body.get("text")
            src_lang = str(body.get("src_lang") or "en")

    if not isinstance(text, dict):
        raise HTTPException(status_code=400, detail="Message text required")

    message = models.normalize_message({
        "author_email": _email(user),
        "src_lang": src_lang,
        "text": text,
        "attachments": attachments,
    })
    message["text"] = models.ensure_translated(message["text"])

    def mut(d):
        for t in d.setdefault("threads", []):
            if isinstance(t, dict) and t.get("id") == thread_id:
                t.setdefault("messages", []).append(message)
                return
        raise HTTPException(status_code=404, detail="Thread not found")

    store.mutate_file(die, config, "threads.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, "create", "thread_message", message["id"], {"thread_id": thread_id})
    return _find_thread(die, config, thread_id)


def _find_thread(die: str, config: str, thread_id: str) -> dict:
    for t in store.load_file(die, config, "threads.json").get("threads", []):
        if isinstance(t, dict) and t.get("id") == thread_id:
            return t
    raise HTTPException(status_code=404, detail="Thread not found")


# ── proposals ────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/proposals")
def list_proposals(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "proposals.json")


@router.post("/{die}/{config}/proposals")
async def create_proposal(request: Request, die: str, config: str):
    user, production = require_member_with_full_view(request, die, config)
    is_admin = _is_admin(user)
    if not models.is_member(_email(user), production) and not is_admin:
        raise HTTPException(status_code=404, detail="Production not found")
    body = await _json_or_form(request)
    part_id = body.get("part_id") if isinstance(body, dict) else None
    text = body.get("text") if isinstance(body, dict) else {}
    if not isinstance(text, dict):
        raise HTTPException(status_code=400, detail="Proposal text required")
    proposal = models.normalize_proposal({
        "part_id": part_id,
        "author_email": _email(user),
        "text": text,
    })
    proposal["text"] = models.ensure_translated(proposal["text"])
    store.mutate_file(die, config, "proposals.json", lambda d: d.setdefault("proposals", []).append(proposal))
    _touch(die, config)
    _audit(_email(user), die, config, "create", "proposal", proposal["id"], {"proposal": proposal})
    return proposal


async def _decide_proposal(request: Request, die: str, config: str, proposal_id: str, action: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)
    note = body.get("note") if isinstance(body, dict) else None
    if note is not None and not isinstance(note, dict):
        note = None

    def mut(d):
        for p in d.setdefault("proposals", []):
            if isinstance(p, dict) and p.get("id") == proposal_id:
                p["status"] = action
                p["resolved_at"] = _now()
                if note:
                    p["decision_note"] = models.ensure_translated(models.normalize_bilingual(note))
                else:
                    p["decision_note"] = None
                if action == "implemented":
                    p["linked_revision"] = _publish_revision_from_proposal(die, config, user, p)
                return
        raise HTTPException(status_code=404, detail="Proposal not found")

    store.mutate_file(die, config, "proposals.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, action, "proposal", proposal_id, {"note": note})
    proposals = store.load_file(die, config, "proposals.json").get("proposals", [])
    for p in proposals:
        if p.get("id") == proposal_id:
            return p
    raise HTTPException(status_code=404, detail="Proposal not found")


@router.post("/{die}/{config}/proposals/{proposal_id}/accept")
async def accept_proposal(request: Request, die: str, config: str, proposal_id: str):
    return await _decide_proposal(request, die, config, proposal_id, "accepted")


@router.post("/{die}/{config}/proposals/{proposal_id}/reject")
async def reject_proposal(request: Request, die: str, config: str, proposal_id: str):
    return await _decide_proposal(request, die, config, proposal_id, "rejected")


@router.post("/{die}/{config}/proposals/{proposal_id}/implement")
async def implement_proposal(request: Request, die: str, config: str, proposal_id: str):
    return await _decide_proposal(request, die, config, proposal_id, "implemented")


# ── revisions ────────────────────────────────────────────────────────────────

@router.get("/{die}/{config}/revisions")
def list_revisions(request: Request, die: str, config: str):
    require_member_with_full_view(request, die, config)
    return store.load_file(die, config, "revisions.json")


@router.post("/{die}/{config}/revisions")
async def publish_revision(request: Request, die: str, config: str):
    user, _production = require_editor_with_full_view(request, die, config)
    body = await _json_or_form(request)
    summary = body.get("summary") if isinstance(body, dict) else {}
    changes = body.get("changes") if isinstance(body, dict) else []
    if not isinstance(summary, dict):
        raise HTTPException(status_code=400, detail="Revision summary required")
    if not isinstance(changes, list):
        changes = []
    summary_norm = models.ensure_translated(models.normalize_bilingual(summary))
    changes_norm = [models.ensure_translated(models.normalize_bilingual(c)) for c in changes]
    revision = _publish_revision(die, config, user, summary_norm, changes_norm)
    return revision


@router.post("/{die}/{config}/revisions/{rev_number}/ack")
async def ack_revision(request: Request, die: str, config: str, rev_number: str):
    user, _production = require_member_with_full_view(request, die, config)

    def mut(d):
        for r in d.setdefault("revisions", []):
            if isinstance(r, dict) and str(r.get("number")) == rev_number:
                acks = r.setdefault("acknowledgements", [])
                acks = [a for a in acks if not (isinstance(a, dict) and a.get("email") == _email(user))]
                acks.append({"email": _email(user), "time": _now()})
                r["acknowledgements"] = acks
                return
        raise HTTPException(status_code=404, detail="Revision not found")

    store.mutate_file(die, config, "revisions.json", mut)
    _touch(die, config)
    _audit(_email(user), die, config, "ack_revision", "revision", rev_number, {})
    revisions = store.load_file(die, config, "revisions.json").get("revisions", [])
    for r in revisions:
        if str(r.get("number")) == rev_number:
            return r
    raise HTTPException(status_code=404, detail="Revision not found")


def _publish_revision(die: str, config: str, user: dict, summary: dict, changes: list) -> dict:
    existing = store.load_file(die, config, "revisions.json").get("revisions", [])
    number = models.next_revision_number(existing)
    revision = models.normalize_revision({
        "number": number,
        "author_email": _email(user),
        "summary": summary,
        "changes": changes,
    })
    store.mutate_file(die, config, "revisions.json", lambda d: d.setdefault("revisions", []).append(revision))
    store.mutate_file(die, config, "production.json", lambda d: d.update({"current_revision": number, "updated_at": _now()}))
    _touch(die, config)
    _audit(_email(user), die, config, "publish_revision", "revision", number, {"revision": revision})
    return revision


def _publish_revision_from_proposal(die: str, config: str, user: dict, proposal: dict) -> str:
    summary = proposal.get("text") or models._machine_fill("Implemented proposal", "en")
    revision = _publish_revision(die, config, user, summary, [])
    return revision["number"]


# ── translation helpers ──────────────────────────────────────────────────────

@router.post("/{die}/{config}/translate/retry")
async def retry_translation(request: Request, die: str, config: str):
    user, _production = require_member_with_full_view(request, die, config)
    ref = await _json_or_form(request)
    if not isinstance(ref, dict):
        raise HTTPException(status_code=400, detail="Translation reference required")
    _apply_translation(die, config, ref, retry=True)
    _audit(_email(user), die, config, "translate_retry", ref.get("entity", ""), ref.get("entity_id", ""), {"ref": ref})
    return {}


@router.post("/{die}/{config}/translate/confirm")
async def confirm_translation(request: Request, die: str, config: str):
    user, _production = require_member_with_full_view(request, die, config)
    body = await _json_or_form(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Translation reference required")
    lang = str(body.get("lang") or "en")
    text = body.get("text")
    if text is not None:
        text = str(text)
    ref = {k: body[k] for k in ("entity", "entity_id", "field", "message_id") if k in body}
    if text is not None and str(ref.get("entity") or "") in _DESIGN_ENTITIES:
        # supplying new text for a part / process / production / revision field is a DESIGN change: editors only
        # (an OEM proposes; confirming an existing machine translation without new text stays open to members)
        require_editor_with_full_view(request, die, config)
    _apply_translation(die, config, ref, lang=lang, text=text)
    _audit(_email(user), die, config, "translate_confirm", ref.get("entity", ""), ref.get("entity_id", ""), {"ref": ref, "lang": lang})
    return {}


_DESIGN_ENTITIES = frozenset({"production", "part", "process", "revision"})


def _do_translate_or_confirm(bi: dict, retry: bool, lang: str, text: Optional[str]):
    if not isinstance(bi, dict) or "src_lang" not in bi:
        raise HTTPException(status_code=400, detail="Field is not bilingual")
    if retry:
        src_lang = bi.get("src_lang") or "en"
        other = "zh" if src_lang == "en" else "en"
        from motor_ai_sim.production import translate as T
        try:
            translated = T.translate_text(bi[src_lang], src_lang, other)
            bi[other] = translated
            bi.setdefault("status", {})[other] = "machine"
        except Exception:                               # noqa: BLE001
            pass
    else:
        if text is not None:
            bi[lang] = str(text)
            bi.setdefault("status", {})[lang] = "confirmed"
        elif bi.get("status", {}).get(lang) in ("machine", "pending") and bi.get(lang):
            bi.setdefault("status", {})[lang] = "confirmed"


def _apply_translation(die: str, config: str, ref: dict, *, retry: bool = False, lang: str = "en", text: Optional[str] = None):
    entity = str(ref.get("entity") or "")
    entity_id = str(ref.get("entity_id") or "")
    field = str(ref.get("field") or "")
    message_id = ref.get("message_id")

    if entity == "production":
        def mut(prod):
            bi = prod.get(field)
            _do_translate_or_confirm(bi, retry, lang, text)
        store.mutate_file(die, config, "production.json", mut)
    elif entity == "part":
        def mut(d):
            for p in d.setdefault("parts", []):
                if p.get("id") == entity_id:
                    bi = p.get(field)
                    _do_translate_or_confirm(bi, retry, lang, text)
                    return
            raise HTTPException(status_code=404, detail="Part not found")
        store.mutate_file(die, config, "parts.json", mut)
    elif entity == "process":
        def mut(d):
            for s in d.setdefault("sections", []):
                if s.get("id") == entity_id:
                    bi = s.get(field)
                    _do_translate_or_confirm(bi, retry, lang, text)
                    return
            raise HTTPException(status_code=404, detail="Process section not found")
        store.mutate_file(die, config, "process.json", mut)
    elif entity == "thread":
        def mut(d):
            for th in d.setdefault("threads", []):
                if th.get("id") == entity_id:
                    for msg in th.get("messages", []):
                        if msg.get("id") == message_id:
                            bi = msg.get(field)
                            _do_translate_or_confirm(bi, retry, lang, text)
                            return
                    raise HTTPException(status_code=404, detail="Message not found")
            raise HTTPException(status_code=404, detail="Thread not found")
        store.mutate_file(die, config, "threads.json", mut)
    elif entity == "proposal":
        def mut(d):
            for p in d.setdefault("proposals", []):
                if p.get("id") == entity_id:
                    bi = p.get(field)
                    _do_translate_or_confirm(bi, retry, lang, text)
                    return
            raise HTTPException(status_code=404, detail="Proposal not found")
        store.mutate_file(die, config, "proposals.json", mut)
    elif entity == "revision":
        def mut(d):
            for r in d.setdefault("revisions", []):
                if r.get("number") == entity_id:
                    head, _, idx = field.partition(".")
                    if idx:
                        items = r.get(head)
                        if not isinstance(items, list) or not idx.isdigit() or int(idx) >= len(items):
                            raise HTTPException(status_code=404, detail="Field not found")
                        bi = items[int(idx)]
                    else:
                        bi = r.get(field)
                    _do_translate_or_confirm(bi, retry, lang, text)
                    return
            raise HTTPException(status_code=404, detail="Revision not found")
        store.mutate_file(die, config, "revisions.json", mut)
    else:
        raise HTTPException(status_code=400, detail="Unknown entity")


async def _json_or_form(request: Request):
    ctype = request.headers.get("content-type") or ""
    if "multipart/form-data" in ctype:
        form = await request.form()
        return {k: form[k] for k in form}
    try:
        return await request.json()
    except Exception:                               # noqa: BLE001
        return {}


# -- assembly process (owner 2026-10-17: the Assembly tab shows the PROCESS, step by step) ------------------------------

@router.get("/{die}/{config}/assembly_process")
def get_assembly_process(request: Request, die: str, config: str):
    from motor_ai_sim.production import process_steps as PS
    access.require_member(request, die, config)
    parts = store.load_file(die, config, "parts.json").get("parts") or []
    return PS.view(store.load_file(die, config, "assembly_process.json"), parts)


@router.put("/{die}/{config}/assembly_process")
async def save_assembly_process(request: Request, die: str, config: str):
    from motor_ai_sim.production import process_steps as PS
    user, _prod = access.require_editor(request, die, config)
    body = await _json_or_form(request)
    try:
        steps = PS.validate_steps((body or {}).get("steps"))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    old = store.load_file(die, config, "assembly_process.json")
    store.save_file(die, config, "assembly_process.json", {"steps": steps})
    _audit(_email(user), die, config, "update", "assembly_process", "steps",
           {"from": len(old.get("steps") or []), "to": len(steps)})
    parts = store.load_file(die, config, "parts.json").get("parts") or []
    return PS.view({"steps": steps}, parts)
