"""Data the cost-estimate ``suggest`` works from (owner 2026-11-02): real cost rows and tooling of motors already built
(Production ``costs.json`` / ``costs_tooling.json`` / work lines), the cost estimates of OTHER projects (their test and
logistics costs), the test records of the analog motors (``motor_tests``: only as a hint, they carry no cost), and the
masses / dimensions of the motors (``motor_mass.app_motor_mass`` = ``masses.compute_masses``, plus the geometry).

Read-only: nothing here writes anything.  Every reader fails soft (a broken production or project is left out), because a
suggestion must never break the estimate."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

#: the production BOM lists: (list name, BOM file, costs file).  Motor list: ``unit_price`` is the cost of the line PER
#: MOTOR.  Tooling list: ``unit_price`` is the price of ONE piece, the line costs price x quantity (``production.routes``).
_LISTS = (("motor", "bom.json", "costs.json"), ("tooling", "bom_tooling.json", "costs_tooling.json"))


def motor_basis(die: str, config: str) -> Dict[str, float]:
    """The scale basis of one motor: active / magnet / copper / steel mass [kg] from the app's mass model, outer diameter
    and stack length [mm] from the geometry.  A value that cannot be read is left out (never zero)."""
    out: Dict[str, float] = {}
    if not die or not config:
        return out
    try:
        from motor_ai_sim.modules import motor_mass as MM
        mm = MM.app_motor_mass(die, config)
        c = mm.components
        for key, value in (("active_mass_kg", c.get("active")), ("magnet_mass_kg", c.get("mag")),
                           ("copper_mass_kg", c.get("cu")),
                           ("steel_mass_kg", (c.get("stator") or 0.0) + (c.get("rotor") or 0.0))):
            if value and value > 0:
                out[key] = round(float(value), 4)
    except Exception:                                       # noqa: BLE001 - no mass is "unknown", not zero
        log.debug("cost estimate: no mass for %s/%s", die, config, exc_info=True)
    try:
        from motor_ai_sim.fscache import read_yaml
        from motor_ai_sim.modules import motor_mass as MM
        ddir = MM.find_die_dir(die)
        if ddir is not None and (ddir / f"{config}.yaml").is_file():
            geo = dict((read_yaml(ddir / "die.yaml") or {}).get("geometry") or {})
            geo.update((read_yaml(ddir / f"{config}.yaml") or {}).get("geometry_overrides") or {})
            od = geo.get("stator_diameter") or (2 * float(geo["stator_outer_radius"]) if geo.get("stator_outer_radius") else None)
            if od and float(od) > 0:
                out["outer_diameter_mm"] = round(float(od), 3)
            if geo.get("motor_length") and float(geo["motor_length"]) > 0:
                out["stack_length_mm"] = round(float(geo["motor_length"]), 3)
    except Exception:                                       # noqa: BLE001
        log.debug("cost estimate: no geometry for %s/%s", die, config, exc_info=True)
    return out


def _name(part: Optional[dict], lang: str) -> str:
    name = (part or {}).get("name")
    if isinstance(name, dict):
        return str(name.get(lang) or "")
    return str(name or "") if lang == "en" else ""


#: what a motor-list price is for, read from the cost row's ``price_per`` field or its note.  The Production sheet labels
#: the motor-list price "Price / motor", but a supplier quote is often typed per piece (a magnet segment), per kg or for
#: the whole order - divided by the whole motor's mass, such a price gives a price per kg that is off by the piece count.
PRICE_PER = ("motor", "piece", "kg", "order")
_PER_KG = re.compile(r"(?:per|/)\s*kg\b|每\s*(?:公斤|千克)|/\s*(?:公斤|千克)", re.I)
_PER_PIECE = re.compile(r"(?:per|/)\s*(?:piece|pieces|pc|pcs|pce|segment|segments|magnet|magnets|ea)\b|\beach\b"
                        r"|每\s*(?:件|片|个|块|只)|/\s*(?:件|片|个|块|只)|单(?:件|片|个|块)价?", re.I)
_PER_ORDER = re.compile(r"(?:per|for the|whole)\s*(?:order|lot|batch)\b|整批|总价", re.I)
_PER_MOTOR = re.compile(r"(?:per|/)\s*(?:motor|set)\b|每\s*(?:台|套)|/\s*(?:台|套)", re.I)


def price_per(raw: Any, note: Any, list_name: str) -> str:
    """``motor`` | ``piece`` | ``kg`` | ``order``: an explicit ``price_per`` on the cost row, else what its note says, else
    the sheet's own convention (motor list: per motor; tooling list: per piece)."""
    explicit = str((raw or {}).get("price_per") or (raw or {}).get("price_unit") or "").strip().lower()
    explicit = {"pc": "piece", "pcs": "piece", "set": "motor", "lot": "order"}.get(explicit, explicit)
    if explicit in PRICE_PER:
        return explicit
    text = str(note or "")
    for kind, rx in (("kg", _PER_KG), ("piece", _PER_PIECE), ("order", _PER_ORDER), ("motor", _PER_MOTOR)):
        if rx.search(text):
            return kind
    return "piece" if list_name == "tooling" else "motor"


