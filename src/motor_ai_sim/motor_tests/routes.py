"""Routes of the Tests section.

Production side (factory + our engineers; ``access.resolve``)::

    GET    /api/motor-tests/{die}/{config}                     tests + comparison with the simulation + what the caller may do
    POST   /api/motor-tests/{die}/{config}/tests               add a test          (admin, production owner/engineer, factory)
    PUT    /api/motor-tests/{die}/{config}/tests/{test_id}     replace a test      (admin, production owner/engineer)
    DELETE /api/motor-tests/{die}/{config}/tests/{test_id}     delete a test       (admin, production owner/engineer)
    PATCH  /api/motor-tests/{die}/{config}/settings            tests_visible_to_customer (admin, production owner/engineer)
    GET    /api/motor-tests/{die}/{config}/audit               the change log      (admin, production owner/engineer)

Project side (read-only, Development)::

    GET    /api/projects/{project_id}/motors/tests                       which linked motors have tests the caller may see
    GET    /api/projects/{project_id}/motors/tests?die=..&config=..      one motor's tests + comparison

Every change is appended to ``audit.jsonl`` (who, when, action, the record before/after).  Nothing here solves.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, Response

from motor_ai_sim.motor_tests import access as A
from motor_ai_sim.motor_tests import comparison, model_source, models, store
from motor_ai_sim.motor_tests.compare import TIPS

router = APIRouter(prefix="/api/motor-tests", tags=["motor-tests"])
project_router = APIRouter(prefix="/api/projects/{project_id}/motors", tags=["motor-tests"])


def _names(die: str, config: str) -> tuple:
    from motor_ai_sim.routes import family
    return family._check_name(die, "die"), family._check_name(config, "configuration")


def _documents(die: str, config: str, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The production documents a test may name, as the caller may see them (the factory never sees internal ones)."""
    if not ctx.get("production"):
        return []
    from motor_ai_sim.production import store as PS
    docs = (PS.load_file(die, config, "documents.json") or {}).get("documents", [])
    hide_internal = not ctx.get("admin") and ctx.get("role") == "oem"
    return [{"id": d.get("id"), "file_id": d.get("file_id"), "name": d.get("name")}
            for d in docs if isinstance(d, dict) and not (hide_internal and d.get("internal"))]


def _check_documents(rec: Dict[str, Any], allowed: List[Dict[str, Any]]) -> None:
    ids = {d.get("id") for d in allowed}
    bad = [d for d in rec.get("documents") or [] if d not in ids]
    if bad:
        raise HTTPException(422, detail=f"documents: not a document of this production: {', '.join(bad)}")


def _entry(who: str, action: str, test_id: str, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"time": models.now(), "who": who, "action": action, "entity": "test", "entity_id": test_id,
            "snapshot": snapshot or {}}


def _payload(die: str, config: str, *, can: Dict[str, bool], documents: Optional[List[Dict[str, Any]]] = None,
             model_die: Optional[str] = None) -> Dict[str, Any]:
    doc = store.load(die, config)
    model = model_source.collect(model_die or die, config)
    return {"die": die, "config": config, "settings": doc.get("settings") or {}, "can": can,
            "tests": doc.get("tests") or [], "compare": comparison.compare_all(doc, model),
            "model": {"available": bool(model.get("available")), "duties": len(model.get("duties") or []),
                      "noload": [r.get("duty") for r in model.get("noload") or []],
                      "magnet": (model.get("magnet") or {}).get("card"),
                      "has_bearings": bool(model.get("has_bearings")), "end3d": model.get("end3d")},
            "tips": TIPS, **({"documents": documents} if documents is not None else {})}


