"""Project motor links using the existing per-user motor grant system."""
from __future__ import annotations

import json
import copy
import math
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


def _metric_projection(value: Any) -> dict:
    """Copy allowlisted numeric fields from this object only; never mix nested runs."""
    output = {}
    if not isinstance(value, dict): return output
    for key, item in value.items():
        norm = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
        if norm in _METRIC_KEYS and isinstance(item, (int, float, bool)):
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

    full_source = next((source for source in (summary, result)
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


def _results(die: str, config: str, cfg: dict, stored_by_duty: dict | None = None) -> list[dict]:
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
        out.append({"duty": name, "has_result": has_result,
                    "operating_point": {k: duty[k] for k in ("rpm", "current_arms", "mode", "gamma_deg") if k in duty},
                    "electromagnetic": em,
                    "stored_results": {str(kind): _metric_projection(row) for kind, row in stored_duty.items()
                                       if isinstance(row, dict) and _metric_projection(row)},
                    "comparison": _comparison_projection({**duty, "result": primary_result}, stored_duty)})
    return out


def _stored_results_index(die: str, cache: dict[str, dict]) -> dict:
    """Return a per-request die index, reading the catalog-backed store once per die."""
    if die not in cache:
        from motor_ai_sim import duty_results
        try:
            cache[die] = duty_results.index(die)
        except Exception:
            cache[die] = {}
    return cache[die]


@router.get("")
def list_project_motors(project_id: str, request: Request):
    user, project, role = access.require_project(request, project_id)
    from motor_ai_sim.routes import family
    results = []
    project_motor_index = (_index().get("motors") or {})
    # duty_results.index() reads each die's catalog-backed result store once;
    # resolving one configuration per linked motor would re-read that whole store.
    result_index = {}
    is_customer = role == "customer" and not access.is_engineer(user)
    for link in project.get("linked_motors", []):
        if not isinstance(link, dict): continue
        die, config = str(link.get("die") or ""), str(link.get("config") or "")
        row = project_motor_index.get(_key(die, config))
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
        if available:
            try:
                stored = (_stored_results_index(open_die, result_index) or {}).get(config, {})
            except Exception:
                stored = {}
        else:
            stored = {}
        item = {"die": open_die if available else die, "canonical_die": die, "config": config, "available": available, "duties": [str(x.get("name")) for x in cfg.get("duties", []) if isinstance(x, dict)],
                "default_duty": next((str(x.get("name")) for x in cfg.get("duties", []) if isinstance(x, dict)), None),
                "results": _results(open_die, config, cfg, stored) if available else []}
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