def production_rows(die: str, config: str, motors: Optional[int] = None) -> List[Dict[str, Any]]:
    """The priced rows of one production: ``{list: motor | tooling | extras, row_id, name_en, name_zh, material, amount,
    currency, unit_price, qty_per_motor, price_per}``.  ``amount`` = the cost PER MOTOR (motor list, work lines: a per-piece
    price x pieces per motor, an order price / the order's ``motors``; ``None`` for a price per kg or an order price
    without an order quantity) or the price of the whole tooling line (price x quantity)."""
    from motor_ai_sim.production import models as PM
    from motor_ai_sim.production import store as PS
    parts = {str(p.get("id")): p for p in (PS.load_file(die, config, "parts.json") or {}).get("parts", []) if isinstance(p, dict)}
    out: List[Dict[str, Any]] = []
    for list_name, bom_file, costs_file in _LISTS:
        bom = (PS.load_file(die, config, bom_file) or {}).get("rows", [])
        costs = PS.load_file(die, config, costs_file) or {}
        qty = {str(b.get("part_id")): max(0, int(b.get("qty_per_motor") or 1)) for b in bom if isinstance(b, dict)}
        for raw in costs.get("rows") or []:
            row = PM.normalize_cost_row(raw)
            pid = row["part_id"]
            part = parts.get(pid)
            if row["unit_price"] is None or (part or {}).get("kind") == "assembly" or (bom and pid not in qty):
                continue
            n = qty.get(pid, 1)
            unit_price = float(row["unit_price"])
            per = price_per(raw, row.get("note"), list_name)
            amount: Optional[float]
            if per == "piece":
                amount = unit_price * n
            elif per == "kg":
                amount = None
            elif per == "order":
                amount = unit_price / motors if motors else None
            else:
                amount = unit_price
            out.append({"list": list_name, "row_id": pid, "name_en": _name(part, "en"), "name_zh": _name(part, "zh"),
                        "material": str((part or {}).get("material") or ""), "amount": amount,
                        "currency": PM.normalize_currency(row["currency"]), "unit_price": unit_price,
                        "qty_per_motor": n, "price_per": per})
        if list_name == "motor":
            for raw in costs.get("extras") or []:
                e = PM.normalize_extra_cost(raw)
                if e["amount"] > 0:
                    out.append({"list": "extras", "row_id": e["id"], "name_en": _name(e, "en"), "name_zh": _name(e, "zh"),
                                "material": "", "amount": float(e["amount"]), "currency": e["currency"],
                                "unit_price": float(e["amount"]), "qty_per_motor": 1, "price_per": "motor"})
    return out


def productions(basis_of=motor_basis) -> List[Dict[str, Any]]:
    """Every production with at least one priced row, as an analog: ``{ref: "die/config", die, config, title, updated_at,
    basis, rows}``."""
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
            try:
                motors = max(0, int(rec.get("order_quantity") or 0)) or None
            except (TypeError, ValueError):
                motors = None
            rows = production_rows(die, config, motors)
            if not rows:
                continue
            title = rec.get("title") if isinstance(rec.get("title"), dict) else {}
            out.append({"ref": f"{die}/{config}", "die": die, "config": config,
                        "title": str(title.get("en") or title.get("zh") or f"{die} {config}"),
                        "updated_at": rec.get("updated_at"), "motors": motors, "basis": basis_of(die, config),
                        "rows": rows})
        except Exception:                                   # noqa: BLE001 - one broken production never hides the rest
            log.warning("cost estimate: production %s unreadable", path, exc_info=True)
    return out


def other_estimates(exclude_project: str) -> List[Dict[str, Any]]:
    """The stored cost estimates of every OTHER project (their filled lines are the test / logistics history)."""
    from motor_ai_sim.development import cost_estimate_store as CS
    from motor_ai_sim.development import store as DS
    out: List[Dict[str, Any]] = []
    for project in DS.list_projects():
        pid = str(project.get("id") or "")
        if not pid or pid == exclude_project:
            continue
        try:
            est = CS.load(pid)
        except Exception:                                   # noqa: BLE001
            est = None
        if isinstance(est, dict):
            out.append({"project_id": pid, "title": str(project.get("title") or pid), "basis": est.get("basis") or {},
                        "iterations": est.get("iterations") or [], "project_lines": est.get("project_lines") or {}})
    return out


def test_records(refs: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Per analog production: the number of distinct test days and records per kind (a hint for the bench time - the
    records carry no cost and no bench hours)."""
    from motor_ai_sim.motor_tests import store as MTS
    out: Dict[str, Dict[str, Any]] = {}
    for ref in refs:
        die, _s, config = str(ref).partition("/")
        try:
            if not die or not config or not MTS.exists(die, config):
                continue
            tests = [t for t in (MTS.load(die, config).get("tests") or []) if isinstance(t, dict)]
        except Exception:                                   # noqa: BLE001
            continue
        kinds: Dict[str, int] = {}
        for t in tests:
            kinds[str(t.get("kind") or "")] = kinds.get(str(t.get("kind") or ""), 0) + 1
        days = len({str(t.get("date")) for t in tests if t.get("date")})
        if tests:
            out[ref] = {"days": days, "kinds": kinds}
    return out


def gather(project_id: str, estimate: dict, *, basis_of=motor_basis) -> Dict[str, Any]:
    """Everything ``cost_estimate.suggest`` needs for ``project_id``."""
    target = (estimate.get("settings") or {}).get("target_motor") or {}
    target_ref = f"{target.get('die')}/{target.get('config')}" if target.get("die") and target.get("config") else ""
    prods = productions(basis_of)
    return {"target_ref": target_ref,
            "target_basis": basis_of(str(target.get("die") or ""), str(target.get("config") or "")) if target_ref else {},
            "productions": prods, "estimates": other_estimates(project_id),
            "tests": test_records(p["ref"] for p in prods)}
