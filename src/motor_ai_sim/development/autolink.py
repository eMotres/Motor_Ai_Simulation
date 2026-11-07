"""A new variant of a project's motor joins the project at once (owner 2026-10-27).

Rule: "link every variant to the project right away and make it the latest one, so everybody sees what is being done in real
time".  When a configuration is created in a die (``POST /api/family/config``, a configuration copy, "save as new die"), and
any configuration of that same die - or of the die it was copied from (``die.yaml`` ``derived_from``) - is linked to a project,
the new configuration is linked to the same project(s) with status ``current`` and an audit entry ``motor.autolink``.

WHICH project (2026-11-02, bug: a copied configuration went to the alphabetically first project that held ANY
configuration of the die - a motor and two generators of one die sit in three projects): the copy joins the project that
holds its SOURCE configuration - the motor it was copied from (``source_config``, in ``copied_from`` or the same die).
The source in no project: nothing is linked.  The source held by more than one project (spellings of one die under two
keys): nothing is linked and each candidate project gets a ``motor.autolink_skipped`` audit note - never a guess.  A
configuration with no known source (created from scratch) falls back to the die family, and only when exactly ONE project
holds that family; several projects = the same skip note.

It never links into a project the die is not already in, never steals a motor that another project holds, and never fails
the route that created the configuration (the caller logs and moves on).

WHO (2026-11-07): only a verified admin writing the shared catalog starts a link (:func:`writes_shared_catalog`).  A link
stores a grant for every member, and such a grant is for a configuration of the catalog.  A workspace copy stays with its
author: it never joins a project and never makes a grant.
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


def _projects_holding(source_die: str, source_config: str, die: str) -> Dict[str, str]:
    """``{project_id: canonical die to link under}`` for every project that holds exactly ``source_die/source_config``."""
    wanted, own, cfg = _names(source_die), _names(die), str(source_config).casefold()
    found: Dict[str, str] = {}
    for key, row in (store.load_index().get("motors") or {}).items():
        if not isinstance(row, dict) or not row.get("project_id"):
            continue
        kdie, _, kcfg = str(key).partition("\0")
        rdie, rcfg = str(row.get("die") or kdie), str(row.get("config") or kcfg)
        if rcfg.casefold() == cfg and _names(rdie) & wanted:
            found.setdefault(str(row["project_id"]), rdie if _names(rdie) & own else str(die))
    return found


def _note_skipped(candidates: List[str], die: str, config: str, source: str, reason: str, actor: str) -> None:
    """An ambiguous source: no link, one audit note in every candidate project (no other project's id in it)."""
    for project_id in candidates:
        try:
            store.append_audit(project_id, actor or "autolink", "motor.autolink_skipped", "motor", f"{die}/{config}",
                               {"reason": reason, "source": source, "candidates": len(candidates)})
        except Exception as exc:                            # noqa: BLE001 - the note is best effort
            log.warning("autolink: could not note the skipped link of %s/%s in project %s: %s", die, config, project_id, exc)
    log.warning("autolink: %s/%s not linked - %s (%d candidate projects)", die, config, reason, len(candidates))


def writes_shared_catalog() -> bool:
    """Does this call's write land in the shared catalog?  A single-layer install has only that one (and only an admin writes
    it); with layering, only a verified admin's write does - with no bound caller, the answer is no."""
    from motor_ai_sim import workspace
    return not workspace.layering() or workspace.is_verified_admin_request()


def autolink_new_config(die: str, config: str, *, copied_from: Optional[str] = None, source_config: Optional[str] = None,
                        actor: str = "", sources: Optional[List[str]] = None) -> List[str]:
    """Link ``die/config`` to the ONE project that holds its source motor.  Returns the project ids it joined (0 or 1).

    ``source_config`` names the configuration the new one was copied from, in ``copied_from`` (another die) or in ``die``
    itself.  Without it the die family decides, and only when exactly one project holds that family."""
    die, config = str(die or "").strip(), str(config or "").strip()
    if not die or not config or any(x in die + config for x in ("/", "\\", "..")):
        return []
    if not writes_shared_catalog():
        return []
    actor = str(actor or "").strip().lower()
    src_cfg = str(source_config or "").strip()
    if src_cfg:
        src_die = str(copied_from or die).strip()
        source = f"{src_die}/{src_cfg}"
        candidates = _projects_holding(src_die, src_cfg, die)
        reason = "the source configuration is held by more than one project"
    else:
        source = str(copied_from or die)
        candidates = _projects_of(sources or source_dies(die, copied_from), die)
        reason = "no source configuration named and the die is in more than one project"
    if not candidates:
        return []                                           # the source motor is in no project: nothing to join
    if len(candidates) > 1:
        _note_skipped(sorted(candidates), die, config, source, reason, actor)
        return []
    (project_id, canonical), = candidates.items()
    try:
        if _link(project_id, canonical, config, actor):
            return [project_id]
    except Exception as exc:                                # noqa: BLE001 - never fails the route that made the variant
        log.warning("autolink of %s/%s into project %s failed: %s", canonical, config, project_id, exc)
    return []
