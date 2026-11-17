"""``GET /api/overview``: the owner's Overview (admins only; owner 2026-11-16).

One answer that says, for every product item (a motor configuration or a board revision), which projects use it and which
productions make it, with the Active / Parked / Archived state of each.  The page puts the catalog tree (``/api/family/tree``:
die names, slots / poles, pictures) around it.

Sources, all READ here, nothing written:
* the live project records (``linked_motors``: current / trial) and the live production records: what is true now;
* the id registry (``object_registry``): the stable ids, the layer(s) an item lives in, removed items, the
  ``analysis_controller`` links and the review list.  Before the first backfill the answer is complete without the ids;
* the display states (``display_state``);
* the archived dies (``archived_dies``): this workspace's die archive (``die_archive``) and the shared dies retired for all
  users, which the catalog tree no longer lists; the page shows them under "Archived" with Restore.

The shown STAGE of a project is derived, never written back (owner's finding 2026-11-17: "Specification" everywhere told him
nothing): a linked item has a production -> ``in_production``; a linked motor has stored results -> ``computed``; else the
project's own status (``spec`` / ``spec_submitted`` / ``design`` ... ``closed``).  The three state lines of the object model
replace this later.

A project about a controller (its title says so) with no linked item, while a board (electronics production) has no
project, is not guessed into a link: both sides go to the review list (``project_without_item``) with what the owner can do.

"Works with" pairs the motors and the boards that the same project links (a project for a motor and its controller); the
controller a motor is analysed with (``analysis_controller``) is listed apart and is not a pairing.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request

from motor_ai_sim import display_state as DS
from motor_ai_sim import object_registry as OR
from motor_ai_sim import production_lifecycle as PL
from motor_ai_sim.production import access as prod_access

router = APIRouter(prefix="/api/overview", tags=["overview"])


def _require_admin(request: Request) -> dict:
    user = prod_access.require_signed_in(request)
    if str(user.get("role") or "").lower() != "admin":
        raise HTTPException(status_code=403, detail="Admin access required.")
    return user


def _title(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("en") or value.get("zh") or "")
    return str(value or "")


#: die -> (monotonic time, configurations with stored results); one minute is fresh enough for "computed"
_RESULTS_CACHE: Dict[str, Tuple[float, frozenset]] = {}
_RESULTS_TTL_S = 60.0


def _computed_configs(die: str) -> frozenset:
    """The configurations of ``die`` with any stored result: ONE layered read per die (``duty_results.index`` with the
    shared parsed-store memo), cached for a minute.  Before (#721) it was a full ``get`` per motor: 6-10 s per page."""
    now = time.monotonic()
    hit = _RESULTS_CACHE.get(die)
    if hit and now - hit[0] < _RESULTS_TTL_S:
        return hit[1]
    try:
        from motor_ai_sim import duty_results
        cfgs = frozenset(cfg for cfg, duties in duty_results.index(die, shared=True).items() if duties)
    except Exception:                                       # noqa: BLE001 - no store: not computed
        cfgs = frozenset()
    _RESULTS_CACHE[die] = (now, cfgs)
    return cfgs


def _has_results(die: str, cfg: str) -> bool:
    return cfg in _computed_configs(die)


#: the derived stage of a motor, in order (owner 2026-11-17: "production starts when the money from the customer arrives")
STAGES = ("specification", "simulation", "confirmation", "in_production", "finished")


def motor_stage(*, lifecycle: Optional[str], has_production: bool, offered: bool, computed: bool, spec_submitted: bool) -> Optional[str]:
    """The one derived stage of a motor, from stored facts only (never guessed):

    * ``finished``      its production's lifecycle is ``finished`` (production_lifecycle, admin-set);
    * ``in_production`` its production's lifecycle is ``in_production`` (started: the customer's payment arrived);
    * ``confirmation``  offered to the customer and waiting for agreement / payment: an open project links it as current AND
                        marks it its recommended variant (the existing staff flag ``recommended`` on the link), or a
                        production record exists that has not started (``prepared``);
    * ``simulation``    stored results exist (computing or computed, not offered);
    * ``specification`` a project that links it has a submitted specification, no results yet;
    * ``None``          none of these."""
    if lifecycle == "finished":
        return "finished"
    if lifecycle == "in_production":
        return "in_production"
    if offered or has_production:
        return "confirmation"
    if computed:
        return "simulation"
    if spec_submitted:
        return "specification"
    return None


def build(projects: List[dict], productions: List[dict], registry: Dict[str, Any], states: Dict[str, Any],
          has_results=_has_results, lifecycle: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The Overview answer from already loaded records (pure apart from ``has_results``: the route and the tests call it)."""
    idx = OR.Index(registry)
    items: Dict[str, Dict[str, Any]] = {}

    def item(die: str, cfg: str, kind: str = "") -> Dict[str, Any]:
        key = f"{die}/{cfg}"
        cur = items.get(key)
        if cur is None:
            iid = idx.resolve("item", "catalog", key) or idx.resolve("item", "production", key)
            obj = idx.obj(iid) if iid else None
            cur = items[key] = {
                "die": die, "config": cfg, "item_id": iid, "kind": (obj or {}).get("item_kind") or kind or "motor",
                "state": (obj or {}).get("state", ""), "layers": (obj or {}).get("layers", []),
                "projects": [], "productions": [], "works_with": [],
                "analysis_controllers": sorted(l["to"].split(":", 1)[1] for l in idx.links_from.get(iid or "", [])
                                               if l.get("kind") == "analysis_controller"),
            }
        if kind == "board":
            cur["kind"] = "board"
        return cur

    prod_rows: Dict[str, Dict[str, Any]] = {}
    for rec in productions:
        die, cfg = str(rec.get("die") or ""), str(rec.get("config") or "")
        if not die or not cfg:
            continue
        key = f"{die}/{cfg}"
        prod_rows[key] = {
            "die": die, "config": cfg, "title": _title(rec.get("title")), "status": rec.get("status") or "active",
            "current_revision": str(rec.get("current_revision") or ""), "order_quantity": rec.get("order_quantity") or 0,
            "product_type": rec.get("product_type") or "motor", "updated_at": rec.get("updated_at") or "",
            "production_id": idx.resolve("production", "production", key),
            "display": DS.effective("production", DS.object_key("production", die=die, config=cfg), rec.get("status"), states),
        }
        prod_rows[key]["lifecycle"] = PL.state_of(die, cfg, lifecycle if lifecycle is not None else {})["state"]
        item(die, cfg, "board" if rec.get("product_type") == "electronics" else "")["productions"].append(key)

    proj_rows: Dict[str, Dict[str, Any]] = {}
    offered: set = set()
    for p in projects:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        linked = [m for m in p.get("linked_motors") or [] if isinstance(m, dict) and m.get("die") and m.get("config")]
        sub = p.get("spec_submitted") if isinstance(p.get("spec_submitted"), dict) else None
        proj_rows[pid] = {
            "id": pid, "title": str(p.get("title") or ""), "customer_org": str(p.get("customer_org") or ""),
            "status": str(p.get("status") or ""), "updated_at": str(p.get("updated_at") or ""),
            "spec_submitted": sub, "project_id": idx.resolve("project", "projects", pid),
            "motors": [f"{m['die']}/{m['config']}" for m in linked],
            "display": DS.effective("project", DS.object_key("project", project_id=pid), p.get("status"), states),
        }
        for m in linked:
            item(str(m["die"]), str(m["config"]))["projects"].append(
                {"id": pid, "link_status": str(m.get("status") or "current")})
            if isinstance(m.get("recommended"), dict):                  # the staff flag: the variant offered to the customer
                offered.add((f"{m['die']}/{m['config']}", pid))

    computed = {k: bool(has_results(v["die"], v["config"])) for k, v in items.items() if v["kind"] == "motor"}
    for k, v in items.items():
        v["computed"] = computed.get(k, False)
        prods = [prod_rows[x] for x in v["productions"] if x in prod_rows]
        life = next((p["lifecycle"] for p in prods if p["lifecycle"] == "in_production"), None) or \
            next((p["lifecycle"] for p in prods if p["lifecycle"] == "finished"), None)
        open_links = [l for l in v["projects"] if proj_rows.get(l["id"], {}).get("status") != "closed"]
        v["stage"] = motor_stage(
            lifecycle=life, has_production=bool(prods),
            offered=any(l["link_status"] != "trial" and (k, l["id"]) in offered for l in open_links),
            computed=v["computed"],
            spec_submitted=any(proj_rows.get(l["id"], {}).get("spec_submitted") for l in open_links))
    for row in proj_rows.values():
        if row["status"] == "closed":
            row["stage"] = "closed"
        elif any(items.get(k, {}).get("stage") for k in row["motors"]):
            # the project is as far as its most advanced motor (the one derived stage, ``motor_stage``)
            row["stage"] = max((items[k]["stage"] for k in row["motors"] if k in items and items[k].get("stage")), key=STAGES.index)
        else:
            row["stage"] = "spec_submitted" if row["status"] == "spec" and row["spec_submitted"] else row["status"]

    review = list(registry.get("review") or [])
    orphan_boards = sorted(k for k, v in items.items() if v["kind"] == "board" and not v["projects"])
    for pid in sorted(pid for pid, r in proj_rows.items() if not r["motors"]):
        # only a project that says it is about a controller: a new customer specification without a motor is not a review item
        if orphan_boards and "controller" in proj_rows[pid]["title"].lower():
            review.append({"code": "project_without_item", "where": proj_rows[pid]["title"], "project": pid,
                           "candidates": orphan_boards})

    for pid, row in proj_rows.items():                    # works with: motors and boards of the same project
        kinds = {k: items[k]["kind"] for k in row["motors"] if k in items}
        boards = [k for k, v in kinds.items() if v == "board"]
        for k, v in kinds.items():
            others = boards if v != "board" else [x for x, y in kinds.items() if y != "board"]
            for o in others:
                if o != k and o not in items[k]["works_with"]:
                    items[k]["works_with"].append(o)

    return {
        "items": items, "projects": proj_rows, "productions": prod_rows,
        "no_item_yet": sorted(pid for pid, r in proj_rows.items() if not r["motors"]),
        "review": review,
        "registry": ({"objects": len(registry.get("objects") or {}),
                      "backfilled_at": (registry.get("backfills") or [{}])[-1].get("at", "")}
                     if registry.get("objects") else None),
    }


@router.get("")
def overview(request: Request):
    _require_admin(request)
    from motor_ai_sim.development import store as project_store
    from motor_ai_sim.production import routes as PR
    out = build(project_store.list_projects(), PR._production_list_for("", True), OR.load(), DS.load()["states"],
                lifecycle=PL.load()["states"])
    out["archived_dies"] = archived_dies()
    return out


def archived_dies(scope: Optional[str] = None) -> List[Dict[str, Any]]:
    """The dies that left the catalog listing, for the "Archived" view: this workspace's archive
    (``motor_ai_sim.die_archive``) and the shared dies retired for all users (tombstones).  ``[]`` on any failure."""
    out: List[Dict[str, Any]] = []
    try:
        from motor_ai_sim import die_archive as DA
        if scope is None:
            from motor_ai_sim.workspace import workspace
            scope = str(workspace().id or "")
        out += [{**r, "retired": False} for r in DA.for_scope(scope)]
    except Exception:                                       # noqa: BLE001
        pass
    try:
        from motor_ai_sim import workspace as _WS
        out += [{"die": k, "layer": "shared", "by": (v or {}).get("by", ""), "at": (v or {}).get("at", ""), "reason": "",
                 "configs": None, "retired": True} for k, v in sorted(_WS._load_tombstones().items())]
    except Exception:                                       # noqa: BLE001
        pass
    return sorted(out, key=lambda r: str(r["die"]))
