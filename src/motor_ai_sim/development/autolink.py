"""A new variant of a project's motor joins the project at once (owner 2026-10-27).

Rule: "link every variant to the project right away and make it the latest one, so everybody sees what is being done in real
time".  When a configuration is created in a die (``POST /api/family/config``, a configuration copy, "save as new die"), and
any configuration of that same die - or of the die it was copied from (``die.yaml`` ``derived_from``) - is linked to a project,
the new configuration is linked to the same project(s) with status ``current`` and an audit entry ``motor.autolink``.

It never links into a project the die is not already in, never steals a motor that another project holds, and never fails
the route that created the configuration (the caller logs and moves on).  No owner check here on purpose: the person who may
create a configuration in that die is the person who is already working on the project's motor; the link only adds what the
project already shows for the sibling variants.
"""
from __future__ import annotations

import copy
import logging
from typing import Dict, List, Optional

from motor_ai_sim import users
from motor_ai_sim.development import motors, store

log = logging.getLogger(__name__)

#: how many ``derived_from`` generations are followed (a die copied from a die copied from ...)
MAX_ANCESTORS = 4


def _names(die: str) -> set:
    names = {str(die).casefold()}
    try:
        from motor_ai_sim.workspace import split_published_label
        names.add(split_published_label(str(die))[0].casefold())
    except Exception:                                       # noqa: BLE001
        pass
    return names


def source_dies(die: str, copied_from: Optional[str] = None) -> List[str]:
    """``die`` and the dies it descends from (explicit ``copied_from`` first, then ``die.yaml`` ``derived_from`` upwards)."""
    out = [str(die)]
    if copied_from and str(copied_from) not in out:
        out.append(str(copied_from))
    try:
        from motor_ai_sim.routes import family
        current = out[-1]
        for _ in range(MAX_ANCESTORS):
            doc = family._load_yaml(family._die_file(current), "die")
            parent = str(((doc or {}).get("derived_from") or {}).get("die") or "").strip()
            if not parent or parent in out:
                break
            out.append(parent)
            current = parent
    except Exception:                                       # noqa: BLE001 - no ancestry is the ordinary case
        pass
    return out


def _projects_of(sources: List[str], die: str) -> Dict[str, str]:
    """``{project_id: canonical die to link under}`` for every project that holds a configuration of one of ``sources``."""
    wanted = set()
    for s in sources:
        wanted |= _names(s)
    own = _names(die)
    found: Dict[str, str] = {}
    for key, row in (store.load_index().get("motors") or {}).items():
        if not isinstance(row, dict) or not row.get("project_id"):
            continue
        rdie = str(row.get("die") or str(key).partition("\0")[0])
        if _names(rdie) & wanted:
            # the same die keeps the project's own spelling of it (a published label); a die copied from a linked one
            # is linked under its own name
            found.setdefault(str(row["project_id"]), rdie if _names(rdie) & own else str(die))
    return found


def _link(project_id: str, die: str, config: str, actor: str) -> bool:
    key = motors._key(die, config)
    grant_key = f"{die}/{config}"
    from motor_ai_sim.development.models import now
    with motors.transaction(), users._LOCK:
        project = store.get_project(project_id)
        if project is None:
            return False
        index = store.load_index()
        if (index.get("motors") or {}).get(key):
            return False                                    # already linked (here or in another project): never steal
        if any(isinstance(m, dict) and motors._key(m.get("die", ""), m.get("config", "")) == key
               for m in project.get("linked_motors", [])):
            return False
        old_project = copy.deepcopy(project)
        stamp = now()
        project.setdefault("linked_motors", []).append({"die": die, "config": config, "status": "current"})
        project.setdefault("motor_history", []).append(
            {"die": die, "config": config, "linked_at": stamp, "linked_by": actor or "autolink"})
        project["updated_at"] = stamp
        try:
            motors._sync_grants(project, key, grant_key)
            store.mutate_project(project_id, lambda doc: doc.update(project))
            from motor_ai_sim import json_store

            def update(doc):
                doc.setdefault("motors", {})[key] = {"project_id": project_id, "die": die, "config": config}
            json_store.mutate_json(store.index_path(), update, default={"motors": {}})
        except Exception:
            store.save_json(project_id, "project.json", old_project)
            store.save_index(index)
            raise
    store.append_audit(project_id, actor or "autolink", "motor.autolink", "motor", f"{die}/{config}")
    try:
        motors._capture_project_motor_snapshot(project_id, die, config, actor or "autolink", "linked motor")
    except Exception as exc:                                # noqa: BLE001 - the history copy is best effort, as for a manual link
        log.warning("project %s autolinked %s/%s but could not capture a snapshot: %s", project_id, die, config, exc)
    return True


def autolink_new_config(die: str, config: str, *, copied_from: Optional[str] = None, actor: str = "",
                        sources: Optional[List[str]] = None) -> List[str]:
    """Link ``die/config`` to every project that already holds a sibling variant.  Returns the project ids it joined."""
    die, config = str(die or "").strip(), str(config or "").strip()
    if not die or not config or any(x in die + config for x in ("/", "\\", "..")):
        return []
    sources = sources or source_dies(die, copied_from)
    joined: List[str] = []
    for project_id, canonical in sorted(_projects_of(sources, die).items()):
        try:
            if _link(project_id, canonical, config, str(actor or "").strip().lower()):
                joined.append(project_id)
        except Exception as exc:                            # noqa: BLE001 - never fails the route that made the variant
            log.warning("autolink of %s/%s into project %s failed: %s", canonical, config, project_id, exc)
    return joined