async def _body(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:                                       # noqa: BLE001
        raise HTTPException(400, detail="a JSON body is expected")


@router.get("/{die}/{config}")
def get_tests(request: Request, die: str, config: str, response: Response):
    die, config = _names(die, config)
    ctx = A.resolve(request, die, config)
    response.headers["Cache-Control"] = "no-store"
    return _payload(die, config, can={"add": bool(ctx["can_add"]), "edit": bool(ctx["can_edit"]),
                                      "settings": bool(ctx["can_edit"])},
                    documents=_documents(die, config, ctx))


@router.post("/{die}/{config}/tests")
async def add_test(request: Request, die: str, config: str):
    die, config = _names(die, config)
    ctx = A.resolve(request, die, config)
    A.require_add(ctx)
    who = A.email(ctx["user"])
    try:
        rec = models.normalize(await _body(request), who=who)
    except models.TestInputError as e:
        raise HTTPException(422, detail=str(e))
    _check_documents(rec, _documents(die, config, ctx))
    store.mutate(die, config, lambda d: d["tests"].append(rec),
                 audit=lambda _r: _entry(who, "create", rec["id"], {"after": rec}))
    return rec


def _find(doc: Dict[str, Any], test_id: str) -> Dict[str, Any]:
    for t in doc.get("tests") or []:
        if isinstance(t, dict) and t.get("id") == test_id:
            return t
    raise HTTPException(404, detail="Test not found")


@router.put("/{die}/{config}/tests/{test_id}")
async def edit_test(request: Request, die: str, config: str, test_id: str):
    die, config = _names(die, config)
    ctx = A.resolve(request, die, config)
    A.require_edit(ctx)
    who = A.email(ctx["user"])
    body = await _body(request)
    allowed = _documents(die, config, ctx)

    def mut(d):
        old = _find(d, test_id)
        try:
            new = models.normalize(body, who=who, previous=old)
        except models.TestInputError as e:
            raise HTTPException(422, detail=str(e))
        _check_documents(new, allowed)
        d["tests"] = [new if (isinstance(t, dict) and t.get("id") == test_id) else t for t in d["tests"]]
        return old, new
    old, new = store.mutate(die, config, mut,
                            audit=lambda r: _entry(who, "update", test_id, {"before": r[0], "after": r[1]}))
    return new


@router.delete("/{die}/{config}/tests/{test_id}")
def delete_test(request: Request, die: str, config: str, test_id: str):
    die, config = _names(die, config)
    ctx = A.resolve(request, die, config)
    A.require_edit(ctx)
    who = A.email(ctx["user"])

    def mut(d):
        old = _find(d, test_id)
        d["tests"] = [t for t in d["tests"] if not (isinstance(t, dict) and t.get("id") == test_id)]
        return old
    store.mutate(die, config, mut, audit=lambda r: _entry(who, "delete", test_id, {"before": r}))
    return {"ok": True}


@router.patch("/{die}/{config}/settings")
async def patch_settings(request: Request, die: str, config: str):
    die, config = _names(die, config)
    ctx = A.resolve(request, die, config)
    A.require_edit(ctx)
    body = await _body(request)
    if not isinstance(body, dict) or not isinstance(body.get("tests_visible_to_customer"), bool):
        raise HTTPException(422, detail="tests_visible_to_customer: true | false expected")
    flag = bool(body["tests_visible_to_customer"])

    def mut(d):
        old = bool((d.get("settings") or {}).get("tests_visible_to_customer"))
        d.setdefault("settings", {})["tests_visible_to_customer"] = flag
        return old
    who = A.email(ctx["user"])
    store.mutate(die, config, mut, audit=lambda r: _entry(who, "settings", "tests_visible_to_customer",
                                                          {"before": r, "after": flag}))
    return {"tests_visible_to_customer": flag}


@router.get("/{die}/{config}/audit")
def get_audit(request: Request, die: str, config: str):
    die, config = _names(die, config)
    ctx = A.resolve(request, die, config)
    A.require_edit(ctx)
    return {"audit": store.read_audit(die, config)}


# ---------------------------------------------------------------------------
# project (Development) view: read-only
# ---------------------------------------------------------------------------

def _project_staff(user: Dict[str, Any], role: Optional[str]) -> bool:
    from motor_ai_sim.development import access as DA
    return DA.is_engineer(user) or role in ("owner", "engineer")


@project_router.get("/tests")
def project_tests(project_id: str, request: Request, response: Response, die: Optional[str] = None,
                  config: Optional[str] = None, show_all: bool = False):
    response.headers["Cache-Control"] = "no-store"
    from motor_ai_sim.development import access as DA
    from motor_ai_sim.development import store as DS
    from motor_ai_sim.development.motors import _project_motor
    if not die or not config:
        user, project, role = DA.require_project(request, project_id)
        staff = _project_staff(user, role)
        out = []
        for link in project.get("linked_motors") or []:
            if not isinstance(link, dict) or not link.get("die") or not link.get("config"):
                continue
            d, c = str(link["die"]), str(link["config"])
            try:
                if not store.exists(d, c):
                    continue
                doc = store.load(d, c)
            except ValueError:
                continue
            visible = bool((doc.get("settings") or {}).get("tests_visible_to_customer"))
            if not ((staff or visible) and doc.get("tests")):
                continue
            # the SAME gate as one motor's detail: linked to THIS project, a trial variant only with show_all, and the
            # caller's grant on the motor - a motor that fails it is not named at all
            try:
                _project_motor(project_id, request, d, c, show_all)
            except HTTPException:
                continue
            out.append({"die": d, "config": c, "count": len(doc["tests"]), "visible_to_customer": visible})
        return {"motors": out}
    user, role, open_die, cfg_name, _path, _cfg, link = _project_motor(project_id, request, die, config, show_all)
    key_die = str(link.get("die") or open_die)
    doc = store.load(key_die, cfg_name)
    staff = _project_staff(user, role)
    if not staff and not bool((doc.get("settings") or {}).get("tests_visible_to_customer")):
        raise HTTPException(404, detail="No tests")
    if not staff:
        DS.append_audit(project_id, A.email(user), "motor.tests.read", "motor", f"{key_die}/{cfg_name}")
    return _payload(key_die, cfg_name, can={"add": False, "edit": False, "settings": False}, model_die=open_die)
