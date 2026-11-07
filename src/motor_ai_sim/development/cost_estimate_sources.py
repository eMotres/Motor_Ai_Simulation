"""Data the simple cost-estimate ``suggest`` works from (owner 2026-11-02): per built motor (Production) its per-motor cost
and its tooling sum, the active mass of every motor from the stored simulation masses (``motor_mass.app_motor_mass``),
and the estimates of OTHER projects (their testing and shipping values).

Only OUR cost is read (BOM + work lines per motor, the tooling list): never the margin and never the price.  Read-only:
nothing here writes anything.  Every reader fails soft (a broken production or project is left out), because a
suggestion must never break the estimate."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: the production cost lists: (list, BOM file, costs file).  Motor list: ``unit_price`` is the cost of the line PER MOTOR.
#: Tooling list: ``unit_price`` is the price of ONE piece and the line costs price x quantity (as ``production.routes``).
_LISTS = (("motor", "bom.json", "costs.json"), ("tooling", "bom_tooling.json", "costs_tooling.json"))


def active_mass(die: str, config: str) -> Optional[float]:
    """The active mass [kg] of one motor from the app's mass model (the stored simulation masses); ``None`` when it
    cannot be read (unknown, never zero)."""
    if not die or not config:
        return None
    try:
        from motor_ai_sim.modules import motor_mass as MM
        value = MM.app_motor_mass(die, config).components.get("active")
        return round(float(value), 4) if value and float(value) > 0 else None
    except Exception:                                       # noqa: BLE001 - no mass is "unknown", not zero
        log.debug("cost estimate: no active mass for %s/%s", die, config, exc_info=True)
        return None


def shipping_mass(die: str, config: str) -> Dict[str, Any]:
    """The masses a shipment is weighed with (``motor_mass.mass_summary``): ``{full_kg, complete, active_kg}``; the full mass
    counts as complete only when it is not partial.  Empty values when the die cannot be read (unknown, never zero)."""
    if not die or not config:
        return {"full_kg": None, "complete": False, "active_kg": None}
    try:
        from motor_ai_sim.modules import motor_mass as MM
        s = MM.mass_summary(die, config) or {}
        return {"full_kg": s.get("full_kg"), "complete": bool(s) and not s.get("partial"), "active_kg": s.get("active_kg")}
    except Exception:                                       # noqa: BLE001 - no mass is "unknown", not zero
        log.debug("cost estimate: no full mass for %s/%s", die, config, exc_info=True)
        return {"full_kg": None, "complete": False, "active_kg": None}


def cost_summary(die: str, config: str, list_name: str) -> Dict[str, Any]:
    """The Production record's cost summary of one list, as the Production costs tab computes it
    (``models.compute_cost_summary``): ``{"cost": {currency: cost per motor (motor list) or the list's sum (tooling)},
    "missing_prices": n}``.  ``cost_per_motor`` = parts + work lines; the margin and the price are never read."""
    from motor_ai_sim.production import models as PM
    from motor_ai_sim.production import store as PS
    bom_file, costs_file = next((b, c) for n, b, c in _LISTS if n == list_name)
    bom_rows = [r for r in (PS.load_file(die, config, bom_file) or {}).get("rows", []) if isinstance(r, dict)]
    costs = PS.load_file(die, config, costs_file) or {}
    cost_rows = [PM.normalize_cost_row(r) for r in costs.get("rows") or [] if isinstance(r, dict)]
    if list_name == "tooling":
        qty = {str(b.get("part_id")): max(0, int(b.get("qty_per_motor") or 1)) for b in bom_rows}
        cost_rows = [{**r, "unit_price": float(r["unit_price"]) * qty[r["part_id"]]}
                     if r.get("unit_price") is not None and r.get("part_id") in qty else r for r in cost_rows]
    summary = PM.compute_cost_summary(bom_rows, cost_rows, costs.get("extras") or [], 0.0, 1)
    cost = {str(c["currency"]): float(c["cost_per_motor"]) for c in summary.get("by_currency") or []
            if c.get("cost_per_motor")}
    return {"cost": cost, "missing_prices": int(summary.get("missing_prices_count") or 0)}


def productions(mass_of=active_mass) -> List[Dict[str, Any]]:
    """Every production with a per-motor cost or a tooling sum: ``{ref: "die/config", title, updated_at, active_mass_kg,
    cost_per_motor: {cur: amount}, tooling: {cur: amount}, missing_prices}``."""
    from motor_ai_sim.production import store as PS
    root = PS.production_root()
    out: List[Dict[str, Any]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*/*/production.json")):
        try:
            rec = PS._read_json(path, default={})
            if not isinstance(rec, dict) or not rec.get("die") or not rec.get("config"):
                continue
            die, config = str(rec["die"]), str(rec["config"])
            motor = cost_summary(die, config, "motor")
            tooling = cost_summary(die, config, "tooling")
            if not motor["cost"] and not tooling["cost"]:
                continue
            title = rec.get("title") if isinstance(rec.get("title"), dict) else {}
            out.append({"ref": f"{die}/{config}", "title": str(title.get("en") or title.get("zh") or f"{die} {config}"),
                        "updated_at": rec.get("updated_at"), "active_mass_kg": mass_of(die, config),
                        "cost_per_motor": motor["cost"], "tooling": tooling["cost"],
                        "missing_prices": motor["missing_prices"]})
        except Exception:                                   # noqa: BLE001 - one broken production never hides the rest
            log.warning("cost estimate: production %s unreadable", path, exc_info=True)
    return out


def other_estimates(exclude_project: str, schema: dict) -> List[Dict[str, Any]]:
    """The stored cost estimates of every OTHER project, in the 4-line shape (an old 68-line one converted on read)."""
    from motor_ai_sim.development import cost_estimate as CE
    from motor_ai_sim.development import cost_estimate_store as CS
    from motor_ai_sim.development import store as DS
    out: List[Dict[str, Any]] = []
    for project in DS.list_projects():
        pid = str(project.get("id") or "")
        if not pid or pid == exclude_project:
            continue
        try:
            raw = CS.load(pid)
            if raw is None:
                continue
            est = CE.from_stored(raw, schema, pid)
        except Exception:                                   # noqa: BLE001
            continue
        out.append({"project_id": pid, "title": str(project.get("title") or pid), "updated_at": est.get("updated_at"),
                    "iterations": est.get("iterations") or []})
    return out


def ship_data(estimate: dict, schema: dict, *, mass_of=active_mass, ship_mass_of=None) -> Dict[str, Any]:
    """Only what a shipment's weight needs: ``{target_mass_kg, motor_mass_kg, motor_mass_basis}`` for the estimate's target motor
    (the complete mass when known, else the active mass x 1.3).  Cheap (no production or other-estimate scan): the save and the
    preview of the automatic DHL lines use it."""
    from motor_ai_sim.development import cost_estimate as CE
    target = (estimate.get("settings") or {}).get("target_motor") or {}
    die, config = str(target.get("die") or ""), str(target.get("config") or "")
    target_mass = mass_of(die, config) if die and config else None
    masses = (ship_mass_of or shipping_mass)(die, config) if die and config else {}
    motor_mass, basis = CE.motor_mass_for_shipping(masses.get("full_kg"), bool(masses.get("complete")),
                                                   masses.get("active_kg") or target_mass, schema)
    return {"target_ref": f"{die}/{config}" if die and config else "", "target_mass_kg": target_mass,
            "motor_mass_kg": motor_mass, "motor_mass_basis": basis}


def gather(project_id: str, estimate: dict, schema: dict, *, mass_of=active_mass, ship_mass_of=None) -> Dict[str, Any]:
    """Everything ``cost_estimate.suggest`` needs for ``project_id``: also the motor mass a shipment is weighed with
    (``motor_mass_kg``: the complete mass when known, else the active mass x 1.3; ``motor_mass_basis`` says which)."""
    ship = ship_data(estimate, schema, mass_of=mass_of, ship_mass_of=ship_mass_of)
    return {**ship, "productions": productions(mass_of), "estimates": other_estimates(project_id, schema)}
